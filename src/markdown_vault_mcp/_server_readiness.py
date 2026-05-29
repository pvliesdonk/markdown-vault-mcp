"""MCP-layer `needs_index_ready` decorator (#513 PR1).

Boundary: the library raises ``IndexNotReadyError`` immediately on
not-ready (PR #525 contract). Blocking semantics live here at the MCP
layer, where the caller's intent — "an MCP client is waiting and
wants to wait" — is unambiguous. Internal callers (lifespan, git
pull loop, CLI, direct library users) do NOT go through this
decorator; they handle "not ready" with their own caller-appropriate
logic (skip, log, retry on next interval).

PR2 (#513) extension: ``@needs_index_ready(embeddings=True)`` waits
for the embeddings phase in addition to FTS, but only when the
Collection has an ``embedding_provider`` configured (the wait is a
no-op otherwise — see :attr:`Collection.has_embedding_provider`).
Worst-case total wait when a provider is configured is
``2 x MARKDOWN_VAULT_MCP_READY_TIMEOUT_S`` (60s per phase, 120s
total with defaults). Applied
to ``get_similar``, ``vault_similar``, ``reindex``, and
``build_embeddings`` — the four surfaces that read or mutate the
vector sidecar. ``reindex`` and ``build_embeddings`` both run inside
``IndexManager.build_embeddings`` (which is lock-free across its
``_load_vectors``/``vectors.save`` critical region), so they must
serialise against the background worker's phase-2 — the embeddings
wait is what guarantees that ordering.
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
            (``reindex``, ``build_embeddings``) — the latter two
            serialise against the background worker's phase-2 via this
            wait (``IndexManager.build_embeddings`` is lock-free
            across ``_load_vectors``/``vectors.save``).

    Stacking order: place ``@needs_index_ready(...)`` BELOW
    ``@mcp.tool(...)`` (or ``@mcp.resource(...)``) — closer to ``def``.

    Raises (propagated to MCP client):
        IndexNotReadyError: timeout exceeded; embeddings phase skipped
            because the FTS phase failed (``embeddings=True`` only); or
            no build was ever scheduled.
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
