"""MCP tool registration, decomposed by facet (issue #578).

``register_tools(mcp)`` is the single entry point ``server.py`` imports; it
delegates to each group module's ``register(mcp)``.
"""

from __future__ import annotations

from fastmcp import FastMCP

from . import git, graph, index, reader, writer

__all__ = ["register_tools"]


def register_tools(mcp: FastMCP) -> None:
    """Register all MCP tools on *mcp*.

    Args:
        mcp: The :class:`~fastmcp.FastMCP` instance to register tools on.
    """
    reader.register(mcp)
    graph.register(mcp)
    index.register(mcp)
    writer.register(mcp)
    git.register(mcp)
