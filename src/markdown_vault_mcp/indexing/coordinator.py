"""Index-write orchestration: owns the IndexWriter and the readiness state.

`IndexWriteCoordinator` is the single owner of index-write orchestration:
the synchronous/asynchronous build entry points, the background-build
readiness state machine (delegated to :class:`ReadinessState`), the
per-variant async error capture, status/drain observation, and dirty-path
routing. `Collection` constructs one coordinator and delegates to it; the
coordinator constructs, starts, and closes the single-owner
:class:`~markdown_vault_mcp.indexing.index_writer.IndexWriter` thread.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.indexing.index_writer import (
    BuildEmbeddings,
    BuildIndex,
    IndexWriter,
    ProcessDirtyPaths,
    ReindexAll,
    WriterContext,
    run_build_embeddings,
    run_build_index,
    run_flush_dirty_embeddings,
    run_process_dirty_paths,
    run_reindex_all,
)
from markdown_vault_mcp.indexing.readiness import ReadinessState
from markdown_vault_mcp.types import IndexStats, ReindexResult

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.managers.index import IndexManager

logger = logging.getLogger(__name__)


class IndexWriteCoordinator:
    """Owns the IndexWriter thread and the build-readiness state machine."""

    def __init__(
        self,
        *,
        fts: FTSIndex,
        index_mgr: IndexManager,
        index_path: Path | str | None,
        file_write_lock: threading.RLock,
    ) -> None:
        self._fts = fts
        self._index_path = index_path
        self._file_write_lock = file_write_lock
        self._readiness = ReadinessState()
        # Deprecated background-build thread bookkeeping (guarded by the
        # injected file-write lock, matching the former Collection locking).
        self._background_build_thread: threading.Thread | None = None
        self._background_started: bool = False
        # Per-async-variant error capture (#561), surfaced via get_index_status.
        self._last_reindex_error: BaseException | None = None
        self._last_build_embeddings_error: BaseException | None = None
        self._writer_ctx = WriterContext(index_manager=index_mgr)
        self._writer = IndexWriter(
            runners={
                "build_index": run_build_index,
                "reindex_all": run_reindex_all,
                "build_embeddings": run_build_embeddings,
                "process_dirty_paths": run_process_dirty_paths,
                "flush_dirty_embeddings": run_flush_dirty_embeddings,
            },
            ctx=self._writer_ctx,
        )
        self._writer_ctx.writer = self._writer
        self._writer.start()

    @property
    def writer(self) -> IndexWriter:
        """The owned writer (accessor for the search-rebuild path and tests)."""
        return self._writer

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def is_queryable(self) -> bool:
        """Return True when the structural preconditions for FTS queries hold.

        A captured build error does NOT demote queryability: it is
        diagnostic state surfaced via :meth:`get_index_status`, not a gate.
        """
        return self._readiness.is_queryable()

    def require_built(self) -> None:
        """Raise :exc:`IndexUnavailableError` if :meth:`build_index` has not run."""
        self._readiness.require_built()

    def wait_until_queryable(self, timeout: float | None = None) -> None:
        """Block until the FTS index is queryable, or raise.

        A captured build error does NOT raise here; it is diagnostic state
        surfaced via :meth:`get_index_status`. Raises only on timeout or
        when the build was never scheduled / did not complete.

        Raises:
            IndexUnavailableError: timeout expired (``reason="timeout"``) or
                the index was never built (``reason="never_built"``).
        """
        if not self._readiness.wait(timeout):
            raise IndexUnavailableError(
                f"Index build still in progress; timed out after {timeout}s.",
                reason="timeout",
            )
        if not self._readiness.is_built:
            raise IndexUnavailableError(
                "Index not built: background build was never scheduled "
                "or did not complete successfully. "
                "Check get_index_status() for diagnostic details, or call "
                "build_index() or build_index_async() to retry.",
                reason="never_built",
            )

    def get_index_status(self) -> dict[str, Any]:
        """Return a non-blocking snapshot of build + writer state (ten keys)."""
        fields = self._readiness.status_fields()
        try:
            documents_indexed = len(self._fts.list_notes())
        except Exception:
            logger.debug(
                "get_index_status: list_notes failed; reporting 0",
                exc_info=True,
            )
            documents_indexed = 0
        result = {
            "status": fields["status"],
            "documents_indexed": documents_indexed,
            "error": fields["error"],
            "last_reindex_error": (
                str(self._last_reindex_error)
                if self._last_reindex_error is not None
                else None
            ),
            "last_build_embeddings_error": (
                str(self._last_build_embeddings_error)
                if self._last_build_embeddings_error is not None
                else None
            ),
        }
        result.update(self._writer.get_status())
        return result

    # ------------------------------------------------------------------
    # Drain observation
    # ------------------------------------------------------------------

    def is_drained(self) -> bool:
        """Return True iff the writer has no pending or in-flight work."""
        status = self._writer.get_status()
        return (
            status["queue_depth"] == 0
            and status["in_flight"] is None
            and status["dirty_paths"] == 0
            and status["dirty_embeddings"] == 0
        )

    def write_generation(self) -> int:
        """Return the writer's monotonic completion counter."""
        return int(self._writer.get_status()["write_generation"])

    def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Block until :meth:`is_drained`, or until *timeout*. Polls every 50ms."""
        deadline = None if timeout is None else time.monotonic() + timeout
        poll_interval = 0.05
        while True:
            if self.is_drained():
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # Synchronous builds
    # ------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index (warm restart is O(1))."""
        if not force and self._fts.is_build_completed():
            existing = self._fts.list_notes()
            if existing:
                logger.debug(
                    "build_index: index already populated (%d docs), skipping",
                    len(existing),
                )
                self._readiness.mark_built()
                return IndexStats(
                    documents_indexed=len(existing),
                    chunks_indexed=0,
                    skipped=0,
                )
        self._readiness.begin_sync_build()
        self._fts.clear_build_completed()
        result: IndexStats = self._writer.submit(BuildIndex(force=force)).result()
        self._fts.set_build_completed()
        self._readiness.mark_built()
        return result

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
        """
        self._readiness.require_built()
        result: ReindexResult = self._writer.submit(ReindexAll()).result()
        return result

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If ``embedding_provider`` / ``embeddings_path`` is unset.
        """
        self._readiness.require_built()
        result: int = self._writer.submit(BuildEmbeddings(force=force)).result()
        return result

    def rebuild_embeddings(self) -> None:
        """Force-rebuild the vector index for the search-recovery path.

        Invoked by ``SearchManager._load_vectors`` on a
        ``VectorIndexCompatibilityError`` (embedding-model upgrade). Runs on
        the writer thread to preserve the single-owner invariant. Carries NO
        ``require_built`` gate: the index is necessarily built when vectors
        are being loaded. The rebuilt-chunk count is discarded — the caller
        invokes this for its side-effect only.
        """
        self._writer.submit(BuildEmbeddings(force=True)).result()

    # ------------------------------------------------------------------
    # Asynchronous builds
    # ------------------------------------------------------------------

    def build_index_async(self, *, force: bool = False) -> Future[IndexStats]:
        """Submit a full FTS index build and return the Future.

        Warm-restart short-circuit returns an already-resolved Future
        without touching the writer queue, mirroring :meth:`build_index`.
        """
        if not force and self._fts.is_build_completed():
            existing = self._fts.list_notes()
            if existing:
                logger.debug(
                    "build_index_async: index already populated (%d docs), skipping",
                    len(existing),
                )
                self._readiness.mark_built()
                fut: Future[IndexStats] = Future()
                fut.set_result(
                    IndexStats(
                        documents_indexed=len(existing),
                        chunks_indexed=0,
                        skipped=0,
                    )
                )
                return fut

        self._readiness.begin_async_build()
        self._fts.clear_build_completed()

        try:
            future = self._writer.submit(BuildIndex(force=force))
        except BaseException as exc:
            self._readiness.fail_build(exc)
            raise

        def _on_done(fut: Future[IndexStats]) -> None:
            try:
                fut.result()
            except BaseException as exc:
                logger.exception("Async index build failed")
                self._readiness.fail_build(exc)
                return
            self._fts.set_build_completed()
            self._readiness.mark_built()

        future.add_done_callback(_on_done)
        return future

    def _on_reindex_done(self, fut: Future[ReindexResult]) -> None:
        """Capture async reindex outcome for visibility via get_index_status (#561)."""
        try:
            fut.result()
            self._last_reindex_error = None
        except BaseException as exc:
            self._last_reindex_error = exc
            logger.exception("Async reindex job failed")

    def _on_build_embeddings_done(self, fut: Future[int]) -> None:
        """Capture async build_embeddings outcome for get_index_status (#561)."""
        try:
            fut.result()
            self._last_build_embeddings_error = None
        except BaseException as exc:
            self._last_build_embeddings_error = exc
            logger.exception("Async build_embeddings job failed")

    def reindex_async(self) -> Future[ReindexResult]:
        """Submit an incremental FTS reindex and return the Future.

        The writer's FIFO queue guarantees any earlier BuildIndex job runs
        first, so this does not require :meth:`build_index` up front.
        """
        fut = self._writer.submit(ReindexAll())
        fut.add_done_callback(self._on_reindex_done)
        return fut

    def build_embeddings_async(self, *, force: bool = False) -> Future[int]:
        """Submit a vector index build and return the Future.

        FIFO ordering guarantees any earlier BuildIndex runs first, so this
        does not require :meth:`build_index` up front.
        """
        fut = self._writer.submit(BuildEmbeddings(force=force))
        fut.add_done_callback(self._on_build_embeddings_done)
        return fut

    # ------------------------------------------------------------------
    # Dirty-path routing
    # ------------------------------------------------------------------

    def mark_paths_dirty(self, paths: Iterable[str]) -> None:
        """Route DocumentManager dirty-marks through the writer (#559).

        The dirty set is updated unconditionally; only the follow-up
        :class:`ProcessDirtyPaths` submission is skipped when the writer is
        closed (avoiding a ``RuntimeError`` on a closed writer at shutdown).
        """
        self._writer.mark_dirty(paths)
        if self._writer.is_closed():
            return
        try:
            self._writer.submit(ProcessDirtyPaths())
        except RuntimeError:
            if not self._writer.is_closed():
                raise
            logger.debug(
                "mark_paths_dirty_writer_closed_after_submit "
                "marks_retained_on_writer_set=True"
            )

    # ------------------------------------------------------------------
    # Deprecated legacy background build (retained for legacy tests)
    # ------------------------------------------------------------------

    def start_background_build_index(self) -> None:
        """Spawn a daemon thread that runs :meth:`build_index` to completion.

        .. deprecated:: 1.28
           Superseded by :meth:`build_index_async`. Retained only for legacy
           tests. One-shot per coordinator lifetime; idempotent.
        """

        def _worker() -> None:
            try:
                self.build_index()
            except Exception as exc:
                self._readiness.record_error(exc)
                logger.exception("Background index build failed")
            except BaseException as exc:
                self._readiness.record_error(exc)
                logger.exception("Background index build interrupted")
                raise
            finally:
                self._readiness.mark_done()

        with self._file_write_lock:
            if self._background_started:
                return
            self._background_started = True
            self._readiness.begin_background_build()
            thread = threading.Thread(
                target=_worker,
                name="markdown-vault-mcp.background-build",
                daemon=True,
            )
            self._background_build_thread = thread
            try:
                thread.start()
            except Exception as exc:
                self._readiness.fail_build(exc)
                raise

    def should_use_background_build(self) -> bool:
        """Return True iff the lifespan should route to the background build.

        .. deprecated:: 1.28
           The lifespan no longer branches on this; retained for legacy tests.
           True only for cold on-disk DBs (real path AND no completeness
           sentinel); False for warm on-disk and in-memory DBs.
        """
        if self._index_path is None or str(self._index_path) == ":memory:":
            return False
        return not self._fts.is_build_completed()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self, timeout: float = 30.0) -> None:
        """Join the legacy background-build thread, then close the writer.

        Closes the writer AFTER joining the background build (whose worker
        submits jobs to the writer). Does NOT close the FTS index — the
        owner (Collection) closes FTS last, after the writer is drained.
        """
        with self._file_write_lock:
            thread = self._background_build_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning(
                    "close: background build thread did not exit within %ss; "
                    "abandoning (daemon thread does not block process)",
                    timeout,
                )
        self._writer.close(timeout=timeout)
