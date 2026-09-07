"""Index facet: the build / readiness / writer-status surface (#604).

A thin view over the
:class:`~markdown_vault_mcp.indexing.IndexWriteCoordinator` (build / reindex /
embeddings sync + async, plus the readiness and writer-status queries) and
:class:`~markdown_vault_mcp.managers.index.IndexManager`
(:meth:`IndexFacet.embeddings_status`, :meth:`IndexFacet.skipped_files`). It
deliberately does NOT expose the coordinator's
internal surface (``close``, ``writer``, ``require_built``,
``mark_paths_dirty``, ``rebuild_embeddings``), which the root owns. Part of the
``vault.py`` facade decomposition (#576).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from concurrent.futures import Future, InvalidStateError
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.indexing import IndexWriteCoordinator
    from markdown_vault_mcp.managers.index import IndexManager
    from markdown_vault_mcp.types import IndexStats, ReindexResult, SkippedFile


logger = logging.getLogger(__name__)


def _resolve(
    future: Future[Any], *, value: Any = None, exception: BaseException | None = None
) -> None:
    """Settle *future*, tolerating a caller that cancelled it first.

    The follow-up runs on its own thread, so the future it settles can be
    cancelled between the check and the call; an unguarded ``set_result``
    would then raise there with nobody to catch it.
    """
    with contextlib.suppress(InvalidStateError):
        if exception is not None:
            future.set_exception(exception)
        else:
            future.set_result(value)


class IndexFacet:
    """Index build, readiness, writer-status, and embeddings-status operations.

    Delegates 1:1 to the :class:`IndexWriteCoordinator` (build / readiness /
    writer status) and to :class:`IndexManager` (:meth:`IndexFacet.embeddings_status`).
    """

    def __init__(
        self,
        *,
        coordinator: IndexWriteCoordinator,
        index_mgr: IndexManager,
        after_index_change: Callable[[tuple[str, ...] | None], None] | None = None,
    ) -> None:
        """Hold the collaborators the index operations delegate to.

        Args:
            coordinator: The shared :class:`IndexWriteCoordinator` owned by the
                root. Only its public operations are surfaced here.
            index_mgr: The shared :class:`IndexManager`, queried by
                :meth:`IndexFacet.embeddings_status`.
            after_index_change: Called once the index has caught up, so a
                projection of it (the OKF ``index.md`` listings, #1392) can
                follow whatever arrived — a pull, the watcher, an explicit
                reindex, a forced rebuild. Receives the folders a reindex
                reported, or ``None`` after a full build, which cannot name
                a delta and so means *every* folder. Never lets an exception
                escape into the caller's result.
        """
        self._coordinator = coordinator
        self._index_mgr = index_mgr
        self._after_index_change = after_index_change
        #: Follow-up threads still running, so shutdown can wait for them
        #: rather than closing the index out from under one.
        self._followers: set[threading.Thread] = set()
        self._followers_lock = threading.Lock()
        #: Set by :meth:`close`. A follow-up spawned after it does nothing:
        #: waiting for a set that is still empty would race a job whose
        #: completion callback has not fired yet.
        self._closed = False

    def is_queryable(self) -> bool:
        """Return True when the FTS index is queryable (precondition snapshot).

        A captured build error does NOT demote queryability: it is
        diagnostic state surfaced via :meth:`IndexFacet.get_index_status`, not a gate.
        """
        return self._coordinator.is_queryable()

    def start_background_build_index(self) -> None:
        """Spawn a daemon thread that runs :meth:`IndexFacet.build_index` to completion.

        .. deprecated:: 1.28
           Superseded by :meth:`IndexFacet.build_index_async`. Retained for legacy tests.
        """
        self._coordinator.start_background_build_index()

    def should_use_background_build(self) -> bool:
        """Return True iff the lifespan should route to the background build.

        .. deprecated:: 1.28
           Retained for legacy tests; the lifespan no longer branches on it.
        """
        return self._coordinator.should_use_background_build()

    def is_drained(self) -> bool:
        """Return True iff the IndexWriter has no pending or in-flight work.

        Reflects the moment of call only; pair with :meth:`IndexFacet.write_generation`
        to detect a complete write cycle inside a read window.
        """
        return self._coordinator.is_drained()

    def write_generation(self) -> int:
        """Return the writer's monotonic completion counter.

        Increments once per completed job. Pair with :meth:`IndexFacet.is_drained` to
        detect a write cycle inside a read window.
        """
        return self._coordinator.write_generation()

    def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Block until :meth:`IndexFacet.is_drained`; ``True`` if drained, ``False`` on timeout."""
        return self._coordinator.wait_for_drain(timeout)

    def get_index_status(self) -> dict[str, Any]:
        """Return a non-blocking twelve-key snapshot of build + writer state.

        Keys: ``status`` (``"queryable"`` | ``"building"`` | ``"failed"``),
        ``documents_indexed``, ``documents_indexed_error``, ``error``,
        ``last_reindex_error``, ``last_build_embeddings_error``, plus
        ``queue_depth``, ``in_flight``, ``dirty_paths``, ``dirty_embeddings``,
        ``write_generation`` merged from the writer, and ``skipped_files`` — a
        list of ``{path, category, detail}`` dicts for files dropped from the
        index for a surfaced deterministic reason (parse / encoding /
        missing-frontmatter / internal-error), read from tracker state
        (#775, #802). A captured build error appears in ``error`` as
        diagnostic context without demoting a ``queryable`` status;
        ``documents_indexed_error`` carries a SQLite read failure
        (``documents_indexed`` stays ``0``) (#583).
        """
        status = self._coordinator.get_index_status()
        status["skipped_files"] = [asdict(sf) for sf in self._index_mgr.skipped_files()]
        return status

    def wait_until_queryable(self, timeout: float | None = None) -> None:
        """Block until the FTS index is queryable, or raise.

        A captured build error does NOT block here; it surfaces as
        ``IndexUnavailableError(reason="build_failed")`` and is also readable
        via :meth:`IndexFacet.get_index_status`. Library bucket-3/4 methods use the
        root's ``_require_built`` instead, which raises immediately.

        Raises:
            IndexUnavailableError: timeout expired (``reason="timeout"``), a
                build ran and failed (``reason="build_failed"``), or no build
                was ever scheduled (``reason="never_built"``).
        """
        self._coordinator.wait_until_queryable(timeout)

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index.

        Warm restarts (existing populated index, ``force=False``) are an O(1)
        no-op keyed on FTS state. ``force=True`` drops and rebuilds; config
        changes require ``force=True`` to apply (see issue #525).

        Returns:
            :class:`~markdown_vault_mcp.types.IndexStats` describing what was indexed.
        """
        stats = self._coordinator.build_index(force=force)
        if stats.rebuilt:
            self._notify(None)
        return stats

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Returns:
            :class:`~markdown_vault_mcp.types.ReindexResult` with counts applied.

        Raises:
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
        """
        result = self._coordinator.reindex()
        self._notify(result.folders_changed)
        return result

    def _notify(self, folders: tuple[str, ...] | None) -> None:
        """Run the follow-up, never letting it fail the caller's operation."""
        if self._after_index_change is None or self._closed:
            return
        try:
            self._after_index_change(folders)
        except Exception:
            logger.warning("after_index_change_hook_failed", exc_info=True)

    def _settle_reindex(
        self, done: Future[ReindexResult], out: Future[ReindexResult]
    ) -> None:
        """Resolve *out* once the follow-up for *done*'s reindex has run."""
        try:
            result = done.result()
        except BaseException as exc:
            _resolve(out, exception=exc)
            return
        self._notify(result.folders_changed)
        _resolve(out, value=result)

    def _settle_build(self, done: Future[IndexStats], out: Future[IndexStats]) -> None:
        """Resolve *out* once the follow-up for *done*'s build has run."""
        try:
            stats = done.result()
        except BaseException as exc:
            _resolve(out, exception=exc)
            return
        if stats.rebuilt:
            # A full build cannot name a delta: everything it indexed is new
            # to the index, so every listing is regenerated (#1392).
            self._notify(None)
        _resolve(out, value=stats)

    def _chain(
        self, done: Future[Any], settle: Callable[[Any, Any], None]
    ) -> Future[Any]:
        """Return a future resolving once *settle* has run for *done*.

        The returned future mirrors *done* in both directions: cancelling it
        cancels the underlying job, and a job cancelled independently — the
        writer cancelling its queue when its worker dies — cancels it back,
        so nothing waits on a job that will never run.

        The follow-up runs on a thread of its own because it drains the
        single-writer, which the writer thread could never wait on. That
        thread deliberately starts with an *empty* context rather than a copy
        of the caller's: a slow reindex is promoted to a background job and
        the tool's commit scope closes at promotion, so writes inheriting
        that scope would be buffered against an end marker already consumed.
        The follow-up binds a scope of its own instead.
        """
        out: Future[Any] = Future()

        def _run(finished: Future[Any]) -> None:
            try:
                settle(finished, out)
            finally:
                with self._followers_lock:
                    self._followers.discard(threading.current_thread())

        def _start(finished: Future[Any]) -> None:
            if finished.cancelled():
                # Cancelled before it ran, from either side: the caller
                # cancelling ``out``, or the writer cancelling its queue when
                # its worker dies. Only the first leaves ``out`` already
                # cancelled, so cancel it here too — returning without
                # resolving it would leave anyone awaiting it waiting for a
                # job that will never run.
                out.cancel()
                return
            thread = threading.Thread(
                target=_run,
                args=(finished,),
                name="after-index-change",
                daemon=True,
            )
            with self._followers_lock:
                self._followers.add(thread)
            thread.start()

        def _forward_cancel(chained: Future[Any]) -> None:
            # The caller holds ``out``, so a cancellation — the job cap
            # cancelling the tool's coroutine, say — arrives there and would
            # otherwise leave the writer job running with nobody waiting.
            if chained.cancelled():
                done.cancel()

        done.add_done_callback(_start)
        out.add_done_callback(_forward_cancel)
        return out

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk.

        Returns:
            Total number of chunks embedded.

        Raises:
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
            EmbeddingsNotConfiguredError: If ``embedding_provider`` or
                ``embeddings_path`` is unset (a ``ValueError`` subclass).
        """
        return self._coordinator.build_embeddings(force=force)

    def build_index_async(self, *, force: bool = False) -> Future[IndexStats]:
        """Submit a full FTS index build and return the Future.

        Caller may ``.result()`` to wait or fire-and-forget. Warm-restart
        short-circuit returns an already-resolved Future without queuing a
        job, mirroring :meth:`IndexFacet.build_index`.

        Args:
            force: When ``True``, drop and rebuild the index unconditionally.

        Returns:
            ``concurrent.futures.Future`` carrying the :class:`IndexStats`.
            With a follow-up wired it resolves once that has run, and a build
            that actually rebuilt regenerates every listing (#1392).
        """
        fut = self._coordinator.build_index_async(force=force)
        if self._after_index_change is None:
            return fut
        chained: Future[IndexStats] = self._chain(fut, self._settle_build)
        return chained

    def reindex_async(self) -> Future[ReindexResult]:
        """Submit an incremental FTS reindex and return the Future.

        Does not require :meth:`IndexFacet.build_index` first — the writer's FIFO queue
        orders any earlier :class:`BuildIndex` before this job. Writer-thread
        failures are surfaced via :meth:`IndexFacet.get_index_status` (#561).

        With a follow-up wired, the returned future resolves only once that
        follow-up has run (see :meth:`_chain`).
        """
        fut = self._coordinator.reindex_async()
        if self._after_index_change is None:
            return fut
        chained: Future[ReindexResult] = self._chain(fut, self._settle_reindex)
        return chained

    def build_embeddings_async(self, *, force: bool = False) -> Future[int]:
        """Submit a vector index build and return the Future.

        Does not require :meth:`IndexFacet.build_index` first — FIFO ordering runs any
        earlier :class:`BuildIndex` first. Writer-thread failures are surfaced
        via :meth:`IndexFacet.get_index_status` (#561).
        """
        return self._coordinator.build_embeddings_async(force=force)

    def embeddings_status(self) -> dict[str, Any]:
        """Return status information about the vector index.

        Returns:
            Dict with keys ``provider``, ``chunk_count``, ``path``,
            ``available``.
        """
        return self._index_mgr.embeddings_status()

    def skipped_files(self) -> list[SkippedFile]:
        """Return files dropped from the index for a surfaced reason (#775).

        Delegates to :meth:`IndexManager.skipped_files`; also merged, as
        plain dicts, into :meth:`IndexFacet.get_index_status`'s
        ``skipped_files`` key.

        Returns:
            Path-sorted list of :class:`~markdown_vault_mcp.types.SkippedFile`.
        """
        return self._index_mgr.skipped_files()

    def stop_followers(self, timeout: float = 10.0) -> None:
        """Stop post-index follow-ups and wait for any in flight, for shutdown.

        Deliberately not named ``close``: the facet must not surface the
        coordinator's own method names (``tests/test_facet_index.py``), and
        this stops one activity rather than closing the index.

        Each follow-up writes through the index, so closing the vault while
        one runs would pull the connection out from under it. Waiting alone
        is not enough: a build or reindex still queued at shutdown registers
        its follow-up only when the job completes, which is *during* the
        drain, so the flag is what stops that one from starting work.

        Args:
            timeout: Seconds to wait for the follow-ups already running. One
                that outlives it is a daemon thread and does not hold the
                process open; it is reported rather than waited on forever.
        """
        self._closed = True
        deadline = time.monotonic() + timeout
        while True:
            with self._followers_lock:
                pending = [t for t in self._followers if t.is_alive()]
            if not pending:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "after_index_change_followers_pending count=%d", len(pending)
                )
                return
            pending[0].join(timeout=min(remaining, 0.1))
