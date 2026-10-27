"""Single-owner writer for FTS and vector indexes.

See docs/design/design.md and docs/design/reference/python-futures.md.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from markdown_vault_mcp.exceptions import StoreUnavailableError

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
    """Full vector index build.

    ``after_fault`` rebuilds a store that would not open instead: forced for
    another identity, an ordinary cold build for a corrupt one moved aside
    (#1505).
    """

    kind: ClassVar[str] = "build_embeddings"
    force: bool = False
    after_fault: bool = False


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
        self._crashed = False
        self._dirty_lock = threading.Lock()
        self._dirty_paths: set[str] = set()
        self._dirty_embeddings: set[str] = set()
        # Paths the embedding provider failed for (#1768): not pending work,
        # so drain status ignores them, but the next flush retries them.
        self._retry_embeddings: set[str] = set()
        self._in_flight_lock = threading.Lock()
        self._in_flight_kind: str | None = None
        # Monotonically increments each time a job finishes. Read under
        # _in_flight_lock alongside _in_flight_kind so callers can pair
        # the snapshot with the state. Used by drift-aware B3 readers
        # (#534) to detect write cycles that fit entirely inside a
        # tool's read window.
        self._write_generation: int = 0

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
        submissions after :meth:`close` raise. A crashed writer rejects all
        submissions, including those from cancellation callbacks on its worker.

        Raises:
            RuntimeError: If the writer crashed, or it is closed and the
                submission comes from outside its worker thread.
        """
        with self._submit_lock:
            if self._closed.is_set() and (
                self._crashed or threading.current_thread() is not self._thread
            ):
                msg = "IndexWriter is closed; cannot submit new jobs"
                raise RuntimeError(msg)
            future: Future[Any] = Future()
            self._queue.put((job, future))
        return future

    def close(self, timeout: float = 30.0) -> bool:
        """Signal shutdown and drain the queue before joining the worker.

        Marks the writer closed so no new external submissions are
        accepted, then posts the shutdown sentinel.  Jobs already in
        the queue, and any follow-up jobs they submit from inside the
        writer thread (e.g. ``ProcessDirtyPaths`` submitting
        ``FlushDirtyEmbeddings``), drain through the worker's FIFO
        before the sentinel terminates the loop.

        The worker thread is daemon; if the drain exceeds *timeout*,
        process exit kills the remainder.

        Returns:
            True when no worker is running any more (or none was started);
            False when the drain outlived *timeout*, or when called from the
            worker itself, which cannot join its own thread. A caller must
            not close what the worker writes to while this is False (#1896).
        """
        with self._submit_lock:
            newly_closed = not self._closed.is_set()
            self._closed.set()
        if newly_closed:
            self._queue.put(_SHUTDOWN_SENTINEL)
        if self._thread is None:
            return True
        if threading.current_thread() is self._thread:
            return False
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

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

    def mark_embedding_retry(self, paths: Iterable[str]) -> None:
        """Keep paths the embedding provider failed for, for the next flush.

        Unlike :meth:`mark_embedding_dirty` they do not count as pending
        work, so a note the provider keeps rejecting cannot hold the writer
        undrained (#1768).
        """
        with self._dirty_lock:
            self._retry_embeddings.update(paths)

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
        """Snapshot-and-clear the vector-dirty set and the retries, under the lock."""
        with self._dirty_lock:
            snapshot = self._dirty_embeddings | self._retry_embeddings
            self._dirty_embeddings.clear()
            self._retry_embeddings.clear()
        return snapshot

    def get_status(self) -> dict[str, Any]:
        """Return non-blocking snapshot of writer state."""
        with self._in_flight_lock:
            in_flight = self._in_flight_kind
            write_generation = self._write_generation
        with self._dirty_lock:
            dirty_paths = len(self._dirty_paths)
            dirty_embeddings = len(self._dirty_embeddings)
        return {
            "queue_depth": self._queue.qsize(),
            "in_flight": in_flight,
            "dirty_paths": dirty_paths,
            "dirty_embeddings": dirty_embeddings,
            "write_generation": write_generation,
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
            # Claim in_flight BEFORE notifying the future, so a
            # concurrent get_status() cannot observe queue_depth=0
            # together with in_flight=None during dispatch (#534/#573).
            with self._in_flight_lock:
                self._in_flight_kind = job.kind
            if not future.set_running_or_notify_cancel():
                with self._in_flight_lock:
                    self._in_flight_kind = None
                continue
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
                    _log_job_failure(job.kind, exc)
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
                self._abort_pending()
                raise
            finally:
                with self._in_flight_lock:
                    self._in_flight_kind = None
                    self._write_generation += 1

    def _abort_pending(self) -> None:
        """Close and drain atomically; notify cancelled callers outside the lock."""
        pending: list[Future[Any]] = []
        with self._submit_lock:
            self._closed.set()
            self._crashed = True
            while True:
                try:
                    queued = self._queue.get_nowait()
                except queue.Empty:
                    break
                if queued is not _SHUTDOWN_SENTINEL:
                    _, future = cast("tuple[Any, Future[Any]]", queued)
                    pending.append(future)
        # Future callbacks may take their owner's scheduling lock or submit
        # another job. Calling them under _submit_lock reverses that lock order.
        for future in pending:
            # Only this worker can claim execution, and it removed these jobs
            # from its queue. None can be running; already-done Futures stay done.
            future.cancel()


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


def _requeue_conflicts(ctx: WriterContext) -> None:
    """Send back the documents a vector write left behind (#1768).

    A conflicting write gets another flush now; a document the provider
    failed for waits for the next flush, without queueing one and without
    counting as pending work, so a provider that is down neither spins the
    writer nor holds it undrained.
    """
    if ctx.writer is None:
        return
    retries = getattr(ctx.index_manager, "take_embedding_retries", None)
    retry = retries() if retries is not None else set()
    if retry:
        ctx.writer.mark_embedding_retry(retry)
    take = getattr(ctx.index_manager, "take_embedding_conflicts", None)
    conflicts = take() if take is not None else set()
    if conflicts:
        ctx.writer.mark_embedding_dirty(conflicts)
        ctx.writer.submit(FlushDirtyEmbeddings())


def run_reindex_all(job: ReindexAll, ctx: WriterContext) -> Any:  # noqa: ARG001
    """Execute an incremental FTS reindex via the change tracker."""
    try:
        return ctx.index_manager.reindex()
    finally:
        _requeue_conflicts(ctx)


def run_build_embeddings(job: BuildEmbeddings, ctx: WriterContext) -> Any:
    """Execute a full vector index build."""
    try:
        if job.after_fault:
            return ctx.index_manager.rebuild_embeddings_after_fault()
        return ctx.index_manager.build_embeddings(force=job.force)
    finally:
        _requeue_conflicts(ctx)


def run_process_dirty_paths(
    job: ProcessDirtyPaths,  # noqa: ARG001
    ctx: WriterContext,
) -> None:
    """Drain the FTS-dirty set, update FTS, mark paths for embedding."""
    if ctx.writer is None:
        msg = "WriterContext.writer must be set before running jobs"
        raise RuntimeError(msg)
    snapshot = ctx.writer.drain_dirty_paths()
    refreshed: set[str] = set()
    try:
        ctx.index_manager.process_dirty_paths(snapshot, on_refreshed=refreshed.add)
    except Exception:
        # Retain the whole snapshot: a graph failure may need all of it retried.
        # The Future still surfaces the original error to dependent mutations.
        ctx.writer.mark_dirty(snapshot)
        raise
    finally:
        # Healthy siblings must reach the vector index even when another path
        # or graph resolution fails. Follow-ups from the writer thread remain
        # accepted during shutdown, so they also participate in its drain.
        if refreshed:
            ctx.writer.mark_embedding_dirty(refreshed)
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
    finally:
        _requeue_conflicts(ctx)


def _log_job_failure(kind: str, exc: Exception) -> None:
    """Log a failed job: a store that is unavailable for now is a WARNING.

    A store that cannot be reached, or that another process is writing
    (#1505), heals itself once it is back or that process exits; anything
    else is a fault, logged with its traceback.
    """
    if isinstance(exc, StoreUnavailableError):
        logger.warning("writer_job_unavailable kind=%s err=%s", kind, exc)
    else:
        logger.exception("writer_job_failed kind=%s", kind)
