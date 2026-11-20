"""MCP transaction tools and request-scoped group binding."""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, TypeVar

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from markdown_vault_mcp import _server_commit_scope
from markdown_vault_mcp._commit_groups import CommitGroupError, GroupCommitResult
from markdown_vault_mcp._icons import _TOOL_ICONS
from markdown_vault_mcp._identity import resolve_mcp_principal
from markdown_vault_mcp._tool_boundary import tool_boundary
from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.domain import get_vault
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from concurrent.futures import Future

_T = TypeVar("_T")


@contextmanager
def _refused() -> Iterator[None]:
    """Translate dispatcher refusals without changing their recovery guidance."""
    try:
        yield
    except CommitGroupError as exc:
        raise ToolError(str(exc), log_level=logging.INFO) from None


async def run_in_transaction(
    vault: Vault, handle: str | None, func: Callable[..., _T], *args: Any, **kwargs: Any
) -> _T:
    """Run a blocking write in a worker thread, inside the transaction if given.

    The group is bound in the worker, so its active-write count lasts as long as
    the write does, even when the request is cancelled and stops waiting.
    The caller's principal is resolved here, on the event loop.

    Args:
        vault: Vault whose write callback owns the transaction group.
        handle: Handle from ``begin_transaction``, or None to run ungrouped.
        func: Blocking write to run in the worker thread.
        *args: Positional arguments for ``func``.
        **kwargs: Keyword arguments for ``func``.

    Returns:
        Whatever ``func`` returns.

    Raises:
        ToolError: If the handle is unknown, closed or unavailable.
    """
    if handle is None:
        return await asyncio.to_thread(func, *args, **kwargs)
    principal = resolve_mcp_principal()

    def run() -> _T:
        with _refused(), vault._write_callback.bind_group(handle, principal):
            return func(*args, **kwargs)

    return await asyncio.to_thread(run)


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
            "Transaction commit is still pending and continues in the background. "
            "Ask the operator to check the server log, Git index and history. "
            "A later git_sync waits only while the commit is still pending.",
            log_level=logging.WARNING,
        )
    return [future.result() for future in wrapped]


async def close_transaction(vault: Vault, handle: str) -> dict[str, Any]:
    """Close a caller's transaction and wait for its commit result."""
    with _refused():
        future = vault._write_callback.close_group(handle, resolve_mcp_principal())
    return asdict((await _await_commits([future]))[0])


async def settle_transactions(vault: Vault, handle: str | None) -> bool:
    """Close the caller's groups and report history failures before sync."""
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
    return any(result.history_failed for result in results)


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
            files (number of committed paths; zero if unknown), and history_failed
            (whether an editorial history entry could not be recorded). Without
            Git, a group with writes reports failed; files remain written. An
            empty group reports no_changes.
        """
        return await close_transaction(vault, handle)
