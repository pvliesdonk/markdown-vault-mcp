"""Server middleware for per-tool-call commit scoping."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware

from markdown_vault_mcp._commit_scope import CommitScope, bound_commit_scope

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from fastmcp.tools.base import ToolResult

logger = logging.getLogger(__name__)


class CommitScopeMiddleware(Middleware):
    """Bind a commit scope around every tool call, and close it afterward.

    Opening the scope is enough for the dispatcher to group the call's writes;
    closing it is what makes the group commit. The close is enqueued rather
    than awaited: the dispatcher runs off the write path, so the tool returns
    before its writes drain, and the queue's FIFO order already guarantees the
    end marker lands behind every write this call fired.

    A tool that writes nothing produces an end marker with an empty group,
    which the dispatcher discards.
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
                self._close(context, scope)

    @staticmethod
    def _close(
        context: MiddlewareContext[mt.CallToolRequestParams],
        scope: CommitScope,
    ) -> None:
        """Enqueue *scope*'s end marker, tolerating a vault that is not up.

        The scope closes on the failure path too, so a tool that raises after
        writing still commits what it wrote rather than stranding the group.
        """
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is None:
            return
        try:
            from markdown_vault_mcp.domain import get_vault

            get_vault(fastmcp_context).end_commit_scope(scope)
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
