"""Shared dependency injection and lifespan for the MCP server.

Provides :func:`get_collection` and :func:`make_collection_lifespan` which are
imported by the tool, resource, and prompt registration modules.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.server.lifespan import lifespan

from markdown_vault_mcp.background_indexer import BackgroundIndexer
from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from markdown_vault_mcp.config import CollectionConfig

logger = logging.getLogger(__name__)


_collection_singleton: Collection | None = None


def set_collection_singleton(collection: Collection | None) -> None:
    """Set the module-level :class:`Collection` singleton.

    Called by the lifespan factory on startup with the live Collection,
    and again on shutdown with ``None`` so a subsequent server in the
    same process starts from a clean slate.

    Args:
        collection: The live :class:`Collection`, or ``None`` to clear.
    """
    global _collection_singleton
    _collection_singleton = collection


def get_collection_singleton() -> Collection:
    """Return the module-level :class:`Collection` singleton.

    Used by HTTP route handlers (e.g. the pvl-core file-exchange upload
    receiver) that run outside FastMCP's ``Depends(get_collection)``
    injection and therefore cannot resolve the Collection from the
    lifespan context.

    Returns:
        The live :class:`Collection` set by the lifespan factory.

    Raises:
        RuntimeError: If the singleton has not been set yet.
    """
    if _collection_singleton is None:
        msg = (
            "Collection not initialised — set_collection_singleton was never "
            "called.  In normal operation the lifespan factory sets it; in "
            "tests, set explicitly via set_collection_singleton(col)."
        )
        raise RuntimeError(msg)
    return _collection_singleton


def make_collection_lifespan(config: CollectionConfig) -> Any:
    """Create a lifespan function that closes over a pre-loaded config.

    Args:
        config: A fully-loaded :class:`~markdown_vault_mcp.config.CollectionConfig`
            instance, typically produced by a single :func:`load_config` call in
            :func:`~markdown_vault_mcp.server.make_server`.

    Returns:
        A FastMCP lifespan coroutine that initialises the
        :class:`~markdown_vault_mcp.collection.Collection` and the
        :class:`~markdown_vault_mcp.background_indexer.BackgroundIndexer`,
        and yields ``{"collection": collection, "config": config, "indexer":
        indexer}`` to the lifespan context.
    """

    @lifespan
    async def _collection_lifespan(
        server: FastMCP,  # noqa: ARG001
    ) -> AsyncIterator[dict[str, Any]]:
        """Build the Collection at server startup, tear down on shutdown."""
        logger.info("Initialising collection from %s", config.source_dir)

        kwargs = config.to_collection_kwargs()
        if kwargs.get("embedding_provider") is not None:
            logger.info(
                "Embedding provider: %s",
                type(kwargs["embedding_provider"]).__name__,
            )
        collection = Collection(**kwargs)
        set_collection_singleton(collection)

        # If periodic git pull is enabled, sync before building the initial index so
        # build_index() scans the freshest working tree.
        await asyncio.to_thread(collection.sync_from_remote_before_index)

        # Embedding phase requires BOTH a provider and a sidecar path; if
        # either is missing, Collection.build_embeddings() raises ValueError.
        # Gate has_provider on both to avoid `state="failed"` on every startup
        # when a provider is configured but the path isn't (PR #515 hit this).
        indexer = BackgroundIndexer(
            collection,
            has_provider=(
                kwargs.get("embedding_provider") is not None
                and kwargs.get("embeddings_path") is not None
            ),
        )
        indexer.start()
        logger.info("background_indexer_started")

        # Start background tasks (e.g. git pull loop).
        collection.start()

        # Artifact store singleton is wired in make_server(), not here —
        # the HTTP route captures the store at server-construction time and
        # tool handlers reach it via get_artifact_store().  Tokens carry
        # eager bytes now, so the lifespan no longer needs to expose the
        # Collection to the HTTP handler.

        try:
            yield {"collection": collection, "config": config, "indexer": indexer}
        finally:
            # Clear the singleton before closing so any in-flight HTTP handler
            # gets a clean RuntimeError instead of touching a Collection
            # mid-close().
            set_collection_singleton(None)
            joined = indexer.stop(timeout=30.0)
            if not joined:
                # Daemon still mid-write. Closing the SQLite connection here
                # would race with that write. We accept the race because the
                # alternative (skipping close) leaks the handle and the next
                # startup's SQLite WAL recovery handles any partial state. Any
                # exception the daemon hits post-close is caught by _run's
                # ``except Exception`` and logged.
                logger.warning(
                    "background_indexer_join_timed_out: proceeding with "
                    "collection.close(); WAL will recover any partial write."
                )
            collection.close()
            logger.info("Collection shut down")

    return _collection_lifespan


def get_collection(ctx: Context = CurrentContext()) -> Collection:
    """Resolve the Collection from lifespan context.

    Used as a ``Depends()`` default in tool/resource/prompt signatures.

    Raises:
        RuntimeError: If the server lifespan has not run.
    """
    collection: Collection | None = ctx.lifespan_context.get("collection")
    if collection is None:
        msg = "Collection not initialised — server lifespan has not run"
        raise RuntimeError(msg)
    return collection


def get_indexer(ctx: Context = CurrentContext()) -> BackgroundIndexer:
    """Resolve the BackgroundIndexer from lifespan context.

    Used as a ``Depends()`` default in tool signatures that need access
    to background-index status.

    Raises:
        RuntimeError: If the server lifespan has not run.
    """
    indexer: BackgroundIndexer | None = ctx.lifespan_context.get("indexer")
    if indexer is None:
        msg = "BackgroundIndexer not initialised — lifespan did not run."
        raise RuntimeError(msg)
    return indexer
