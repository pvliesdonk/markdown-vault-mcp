"""Build-readiness state machine for the index coordinator.

Encapsulates the (_index_built, done-event, error) triple that was
formerly scattered across Vault. Sole owner: IndexWriteCoordinator.

Invariant: a captured build error never gates queryability —
``is_queryable`` ignores it, ``wait`` does not raise on it,
``status_fields`` surfaces it as diagnostic state. ``require_built``
reads it only to label its raise (``build_failed`` vs ``never_built``,
#586), never to decide *whether* it raises. (See issue #531's lesson.)
"""

from __future__ import annotations

import threading
from typing import Any

from markdown_vault_mcp.exceptions import IndexUnavailableError


class ReadinessState:
    """Tracks whether the FTS index is built and queryable."""

    def __init__(self) -> None:
        self._index_built = False
        # Pre-set: a freshly constructed vault that never called
        # build_index() must not look "building" forever to waiters.
        self._done = threading.Event()
        self._done.set()
        self._error: BaseException | None = None

    # -- transitions (each mirrors one former Vault mutation set) --

    def begin_sync_build(self) -> None:
        """Sync build_index cold path: clears built + error + done (#587)."""
        self._index_built = False
        self._error = None
        self._done.clear()

    def begin_async_build(self) -> None:
        """Async build_index_async cold path: clears built + error + done."""
        self._index_built = False
        self._error = None
        self._done.clear()

    def begin_background_build(self) -> None:
        """Deprecated start_background_build_index: clears error + done."""
        self._error = None
        self._done.clear()

    def mark_built(self) -> None:
        """Warm short-circuit / sync success / async-done success."""
        self._index_built = True
        self._error = None
        self._done.set()

    def fail_build(self, exc: BaseException) -> None:
        """Submit failure / async-done failure / background failure."""
        self._error = exc
        self._done.set()

    def record_error(self, exc: BaseException) -> None:
        """Record an error WITHOUT touching the done-event.

        For the deprecated background worker, whose ``except`` records the
        error and whose ``finally`` sets the done-event regardless.
        """
        self._error = exc

    def mark_done(self) -> None:
        """Idempotently set the done-event so waiters unblock (any liveness finally)."""
        self._done.set()

    # -- queries --

    @property
    def is_built(self) -> bool:
        return self._index_built

    @property
    def error(self) -> BaseException | None:
        return self._error

    def is_queryable(self) -> bool:
        if not self._index_built:
            return False
        return self._done.is_set()

    def wait(self, timeout: float | None) -> bool:
        return self._done.wait(timeout=timeout)

    def require_built(self) -> None:
        """Raise if not built, distinguishing build_failed from never_built (#586)."""
        if self._index_built:
            return
        if self._error is not None:
            raise IndexUnavailableError(
                f"Index build failed: {self._error}. "
                "Inspect get_index_status() for the captured error.",
                reason="build_failed",
            )
        raise IndexUnavailableError(
            "Index not built. Call build_index() or build_index_async() first.",
            reason="never_built",
        )

    def status_fields(self) -> dict[str, Any]:
        if self.is_queryable():
            status = "queryable"
            error = str(self._error) if self._error is not None else None
        elif self._done.is_set() and self._error is not None:
            status = "failed"
            error = str(self._error)
        else:
            status = "building"
            error = None
        return {"status": status, "error": error}
