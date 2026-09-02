"""Per-tool-call commit scoping for the write-callback dispatcher.

A vault write commits per file. That is correct for a single ``write``, and
wrong for anything that touches many: ``okf_convert_links`` over a real vault
produced **2,595 commits for one logical migration**, and every concurrent MCP
write queued behind 2,595 sequential ``git commit`` invocations until the call
timed out.

This module supplies the missing boundary. A :class:`CommitScopeMiddleware`
binds a scope for the duration of each tool call; the dispatcher snapshots that
scope when a write is fired and groups the call's writes into **one** commit.
A call that touched many files is named after the tool that caused them; a call
that touched exactly one keeps that file's own path as the subject, so ordinary
writes read in ``git log`` exactly as they always have.

The scope travels in a :class:`~contextvars.ContextVar` and is read in
``fire``, never on the dispatcher's worker thread — the same constraint that
governs :func:`~markdown_vault_mcp._identity.current_principal` (#1218). ``fire``
runs on the request's ``to_thread`` worker, whose copied context carries the
variable; the worker thread has no request context at all, so reading there
would silently fall back to "no scope" and reinstate per-file commits.

Grouping is keyed by scope token rather than by queue position. The queue is
FIFO, but a shared server interleaves concurrent tool calls, and position-based
grouping would fold two unrelated calls into one commit — the failure this
boundary exists to prevent.
"""

from __future__ import annotations

import itertools
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware

if TYPE_CHECKING:
    from collections.abc import Iterator

    import mcp.types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from fastmcp.tools.base import ToolResult

logger = logging.getLogger(__name__)

_counter = itertools.count()


@dataclass(frozen=True, slots=True)
class CommitScope:
    """One tool invocation's commit boundary.

    Attributes:
        token: Process-unique id distinguishing concurrent invocations. Two
            calls to the same tool get different tokens, so their writes are
            never merged into one commit.
        tool_name: The MCP tool that opened the scope. Becomes the commit
            subject when the call wrote more than one file; a single-file
            call commits under that file's own path instead.
    """

    token: int
    tool_name: str


_scope_var: ContextVar[CommitScope | None] = ContextVar(
    "markdown_vault_mcp_commit_scope", default=None
)


@contextmanager
def bound_commit_scope(tool_name: str) -> Iterator[CommitScope]:
    """Bind a fresh commit scope for the duration, resetting it afterward.

    Args:
        tool_name: Name of the tool being invoked.

    Yields:
        The bound scope, so a caller can enqueue its end marker.
    """
    scope = CommitScope(token=next(_counter), tool_name=tool_name)
    var_token = _scope_var.set(scope)
    try:
        yield scope
    finally:
        _scope_var.reset(var_token)


def current_commit_scope() -> CommitScope | None:
    """Return the scope bound to the current context, or ``None``.

    ``None`` means the write has no owning tool call — a background or
    startup write — and must commit on its own, exactly as before.
    """
    return _scope_var.get()


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
