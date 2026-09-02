"""Background dispatcher for deferred write callbacks (issue #175).

Extracted from :class:`~markdown_vault_mcp.vault.Vault` (issue #599)
so the git-commit dispatch concern lives apart from the index-write machinery
in :mod:`markdown_vault_mcp.indexing`.

A single daemon worker thread drains a FIFO queue, invoking the configured
``on_write`` callback (typically a git commit) off the write path, so write
methods return as soon as the FTS update lands.

Writes fired inside a :class:`~markdown_vault_mcp._commit_scope.CommitScope`
are buffered by scope token and dispatched together when that scope closes
(#1264), so one tool call yields one commit rather than one per file.

Buffering happens **only** for a callback that opted into
:data:`~markdown_vault_mcp.types.ACCEPTS_BATCH_ATTR`. Without that opt-in there
is nothing to group into, so holding the writes back would cost the delay and
the retained state and buy nothing: such a callback is fired per write, as it
arrives, exactly as before. A write with no owning scope — a background or
startup write — takes the same immediate path.

One tool call normally yields one commit, with one documented exception: a
:class:`_DrainMarker` reached mid-call flushes what is buffered so far, so a
call straddling a pull can land as two accurate commits rather than one.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, cast

from markdown_vault_mcp._commit_scope import current_commit_scope
from markdown_vault_mcp._identity import current_principal
from markdown_vault_mcp.types import (
    ACCEPTS_BATCH_ATTR,
    ACCEPTS_OLD_PATH_ATTR,
    ACCEPTS_PRINCIPAL_ATTR,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from markdown_vault_mcp._commit_scope import CommitScope
    from markdown_vault_mcp._identity import Principal
    from markdown_vault_mcp.types import (
        BatchAwareWriteCallback,
        PrincipalAwareWriteCallback,
        WriteBatchItem,
        WriteCallback,
        WriteOperation,
    )

logger = logging.getLogger(__name__)


class _DrainMarker:
    """Queue sentinel meaning 'all items enqueued before me are processed'.

    Unlike the ``None`` close-sentinel, the worker does NOT exit on this: it
    sets ``event`` and continues. FIFO ordering guarantees every real item
    enqueued before the marker has been processed when the worker reaches it.

    Reaching this marker also flushes every still-open commit scope. A scope
    normally closes when its tool call returns, but ``drain`` runs before a git
    pull specifically so queued commits land before the merge touches the
    working tree — leaving a buffered group uncommitted would let the pull
    merge over writes that are on disk but not yet in a commit.
    """

    __slots__ = ("event",)

    def __init__(self) -> None:
        self.event = threading.Event()


class _ScopeEnd:
    """Queue sentinel closing one commit scope, flushing its buffered writes.

    Enqueued by :class:`~markdown_vault_mcp._commit_scope.CommitScopeMiddleware`
    when a tool call returns. FIFO ordering guarantees every write that call
    fired precedes this marker, so no wait or handshake is needed.
    """

    __slots__ = ("token",)

    def __init__(self, token: int) -> None:
        self.token = token


class WriteCallbackDispatcher:
    """Run write callbacks on a single background thread, in FIFO order.

    The worker starts lazily on the first :meth:`fire` (only when a callback
    is configured) and is joined by :meth:`close`. A ``None`` callback makes
    :meth:`fire` a no-op. A callback that raises is logged and skipped; the
    worker keeps processing subsequent items.
    """

    def __init__(self, on_write: WriteCallback | None) -> None:
        """Store the callback; the worker is started lazily by :meth:`fire`.

        Args:
            on_write: Invoked as ``on_write(abs_path, content, operation)`` for
                each fired write, or ``None`` to disable dispatch entirely.
                A callback that sets the
                :data:`~markdown_vault_mcp.types.ACCEPTS_OLD_PATH_ATTR`
                attribute to ``True`` also receives ``old_path=`` on
                ``rename`` dispatches (#894).
        """
        self._on_write = on_write
        # Read the opt-ins once, here, rather than per dispatch: the callback
        # is fixed for the dispatcher's lifetime, and the worker loop is on
        # the write path.
        self._accepts_old_path = bool(getattr(on_write, ACCEPTS_OLD_PATH_ATTR, False))
        self._accepts_principal = bool(getattr(on_write, ACCEPTS_PRINCIPAL_ATTR, False))
        # A callback that cannot take a batch still gets every write, one call
        # at a time — grouping is an optimisation the callback opts into, never
        # a contract change third-party callbacks must absorb.
        self._accepts_batch = bool(getattr(on_write, ACCEPTS_BATCH_ATTR, False))
        self._queue: queue.Queue[
            tuple[
                Path,
                str,
                WriteOperation,
                Path | None,
                Principal | None,
                CommitScope | None,
            ]
            | _DrainMarker
            | _ScopeEnd
            | None
        ] = queue.Queue()
        # Buffered writes per open scope token, drained by _ScopeEnd (or by a
        # _DrainMarker, which must not report success over uncommitted work).
        self._open_scopes: dict[int, tuple[CommitScope, list[WriteBatchItem]]] = {}
        self._worker: threading.Thread | None = None
        # Guards every read/write of ``_worker`` and ``_closed`` AND the
        # ``_queue.put`` of a real item, so ``fire`` is atomic with respect to
        # ``close``: an item is enqueued only while not closed, and never after
        # ``close`` has queued the sentinel.
        self._worker_lock = threading.Lock()
        self._closed = False

    def fire(
        self,
        abs_path: Path,
        content: str,
        operation: WriteOperation,
        old_path: Path | None = None,
    ) -> None:
        """Queue a callback invocation, starting the worker if needed.

        No-op when no callback is configured. After :meth:`close`, this is a
        logged no-op — it does not resurrect a worker or enqueue an item that
        would never be drained.

        Args:
            abs_path: Absolute path of the written file.
            content: File content at write time (empty for deletes).
            operation: The kind of write that occurred.
            old_path: For ``rename``, the absolute path the file moved *from*,
                so the callback can scope its staging to the two paths the
                rename actually touched (#894). Forwarded only to a callback
                that opted in via
                :data:`~markdown_vault_mcp.types.ACCEPTS_OLD_PATH_ATTR`;
                ignored otherwise, and unused for every other operation.

        The currently bound :class:`~markdown_vault_mcp._identity.Principal`
        is snapshotted **here**, not on the worker: ``fire`` runs on the
        request's ``to_thread`` worker (whose copied context carries the
        contextvar), while the dispatcher's own thread has no request context
        at all — reading identity there is exactly the silent-fallback bug of
        #1218. The snapshot is forwarded only to a callback that opted in via
        :data:`~markdown_vault_mcp.types.ACCEPTS_PRINCIPAL_ATTR`.
        """
        if self._on_write is None:
            return
        principal = current_principal()
        # Snapshotted here for the same reason as the principal above: ``fire``
        # runs on the request's ``to_thread`` worker, whose copied context
        # carries the variable, while the dispatcher's own thread has no request
        # context at all. Reading the scope on the worker would always see None
        # and silently reinstate per-file commits.
        #
        # Gated on the opt-in: a callback that cannot consume a batch must not
        # pay for one. Attaching the scope unconditionally would buffer its
        # writes for the length of the tool call and then replay them one by
        # one — the delay and the retained state of grouping, with none of it.
        scope = current_commit_scope() if self._accepts_batch else None
        with self._worker_lock:
            if self._closed:
                logger.warning(
                    "Write callback fired after close(); dropping %s (%s)",
                    abs_path,
                    operation,
                )
                return
            self._ensure_worker_locked()
            # Enqueue under the lock so close() cannot slip the sentinel in
            # ahead of this item (which would leave it permanently undrained).
            self._queue.put((abs_path, content, operation, old_path, principal, scope))

    def end_scope(self, scope: CommitScope) -> None:
        """Close *scope*, committing everything fired under it as one group.

        A no-op when no callback is configured, when the callback did not opt
        into :data:`~markdown_vault_mcp.types.ACCEPTS_BATCH_ATTR`, after
        :meth:`close`, or when the worker was never started — in each case
        nothing was buffered under this scope, so there is nothing to flush.
        Never blocks: the marker is enqueued and the worker flushes it in FIFO
        order, behind every write the scope fired.

        Args:
            scope: The scope to close, as returned by
                :func:`~markdown_vault_mcp._commit_scope.bound_commit_scope`.
        """
        if self._on_write is None or not self._accepts_batch:
            return
        with self._worker_lock:
            if self._closed or self._worker is None:
                return
            self._queue.put(_ScopeEnd(scope.token))

    def _flush_scope(self, token: int) -> None:
        """Dispatch one buffered scope's writes, then forget it.

        Silent when the token has no buffer: a tool that wrote nothing still
        closes its scope, and a drain may already have flushed it.
        """
        entry = self._open_scopes.pop(token, None)
        if entry is None:
            return
        scope, items = entry
        on_write = self._on_write
        if not items or on_write is None:
            return
        try:
            # Only a callback that opted in is ever buffered (see ``fire``),
            # so reaching here means ``on_write_batch`` exists.
            batch = cast("BatchAwareWriteCallback", on_write)
            batch.on_write_batch(items, scope.tool_name)
        except Exception:
            logger.error(
                "write_callback_batch_failed tool=%s count=%s",
                scope.tool_name,
                len(items),
                exc_info=True,
            )

    def _flush_all_scopes(self) -> None:
        """Flush every open scope, newest last.

        Called when a drain marker is reached: ``drain`` is what a git pull
        waits on before merging, so returning with writes still buffered would
        let the merge run over changes no commit contains.
        """
        for token in list(self._open_scopes):
            self._flush_scope(token)

    def _dispatch_one(
        self,
        abs_path: Path,
        content: str,
        operation: WriteOperation,
        old_path: Path | None,
        principal: Principal | None,
    ) -> None:
        """Invoke the callback for a single write with its opted-in keywords."""
        on_write = self._on_write
        if on_write is None:
            return
        try:
            # Build the opted-in keywords flat so the four
            # (old_path, principal) combinations stay one call site.
            extra: dict[str, Any] = {}
            if old_path is not None and self._accepts_old_path:
                extra["old_path"] = old_path
            if principal is not None and self._accepts_principal:
                extra["principal"] = principal
            if extra:
                aware = cast("PrincipalAwareWriteCallback", on_write)
                aware(abs_path, content, operation, **extra)
            else:
                on_write(abs_path, content, operation)
        except Exception:
            logger.error(
                "Write callback failed for %s (%s)",
                abs_path,
                operation,
                exc_info=True,
            )

    def _ensure_worker_locked(self) -> None:
        """Start the background worker if it is not running.

        Caller MUST hold ``_worker_lock``.
        """
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run, daemon=True, name="write-callback"
        )
        self._worker.start()

    def _run(self) -> None:
        """Worker loop: drain the queue until the sentinel is dequeued."""
        # The worker is started only by ``fire`` (via ``_ensure_worker_locked``),
        # and ``fire`` runs only when ``_on_write`` is not None. ``_on_write`` is
        # set once in ``__init__`` and never reassigned, so it is non-None here.
        on_write = self._on_write
        assert on_write is not None
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    # Closing: flush what is buffered rather than discarding
                    # writes that are already on disk.
                    self._flush_all_scopes()
                    break
                if isinstance(item, _DrainMarker):
                    self._flush_all_scopes()
                    item.event.set()
                    continue
                if isinstance(item, _ScopeEnd):
                    self._flush_scope(item.token)
                    continue
                abs_path, content, operation, old_path, principal, scope = item
                if scope is None:
                    # No owning tool call — a background or startup write.
                    # Commits on its own, exactly as before.
                    self._dispatch_one(
                        abs_path, content, operation, old_path, principal
                    )
                    continue
                _, items = self._open_scopes.setdefault(scope.token, (scope, []))
                # Content is deliberately dropped: staging reads the file from
                # disk, so retaining it would hold every written file's text
                # for the length of the call with nothing to read it.
                items.append((abs_path, operation, old_path, principal))
        except BaseException:
            # A BaseException (SystemExit/KeyboardInterrupt/MemoryError) kills the
            # worker thread. Log it so drain()/close() are not the only signal —
            # otherwise the death is silent and a later drain() can only report a
            # generic timeout (or, worse, return success against a dead worker).
            logger.error("write_callback_worker_died", exc_info=True)
            raise

    def drain(self, timeout: float = 30.0) -> bool:
        """Block until all currently-queued callbacks have been processed.

        Unlike :meth:`close`, the dispatcher stays open: the worker keeps
        running and :meth:`fire` continues to work afterward. Used before a git
        pull so every already-queued commit lands before the merge touches the
        working tree.

        Returns:
            ``True`` when there was nothing to drain (no callback configured,
            already closed, or the worker was never started) or the queue
            drained within ``timeout``. ``False`` when the drain did not finish
            in time, or the worker thread has died — the caller should treat a
            ``False`` as "pending commits may not have landed" and decide
            accordingly (e.g. warn and proceed). Never blocks beyond ``timeout``.

        Args:
            timeout: Seconds to wait for the queued items to drain.
        """
        if self._on_write is None:
            return True
        with self._worker_lock:
            if self._closed:
                return True
            worker = self._worker
            if worker is None:
                return True  # never started -> nothing was ever queued
            if not worker.is_alive():
                # Worker exits only via the None sentinel (close, which sets
                # _closed -- handled above) or a BaseException death (logged in
                # _run). Reaching here means it died; do NOT report success.
                # It typically died on an in-flight commit that was already
                # dequeued (so NOT counted by qsize()), so the stranded backlog
                # is the queued items plus that one: qsize() + 1. (If it instead
                # died while idle this over-reports by one -- acceptable for an
                # alert; do NOT "simplify" it back to qsize(), which undercounts
                # the common case.)
                logger.error(
                    "Write-callback drain found a dead worker; ~%d pending git "
                    "commit(s) will never be committed.",
                    self._queue.qsize() + 1,
                )
                return False
            marker = _DrainMarker()
            # Enqueue under the lock, mirroring fire(), so close() cannot slip
            # its sentinel ahead of this marker.
            self._queue.put(marker)
        if marker.event.wait(timeout):
            return True
        # On timeout the worker is blocked on an in-flight item (else it would
        # have reached the marker), so qsize() counts the queued real items plus
        # our still-queued marker but NOT that in-flight commit. The marker (+1)
        # and the uncounted in-flight commit (-1) cancel, so qsize() equals the
        # number of commits genuinely at risk -- same accounting as close().
        # Do NOT "correct" this to qsize()-1.
        logger.warning(
            "Write-callback drain did not finish within %s s; "
            "%d pending git commit(s) not yet committed before pull.",
            timeout,
            self._queue.qsize(),
        )
        return False

    def close(self, timeout: float = 30.0) -> None:
        """Drain pending callbacks and join the worker (bounded by ``timeout``).

        Safe to call when the worker was never started, and idempotent: a second
        call returns immediately. After ``close`` returns, :meth:`fire` is a
        logged no-op. Logs a warning (with the number of still-queued commits) if
        the worker does not finish in time.

        Args:
            timeout: Seconds to wait for the worker to drain and exit.
        """
        with self._worker_lock:
            if self._closed:
                return
            self._closed = True
            worker = self._worker
        if worker is not None and worker.is_alive():
            self._queue.put(None)  # sentinel
            worker.join(timeout=timeout)
            if worker.is_alive():
                # qsize() counts the still-queued real items plus the sentinel,
                # but excludes the one item the hung worker already dequeued and
                # is blocked on. Those two offsets cancel, so qsize() equals the
                # number of commits genuinely at risk. Do NOT "fix" this to
                # qsize()-1 — that would undercount by one.
                logger.warning(
                    "Write-callback worker did not finish within %s s; "
                    "%d pending git commit(s) may be lost.",
                    timeout,
                    self._queue.qsize(),
                )


# ``fire`` takes ``old_path`` itself, so the probe works one seam further in:
# DocumentManager is handed this bound method and asks the same question of it
# that this dispatcher asks of the callback underneath (#894).  Set on the
# function rather than the instance because a bound method proxies attribute
# lookup to its underlying function.
setattr(WriteCallbackDispatcher.fire, ACCEPTS_OLD_PATH_ATTR, True)
