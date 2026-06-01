"""Single-owner writer for FTS and vector indexes.

See `docs/superpowers/specs/2026-05-31-issue-559-single-writer-for-indexes-design.md`.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import CancelledError as _CancelledError
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

    State machine::

        Constructed -> Started -> (Closing | Crashed) -> Terminal

    All transitions between states are atomic under ``_submit_lock``.
    The lock protects:

    - :meth:`start` — thread creation and ``_thread`` assignment.
    - :meth:`submit` — ``_closed`` check + queue ``put()``.
    - :meth:`close` — ``_closed.set()`` + pending-queue drain.
    - Worker BaseException branch in :meth:`_run` —
      ``_closed.set()`` + pending drain.

    Future-side atomicity (``set_running_or_notify_cancel`` /
    ``not future.done()`` guards) prevents secondary exceptions when a
    Future is in transient states between the worker's runner call and
    the surrounding lock release.

    External observers (:meth:`is_closed`, :meth:`submit`, callers
    waiting on Futures returned by :meth:`submit`) see a consistent
    view of writer state regardless of which transition is in flight.

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
        """Spawn the worker thread. Idempotent and thread-safe.

        The check-create-assign sequence runs under ``_submit_lock`` so
        two concurrent callers cannot both pass the ``_thread is None``
        check and both create a worker thread.

        Raises:
            RuntimeError: If thread creation fails (e.g. system thread
                limit exhausted). ``self._thread`` is left ``None`` so
                a retry can attempt to start again — the failed thread
                is never latched (replay-guard for the PR #528 finding).
        """
        with self._submit_lock:
            if self._thread is not None:
                return
            thread = threading.Thread(
                target=self._run,
                name="markdown-vault-mcp.writer",
                daemon=True,
            )
            # Assign self._thread only AFTER thread.start() succeeds so
            # a thread-creation failure leaves the writer retry-able
            # instead of latching the never-started thread.
            thread.start()
            self._thread = thread

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
                # Logging-handler failure (e.g. SocketHandler with
                # dropped connection, custom handler bug) must not
                # kill the worker.  The original exception is already
                # on the Future; the log line is for observability
                # only.  Without this guard the handler's exception
                # escapes _run() — the worker exits without setting
                # _closed or draining pending Futures, so every
                # submit().result() hangs.
                with contextlib.suppress(Exception):
                    logger.exception("Writer job %s failed", job.kind)
            except BaseException as exc:
                # KeyboardInterrupt / SystemExit / asyncio.CancelledError —
                # capture into the Future so waiters unblock, then re-raise
                # so the worker thread terminates. The finally block clears
                # _in_flight_kind before the re-raise so status reads on
                # other threads are not stuck on a stale value.
                #
                # Guard against ``future`` already being completed (e.g.
                # ``set_result`` ran a moment earlier and the BaseException
                # bubbled from a later statement): ``set_exception`` on a
                # done Future raises ``RuntimeError`` and shadows the
                # original BaseException.
                if not future.done():
                    future.set_exception(exc)
                # Logging-handler failure must not shadow the
                # BaseException nor skip the close-and-drain logic
                # below.  Mirror the Exception branch's guard.
                with contextlib.suppress(Exception):
                    logger.error(
                        "writer_job_basexception kind=%s",
                        job.kind,
                        exc_info=True,
                    )
                # Worker thread is terminating. Mark writer closed AND
                # drain pending items atomically under _submit_lock so a
                # concurrent submit() cannot pass the is_set() check and
                # queue.put() a Future past the drain (orphan-Future
                # leak).  Subsequent external submits fail fast with
                # RuntimeError; drained Futures unblock waiters with
                # CancelledError instead of hanging.
                with self._submit_lock:
                    self._closed.set()
                    while True:
                        try:
                            queued = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if queued is _SHUTDOWN_SENTINEL:
                            continue
                        _, pending_future = cast("tuple[Any, Future[Any]]", queued)
                        if not pending_future.cancel() and not pending_future.done():
                            pending_future.set_exception(_CancelledError())
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
    try:
        ctx.index_manager.process_dirty_paths(snapshot)
    except Exception:
        # Restore the snapshot so a future ProcessDirtyPaths job can
        # retry these paths.  Without this, a non-per-path failure
        # (sqlite3.OperationalError on disk-full, WAL lock, etc.)
        # silently drops the entire snapshot — the dirty set was
        # cleared by drain_dirty_paths().  The exception still
        # propagates to the Future for caller observability.
        ctx.writer.mark_dirty(snapshot)
        raise
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
    try:
        ctx.index_manager.flush_dirty_embeddings(snapshot)
    except Exception:
        # Restore the snapshot so a future FlushDirtyEmbeddings job
        # can retry these paths.  Mirrors the recovery in
        # run_process_dirty_paths — a non-per-path failure must not
        # silently drop the drained set.
        ctx.writer.mark_embedding_dirty(snapshot)
        raise
