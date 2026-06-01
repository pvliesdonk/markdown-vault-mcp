"""Single-owner writer for FTS and vector indexes.

See `docs/superpowers/specs/2026-05-31-issue-559-single-writer-for-indexes-design.md`.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import CancelledError, Future
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

        Raises:
            RuntimeError: If :meth:`close` has been called.
        """
        with self._submit_lock:
            if self._closed.is_set():
                msg = "IndexWriter is closed; cannot submit new jobs"
                raise RuntimeError(msg)
            future: Future[Any] = Future()
            self._queue.put((job, future))
        return future

    def close(self, timeout: float = 30.0) -> None:
        """Signal shutdown, cancel pending jobs, join the worker.

        Pending queued jobs (those that haven't started running yet)
        have their Futures resolved with
        :class:`concurrent.futures.CancelledError`. The in-flight job
        (if any) is allowed to complete normally. The worker thread is
        daemon; if the in-flight job exceeds *timeout*, process exit
        kills it.
        """
        with self._submit_lock:
            if self._closed.is_set():
                return
            self._closed.set()
            # Drain pending items under the lock so no new submission can
            # race with the cancellation pass. The worker may still be
            # processing the in-flight item; FIFO ordering means anything
            # already in the queue has not yet been pulled by the worker.
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is _SHUTDOWN_SENTINEL:
                    continue
                _, future = cast("tuple[Any, Future[Any]]", item)
                if not future.cancel() and not future.done():
                    future.set_exception(CancelledError())
            self._queue.put(_SHUTDOWN_SENTINEL)
        # Sentinel posted; join outside the lock.
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
        """Worker loop."""
        while True:
            item = self._queue.get()
            if item is _SHUTDOWN_SENTINEL:
                return
            job, future = cast("tuple[Any, Future[Any]]", item)
            if not future.set_running_or_notify_cancel():
                continue
            with self._in_flight_lock:
                self._in_flight_kind = job.kind
            try:
                runner = self._runners[job.kind]
                result = runner(job, self._ctx)
                future.set_result(result)
            except BaseException as exc:
                future.set_exception(exc)
                logger.exception("Writer job %s failed", job.kind)
            finally:
                with self._in_flight_lock:
                    self._in_flight_kind = None
