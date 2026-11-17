"""Server middleware for per-tool-call commit scoping."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware

from markdown_vault_mcp._commit_scope import CommitScope, bound_commit_scope

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from fastmcp.tools.base import ToolResult

logger = logging.getLogger(__name__)
_SCOPE_WAIT_TIMEOUT = 30.0


class CommitScopeMiddleware(Middleware):
    """Bind a commit scope around every tool call, and close it afterward.

    Opening the scope is enough for the dispatcher to group the call's writes;
    closing it makes the group commit. A writing call waits for its callback
    attempt, bounded by the dispatcher's timeout, without blocking the loop.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Run *call_next* with a bound scope, then close that scope.

        Args:
            context: The in-flight tool call.
            call_next: The rest of the middleware chain.

        Returns:
            Whatever the tool returned, unchanged.
        """
        with bound_commit_scope(context.message.name) as scope:
            try:
                return await call_next(context)
            finally:
                await self._close(context, scope)

    @staticmethod
    async def _close(
        context: MiddlewareContext[mt.CallToolRequestParams],
        scope: CommitScope,
    ) -> None:
        """Close *scope*, tolerating a vault that is not up.

        The scope closes on the failure path too, so a tool that raises after
        writing still commits what it wrote rather than stranding the group.
        """
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is None:
            return
        try:
            from markdown_vault_mcp.domain import get_vault

            vault = get_vault(fastmcp_context)
            done = vault._enqueue_commit_scope_end(scope)
            if done is not None:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.wrap_future(done)), _SCOPE_WAIT_TIMEOUT
                    )
                except TimeoutError:
                    logger.warning(
                        "commit_scope_wait_timeout tool=%s token=%s timeout_s=%s outcome=commit_pending",
                        scope.tool_name,
                        scope.token,
                        _SCOPE_WAIT_TIMEOUT,
                    )
        except Exception:
            # Deliberately broad, and deliberately swallowed. This runs in the
            # ``finally`` of on_call_tool, so ANY exception escaping here
            # replaces the tool's return value — or masks the tool's own
            # exception — with a failure in commit bookkeeping. The known cases
            # are a missing lifespan (RuntimeError) and a vault without a
            # dispatcher (AttributeError), but catching only those let a
            # ValueError from get_vault destroy a successful tool result.
            # Losing the grouping degrades to per-file commits; losing the
            # tool's result is a defect.
            logger.warning(
                "commit_scope_close_failed tool=%s token=%s",
                scope.tool_name,
                scope.token,
                exc_info=True,
            )
