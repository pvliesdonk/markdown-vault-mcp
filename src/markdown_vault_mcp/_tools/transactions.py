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

from markdown_vault_mcp import _server_commit_scope
from markdown_vault_mcp._commit_groups import CommitGroupError, GroupCommitResult
from markdown_vault_mcp._icons import _TOOL_ICONS
from markdown_vault_mcp._identity import resolve_mcp_principal
from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.domain import get_vault
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from concurrent.futures import Future


@contextmanager
def _refused() -> Iterator[None]:
    """Translate dispatcher refusals without changing their recovery guidance."""
    try:
        yield
    except CommitGroupError as exc:
        raise ToolError(str(exc), log_level=logging.INFO) from None


@contextmanager
def transaction_scope(vault: Vault, handle: str | None) -> Iterator[None]:
    """Bind an explicit transaction to this call, if one was supplied."""
    if handle is None:
        yield
        return
    with _refused(), vault._write_callback.bind_group(handle, resolve_mcp_principal()):
        yield


async def _await_commits(
    futures: Sequence[Future[GroupCommitResult]],
) -> list[GroupCommitResult]:
    """Wait once for all commits without cancelling work on timeout."""
    if not futures:
        return []
    wrapped = [asyncio.wrap_future(future) for future in futures]
    _, pending = await asyncio.wait(
        wrapped, timeout=_server_commit_scope._SCOPE_WAIT_TIMEOUT
    )
    if pending:
        raise ToolError(
            "Transaction commit is still pending and continues in the background; "
            "no sync leg ran. Ask the operator to check the server log, Git index and history. "
            "A later git_sync waits only while the commit is still pending.",
            log_level=logging.WARNING,
        )
    return [future.result() for future in wrapped]


async def close_transaction(vault: Vault, handle: str) -> dict[str, Any]:
    """Close a caller's transaction and wait for its commit result."""
    with _refused():
        future = vault._write_callback.close_group(handle, resolve_mcp_principal())
    return asdict((await _await_commits([future]))[0])


async def settle_transactions(vault: Vault, handle: str | None) -> None:
    """Close the caller's groups before sync, refusing failed commits."""
    with _refused():
        futures = vault._write_callback.close_owner_groups(
            resolve_mcp_principal(), handle
        )
    results = await _await_commits(futures)
    if any(result.status == "failed" for result in results):
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
        with _refused():
            principal = resolve_mcp_principal()
            group = await asyncio.to_thread(
                vault._write_callback.open_group, description, principal
            )
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

        If the commit is still pending after 30 seconds, it continues in the
        background. Ask the operator to check the server log, Git index and history.
        A later git_sync waits only while the commit is still pending.

        Args:
            handle: Handle returned by begin_transaction.

        Returns:
            Dict with status (committed, no_changes or failed), `sha` (str or null),
            and files (number of committed paths; zero if unknown). Without Git
            commit support, a group with writes reports failed; the files remain
            written. An empty group reports no_changes.
        """
        return await close_transaction(vault, handle)
