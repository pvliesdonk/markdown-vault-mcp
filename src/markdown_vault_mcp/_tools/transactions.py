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
            f"{exc}. Check the handle from begin_transaction and transaction state; wait for active writes to finish.",
            log_level=logging.INFO,
        ) from None


async def close_transaction(vault: Vault, handle: str) -> dict[str, Any]:
    """Close a caller's transaction and wait for its commit result."""
    try:
        future = vault._write_callback.close_group(handle, resolve_mcp_principal())
    except CommitGroupError as exc:
        raise ToolError(
            f"{exc}. Check the handle from begin_transaction and transaction state; wait for active writes to finish.",
            log_level=logging.INFO,
        ) from None
    result: GroupCommitResult = await asyncio.wrap_future(future)
    return asdict(result)


async def settle_transactions(vault: Vault, handle: str | None) -> None:
    """Close the caller's groups before sync, refusing failed commits."""
    try:
        futures = vault._write_callback.close_owner_groups(
            resolve_mcp_principal(), handle
        )
    except CommitGroupError as exc:
        raise ToolError(
            f"{exc}. Check the handle from begin_transaction and transaction state; wait for active writes to finish.",
            log_level=logging.INFO,
        ) from None
    for future in futures:
        result = await asyncio.wrap_future(future)
        if result.status == "failed":
            raise ToolError(
                "Transaction commit failed; sync was not run. Changes may remain staged. "
                "Ask the operator to inspect the Git index and resolve the commit failure.",
                log_level=logging.ERROR,
            )


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
        immediately; this groups commits and does not roll back changes. Omitting
        the handle commits that call alone. Fetch, uploads and OKF tools do not
        accept a handle and stay outside the group. Without Git commit support,
        writes still occur but no grouped commit is made.

        Args:
            description: Single-line commit subject for the group.

        Returns:
            Dict with handle (str), to pass to supported writes and end_transaction.
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

        Returns:
            Dict with status (committed, no_changes or failed), `sha` (str or null),
            and files (number of committed paths; zero if unknown). Without Git
            commit support, no_changes reports that no commit was made.
        """
        return await close_transaction(vault, handle)
