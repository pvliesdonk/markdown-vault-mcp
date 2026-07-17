"""In-memory store for background ``summarize`` jobs (#937).

When a ``summarize`` call runs past its inline soft-deadline the server
promotes it to a background job and returns a job id immediately, instead
of letting the MCP client abandon the request at its own opaque timeout.
The finished summary (or the failure) lands here, keyed by that id, and is
retrieved by the ``get_summary`` tool.

The store is deliberately ephemeral — a process-local dict, lost on
restart — mirroring the other non-durable server state (index status,
transfer tokens). Entries are swept once their TTL elapses and the store
is capped so a long-lived server cannot accumulate unbounded finished
jobs.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.types import SummaryResult

# Finished (or failed) jobs live this long before a sweep drops them; an
# in-progress job is never swept while it is still running.
_TTL_SECONDS = 3600.0

# Hard cap on retained jobs. When exceeded, the oldest finished jobs are
# evicted first (an in-progress job is never evicted).
_MAX_JOBS = 256

JobStatus = Literal["in_progress", "completed", "failed"]


@dataclass
class SummaryJob:
    """One background summarize job.

    Attributes:
        job_id: Unguessable URL-safe identifier returned to the caller.
        status: ``"in_progress"`` until the work finishes, then
            ``"completed"`` or ``"failed"``.
        result: The finished :class:`~markdown_vault_mcp.types.SummaryResult`
            on success; ``None`` otherwise.
        error: The failure message on error; ``None`` otherwise.
        created_at: Monotonic timestamp when the job was registered.
        finished_at: Monotonic timestamp when it completed/failed; ``None``
            while in progress.
    """

    job_id: str
    status: JobStatus
    result: SummaryResult | None
    error: str | None
    created_at: float
    finished_at: float | None


class SummaryJobStore:
    """Thread-safe, ephemeral store of background summarize jobs.

    Per-job lifecycle is ``in_progress → completed`` or
    ``in_progress → failed``; a terminal job is retained for ``ttl_seconds``
    so the caller has a window to poll it, then swept. The background worker
    completes a job from the event-loop thread while ``get_summary`` reads it
    from a request thread, so all access is guarded by a single lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = _TTL_SECONDS,
        max_jobs: int = _MAX_JOBS,
    ) -> None:
        """Initialise the store.

        Args:
            clock: Zero-arg callable returning monotonic seconds (injectable
                for tests).
            ttl_seconds: How long a finished job is retained before a sweep
                drops it.
            max_jobs: Cap on retained jobs; the oldest finished jobs are
                evicted first once it is exceeded.
        """
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._max_jobs = max_jobs
        self._jobs: dict[str, SummaryJob] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        """Register a new ``in_progress`` job and return its id."""
        job_id = secrets.token_urlsafe(16)
        now = self._clock()
        with self._lock:
            self._sweep_locked()
            self._jobs[job_id] = SummaryJob(
                job_id=job_id,
                status="in_progress",
                result=None,
                error=None,
                created_at=now,
                finished_at=None,
            )
            self._enforce_cap_locked()
        return job_id

    def complete(self, job_id: str, result: SummaryResult) -> None:
        """Mark a job completed with its result (idempotent, no-op if gone)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "completed"
            job.result = result
            job.error = None
            job.finished_at = self._clock()

    def fail(self, job_id: str, error: str) -> None:
        """Mark a job failed with an error message (idempotent, no-op if gone)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "failed"
            job.result = None
            job.error = error
            job.finished_at = self._clock()

    def get(self, job_id: str) -> SummaryJob | None:
        """Return the job (sweeping expired entries first), or ``None``."""
        with self._lock:
            self._sweep_locked()
            return self._jobs.get(job_id)

    def _sweep_locked(self) -> None:
        """Drop terminal jobs whose TTL has elapsed. Caller holds the lock."""
        now = self._clock()
        expired = [
            jid
            for jid, job in self._jobs.items()
            if job.finished_at is not None
            and now - job.finished_at >= self._ttl_seconds
        ]
        for jid in expired:
            del self._jobs[jid]

    def _enforce_cap_locked(self) -> None:
        """Evict oldest finished jobs beyond the cap. Caller holds the lock."""
        if len(self._jobs) <= self._max_jobs:
            return
        # Only terminal jobs are evictable; oldest-finished first.
        finished = sorted(
            (job for job in self._jobs.values() if job.finished_at is not None),
            key=lambda job: job.finished_at or 0.0,
        )
        overflow = len(self._jobs) - self._max_jobs
        for job in finished[:overflow]:
            del self._jobs[job.job_id]
