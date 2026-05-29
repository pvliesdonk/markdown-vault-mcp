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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _resolve_ready_timeout() -> float:
    """Read env var at call time so tests can monkeypatch.setenv it."""
    return float(os.environ.get("MARKDOWN_VAULT_MCP_READY_TIMEOUT_S", "60"))


def needs_index_ready(
    timeout: float | None = None,
    embeddings: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for bucket-3/4 MCP tool and resource handlers.

    Before invoking the wrapped handler, blocks on
    ``Collection.wait_for_index_ready(timeout)``. When ``embeddings=True``,
    additionally blocks on ``Collection.wait_for_embeddings_ready(timeout)``
    after the FTS wait returns. Worst-case total wait is ``2 x timeout``.

    Both waits are skipped when the corresponding ``is_*_ready()`` check
    returns True — warm path has no thread-pool overhead.

    Args:
        timeout: Maximum seconds for EACH wait (FTS and embeddings).
            ``None`` uses ``MARKDOWN_VAULT_MCP_READY_TIMEOUT_S`` (default
            60). Read at call time so tests can ``monkeypatch.setenv``.
        embeddings: When True, also wait for the embeddings phase.
            Apply to handlers that need vector search (``get_similar``,
            ``vault_similar``) or that mutate the vector sidecar
            (``reindex``).

    Stacking order: place ``@needs_index_ready(...)`` BELOW
    ``@mcp.tool(...)`` (or ``@mcp.resource(...)``) — closer to ``def``.

    Raises (propagated to MCP client):
        IndexNotReadyError: timeout exceeded or never scheduled.
        IndexBuildFailedError: a prior background build (FTS or
            embeddings) raised.
    """

    def deco(handler: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(handler)

        @functools.wraps(handler)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            collection = sig.bind_partial(*args, **kwargs).arguments.get("collection")
            if collection is None:
                raise RuntimeError(
                    "needs_index_ready: collection was not injected; "
                    "handler must declare "
                    "`collection: Collection = Depends(get_collection)`."
                )
            effective = timeout if timeout is not None else _resolve_ready_timeout()
            if not collection.is_index_ready():
                await asyncio.to_thread(collection.wait_for_index_ready, effective)
            if (
                embeddings
                and collection.has_embedding_provider
                and not collection.is_embeddings_ready()
            ):
                await asyncio.to_thread(collection.wait_for_embeddings_ready, effective)
            return await handler(*args, **kwargs)

        return wrapper

    return deco
