"""Shared dependency injection and lifespan for the MCP server.

Provides :func:`get_collection` and :func:`make_collection_lifespan` which are
imported by the tool, resource, and prompt registration modules.
"""

from __future__ import annotations

import asyncio
import logging
import os
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
        set_collection_singleton(collection)

        # If periodic git pull is enabled, sync before submitting the
        # initial index build so the scan sees the latest working tree.
        await asyncio.to_thread(collection.sync_from_remote_before_index)

        # Submit the initial build jobs to the IndexWriter (#559).  The
        # warm-restart short-circuit (PR #526 sentinel) inside
        # :meth:`IndexManager.build_index` returns in O(1) so warm vaults
        # complete near-instantly.  We run the synchronous
        # :meth:`Collection.build_index` via :func:`asyncio.to_thread`
        # so the FTS index is queryable by the time the lifespan yields
        # and the MCP server starts handling tool calls — bucket-2
        # tools (search/list/stats) would otherwise return empty until
        # the writer drained.  Calling the synchronous wrapper (rather
        # than the fire-and-forget :meth:`build_index_async`) gives us
        # deterministic Collection-level state ordering:
        # ``_index_built`` and the build-completion event flip on the
        # calling thread the moment the writer's Future resolves.
        # Embeddings are submitted to the same FIFO and left to drain
        # in the background; semantic search returns empty until the
        # BuildEmbeddings job completes.
        build_timeout = float(
            os.environ.get("MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S", "60")
        )

        async def _await_build_index() -> None:
            await asyncio.to_thread(collection.build_index)

        logger.info("Submitted BuildIndex job to writer")
        try:
            await asyncio.wait_for(_await_build_index(), timeout=build_timeout)
            logger.info("BuildIndex job completed")
        except TimeoutError:
            logger.warning(
                "BuildIndex job did not complete within %.1fs; yielding lifespan "
                "with index still building (bucket-3/4 tools will wait)",
                build_timeout,
            )

        if kwargs.get("embedding_provider") is not None:
            collection.build_embeddings_async()
            logger.info("Submitted BuildEmbeddings job to writer")

        # Start any other background tasks (e.g. git pull loop).
        collection.start()

        # Artifact store singleton is wired in make_server(), not here —
        # the HTTP route captures the store at server-construction time and
        # tool handlers reach it via get_artifact_store().  Tokens carry
        # eager bytes now, so the lifespan no longer needs to expose the
        # Collection to the HTTP handler.

        try:
            yield {"collection": collection, "config": config}
        finally:
            # Clear the singleton before closing so any in-flight HTTP handler
            # gets a clean RuntimeError instead of touching a Collection
            # mid-close().
            set_collection_singleton(None)
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
