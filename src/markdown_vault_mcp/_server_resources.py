"""MCP resource registrations for the markdown-vault-mcp server.

Call :func:`register_resources` after constructing the
:class:`~fastmcp.FastMCP` instance in
:func:`~markdown_vault_mcp.server.make_server`.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from dataclasses import asdict
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.server.context import Context

from markdown_vault_mcp.config import VaultConfig
from markdown_vault_mcp.vault import Vault

from ._icons import _TOOL_ICONS
from ._server_deps import get_vault
from ._server_queryable import needs_queryable


def _get_config(ctx: Context) -> VaultConfig:
    """Retrieve the cached :class:`~markdown_vault_mcp.config.VaultConfig` from lifespan context.

    Args:
        ctx: The current request context.

    Returns:
        The ``VaultConfig`` stored by the lifespan hook.
    """
    config: VaultConfig | None = ctx.lifespan_context.get("config")
    if config is None:
        msg = "Config not initialised — server lifespan has not run"
        raise RuntimeError(msg)
    return config


def register_resources(mcp: FastMCP) -> None:
    """Register all 8 MCP resources on *mcp*.

    Args:
        mcp: The :class:`~fastmcp.FastMCP` instance to register resources on.
    """

    @mcp.resource(
        "config://vault", mime_type="application/json", icons=_TOOL_ICONS["stats"]
    )
    async def vault_config(
        ctx: Context = CurrentContext(),
        vault: Vault = Depends(get_vault),
    ) -> str:
        """Vault configuration: source path, read-only mode, indexed frontmatter fields, exclude patterns, allowed attachment extensions. For counts and search capabilities, use stats://vault."""
        config = _get_config(ctx)
        stats = await asyncio.to_thread(vault.reader.stats)
        return json.dumps(
            {
                "source_dir": str(config.source_dir),
                "read_only": config.read_only,
                "indexed_fields": config.indexing.indexed_frontmatter_fields or [],
                "required_fields": config.indexing.required_frontmatter or [],
                "exclude_patterns": config.indexing.exclude_patterns or [],
                "templates_folder": config.content.templates_folder,
                "semantic_search_available": stats.semantic_search_available,
                "attachment_extensions": stats.attachment_extensions,
            }
        )

    @mcp.resource(
        "stats://vault", mime_type="application/json", icons=_TOOL_ICONS["stats"]
    )
    async def vault_stats(
        vault: Vault = Depends(get_vault),
    ) -> str:
        """Vault statistics — document count, chunk count, capabilities."""
        result = await asyncio.to_thread(vault.reader.stats)
        return json.dumps(asdict(result))

    @mcp.resource(
        "tags://vault", mime_type="application/json", icons=_TOOL_ICONS["list_tags"]
    )
    async def vault_tags(
        vault: Vault = Depends(get_vault),
    ) -> str:
        """All tags grouped by indexed field."""
        stats = await asyncio.to_thread(vault.reader.stats)
        tag_lists: list[list[str]] = list(
            await asyncio.gather(
                *[
                    asyncio.to_thread(vault.reader.list_tags, field)
                    for field in stats.indexed_frontmatter_fields
                ]
            )
        )
        grouped: dict[str, list[str]] = dict(
            zip(stats.indexed_frontmatter_fields, tag_lists, strict=False)
        )
        return json.dumps(grouped)

    @mcp.resource(
        "tags://vault/{field}",
        mime_type="application/json",
        icons=_TOOL_ICONS["list_tags"],
    )
    async def vault_tags_by_field(
        field: str,
        vault: Vault = Depends(get_vault),
    ) -> str:
        """Tags for a specific indexed field."""
        values = await asyncio.to_thread(vault.reader.list_tags, field)
        return json.dumps(values)

    @mcp.resource(
        "folders://vault",
        mime_type="application/json",
        icons=_TOOL_ICONS["list_folders"],
    )
    async def vault_folders(
        vault: Vault = Depends(get_vault),
    ) -> str:
        """All folder paths in the vault."""
        folders = await asyncio.to_thread(vault.reader.list_folders)
        return json.dumps(folders)

    @mcp.resource(
        "toc://vault/{path}", mime_type="application/json", icons=_TOOL_ICONS["read"]
    )
    @needs_queryable()
    async def vault_toc(
        path: str,
        vault: Vault = Depends(get_vault),
    ) -> str:
        """Table of contents — ordered list of {level, text, anchor} headings. Useful for navigating long notes without reading full content."""
        toc = await asyncio.to_thread(vault.reader.get_toc, path)
        return json.dumps(toc)

    @mcp.resource(
        "similar://vault/{path}",
        mime_type="application/json",
        icons=_TOOL_ICONS["get_similar"],
    )
    @needs_queryable()
    async def vault_similar(
        path: str,
        vault: Vault = Depends(get_vault),
    ) -> str:
        """Top 10 semantically similar notes for a document."""
        results = await asyncio.to_thread(vault.reader.get_similar, path, limit=10)
        return json.dumps([asdict(r) for r in results])

    @mcp.resource(
        "recent://vault",
        mime_type="application/json",
        icons=_TOOL_ICONS["get_recent"],
    )
    async def vault_recent(
        vault: Vault = Depends(get_vault),
    ) -> str:
        """20 most recently modified notes."""
        results = await asyncio.to_thread(vault.reader.get_recent, limit=20)
        items: list[dict[str, Any]] = [
            {
                **asdict(r),
                "modified_at_iso": datetime.datetime.fromtimestamp(
                    r.modified_at, tz=datetime.UTC
                ).isoformat(),
            }
            for r in results
        ]
        return json.dumps(items)
