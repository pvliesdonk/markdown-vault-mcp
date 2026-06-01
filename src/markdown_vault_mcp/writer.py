"""Single-owner writer for FTS and vector indexes.

See `docs/superpowers/specs/2026-05-31-issue-559-single-writer-for-indexes-design.md`.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, ClassVar, cast

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildIndex:
    """Full FTS index build."""

    kind: ClassVar[str] = "build_index"
    force: bool = False


@dataclass(frozen=True)
class ReindexAll:
    """Incremental FTS reindex via change tracker."""

    kind: ClassVar[str] = "reindex_all"


@dataclass(frozen=True)
class BuildEmbeddings:
    """Full vector index build."""

    kind: ClassVar[str] = "build_embeddings"
    force: bool = False


@dataclass(frozen=True)
class ProcessDirtyPaths:
    """Drain the FTS-dirty-paths set."""

    kind: ClassVar[str] = "process_dirty_paths"


@dataclass(frozen=True)
class FlushDirtyEmbeddings:
    """Drain the vector-dirty-paths set."""

    kind: ClassVar[str] = "flush_dirty_embeddings"


# Sentinel placed in the queue by close() to wake the worker.
_SHUTDOWN_SENTINEL: object = object()

# Type alias for a job-runner: takes the job and a writer-supplied context,
# returns the value to set on the Future. Exceptions propagate to the Future.
JobRunner = Callable[[Any, Any], Any]


class IndexWriter:
    """Single-owner writer thread serving a FIFO job queue.

    Construction does NOT start the worker thread; call :meth:`start`
    explicitly. Submission is rejected from the moment :meth:`close`
    is called.

    Args:
        runners: Mapping from job kind string to handler callable.
        ctx: Opaque context object passed to every runner.
    """

    def __init__(
        self,
        *,
        runners: dict[str, JobRunner],
        ctx: Any,
    ) -> None:
        self._runners = dict(runners)
        self._ctx = ctx
        self._queue: queue.Queue[tuple[Any, Future[Any]] | object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._submit_lock = threading.Lock()
        self._closed = threading.Event()
        self._dirty_lock = threading.Lock()
        self._dirty_paths: set[str] = set()
        self._dirty_embeddings: set[str] = set()
        self._in_flight_lock = threading.Lock()
        self._in_flight_kind: str | None = None

    def start(self) -> None:
        """Spawn the worker thread. Idempotent."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="markdown-vault-mcp.writer",
            daemon=True,
        )
        self._thread.start()

    def submit(self, job: Any) -> Future[Any]:
        """Submit a job for execution.

        Follow-up submissions issued from inside the writer thread
        itself (e.g. ``ProcessDirtyPaths`` submitting
        ``FlushDirtyEmbeddings``) are accepted during shutdown drain so
        the dirty sets can flush before the sentinel pops.  External
        submissions after :meth:`close` raise.

        Raises:
            RuntimeError: If :meth:`close` has been called from a thread
                other than the writer's own worker thread.
        """
        with self._submit_lock:
            if self._closed.is_set() and threading.current_thread() is not self._thread:
                msg = "IndexWriter is closed; cannot submit new jobs"
                raise RuntimeError(msg)
            future: Future[Any] = Future()
            self._queue.put((job, future))
        return future

    def close(self, timeout: float = 30.0) -> None:
        """Signal shutdown and drain the queue before joining the worker.

        Marks the writer closed so no new external submissions are
        accepted, then posts the shutdown sentinel.  Jobs already in
        the queue, and any follow-up jobs they submit from inside the
        writer thread (e.g. ``ProcessDirtyPaths`` submitting
        ``FlushDirtyEmbeddings``), drain through the worker's FIFO
        before the sentinel terminates the loop.

        The worker thread is daemon; if the drain exceeds *timeout*,
        process exit kills the remainder.
        """
        with self._submit_lock:
            if self._closed.is_set():
                return
            self._closed.set()
        self._queue.put(_SHUTDOWN_SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_closed(self) -> bool:
        """Return True if :meth:`close` has been called."""
        return self._closed.is_set()

    def mark_dirty(self, paths: Iterable[str]) -> None:
        """Mark file paths needing FTS re-index."""
        with self._dirty_lock:
            self._dirty_paths.update(paths)

    def mark_embedding_dirty(self, paths: Iterable[str]) -> None:
        """Mark file paths needing vector re-embedding."""
        with self._dirty_lock:
            self._dirty_embeddings.update(paths)

    def snapshot_dirty_paths(self) -> set[str]:
        """Return a snapshot of the FTS-dirty set without clearing it."""
        with self._dirty_lock:
            return set(self._dirty_paths)

    def snapshot_dirty_embeddings(self) -> set[str]:
        """Return a snapshot of the vector-dirty set without clearing it."""
        with self._dirty_lock:
            return set(self._dirty_embeddings)

    def drain_dirty_paths(self) -> set[str]:
        """Snapshot-and-clear the FTS-dirty set under the lock."""
        with self._dirty_lock:
            snapshot = set(self._dirty_paths)
            self._dirty_paths.clear()
        return snapshot

    def drain_dirty_embeddings(self) -> set[str]:
        """Snapshot-and-clear the vector-dirty set under the lock."""
        with self._dirty_lock:
            snapshot = set(self._dirty_embeddings)
            self._dirty_embeddings.clear()
        return snapshot

    def get_status(self) -> dict[str, Any]:
        """Return non-blocking snapshot of writer state."""
        with self._in_flight_lock:
            in_flight = self._in_flight_kind
        with self._dirty_lock:
            dirty_paths = len(self._dirty_paths)
            dirty_embeddings = len(self._dirty_embeddings)
        return {
            "queue_depth": self._queue.qsize(),
            "in_flight": in_flight,
            "dirty_paths": dirty_paths,
            "dirty_embeddings": dirty_embeddings,
        }

    def _run(self) -> None:
        """Worker loop.

        Processes jobs FIFO until the shutdown sentinel pops AND the
        queue is empty.  This lets runners (e.g. ``ProcessDirtyPaths``)
        submit follow-up jobs even after :meth:`close` so the dirty
        sets can flush before the worker exits.
        """
        sentinel_seen = False
        while True:
            if sentinel_seen:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    return
            else:
                item = self._queue.get()
            if item is _SHUTDOWN_SENTINEL:
                sentinel_seen = True
                continue
            job, future = cast("tuple[Any, Future[Any]]", item)
            if not future.set_running_or_notify_cancel():
                continue
            with self._in_flight_lock:
                self._in_flight_kind = job.kind
            try:
                runner = self._runners[job.kind]
                result = runner(job, self._ctx)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)
                logger.exception("Writer job %s failed", job.kind)
            except BaseException as exc:
                # KeyboardInterrupt / SystemExit / asyncio.CancelledError —
                # capture into the Future so waiters unblock, then re-raise
                # so the worker thread terminates. The finally block clears
                # _in_flight_kind before the re-raise so status reads on
                # other threads are not stuck on a stale value.
                future.set_exception(exc)
                logger.error(
                    "writer_job_basexception kind=%s",
                    job.kind,
                    exc_info=True,
                )
                raise
            finally:
                with self._in_flight_lock:
                    self._in_flight_kind = None


