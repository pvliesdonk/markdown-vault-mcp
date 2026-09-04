"""Whether a syncing clone is still reaching its remote (#1287).

A clone whose pushes are being rejected keeps accepting writes: the commit
lands, ``read`` serves it back, and every signal available to the caller says
the write succeeded.  The commits are real but they never leave the host, so
an agent whose only route to the repository is this server believes it saved
work it did not save.

:class:`SyncHealthTracker` is the one place that remembers whether the clone
is reaching its remote.  The git layer feeds it the outcome of every push and
pull; the MCP write path reads :meth:`SyncHealthTracker.snapshot` and, while
the clone is unsynced, says so on the write's own result.

Two properties matter for how it is used:

* **Reads are lock-free.** The snapshot is one immutable object swapped
  atomically, never assembled by the reader.  The strategy-wide lock is held
  across a whole fetch + merge, so reading health under it would make every
  write response block behind a pull.
* **The log records transitions, not cycles.** Entering the unsynced state
  logs once at ERROR and recovery once at INFO.  The repeating per-cycle
  warning is what let the incident in #1287 run for hours unnoticed.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from markdown_vault_mcp.git.types import (
    PULL_REASON_CONFLICT_RESOLUTION_FAILED,
    PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS,
    PUSH_REASON_NON_FAST_FORWARD,
    PUSH_REASON_PUSH_FAILED,
    REMOTE_STATE_UNSYNCED,
)

logger = logging.getLogger(__name__)

#: Push outcomes that prove local commits are not reaching the remote.
#: ``no_remote`` and ``dry_run_unsupported`` are deliberately absent: neither
#: is evidence of stranded commits — the first says no remote was configured
#: to strand them on, the second that no push was attempted.
_PUSH_CONDITIONS = frozenset({PUSH_REASON_PUSH_FAILED, PUSH_REASON_NON_FAST_FORWARD})

#: Pull outcomes that prove the clone cannot reconcile with its remote, and
#: so that pushes will keep being rejected.  ``fetch_failed`` is absent: a
#: failed fetch is a network event, not evidence that anything is stranded.
_PULL_CONDITIONS = frozenset(
    {
        PULL_REASON_CONFLICT_RESOLUTION_FAILED,
        PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS,
    }
)

_Kind = Literal["push", "pull"]

#: Fragments git prints when a push is rejected because the remote holds
#: commits the clone has not seen.  Git's wording is stable: both appear in
#: the rejection line and the hint paragraph.
_NON_FAST_FORWARD_MARKERS = ("non-fast-forward", "fetch first")


def push_failure_reason(stderr: str) -> str:
    """Classify a failed ``git push`` from its stderr.

    Args:
        stderr: The push's stderr, already token-redacted.

    Returns:
        :data:`~markdown_vault_mcp.git.types.PUSH_REASON_NON_FAST_FORWARD`
        when the remote rejected the push as behind, otherwise
        :data:`~markdown_vault_mcp.git.types.PUSH_REASON_PUSH_FAILED`.  Both
        strand the caller's commits; they differ in what an operator does
        next.
    """
    if any(marker in stderr for marker in _NON_FAST_FORWARD_MARKERS):
        return PUSH_REASON_NON_FAST_FORWARD
    return PUSH_REASON_PUSH_FAILED


@dataclass(frozen=True, slots=True)
class SyncHealth:
    """A clone that is known not to be reaching its remote.

    There is no healthy variant of this type — a healthy clone is reported as
    ``None``, so a caller that sees an instance at all knows something is
    wrong without comparing against a state name.

    Attributes:
        state: Always :data:`~markdown_vault_mcp.git.types.REMOTE_STATE_UNSYNCED`.
            Present so a caller can branch on a field rather than on the key's
            existence.
        reason: The ``PUSH_REASON_*`` / ``PULL_REASON_*`` code of the outcome
            that opened the condition still in force.  A push failure outranks
            a pull failure: it is the half that strands the caller's writes.
        since: When the clone was first observed not reaching its remote, in
            UTC.  Dates the outage, not the most recent failed attempt.
    """

    state: str
    reason: str
    since: datetime

    def as_payload(self) -> dict[str, str]:
        """Project the snapshot into the dict a write tool returns.

        Returns:
            ``state`` / ``reason`` / ``since`` (ISO 8601) plus ``detail``, a
            self-contained sentence for a caller that reads prose rather than
            reason codes.
        """
        # Seconds resolution: the field dates an outage, and microseconds
        # would spend a caller's context on noise.
        since = self.since.isoformat(timespec="seconds")
        return {
            "state": self.state,
            "reason": self.reason,
            "since": since,
            "detail": (
                f"This vault's git clone has not reached its remote since {since} "
                f"({self.reason}). Your content is committed locally only and is "
                "not replicated — keep your own copy of anything written "
                "meanwhile."
            ),
        }


@dataclass(frozen=True, slots=True)
class _Condition:
    """One reason the clone is not reaching its remote, and when it started."""

    reason: str
    since: datetime


class SyncHealthTracker:
    """Remember whether pushes and pulls are reaching the remote.

    Push and pull are tracked as independent conditions, because recovering
    one does not recover the other: a clone can pull cleanly while every push
    is still rejected, which is exactly the incident in #1287.  The clone is
    unsynced while either condition holds.

    Every method is safe to call from any thread.  Writers serialise on a
    lock private to this object — never the strategy-wide lock — and readers
    take no lock at all.  That private lock is held only for a dict update
    and a log call — never across a git subprocess or another component's
    lock — so recording an outcome from inside the strategy lock (as the
    deferred push does) cannot deadlock.
    """

    def __init__(self) -> None:
        """Start out making no claim: nothing has been observed yet."""
        self._lock = threading.Lock()
        self._conditions: dict[_Kind, _Condition] = {}
        self._snapshot: SyncHealth | None = None

    def snapshot(self) -> SyncHealth | None:
        """Return the current health, or ``None`` while the clone is fine.

        Returns:
            An immutable :class:`SyncHealth`, or ``None`` when the clone is
            reaching its remote (or has not tried yet).
        """
        return self._snapshot

    def push_failed(self, reason: str) -> None:
        """Record a push that did not reach the remote.

        Args:
            reason: A ``PUSH_REASON_*`` code.  Codes that do not prove
                commits are stranded are ignored (see :data:`_PUSH_CONDITIONS`).
        """
        self._open("push", reason, _PUSH_CONDITIONS)

    def push_succeeded(self) -> None:
        """Record that local commits reached the remote."""
        self._close("push")

    def pull_failed(self, reason: str) -> None:
        """Record a pull that could not reconcile with the remote.

        Args:
            reason: A ``PULL_REASON_*`` code.  Codes that do not prove the
                clone has diverged unrecoverably are ignored (see
                :data:`_PULL_CONDITIONS`).
        """
        self._open("pull", reason, _PULL_CONDITIONS)

    def pull_succeeded(self) -> None:
        """Record that the clone reconciled with the remote."""
        self._close("pull")

    def _open(self, kind: _Kind, reason: str, conditions: frozenset[str]) -> None:
        """Open the *kind* condition, logging the transition into it once."""
        if reason not in conditions:
            return
        with self._lock:
            if kind in self._conditions:
                logger.debug(
                    "git_remote_still_unsynced kind=%s reason=%s", kind, reason
                )
                return
            was_healthy = self._snapshot is None
            self._conditions[kind] = _Condition(reason=reason, since=datetime.now(UTC))
            self._recompute()
            if was_healthy:
                logger.error(
                    "git_remote_unsynced kind=%s reason=%s "
                    "detail=writes are committed locally and are not reaching the remote",
                    kind,
                    reason,
                )
            else:
                logger.debug("git_remote_unsynced_also kind=%s reason=%s", kind, reason)

    def _close(self, kind: _Kind) -> None:
        """Close the *kind* condition, logging recovery once when it was the last."""
        with self._lock:
            closed = self._conditions.pop(kind, None)
            if closed is None:
                return
            self._recompute()
            if self._snapshot is None:
                logger.info(
                    "git_remote_resynced kind=%s unsynced_since=%s",
                    kind,
                    closed.since.isoformat(),
                )
            else:
                logger.debug("git_remote_partly_resynced kind=%s", kind)

    def _recompute(self) -> None:
        """Rebuild the published snapshot from the open conditions.

        Called under :attr:`_lock`.  Publishes one fully built immutable
        object so a concurrent reader never observes a half-updated state.
        """
        active = self._conditions.get("push") or self._conditions.get("pull")
        if active is None:
            self._snapshot = None
            return
        self._snapshot = SyncHealth(
            state=REMOTE_STATE_UNSYNCED,
            reason=active.reason,
            since=min(condition.since for condition in self._conditions.values()),
        )
