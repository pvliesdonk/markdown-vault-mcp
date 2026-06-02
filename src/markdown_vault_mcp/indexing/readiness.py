"""Build-readiness state machine for the index coordinator.

Encapsulates the (_index_built, done-event, error) triple that was
formerly scattered across Collection. Sole owner: IndexWriteCoordinator.

Invariant: a captured build error is *diagnostic state*, never a
control-flow gate. ``is_queryable`` ignores it; ``wait`` does not raise
on it; ``status_fields`` surfaces it. (See issue #531's lesson.)
"""

from __future__ import annotations

import threading
from typing import Any

from markdown_vault_mcp.exceptions import IndexUnavailableError


class ReadinessState:
    """Tracks whether the FTS index is built and queryable."""

    def __init__(self) -> None:
        self._index_built = False
        # Pre-set: a freshly constructed collection that never called
        # build_index() must not look "building" forever to waiters.
        self._done = threading.Event()
        self._done.set()
        self._error: BaseException | None = None

    # -- transitions (each mirrors one former Collection mutation set) --

    def begin_sync_build(self) -> None:
        """Sync build_index cold path: clears built only."""
        self._index_built = False

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
        """Background worker finally clause: ensure waiters unblock."""
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
        if not self._index_built:
            raise IndexUnavailableError(
                "Index not built. Call build_index() before this method.",
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