@dataclass
class WriterContext:
    """References passed to job runners.

    Set ``writer`` after constructing the IndexWriter so runners can
    submit follow-up jobs (e.g. ProcessDirtyPaths submitting
    FlushDirtyEmbeddings).
    """

    index_manager: Any
    writer: IndexWriter | None = None


def run_build_index(job: BuildIndex, ctx: WriterContext) -> Any:
    """Execute a full FTS index build."""
    return ctx.index_manager.build_index(force=job.force)


def run_reindex_all(job: ReindexAll, ctx: WriterContext) -> Any:  # noqa: ARG001
    """Execute an incremental FTS reindex via the change tracker."""
    return ctx.index_manager.reindex()


def run_build_embeddings(job: BuildEmbeddings, ctx: WriterContext) -> Any:
    """Execute a full vector index build."""
    return ctx.index_manager.build_embeddings(force=job.force)


def run_process_dirty_paths(
    job: ProcessDirtyPaths,  # noqa: ARG001
    ctx: WriterContext,
) -> None:
    """Drain the FTS-dirty set, update FTS, mark paths for embedding."""
    if ctx.writer is None:
        msg = "WriterContext.writer must be set before running jobs"
        raise RuntimeError(msg)
    snapshot = ctx.writer.drain_dirty_paths()
    ctx.index_manager.process_dirty_paths(snapshot)
    # After FTS is up-to-date, queue the same paths for vector re-embedding.
    # The writer is now the sole owner of embedding flushes (no inline
    # callback inside semantic search).  Follow-up submissions from
    # inside the writer thread succeed even during shutdown drain so the
    # vector-dirty set flushes before the worker exits.
    if snapshot:
        ctx.writer.mark_embedding_dirty(snapshot)
        ctx.writer.submit(FlushDirtyEmbeddings())


def run_flush_dirty_embeddings(
    job: FlushDirtyEmbeddings,  # noqa: ARG001
    ctx: WriterContext,
) -> None:
    """Drain the vector-dirty set and route the snapshot to the index manager."""
    if ctx.writer is None:
        msg = "WriterContext.writer must be set before running jobs"
        raise RuntimeError(msg)
    snapshot = ctx.writer.drain_dirty_embeddings()
    ctx.index_manager.flush_dirty_embeddings(snapshot)
