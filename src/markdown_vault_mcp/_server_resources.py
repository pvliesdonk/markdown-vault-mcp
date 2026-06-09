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
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.context import Context

from markdown_vault_mcp.config import VaultConfig
from markdown_vault_mcp.vault import Vault

from ._icons import _TOOL_ICONS
from ._server_deps import get_vault
from ._server_queryable import needs_queryable


def _stale_resource(vault: Vault, contents: str, gen_before: int) -> ResourceResult:
    """Wrap a resource's JSON body with index-freshness metadata.

    Resources keep their bare JSON contents unchanged; freshness rides
    out-of-band in the MCP ``_meta`` field as ``index_stale``, retrievable
    via ``read_resource_mcp(uri).meta``. Resources do not expose a
    ``wait_for_pending_writes`` knob — a resource URI template binds only address path
    segments, with no mechanism for an ad-hoc control parameter — so they
    only signal: ``index_stale`` is True when a write landed during the read
    (``write_generation`` advanced past ``gen_before``) or the writer was
    non-idle at response time.

    The body is wrapped in an explicit ``application/json`` ``ResourceContent``
    so the declared MIME type survives — a bare ``str`` in ``ResourceResult``
    defaults to ``text/plain``, dropping the ``@mcp.resource`` MIME type.
    """
    index_stale = (vault.index.write_generation() != gen_before) or (
        not vault.index.is_drained()
    )
    return ResourceResult(
        contents=[ResourceContent(contents, mime_type="application/json")],
        meta={"index_stale": index_stale},
    )


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
    ) -> ResourceResult:
        """Vault configuration: source path, read-only mode, indexed frontmatter fields, exclude patterns, allowed attachment extensions. For counts and search capabilities, use stats://vault.

        Index freshness is reported in _meta.index_stale.
        """
        config = _get_config(ctx)
        gen_before = vault.index.write_generation()
        stats = await asyncio.to_thread(vault.reader.stats)
        return _stale_resource(
            vault,
            json.dumps(
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
            ),
            gen_before,
        )

    @mcp.resource(
        "stats://vault", mime_type="application/json", icons=_TOOL_ICONS["stats"]
    )
    async def vault_stats(
        vault: Vault = Depends(get_vault),
    ) -> ResourceResult:
        """Vault statistics — document count, chunk count, capabilities.

        Index freshness is reported in _meta.index_stale.
        """
        gen_before = vault.index.write_generation()
        result = await asyncio.to_thread(vault.reader.stats)
        return _stale_resource(vault, json.dumps(asdict(result)), gen_before)

    @mcp.resource(
        "tags://vault", mime_type="application/json", icons=_TOOL_ICONS["list_tags"]
    )
    async def vault_tags(
        vault: Vault = Depends(get_vault),
    ) -> ResourceResult:
        """All tags grouped by indexed field.

        Index freshness is reported in _meta.index_stale.
        """
        gen_before = vault.index.write_generation()
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
        return _stale_resource(vault, json.dumps(grouped), gen_before)

    @mcp.resource(
        "tags://vault/{field}",
        mime_type="application/json",
        icons=_TOOL_ICONS["list_tags"],
    )
    async def vault_tags_by_field(
        field: str,
        vault: Vault = Depends(get_vault),
    ) -> ResourceResult:
        """Tags for a specific indexed field.

        Index freshness is reported in _meta.index_stale.
        """
        gen_before = vault.index.write_generation()
        values = await asyncio.to_thread(vault.reader.list_tags, field)
        return _stale_resource(vault, json.dumps(values), gen_before)

    @mcp.resource(
        "folders://vault",
        mime_type="application/json",
        icons=_TOOL_ICONS["list_folders"],
    )
    async def vault_folders(
        vault: Vault = Depends(get_vault),
    ) -> ResourceResult:
        """All folder paths in the vault.

        Index freshness is reported in _meta.index_stale.
        """
        gen_before = vault.index.write_generation()
        folders = await asyncio.to_thread(vault.reader.list_folders)
        return _stale_resource(vault, json.dumps(folders), gen_before)

    @mcp.resource(
        "toc://vault/{path}", mime_type="application/json", icons=_TOOL_ICONS["read"]
    )
    @needs_queryable()
    async def vault_toc(
        path: str,
        vault: Vault = Depends(get_vault),
    ) -> ResourceResult:
        """Table of contents — ordered list of {level, text, anchor} headings. Useful for navigating long notes without reading full content.

        Index freshness is reported in _meta.index_stale.
        """
        gen_before = vault.index.write_generation()
        toc = await asyncio.to_thread(vault.reader.get_toc, path)
        return _stale_resource(vault, json.dumps(toc), gen_before)

    @mcp.resource(
        "similar://vault/{path}",
        mime_type="application/json",
        icons=_TOOL_ICONS["get_similar"],
    )
    @needs_queryable()
    async def vault_similar(
        path: str,
        vault: Vault = Depends(get_vault),
    ) -> ResourceResult:
        """Top 10 semantically similar notes for a document.

        Index freshness is reported in _meta.index_stale.
        """
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(vault.reader.get_similar, path, limit=10)
        return _stale_resource(
            vault, json.dumps([asdict(r) for r in results]), gen_before
        )

    @mcp.resource(
        "recent://vault",
        mime_type="application/json",
        icons=_TOOL_ICONS["get_recent"],
    )
    async def vault_recent(
        vault: Vault = Depends(get_vault),
    ) -> ResourceResult:
        """20 most recently modified notes.

        Index freshness is reported in _meta.index_stale.
        """
        gen_before = vault.index.write_generation()
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
        return _stale_resource(vault, json.dumps(items), gen_before)
