"""MCP tool registration, decomposed by facet (issue #578).

This is the template's seeded ``tools.py``; the tool groups themselves live
in the private ``_tools`` package so this module stays the one file the
template expects to find here.

``register_tools(mcp)`` is the single entry point ``server.py`` imports; it
delegates to each group module's ``register(mcp)``.  The ``summarize`` group
is the one exception: its registration needs the server's ``Jobs`` mechanics
(built from the loaded config), so it is registered from ``_server_wiring``,
which ``make_server``'s DOMAIN-WIRING block calls, instead (#1033) — the same
already-loaded-config pattern as ``register_domain_prompts`` (#609).
"""

from __future__ import annotations

from fastmcp import FastMCP

from markdown_vault_mcp._tools import git, graph, index, reader, writer

__all__ = ["register_tools"]


def register_tools(mcp: FastMCP) -> None:
    """Register all config-free MCP tools on *mcp*.

    Args:
        mcp: The :class:`~fastmcp.FastMCP` instance to register tools on.
    """
    reader.register(mcp)
    graph.register(mcp)
    index.register(mcp)
    writer.register(mcp)
    git.register(mcp)
