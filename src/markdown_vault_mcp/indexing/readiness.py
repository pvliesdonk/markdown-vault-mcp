"""Build-readiness state machine for the index coordinator.

Encapsulates the (built, done-event, error) triple that was
formerly scattered across Vault. Sole owner: IndexWriteCoordinator.

Invariant: a captured build error never gates queryability —
``is_queryable`` ignores it, ``wait`` does not raise on it,
``status_fields`` surfaces it as diagnostic state. ``require_built``
reads it only to label its raise (``build_failed`` vs ``never_built``,
#586), never to decide *whether* it raises. (See issue #531's lesson.)

The ``built`` flag and ``error`` are stored together in a single immutable
``_Verdict`` published as one atomic reference (#598). Compound readers
(``require_built``, ``status_fields``) capture the verdict once via
``_snapshot`` and decide from that snapshot, so a build transition landing
between two field reads can never produce a torn ``(built, error)`` pair —
e.g. a direct (non-waiting) ``require_built`` caller is never told
``never_built`` because it read a pre-build ``built`` and a post-build
``error``. The done-event stays a separate primitive (its own synchronisation
object); only the ``(built, error)`` pair is snapshotted.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from markdown_vault_mcp.exceptions import IndexUnavailableError


@dataclass(frozen=True, slots=True)
class _Verdict:
    """Immutable ``(built, error)`` pair, published as one atomic reference.

    Frozen so a published verdict can never be mutated in place: every
    transition assigns a brand-new instance to ``ReadinessState._verdict``,
    and every compound read captures the current instance once. This is what
    makes the pair tear-free under concurrent reads (#598).
    """

    built: bool
    error: BaseException | None


class ReadinessState:
    """Tracks whether the FTS index is built and queryable."""

    def __init__(self) -> None:
        self._verdict = _Verdict(built=False, error=None)
        # Pre-set: a freshly constructed vault that never called
        # build_index() must not look "building" forever to waiters.
        self._done = threading.Event()
        self._done.set()

    # -- transitions (each mirrors one former Vault mutation set) --
    #
    # Writes are serialised in time by the coordinator within a single build
    # lifecycle (begin -> mark_built/fail_build -> mark_done), so the
    # read-modify-write transitions below (which preserve the untouched field)
    # are safe; readers never write. Each transition publishes a complete new
    # ``_Verdict`` with a single attribute store.

    def begin_sync_build(self) -> None:
        """Sync build_index cold path: clears built + error + done (#587)."""
        self._verdict = _Verdict(built=False, error=None)
        self._done.clear()

    def begin_async_build(self) -> None:
        """Async build_index_async cold path: clears built + error + done."""
        self._verdict = _Verdict(built=False, error=None)
        self._done.clear()

    def begin_background_build(self) -> None:
        """Deprecated start_background_build_index: clears error + done.

        Preserves the ``built`` flag (this path never resets it).
        """
        self._verdict = _Verdict(built=self._verdict.built, error=None)
        self._done.clear()

    def mark_built(self) -> None:
        """Warm short-circuit / sync success / async-done success."""
        self._verdict = _Verdict(built=True, error=None)
        self._done.set()

    def fail_build(self, exc: BaseException) -> None:
        """Submit failure / async-done failure / background failure.

        Records the error and leaves the ``built`` flag untouched.
        """
        self._verdict = _Verdict(built=self._verdict.built, error=exc)
        self._done.set()

    def record_error(self, exc: BaseException) -> None:
        """Record an error WITHOUT touching the done-event.

        For the deprecated background worker, whose ``except`` records the
        error and whose ``finally`` sets the done-event regardless. Leaves the
        ``built`` flag untouched.
        """
        self._verdict = _Verdict(built=self._verdict.built, error=exc)

    def mark_done(self) -> None:
        """Idempotently set the done-event so waiters unblock (any liveness finally)."""
        self._done.set()

    # -- queries --

    def _snapshot(self) -> _Verdict:
        """Capture the current ``(built, error)`` verdict as one atomic read.

        A single attribute load is atomic under the GIL, so compound readers
        that go through this method observe a self-consistent pair (#598).
        """
        return self._verdict

    @property
    def is_built(self) -> bool:
        return self._verdict.built

    @property
    def error(self) -> BaseException | None:
        return self._verdict.error

    def is_queryable(self) -> bool:
        if not self._verdict.built:
            return False
        return self._done.is_set()

    def wait(self, timeout: float | None) -> bool:
        return self._done.wait(timeout=timeout)

    def require_built(self) -> None:
        """Raise if not built, distinguishing build_failed from never_built (#586).

        Reads ``(built, error)`` from a single snapshot so a concurrent build
        completion can never split the pair into a spurious ``never_built``
        (#598).
        """
        verdict = self._snapshot()
        if verdict.built:
            return
        if verdict.error is not None:
            raise IndexUnavailableError(
                f"Index build failed: {verdict.error}. "
                "Inspect get_index_status() for the captured error.",
                reason="build_failed",
            )
        raise IndexUnavailableError(
            "Index not built. Call build_index() or build_index_async() first.",
            reason="never_built",
        )

    def status_fields(self) -> dict[str, Any]:
        # Read the done-event BEFORE the verdict. Every transition that sets
        # the done-event publishes its terminal verdict first (see the
        # transition methods: ``self._verdict = ...`` precedes
        # ``self._done.set()``). So observing ``done`` True here guarantees the
        # verdict read next reflects that done-setting transition (or a newer
        # one) — the pair is consistent without a lock, and a just-failed build
        # can never be misreported as "building" from a stale ``error=None``
        # verdict (#598/#706).
        done = self._done.is_set()
        verdict = self._snapshot()
        if verdict.built and done:
            status = "queryable"
            error = str(verdict.error) if verdict.error is not None else None
        elif done and verdict.error is not None:
            status = "failed"
            error = str(verdict.error)
        else:
            status = "building"
            error = None
        return {"status": status, "error": error}
