"""Build attempts and their single completion owner (#1483).

Every entry point schedules the same command. Its public Future completes only
once the scan, persistent marker and readiness publication have finished. Each
attempt retains its own outcome: a later build cannot rewrite an earlier waiter's
result. Readiness is a projection of the latest scheduled attempt for query APIs.
Future ordering: docs/design/reference/python-futures.md.
"""

from __future__ import annotations

import threading
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.indexing.index_writer import (
    BuildIndex,
    IndexWriter,
    ProcessDirtyPaths,
    WriterContext,
    run_build_index,
)
from markdown_vault_mcp.types import IndexStats

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.indexing.readiness import ReadinessState
    from markdown_vault_mcp.interfaces import KeywordIndex


@dataclass(frozen=True)
class _Succeeded:
    stats: IndexStats


@dataclass(frozen=True)
class _Unsuccessful:
    state: Literal["failed", "cancelled", "interrupted"]
    error: BaseException


@dataclass(eq=False)
class BuildAttempt:
    """One scheduled build, with an outcome independent of subsequent builds."""

    future: Future[IndexStats] = field(default_factory=Future)
    outcome: _Succeeded | _Unsuccessful | None = None

    def require_success(self, timeout: float) -> None:
        """Wait for this attempt and reject an incomplete or unsuccessful build.

        Args:
            timeout: Remaining seconds in the dependent operation's budget.

        Raises:
            TimeoutError: The attempt has not completed within the budget.
            IndexUnavailableError: The attempt failed, was cancelled or interrupted.
        """
        try:
            self.future.result(timeout=timeout)
        except BaseException as exc:
            outcome = self.outcome
            if isinstance(exc, CancelledError) and self.future.cancelled():
                reason: Literal["never_built", "build_failed"] = "never_built"
                state = "cancelled"
            elif isinstance(outcome, _Unsuccessful) and outcome.error is exc:
                reason = "build_failed" if outcome.state == "failed" else "never_built"
                state = outcome.state
            else:
                # A timeout or signal interrupting this caller is not a failed
                # build. Only translate the exception belonging to the attempt.
                raise
            raise IndexUnavailableError(
                f"Index build {state}: {exc}. "
                "Complete a successful build before retrying the mutation.",
                reason=reason,
            ) from exc


@dataclass(frozen=True)
class _BuildCommand(BuildIndex):
    attempt: BuildAttempt = field(kw_only=True)


class BuildLifecycle:
    """Own build scheduling, execution, outcome publication and readiness.

    The scheduling lock orders build requests and mutation barriers. It is never
    held while waiting for the writer. Only the latest request publishes global
    readiness; every request publishes its own Future regardless of that ordering.
    """

    def __init__(
        self,
        fts: KeywordIndex,
        readiness: ReadinessState,
        is_warm: Callable[[], bool],
    ) -> None:
        self._fts = fts
        self._readiness = readiness
        self._is_warm = is_warm
        self._lock = threading.RLock()
        self._latest: BuildAttempt | None = None
        self._legacy_started = False
        self._pending: set[BuildAttempt] = set()

    def submit(self, writer: IndexWriter, *, force: bool = False) -> Future[IndexStats]:
        """Schedule a complete build; an idle warm restart resolves immediately."""
        with self._lock:
            active = any(not pending.future.done() for pending in self._pending)
            attempt = BuildAttempt()
            self._latest = attempt
            self._readiness.begin_sync_build()
            attempt.future.add_done_callback(lambda _: self._on_cancelled(attempt))
            try:
                if not active and not force and self._is_warm():
                    stats = IndexStats(
                        documents_indexed=self._fts.count_documents(),
                        chunks_indexed=0,
                        skipped=0,
                    )
                    attempt.future.set_running_or_notify_cancel()
                    self._finish(attempt, _Succeeded(stats))
                else:
                    self._pending.add(attempt)
                    queued = writer.submit(_BuildCommand(force=force, attempt=attempt))
                    queued.add_done_callback(
                        lambda future: self._on_dispatch_done(attempt, future)
                    )
            except BaseException as exc:
                self._pending.discard(attempt)
                attempt.future.set_running_or_notify_cancel()
                self._finish(attempt, _Unsuccessful("failed", exc))
                raise
            return attempt.future

    def start_once(self, writer: IndexWriter) -> None:
        """Schedule the legacy one-shot background build through the same pipeline."""
        with self._lock:
            if self._legacy_started:
                return
            self._legacy_started = True
            self.submit(writer)

    def enqueue_refresh(
        self, writer: IndexWriter
    ) -> tuple[BuildAttempt | None, Future[None]]:
        """Capture the preceding build and queue a refresh in one scheduling step."""
        with self._lock:
            return self._latest, writer.submit(ProcessDirtyPaths())

    def run(self, job: BuildIndex, ctx: WriterContext) -> IndexStats | None:
        """Execute the full lifecycle on the writer, including marker writes."""
        if not isinstance(job, _BuildCommand):
            # Preserve the low-level IndexWriter/BuildIndex interface: raw jobs
            # still execute their runner without opting into coordinator policy.
            raw_result: IndexStats = run_build_index(job, ctx)
            return raw_result
        attempt = job.attempt
        if not attempt.future.set_running_or_notify_cancel():
            return None
        scanning = True
        try:
            self._fts.clear_build_completed()
            stats: IndexStats = run_build_index(job, ctx)
            scanning = False
            self._fts.set_build_completed()
        except CancelledError as exc:
            self._finish(attempt, _Unsuccessful("cancelled", exc))
            raise
        except BaseException as exc:
            # Preserve #585/#591: process interruption during marker publication
            # unblocks waiters but is not labelled an ordinary failed scan.
            state: Literal["failed", "interrupted"] = (
                "failed" if scanning or isinstance(exc, Exception) else "interrupted"
            )
            self._finish(attempt, _Unsuccessful(state, exc))
            raise
        self._finish(attempt, _Succeeded(stats))
        return stats

    def _record(
        self, attempt: BuildAttempt, outcome: _Succeeded | _Unsuccessful
    ) -> None:
        with self._lock:
            attempt.outcome = outcome
            if self._latest is attempt:
                if isinstance(outcome, _Succeeded):
                    self._readiness.mark_built()
                elif outcome.state == "failed":
                    self._readiness.fail_build(outcome.error)
                else:
                    self._readiness.mark_done()

    def _finish(
        self, attempt: BuildAttempt, outcome: _Succeeded | _Unsuccessful
    ) -> None:
        self._record(attempt, outcome)
        if isinstance(outcome, _Succeeded):
            attempt.future.set_result(outcome.stats)
        else:
            attempt.future.set_exception(outcome.error)

    def _on_cancelled(self, attempt: BuildAttempt) -> None:
        if attempt.future.cancelled():
            self._record(attempt, _Unsuccessful("cancelled", CancelledError()))

    def _on_dispatch_done(self, attempt: BuildAttempt, queued: Future[object]) -> None:
        # A queued command can be cancelled by writer shutdown before run() gets
        # a chance to publish anything. Transport failure must also settle it.
        with self._lock:
            self._pending.discard(attempt)
        if attempt.future.done():
            return
        try:
            queued.result()
            raise RuntimeError("Build command returned without publishing its outcome")
        except CancelledError:
            attempt.future.cancel()
        except BaseException as exc:
            if attempt.future.set_running_or_notify_cancel():
                self._finish(attempt, _Unsuccessful("failed", exc))
