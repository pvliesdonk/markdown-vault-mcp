"""MCP-layer `needs_queryable` decorator (#513 PR1).

Boundary: the library raises ``IndexUnavailableError`` immediately on
not-queryable (PR #525 contract). Blocking semantics live here at the MCP
layer, where the caller's intent — "an MCP client is waiting and
wants to wait" — is unambiguous. Internal callers (lifespan, git
pull loop, CLI, direct library users) do NOT go through this
decorator; they handle "not queryable" with their own caller-appropriate
logic (skip, log, retry on next interval).
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os
import sqlite3
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import (
    IndexUnavailableError,
    IndexUnavailableReason,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


_BUSY_ERROR_NAMES: frozenset[str] = frozenset(
    {
        "SQLITE_BUSY",
        "SQLITE_LOCKED",
    }
)


def _resolve_build_timeout() -> float:
    """Read env var at call time so tests can monkeypatch.setenv it."""
    return float(os.environ.get("MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S", "60"))


def _classify_operational_error(
    exc: sqlite3.OperationalError,
) -> tuple[IndexUnavailableReason, str]:
    """Map a SQLite OperationalError to a ``(reason, message)`` pair.

    Conservative broken-default: only the well-known lock-contention
    errornames (BUSY, LOCKED) classify as ``"busy"``. Everything else
    (CORRUPT, NOTADB, IOERR, FULL, generic ERROR, unknown future
    codes, or a missing ``sqlite_errorname`` attribute) classifies as
    ``"broken"`` so operators stay in the loop rather than silently
    retrying through degradation. Note: ``SQLITE_FULL`` (disk full)
    requires operator action to free space, not retry, so it
    classifies as ``"broken"``.
    """
    errorname = getattr(exc, "sqlite_errorname", None)
    if errorname in _BUSY_ERROR_NAMES:
        return ("busy", "Index temporarily busy; try again shortly.")
    return ("broken", "Index appears broken; operation could not complete.")


def needs_queryable(
    timeout: float | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for bucket-3/4 MCP tool and resource handlers.

    Before invoking the wrapped handler, blocks on
    ``IndexFacet.wait_until_queryable(timeout)``. On the warm path
    (``is_queryable`` already True) the wait is skipped — no
    thread-pool overhead.

    The wrapper does NOT redeclare ``vault`` in its own
    signature. ``functools.wraps`` preserves the wrapped handler's
    signature so FastMCP's introspection sees the original
    ``vault: Vault = Depends(get_vault)`` parameter
    and injects it via kwargs. The wrapper reads ``vault`` from
    kwargs and passes args/kwargs through unchanged.

    Stacking order: place ``@needs_queryable(...)`` BELOW
    ``@mcp.tool(...)`` (or ``@mcp.resource(...)``) — that is,
    closer to ``def``. Python applies decorators bottom-up, so
    ``@needs_queryable`` wraps the handler first; then
    ``@mcp.tool`` runs on the result and FastMCP registers the
    already-wrapped function.

    Raises (propagated to MCP client via FastMCP error middleware):
        IndexUnavailableError: ``reason="timeout"`` when the bounded
            wait elapsed, ``reason="never_built"`` when no build was
            ever scheduled, ``reason="build_failed"`` when a build ran
            and failed (read ``get_index_status``'s ``error`` field),
            ``reason="broken"`` when a handler raised
            ``sqlite3.OperationalError`` from a non-busy errorname
            (CORRUPT, NOTADB, IOERR, FULL, etc.) — inspect
            ``__cause__`` for the underlying exception, or
            ``reason="busy"`` when the errorname is in
            ``{SQLITE_BUSY, SQLITE_LOCKED}`` (lock contention; retry
            may succeed).
    """

    def deco(handler: Callable[..., Any]) -> Callable[..., Any]:
        # Capture the handler's signature once at decoration time so
        # vault can be looked up regardless of whether it was
        # passed positionally or by keyword.
        sig = inspect.signature(handler)

        @functools.wraps(handler)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = sig.bind_partial(*args, **kwargs)
            vault = bound.arguments.get("vault")
            if vault is None:
                raise RuntimeError(
                    "needs_queryable: vault was not injected; "
                    "handler must declare "
                    "`vault: Vault = Depends(get_vault)`."
                )
            if not vault.index.is_queryable():
                effective = timeout if timeout is not None else _resolve_build_timeout()
                await asyncio.to_thread(vault.index.wait_until_queryable, effective)
            try:
                return await handler(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                reason, message = _classify_operational_error(exc)
                raise IndexUnavailableError(message, reason=reason) from exc

        return wrapper

    return deco
