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

from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from markdown_vault_mcp.config import CollectionConfig

logger = logging.getLogger(__name__)


def make_collection_lifespan(config: CollectionConfig) -> Any:
    """Create a lifespan function that closes over a pre-loaded config.

    Args:
        config: A fully-loaded :class:`~markdown_vault_mcp.config.CollectionConfig`
            instance, typically produced by a single :func:`load_config` call in
            :func:`~markdown_vault_mcp.mcp_server.create_server`.

    Returns:
        A FastMCP lifespan coroutine that initialises the
        :class:`~markdown_vault_mcp.collection.Collection` and yields
        ``{"collection": collection, "config": config}`` to the lifespan context.
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

        # If periodic git pull is enabled, sync before building the initial index so
        # build_index() scans the freshest working tree.
        await asyncio.to_thread(collection.sync_from_remote_before_index)

        # Build index eagerly so first tool call is fast.
        stats = await asyncio.to_thread(collection.build_index)
        logger.info(
            "Index built: %d documents, %d chunks",
            stats.documents_indexed,
            stats.chunks_indexed,
        )

        # Build embeddings eagerly when an embedding provider is configured.
        # build_embeddings() skips work if the vector index already exists on disk,
        # so this is safe to call on every startup.
        if kwargs.get("embedding_provider") is not None:
            chunks_embedded = await asyncio.to_thread(collection.build_embeddings)
            logger.info("Embeddings ready: %d chunks", chunks_embedded)

        # Start background tasks (e.g. git pull loop) after index is built.
        collection.start()

        # Initialise artifact store singletons so the HTTP artifact endpoint
        # can resolve vault paths outside of FastMCP's DI context.
        from markdown_vault_mcp.artifacts import (
            ArtifactStore,
            set_artifact_store,
            set_collection_store,
        )

        set_artifact_store(ArtifactStore())
        set_collection_store(collection)

        try:
            yield {"collection": collection, "config": config}
        finally:
            set_artifact_store(None)
            set_collection_store(None)
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
