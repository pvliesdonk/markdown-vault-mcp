"""MCP-layer `needs_index_ready` decorator (#513 PR1).

Boundary: the library raises ``IndexNotReadyError`` immediately on
not-ready (PR #525 contract). Blocking semantics live here at the MCP
layer, where the caller's intent — "an MCP client is waiting and
wants to wait" — is unambiguous. Internal callers (lifespan, git
pull loop, CLI, direct library users) do NOT go through this
decorator; they handle "not ready" with their own caller-appropriate
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

from markdown_vault_mcp.exceptions import IndexNotReadyError

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _resolve_ready_timeout() -> float:
    """Read env var at call time so tests can monkeypatch.setenv it."""
    return float(os.environ.get("MARKDOWN_VAULT_MCP_READY_TIMEOUT_S", "60"))


def needs_index_ready(
    timeout: float | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for bucket-3/4 MCP tool and resource handlers.

    Before invoking the wrapped handler, blocks on
    ``Collection.wait_for_index_ready(timeout)``. On the warm path
    (``is_index_ready`` already True) the wait is skipped — no
    thread-pool overhead.

    The wrapper does NOT redeclare ``collection`` in its own
    signature. ``functools.wraps`` preserves the wrapped handler's
    signature so FastMCP's introspection sees the original
    ``collection: Collection = Depends(get_collection)`` parameter
    and injects it via kwargs. The wrapper reads ``collection`` from
    kwargs and passes args/kwargs through unchanged.

    Stacking order: place ``@needs_index_ready(...)`` BELOW
    ``@mcp.tool(...)`` (or ``@mcp.resource(...)``) — that is,
    closer to ``def``. Python applies decorators bottom-up, so
    ``@needs_index_ready`` wraps the handler first; then
    ``@mcp.tool`` runs on the result and FastMCP registers the
    already-wrapped function.

    Raises (propagated to MCP client via FastMCP error middleware):
        IndexNotReadyError: with ``reason="timeout"`` if the bounded
            wait elapsed before the background build completed;
            ``reason="never_built"`` if the build was never scheduled;
            ``reason="broken"`` if the wrapped handler raised
            ``sqlite3.OperationalError`` (reactive broken-detection
            boundary — issue #533).
    """

    def deco(handler: Callable[..., Any]) -> Callable[..., Any]:
        # Capture the handler's signature once at decoration time so
        # collection can be looked up regardless of whether it was
        # passed positionally or by keyword.
        sig = inspect.signature(handler)

        @functools.wraps(handler)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = sig.bind_partial(*args, **kwargs)
            collection = bound.arguments.get("collection")
            if collection is None:
                raise RuntimeError(
                    "needs_index_ready: collection was not injected; "
                    "handler must declare "
                    "`collection: Collection = Depends(get_collection)`."
                )
            if not collection.is_index_ready():
                effective = timeout if timeout is not None else _resolve_ready_timeout()
                await asyncio.to_thread(collection.wait_for_index_ready, effective)
            try:
                return await handler(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                raise IndexNotReadyError(
                    "Index appears broken; operation could not complete.",
                    reason="broken",
                ) from exc

        return wrapper

    return deco
