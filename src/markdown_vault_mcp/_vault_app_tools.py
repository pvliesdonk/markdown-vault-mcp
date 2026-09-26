"""The vault's MCP Apps tools, registered from ``_server_apps``' DOMAIN-APP-TOOLS.

``_server_apps.py`` is template-owned: its ``DOMAIN-APP-TOOLS`` block keeps
the template's own text and calls :func:`register_vault_app_tools`, so the
vault's tools live here rather than in lines a ``copier update`` re-renders.
App-only tools take the template's ``_app_tool_meta`` as *tool_meta*, and every
tool is registered in the order the block used to register it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.apps import AppConfig
from fastmcp.dependencies import Depends

from markdown_vault_mcp._icons import _TOOL_ICONS
from markdown_vault_mcp._vault_apps import _graph_view_payload
from markdown_vault_mcp.domain import get_vault
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from fastmcp import FastMCP

ToolMeta = Callable[[str], dict[str, Any]]


def register_vault_app_tools(mcp: FastMCP, app_uri: str, tool_meta: ToolMeta) -> None:
    """Register the vault's MCP Apps tools on *mcp*.

    Args:
        mcp: The server ``register_apps`` is building.
        app_uri: The app-shell resource URI the tools render into.
        tool_meta: The template's ``_app_tool_meta``: the per-tool ``meta``
            for an app-only tool, checked against ``_APP_TOOL_NAMES``.
    """
    _register_browse(mcp, app_uri)
    _register_context(mcp, app_uri, tool_meta)
    _register_graph(mcp, app_uri, tool_meta)
    _register_browser(mcp, app_uri, tool_meta)


def _register_browse(mcp: FastMCP, app_uri: str) -> None:
    """Register the model-facing browse_vault tool."""

    @mcp.tool(
        tags={"apps-ui"},
        icons=_TOOL_ICONS["browse_vault"],
        annotations={
            "title": "Browse Vault",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
        app=AppConfig(resource_uri=app_uri),
    )
    async def browse_vault(
        path: str | None = None,
        view: Literal["context", "graph", "browse", "note"] | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Open a visual vault explorer UI for the user — not for reading vault content.

        Displays an interactive visual panel (MCP Apps) to the **user** so they can
        browse the file tree, explore the link graph, or view a note's relationships.
        Do NOT call this to retrieve or inspect vault content programmatically — use
        ``search`` to find notes, ``read`` for note content, ``list_documents`` to
        enumerate files, and ``get_context`` for a note's relationships instead.

        Only call this when the user explicitly asks to open the visual vault browser
        or explorer (e.g. "show me the vault browser", "open the graph view").

        Args:
            path: Optional note path to focus on (e.g. ``"Journal/2024-01-15.md"``).
            view: Which view to open: ``"context"`` (note relationships),
                ``"graph"`` (link visualization), ``"browse"`` (file tree),
                or ``"note"`` (full note preview).
                Defaults to ``"context"`` if a path is given, ``"browse"`` otherwise.

        Returns:
            - path (str | None): The requested note path, or null if none given.
            - view (str): The active view ("context", "graph", "browse", or "note").
            - summary (str): Text summary of vault or note state for non-Apps clients.
        """
        effective_view = view or ("context" if path else "browse")
        summary_parts: list[str] = []

        if path:
            meta = await asyncio.to_thread(vault.reader.get_metadata, path)
            if meta:
                summary_parts.append(f"Note: {meta.title} ({path})")
                summary_parts.append(f"Folder: {meta.folder}")
                if meta.frontmatter:
                    fm_keys = ", ".join(meta.frontmatter.keys())
                    summary_parts.append(f"Frontmatter: {fm_keys}")
            else:
                summary_parts.append(f"Note not found: {path}")
        else:
            stats = await asyncio.to_thread(vault.reader.stats)
            summary_parts.append(
                f"Vault: {stats.document_count} notes, {stats.folder_count} folders"
            )
            if stats.semantic_search_available:
                summary_parts.append("Semantic search: available")

        return {
            "path": path,
            "view": effective_view,
            "summary": "\n".join(summary_parts),
        }

    # -- App-only tools (hidden from LLM, used by SPA via callServerTool) ---


