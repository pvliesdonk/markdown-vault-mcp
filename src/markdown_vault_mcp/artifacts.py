"""Module-level artifact store wiring for MV tool handlers.

The heavy lifting — UUID tokens, TTL expiry, the HTTP route — lives in
:mod:`fastmcp_pvl_core._artifacts`.  This module only keeps a
module-level singleton so ``create_download_link`` (a tool handler
running outside FastMCP's DI context) can reach the same
:class:`ArtifactStore` that the HTTP route is serving.
"""

from __future__ import annotations

import logging

from fastmcp_pvl_core import ArtifactStore, TokenRecord

logger = logging.getLogger(__name__)

__all__ = [
    "ARTIFACT_TTL_SECONDS",
    "ArtifactStore",
    "TokenRecord",
    "get_artifact_store",
    "set_artifact_store",
]


# Single process-wide artifact TTL.  Core's ArtifactStore takes this at
# construction and applies it to every token — per-token TTL isn't
# supported upstream, so callers that request a shorter lifetime get
# this value back in ``expires_in_seconds`` for honesty.
ARTIFACT_TTL_SECONDS = 3600


_artifact_store: ArtifactStore | None = None


def set_artifact_store(store: ArtifactStore | None) -> None:
    """Set the module-level artifact store.

    Called from :func:`markdown_vault_mcp.mcp_server.create_server` on
    each server construction.  The server owns the store for its
    lifetime; there is no lifespan teardown hook, so a later
    ``create_server`` call simply replaces the singleton.  Passing
    ``None`` is supported (tests use it to exercise the uninitialised
    path), but is not invoked by the normal server lifecycle.

    Args:
        store: The :class:`ArtifactStore` instance, or ``None`` to clear.
    """
    global _artifact_store
    _artifact_store = store


def get_artifact_store() -> ArtifactStore:
    """Return the module-level artifact store.

    Returns:
        The active :class:`ArtifactStore`.

    Raises:
        RuntimeError: If the store has not been initialised yet.
    """
    if _artifact_store is None:
        msg = "ArtifactStore not initialised — server has not been constructed"
        raise RuntimeError(msg)
    return _artifact_store
