"""Shared dependency injection and lifespan for the MCP server.

Provides :func:`get_vault` and :func:`make_vault_lifespan` which are
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

from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from markdown_vault_mcp.config import VaultConfig

logger = logging.getLogger(__name__)


_vault_singleton: Vault | None = None


def set_vault_singleton(vault: Vault | None) -> None:
    """Set the module-level :class:`Vault` singleton.

    Called by the lifespan factory on startup with the live Vault,
    and again on shutdown with ``None`` so a subsequent server in the
    same process starts from a clean slate.

    Args:
        vault: The live :class:`Vault`, or ``None`` to clear.
    """
    global _vault_singleton
    _vault_singleton = vault


def get_vault_singleton() -> Vault:
    """Return the module-level :class:`Vault` singleton.

    Used by HTTP route handlers (e.g. the GitHub webhook route handler)
    that run outside FastMCP's ``Depends(get_vault)`` injection and
    therefore cannot resolve the Vault from the lifespan context.

    Returns:
        The live :class:`Vault` set by the lifespan factory.

    Raises:
        RuntimeError: If the singleton has not been set yet.
    """
    if _vault_singleton is None:
        msg = (
            "Vault not initialised — set_vault_singleton was never "
            "called.  In normal operation the lifespan factory sets it; in "
            "tests, set explicitly via set_vault_singleton(col)."
        )
        raise RuntimeError(msg)
    return _vault_singleton


def make_vault_lifespan(config: VaultConfig) -> Any:
    """Create a lifespan function that closes over a pre-loaded config.

    Args:
        config: A fully-loaded :class:`~markdown_vault_mcp.config.VaultConfig`
            instance, typically produced by a single :func:`load_config` call in
            :func:`~markdown_vault_mcp.server.make_server`.

    Returns:
        A FastMCP lifespan coroutine that initialises the
        :class:`~markdown_vault_mcp.vault.Vault` and yields
        ``{"vault": vault, "config": config}`` to the lifespan context.
    """

    @lifespan
    async def _vault_lifespan(
        server: FastMCP,  # noqa: ARG001
    ) -> AsyncIterator[dict[str, Any]]:
        """Build the Vault at server startup, tear down on shutdown."""
        logger.info("Initialising vault from %s", config.source_dir)

        kwargs = config.to_vault_kwargs()
        if kwargs.get("embedding_provider") is not None:
            logger.info(
                "Embedding provider: %s",
                type(kwargs["embedding_provider"]).__name__,
            )
        vault = Vault(**kwargs)
        set_vault_singleton(vault)

        # If periodic git pull is enabled, sync before submitting the
        # initial index build so the scan sees the latest working tree.
        await asyncio.to_thread(vault.sync_from_remote_before_index)

        # Submit the initial build jobs to the IndexWriter and yield
        # immediately (#559). build_index_async() short-circuits in
        # O(1) on warm restarts (existing FTS sentinel from PR #526);
        # cold restarts submit a BuildIndex job that the writer
        # processes asynchronously while the lifespan yields.
        # Bucket-3 tools block on @needs_queryable until the build
        # completes; bucket-2 tools return whatever is currently in
        # the index per #526.
        vault.index.build_index_async()
        logger.info("Submitted BuildIndex job to writer")

        if kwargs.get("embedding_provider") is not None:
            vault.index.build_embeddings_async()
            logger.info("Submitted BuildEmbeddings job to writer")

        # Start any other background tasks (e.g. git pull loop).
        vault.start()

        # File watcher — only when git pull and webhook are both inactive so the
        # watcher and git checkout don't race to trigger reindex (#558).
        from markdown_vault_mcp._file_watcher import (
            VaultFileWatcher,
            should_start_file_watcher,
        )
        from markdown_vault_mcp.exceptions import IndexUnavailableError

        # Use the *resolved* pull interval from kwargs, not config.git.pull_interval_s:
        # the config default is 600 even on non-git vaults, but to_vault_kwargs()
        # only passes a non-zero interval through when a git strategy is configured.
        git_pull_active = kwargs.get("git_pull_interval_s", 0) > 0

        file_watcher = None
        if should_start_file_watcher(
            config.sync.file_watcher_enabled,
            git_pull_active,
            config.sync.github_webhook_secret,
        ):

            def _on_file_change() -> None:
                try:
                    with vault.pause_writes():
                        vault.index.reindex()
                except IndexUnavailableError:
                    logger.info(
                        "file_watcher: index not yet queryable, skipping reindex"
                    )
                except Exception:
                    logger.error("file_watcher: reindex failed", exc_info=True)

            file_watcher = VaultFileWatcher(
                config.source_dir,
                _on_file_change,
                debounce_s=config.sync.file_watcher_debounce_s,
            )
            file_watcher.start()
        elif not config.sync.file_watcher_enabled:
            logger.debug("file_watcher: disabled via FILE_WATCHER=false")
        else:
            logger.info(
                "file_watcher: disabled — git pull loop / webhook handles reindex cadence"
            )

        try:
            yield {"vault": vault, "config": config}
        finally:
            if file_watcher is not None:
                file_watcher.stop()
            # Clear the singleton before closing so any in-flight HTTP handler
            # gets a clean RuntimeError instead of touching a Vault
            # mid-close().
            set_vault_singleton(None)
            vault.close()
            logger.info("Vault shut down")

    return _vault_lifespan


def get_vault(ctx: Context = CurrentContext()) -> Vault:
    """Resolve the Vault from lifespan context.

    Used as a ``Depends()`` default in tool/resource/prompt signatures.

    Raises:
        RuntimeError: If the server lifespan has not run.
    """
    vault: Vault | None = ctx.lifespan_context.get("vault")
    if vault is None:
        msg = "Vault not initialised — server lifespan has not run"
        raise RuntimeError(msg)
    return vault