def _register_context(mcp: FastMCP, app_uri: str, tool_meta: ToolMeta) -> None:
    """Register the note-context tools: the SPA's vault_context and the model-facing show_context."""

    @mcp.tool(
        icons=_TOOL_ICONS["vault_context"],
        annotations={
            "title": "Vault Context",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
            "open_world_hint": False,
        },
        meta=tool_meta("vault_context"),
        app=AppConfig(resource_uri=app_uri, visibility=["app"]),
    )
    async def vault_context(
        path: str,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return the full NoteContext for a note (app-only).

        Called by the SPA context card view via ``app.callServerTool()``.
        Not visible to the LLM.

        Args:
            path: Relative note path (e.g. ``"Journal/2024-01-15.md"``).

        Returns:
            Dict with path, title, folder, frontmatter, modified_at, backlinks,
            outlinks, similar, folder_notes, and tags — see 'get_context' for
            field details. Returns {"error": "..."} if the note is not found.
        """
        try:
            ctx = await asyncio.to_thread(vault.reader.get_context, path)
        except ValueError:
            return {"error": f"Note not found: {path}"}
        return asdict(ctx)

    @mcp.tool(
        tags={"apps-ui"},
        icons=_TOOL_ICONS["show_context"],
        annotations={
            "title": "Context Card",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
        app=AppConfig(resource_uri=app_uri),
    )
    async def show_context(
        path: str,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Open a visual context card UI for the user — not for reading note relationships.

        Displays an interactive context panel (MCP Apps) to the **user** showing a
        note's backlinks, outlinks, similar notes, tags, and frontmatter visually.
        Do NOT call this to retrieve note relationship data programmatically — use
        ``get_context`` instead, which returns the full structured data.

        Only call this when the user explicitly asks to open the visual context card
        or explorer (e.g. "show me the context card for this note").

        Args:
            path: Relative note path (e.g. ``"Journal/2024-01-15.md"``).

        Returns:
            - path (str): The note path.
            - view (str): Always "context".
            - summary (str): Text summary with backlink, outlink, and similarity counts.
        """
        try:
            ctx = await asyncio.to_thread(vault.reader.get_context, path)
        except ValueError:
            return {
                "path": path,
                "view": "context",
                "summary": f"Note not found: {path}",
            }
        summary_parts = [
            f"Context for: {ctx.title} ({path})",
            f"Folder: {ctx.folder}",
            f"Backlinks: {len(ctx.backlinks)}",
            f"Outlinks: {len(ctx.outlinks)}",
            f"Similar notes: {len(ctx.similar)}",
            f"Folder peers: {len(ctx.folder_notes)}",
        ]
        if ctx.tags:
            tag_count = sum(len(v) for v in ctx.tags.values())
            summary_parts.append(f"Tags: {tag_count} across {len(ctx.tags)} fields")

        return {
            "path": path,
            "view": "context",
            "summary": "\n".join(summary_parts),
        }


def _register_graph(mcp: FastMCP, app_uri: str, tool_meta: ToolMeta) -> None:
    """Register the SPA's graph tools."""

    @mcp.tool(
        icons=_TOOL_ICONS["vault_graph_neighborhood"],
        annotations={
            "title": "Graph Neighborhood",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
            "open_world_hint": False,
        },
        meta=tool_meta("vault_graph_neighborhood"),
        app=AppConfig(resource_uri=app_uri, visibility=["app"]),
    )
    async def vault_graph_neighborhood(
        path: str,
        depth: int = 1,
        include_semantic: bool = False,
        max_nodes: int = 200,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return the link neighborhood of a note as a node/edge graph (app-only).

        Called by the SPA graph view via ``app.callServerTool()``.
        Not visible to the LLM.

        Args:
            path: Center note path.
            depth: How many hops to traverse (default 1).
            include_semantic: When True, add dashed semantic-similarity edges
                for each interior node (requires embeddings to be configured;
                silently omitted when unavailable).
            max_nodes: Soft cap on returned node count (default 200). BFS
                and any semantic expansion both stop once the cap is hit;
                the response sets ``truncated=True``. Bounds dense-vault
                depth=2 traversals that would otherwise bog down vis-network.

        Returns:
            Dict with:

            - nodes (list): List of dicts, each with:

              - id (str): Unique identifier for the node.
              - label (str): Display name for the node.
              - group (str): "note" or "orphan".
              - folder (str): Parent folder path.
              - backlink_count (int): Number of inbound links.
              - note_type (str, optional): The note's OKF ``type``
                frontmatter value; present only on an active OKF
                bundle for notes that declare one.

            - edges (list): List of dicts, each with:

              - from (str): Source node ID.
              - to (str): Target node ID.
              - type (str): "markdown", "wikilink", "reference", or "semantic".

            - truncated (bool): True when BFS hit the ``max_nodes`` cap.
        """
        view = await asyncio.to_thread(
            vault.graph.get_neighborhood,
            path,
            depth=depth,
            include_semantic=include_semantic,
            max_nodes=max_nodes,
        )
        return _graph_view_payload(view, include_truncated=True)

    @mcp.tool(
        icons=_TOOL_ICONS["vault_graph_hubs"],
        annotations={
            "title": "Graph Hubs",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
            "open_world_hint": False,
        },
        meta=tool_meta("vault_graph_hubs"),
        app=AppConfig(resource_uri=app_uri, visibility=["app"]),
    )
    async def vault_graph_hubs(
        limit: int = 20,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return the most-linked notes and their connections as a graph (app-only).

        Called by the SPA graph view for the hub overview.
        Not visible to the LLM.

        Args:
            limit: Max number of hub notes to include.

        Returns:
            Dict with:

            - nodes (list): List of dicts, each with:

              - id (str): Unique identifier for the node.
              - label (str): Display name for the node.
              - group (str): "hub" or "note".
              - folder (str): Parent folder path.
              - backlink_count (int): Number of inbound links.
              - note_type (str, optional): The note's OKF ``type``
                frontmatter value; present only on an active OKF
                bundle for notes that declare one.

            - edges (list): List of dicts, each with:

              - from (str): Source node ID.
              - to (str): Target node ID.
              - type (str): "markdown", "wikilink", or "reference".
        """
        view = await asyncio.to_thread(vault.graph.get_hub_graph, limit=limit)
        return _graph_view_payload(view, include_truncated=False)


def _register_browser(mcp: FastMCP, app_uri: str, tool_meta: ToolMeta) -> None:
    """Register the SPA's file-browser tools: list, read and search."""

    @mcp.tool(
        icons=_TOOL_ICONS["vault_list"],
        annotations={
            "title": "Vault List",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
            "open_world_hint": False,
        },
        meta=tool_meta("vault_list"),
        app=AppConfig(resource_uri=app_uri, visibility=["app"]),
    )
    async def vault_list(
        folder: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """List folders and notes in a vault directory (app-only).

        Called by the SPA browser view via ``app.callServerTool()``.
        Not visible to the LLM.

        Args:
            folder: Folder to list (root if omitted).

        Returns:
            Dict with:

            - folders (list[str]): Direct child folder paths.
            - notes (list): Notes directly inside this folder. List of dicts,
              each with:

              - path (str): Relative path of the note.
              - title (str): Document title.
              - kind (str): "note" or "attachment".
        """
        docs = await asyncio.to_thread(
            vault.reader.list_documents, folder=folder, include_attachments=True
        )
        folders = await asyncio.to_thread(vault.reader.list_folders)

        # Build direct children: extract the first path component after the
        # prefix from every folder that lives under it.  This handles vaults
        # where list_folders() returns only leaf paths (e.g. "AI/LLM Tooling"
        # not "AI"), so top-level directories like "AI" still appear.
        prefix = (folder.rstrip("/") + "/") if folder else ""
        child_folders = sorted(
            {
                prefix + f[len(prefix) :].split("/")[0]
                for f in folders
                if f and f.startswith(prefix) and f != (folder or "")
            }
        )

        # Only return notes directly inside this folder (not nested ones)
        target_folder = folder or ""
        notes = [
            {
                "path": d.path,
                "title": getattr(d, "title", d.path.rsplit("/", 1)[-1]),
                "kind": d.kind,
            }
            for d in docs
            if d.folder == target_folder
        ]

        return {"folders": child_folders, "notes": notes}

    @mcp.tool(
        icons=_TOOL_ICONS["vault_read"],
        annotations={
            "title": "Vault Read",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
            "open_world_hint": False,
        },
        meta=tool_meta("vault_read"),
        app=AppConfig(resource_uri=app_uri, visibility=["app"]),
    )
    async def vault_read(
        path: str,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any] | None:
        """Read a note's full content for preview rendering (app-only).

        Called by the SPA browser view via ``app.callServerTool()``.
        Not visible to the LLM.

        Args:
            path: Relative note path.

        Returns:
            Dict with path, title, frontmatter, content (the full raw file,
            including the frontmatter block), and modified_at (Unix timestamp),
            or null if the note is not found.
        """
        note = await asyncio.to_thread(vault.reader.read, path)
        if note is None:
            return None
        return {
            "path": note.path,
            "title": note.title,
            "frontmatter": note.frontmatter,
            "content": note.content,
            "modified_at": note.modified_at,
        }

    @mcp.tool(
        icons=_TOOL_ICONS["vault_search"],
        annotations={
            "title": "Vault Search",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
            "open_world_hint": False,
        },
        meta=tool_meta("vault_search"),
        app=AppConfig(resource_uri=app_uri, visibility=["app"]),
    )
    async def vault_search(
        query: str,
        mode: Literal["keyword", "semantic", "hybrid"] = "hybrid",
        limit: int = 20,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Search the vault (app-only).

        Called by the SPA browser search bar via ``app.callServerTool()``.
        Not visible to the LLM.

        Args:
            query: Search query string.
            mode: Search mode (keyword, semantic, or hybrid).
            limit: Max results.

        Returns:
            List of result dicts, each with path (str), title (str),
            snippet (str, first 200 chars of matched chunk), and
            score (float). Returns [{"error": "..."}] on search failure.
        """
        try:
            results = await asyncio.to_thread(
                vault.reader.search, query, limit=limit, mode=mode
            )
        except ValueError as exc:
            return [{"error": str(exc)}]
        # GroupedResult.sections is non-empty for any hit returned by
        # SearchManager.search; pull the snippet from the top section.
        # The SPA browser view consumes a flat {path, title, snippet,
        # score} shape — surfacing only the best section keeps the
        # payload small and matches the pre-collapse rendering.
        return [
            {
                "path": r.path,
                "title": r.title,
                "snippet": (
                    r.sections[0].content[:200]
                    if r.sections and r.sections[0].content
                    else ""
                ),
                "score": r.score,
            }
            for r in results
        ]
