"""Read-only bundle inventory tool."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from markdown_vault_mcp._icons import _TOOL_ICONS
from markdown_vault_mcp._tool_boundary import tool_boundary
from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.bundle_inventory import (
    BundleTreeRequest,
    ParseState,
    SearchState,
    TypeState,
)
from markdown_vault_mcp.domain import get_vault
from markdown_vault_mcp.vault import Vault


def register(mcp: FastMCP) -> None:
    """Register the bundle inventory reader."""

    @mcp.tool(
        tags={"group:reader"},
        icons=_TOOL_ICONS["list_documents"],
        annotations={
            "title": "Inspect Bundle Inventory",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def okf_inventory(
        root: str,
        type_state: TypeState | None = None,
        parse_state: ParseState | None = None,
        search_state: SearchState | None = None,
        entry_limit: int = 100,
        cursor: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Inspect concept candidates in a bundle, including notes outside search.

        Unknown type or parse state can match. Search-state filters are exact;
        directory boundaries remain visible so they can be selected as root.

        Args:
            root: Vault-relative bundle root; use an empty string for the vault root.
            type_state: Filter by observed type presence, such as absent.
            parse_state: Filter by metadata parsing outcome.
            search_state: Filter by observed search admission; not_evaluated means unknown.
            entry_limit: Maximum returned observations, from 1 to 1000; each call scans up to 1000.
            cursor: Pass next_cursor from the previous page to continue, even if it was empty.

        Returns:
            Observations, scope and enumeration states, and an optional next_cursor.
        """
        try:
            request = BundleTreeRequest(
                root=root, entry_limit=entry_limit, cursor=cursor
            )
        except ValueError:
            raise ToolError(
                "Invalid inventory selection. Use a relative root and a cursor returned by okf_inventory, with entry_limit from 1 to 1000.",
                log_level=logging.INFO,
            ) from None
        result = await asyncio.to_thread(
            vault.reader.okf_inventory,
            request,
            type_state=type_state,
            parse_state=parse_state,
            search_state=search_state,
        )
        return asdict(result)

    @mcp.tool(
        tags={"group:reader"},
        icons=_TOOL_ICONS["list_documents"],
        annotations={
            "title": "Read Folder Navigation",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def okf_navigation(
        folder: str = "",
        entry_limit: int = 100,
        cursor: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Read a folder's authored or synthesized navigation and its source.

        Use okf_inventory to page through a bundle subtree. Synthesis covers
        immediate children only. Each entry's link text is the child's name with
        backslash, [ and ] backslash-escaped; remove those backslashes and join
        the name to folder to read a note or list a child. A folder entry is
        followed by the word folder.

        Args:
            folder: Vault-relative folder; empty selects the vault root.
            entry_limit: Maximum entries observed per page, from 1 to 1000; some may not render (index, log, attachments).
            cursor: Pass next_cursor from the previous page with the same folder to continue, even if that page was empty.

        Returns:
            Navigation body, source, scope and enumeration observations.
        """
        try:
            result = await asyncio.to_thread(
                vault.reader.okf_navigation,
                folder,
                entry_limit=entry_limit,
                cursor=cursor,
            )
        except ValueError:
            raise ToolError(
                'Invalid navigation selection. Call okf_navigation with folder="" for the root or a relative folder of plain components (no . or ..), entry_limit from 1 to 1000, and a cursor only as returned by okf_navigation for the same folder.',
                log_level=logging.INFO,
            ) from None
        return asdict(result)
