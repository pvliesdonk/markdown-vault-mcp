"""MCP transaction tools and request-scoped group binding."""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from fastmcp_pvl_core import tool_boundary

from markdown_vault_mcp._commit_groups import CommitGroupError, GroupCommitResult
from markdown_vault_mcp._icons import _TOOL_ICONS
from markdown_vault_mcp._identity import resolve_mcp_principal
from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.domain import get_vault
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def transaction_scope(vault: Vault, handle: str | None) -> Iterator[None]:
    """Bind an explicit transaction to this call, if one was supplied."""
    if handle is None:
        yield
        return
    try:
        with vault._write_callback.bind_group(handle, resolve_mcp_principal()):
            yield
    except CommitGroupError as exc:
        raise ToolError(
            f"{exc}. Use an open handle from begin_transaction, or start a new transaction.",
            log_level=logging.INFO,
        ) from None


async def close_transaction(vault: Vault, handle: str) -> dict[str, Any]:
    """Close a caller's transaction and wait for its commit result."""
    try:
        future = vault._write_callback.close_group(handle, resolve_mcp_principal())
    except CommitGroupError as exc:
        raise ToolError(
            f"{exc}. Use a handle from begin_transaction; start a new transaction if needed.",
            log_level=logging.INFO,
        ) from None
    result: GroupCommitResult = await asyncio.wrap_future(future)
    return asdict(result)


def register(mcp: FastMCP) -> None:
    """Register transaction tools on the server."""

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["write"],
        annotations={
            "title": "Begin Transaction",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def begin_transaction(
        description: str, vault: Vault = Depends(get_vault)
    ) -> dict[str, str]:
        """Open a group of writes for one Git commit.

        Pass the returned handle as transaction to write, edit, append, delete,
        rename or move_folder, then call end_transaction. Writes are visible
        immediately; this groups commits and does not roll back changes.

        Args:
            description: Single-line commit subject for the group.
        """
        try:
            group = vault._write_callback.open_group(
                description, resolve_mcp_principal()
            )
        except CommitGroupError as exc:
            raise ToolError(str(exc), log_level=logging.INFO) from None
        return {"handle": group.handle}

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["write"],
        annotations={
            "title": "End Transaction",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def end_transaction(
        handle: str, vault: Vault = Depends(get_vault)
    ) -> dict[str, Any]:
        """Close a transaction and return its commit status, SHA and file count.

        Args:
            handle: Handle returned by begin_transaction.
        """
        return await close_transaction(vault, handle)
