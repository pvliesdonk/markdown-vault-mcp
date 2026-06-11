"""Background dispatcher for deferred write callbacks (issue #175).

Extracted from :class:`~markdown_vault_mcp.vault.Vault` (issue #599)
so the git-commit dispatch concern lives apart from the index-write machinery
in :mod:`markdown_vault_mcp.indexing`.

A single daemon worker thread drains a FIFO queue, invoking the configured
``on_write`` callback (typically a git commit) off the write path, so write
methods return as soon as the FTS update lands.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.types import WriteCallback, WriteOperation

logger = logging.getLogger(__name__)


class _DrainMarker:
    """Queue sentinel meaning 'all items enqueued before me are processed'.

    Unlike the ``None`` close-sentinel, the worker does NOT exit on this: it
    sets ``event`` and continues. FIFO ordering guarantees every real item
    enqueued before the marker has been processed when the worker reaches it.
    """

    __slots__ = ("event",)

    def __init__(self) -> None:
        self.event = threading.Event()


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
        """
        self._on_write = on_write
        self._queue: queue.Queue[
            tuple[Path, str, WriteOperation] | None | _DrainMarker
        ] = queue.Queue()
        self._worker: threading.Thread | None = None
        # Guards every read/write of ``_worker`` and ``_closed`` AND the
        # ``_queue.put`` of a real item, so ``fire`` is atomic with respect to
        # ``close``: an item is enqueued only while not closed, and never after
        # ``close`` has queued the sentinel.
        self._worker_lock = threading.Lock()
        self._closed = False

    def fire(self, abs_path: Path, content: str, operation: WriteOperation) -> None:
        """Queue a callback invocation, starting the worker if needed.

        No-op when no callback is configured. After :meth:`close`, this is a
        logged no-op — it does not resurrect a worker or enqueue an item that
        would never be drained.

        Args:
            abs_path: Absolute path of the written file.
            content: File content at write time (empty for deletes).
            operation: The kind of write that occurred.
        """
        if self._on_write is None:
            return
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
            self._queue.put((abs_path, content, operation))

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
                    break
                if isinstance(item, _DrainMarker):
                    item.event.set()
                    continue
                abs_path, content, operation = item
                try:
                    on_write(abs_path, content, operation)
                except Exception:
                    logger.error(
                        "Write callback failed for %s (%s)",
                        abs_path,
                        operation,
                        exc_info=True,
                    )
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
