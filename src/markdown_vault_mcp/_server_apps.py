"""MCP Apps registrations — SPA shell resource and app-related tools.

Provides :func:`register_apps` to set up the vault SPA shell as a
``ui://`` resource and register app-aware tools (``browse_vault``,
``show_context``, and app-only data-fetching tools hidden from the LLM).

Call :func:`register_apps` after constructing the :class:`~fastmcp.FastMCP`
instance in :func:`~markdown_vault_mcp.mcp_server.create_server`.
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import importlib.resources
import logging
import os
from dataclasses import asdict
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.apps import AppConfig, ResourceCSP
from fastmcp.dependencies import Depends

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.config import _ENV_PREFIX

from ._icons import _TOOL_ICONS
from ._server_deps import get_collection

logger = logging.getLogger(__name__)

_VAULT_APP_URI = "ui://vault/app.html"

# All SPA dependencies are vendored inline (see scripts/vendor_spa.py).
# No external CDN domains needed at runtime.
_CDN_RESOURCE_DOMAINS: list[str] = []


def _compute_claude_app_domain() -> str | None:
    """Auto-compute Claude's MCP Apps sandbox domain from BASE_URL.

    Claude requires ``{sha256_prefix}.claudemcpcontent.com`` where the hash
    is derived from the full MCP endpoint URL the client connects to.

    Returns:
        The computed domain string, or ``None`` when ``BASE_URL`` is not set
        (e.g. stdio transport or local development).
    """
    base_url = os.environ.get(f"{_ENV_PREFIX}_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        return None
    http_path = os.environ.get(f"{_ENV_PREFIX}_HTTP_PATH", "/mcp").strip() or "/mcp"
    if not http_path.startswith("/"):
        http_path = f"/{http_path}"
    if len(http_path) > 1:
        http_path = http_path.rstrip("/")
    mcp_url = f"{base_url}{http_path}"
    hash_prefix = hashlib.sha256(mcp_url.encode()).hexdigest()[:32]
    return f"{hash_prefix}.claudemcpcontent.com"


# ---------------------------------------------------------------------------
# SPA shell HTML — loaded from static/app.html
# ---------------------------------------------------------------------------

_SPA_SHELL_HTML = (
    importlib.resources.files("markdown_vault_mcp")
    .joinpath("static/app.html")
    .read_text(encoding="utf-8")
    .rstrip("\n")
)


def register_apps(mcp: FastMCP) -> None:
    """Register MCP Apps resources and app-related tools on *mcp*.

    Sets up the vault SPA shell as a ``ui://vault/app.html`` resource
    and registers the ``browse_vault`` primary tool and app-only tools
    for downstream views.

    Args:
        mcp: The :class:`~fastmcp.FastMCP` instance to register on.
    """
    # Resolve app domain for Claude's sandbox iframe.
    app_domain: str | None = (
        os.environ.get(f"{_ENV_PREFIX}_APP_DOMAIN", "").strip()
        or _compute_claude_app_domain()
    )
    if app_domain:
        logger.info("MCP Apps domain: %s", app_domain)
    else:
        logger.debug("MCP Apps domain: not configured (stdio or no BASE_URL)")

    app_config = AppConfig(
        domain=app_domain,
        csp=ResourceCSP(resourceDomains=_CDN_RESOURCE_DOMAINS),
    )

    # -- SPA shell resource -------------------------------------------------

    @mcp.resource(
        _VAULT_APP_URI,
        description="Interactive vault explorer with context card, graph, and browser views.",
        icons=_TOOL_ICONS["browse_vault"],
        app=app_config,
    )
    def vault_app() -> str:
        """HTML SPA shell loaded by MCP Apps-capable clients.

        Provides tabbed navigation between Context, Graph, and Browse views.
        Loaded in a sandboxed iframe by Claude Desktop or claude.ai.
        """
        return _SPA_SHELL_HTML

    # -- Primary tool: browse_vault -----------------------------------------

    @mcp.tool(
        icons=_TOOL_ICONS["browse_vault"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        app=AppConfig(resourceUri=_VAULT_APP_URI),
    )
    async def browse_vault(
        path: str | None = None,
        view: Literal["context", "graph", "browse", "note"] | None = None,
        collection: Collection = Depends(get_collection),
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
            note = await asyncio.to_thread(collection.read, path)
            if note:
                summary_parts.append(f"Note: {note.title} ({path})")
                summary_parts.append(f"Folder: {note.folder}")
                if note.frontmatter:
                    fm_keys = ", ".join(note.frontmatter.keys())
                    summary_parts.append(f"Frontmatter: {fm_keys}")
            else:
                summary_parts.append(f"Note not found: {path}")
        else:
            stats = await asyncio.to_thread(collection.stats)
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

    @mcp.tool(
        icons=_TOOL_ICONS["vault_context"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"fastmcp": {"app": "vault"}},
        app=AppConfig(resourceUri=_VAULT_APP_URI, visibility=["app"]),
    )
    async def vault_context(
        path: str,
        collection: Collection = Depends(get_collection),
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
            ctx = await asyncio.to_thread(collection.get_context, path)
        except ValueError:
            return {"error": f"Note not found: {path}"}
        return asdict(ctx)

    @mcp.tool(
        icons=_TOOL_ICONS["show_context"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
        app=AppConfig(resourceUri=_VAULT_APP_URI),
    )
    async def show_context(
        path: str,
        collection: Collection = Depends(get_collection),
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
            ctx = await asyncio.to_thread(collection.get_context, path)
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

    @mcp.tool(
        icons=_TOOL_ICONS["vault_graph_neighborhood"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"fastmcp": {"app": "vault"}},
        app=AppConfig(resourceUri=_VAULT_APP_URI, visibility=["app"]),
    )
    async def vault_graph_neighborhood(
        path: str,
        depth: int = 1,
        include_semantic: bool = False,
        collection: Collection = Depends(get_collection),
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

        Returns:
            Dict with:

            - nodes (list): List of dicts, each with:

              - id (str): Unique identifier for the node.
              - label (str): Display name for the node.
              - group (str): "note" or "orphan".
              - folder (str): Parent folder path.
              - backlink_count (int): Number of inbound links.

            - edges (list): List of dicts, each with:

              - from (str): Source node ID.
              - to (str): Target node ID.
              - type (str): "markdown", "wikilink", "reference", or "semantic".
        """
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        visited: set[str] = set()
        queue: collections.deque[tuple[str, int]] = collections.deque([(path, 0)])

        while queue:
            current, d = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            # Add node
            note = await asyncio.to_thread(collection.read, current)
            label = (
                note.title if note else current.rsplit("/", 1)[-1].replace(".md", "")
            )
            folder = note.folder if note else ""

            if d >= depth:
                # Boundary nodes are reachable by definition — skip DB calls
                nodes[current] = {
                    "id": current,
                    "label": label,
                    "group": "note",
                    "folder": folder,
                    "backlink_count": 0,
                }
                continue

            # Fetch backlinks/outlinks for interior nodes (orphan detection + edges)
            try:
                backlinks = await asyncio.to_thread(collection.get_backlinks, current)
            except ValueError:
                backlinks = []
            try:
                outlinks = await asyncio.to_thread(collection.get_outlinks, current)
            except ValueError:
                outlinks = []
            is_orphan = len(backlinks) == 0 and len(outlinks) == 0

            nodes[current] = {
                "id": current,
                "label": label,
                "group": "orphan" if is_orphan else "note",
                "folder": folder,
                "backlink_count": len(backlinks),
            }

            for bl in backlinks:
                edges.append(
                    {
                        "from": bl.source_path,
                        "to": current,
                        "type": bl.link_type,
                    }
                )
                if bl.source_path not in visited:
                    queue.append((bl.source_path, d + 1))

            # Process outlinks (already fetched above for orphan detection)
            for ol in outlinks:
                if ol.exists:
                    edges.append(
                        {
                            "from": current,
                            "to": ol.target_path,
                            "type": ol.link_type,
                        }
                    )
                    if ol.target_path not in visited:
                        queue.append((ol.target_path, d + 1))

        # Deduplicate explicit edges
        seen_edges: set[tuple[str, str]] = set()
        unique_edges: list[dict[str, Any]] = []
        for e in edges:
            key = (e["from"], e["to"])
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

        # Semantic similarity edges (optional, requires embeddings)
        if include_semantic:
            sem_seen: set[frozenset[str]] = set()
            for node_path in list(nodes.keys()):
                try:
                    similar = await asyncio.to_thread(
                        collection.get_similar, node_path, limit=5
                    )
                except ValueError:
                    # Expected when embeddings are not configured for this collection
                    continue
                except Exception:
                    logger.warning(
                        "get_similar failed for %s", node_path, exc_info=True
                    )
                    continue
                seen_sr: set[str] = set()
                for sr in similar:
                    if sr.path == node_path or sr.path in seen_sr:
                        continue
                    seen_sr.add(sr.path)
                    pair: frozenset[str] = frozenset({node_path, sr.path})
                    if pair in sem_seen:
                        continue
                    sem_seen.add(pair)
                    if sr.path not in nodes:
                        sim_note = await asyncio.to_thread(collection.read, sr.path)
                        sim_label = (
                            sim_note.title
                            if sim_note
                            else sr.path.rsplit("/", 1)[-1].replace(".md", "")
                        )
                        sim_folder = sim_note.folder if sim_note else ""
                        nodes[sr.path] = {
                            "id": sr.path,
                            "label": sim_label,
                            "group": "note",
                            "folder": sim_folder,
                            "backlink_count": 0,
                        }
                    unique_edges.append(
                        {"from": node_path, "to": sr.path, "type": "semantic"}
                    )

        return {"nodes": list(nodes.values()), "edges": unique_edges}

    @mcp.tool(
        icons=_TOOL_ICONS["vault_graph_hubs"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"fastmcp": {"app": "vault"}},
        app=AppConfig(resourceUri=_VAULT_APP_URI, visibility=["app"]),
    )
    async def vault_graph_hubs(
        limit: int = 20,
        collection: Collection = Depends(get_collection),
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

            - edges (list): List of dicts, each with:

              - from (str): Source node ID.
              - to (str): Target node ID.
              - type (str): "markdown", "wikilink", or "reference".
        """
        hubs = await asyncio.to_thread(collection.get_most_linked, limit=limit)
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str]] = set()

        for hub in hubs:
            # TODO: extend get_most_linked to return folder, eliminating this per-hub read
            hub_note = await asyncio.to_thread(collection.read, hub.path)
            hub_folder = hub_note.folder if hub_note else ""
            nodes[hub.path] = {
                "id": hub.path,
                "label": hub.title,
                "group": "hub",
                "backlink_count": hub.backlink_count,
                "folder": hub_folder,
            }

            # Get immediate connections for each hub
            try:
                backlinks = await asyncio.to_thread(collection.get_backlinks, hub.path)
            except ValueError:
                backlinks = []
            for bl in backlinks:
                if bl.source_path not in nodes:
                    note = await asyncio.to_thread(collection.read, bl.source_path)
                    label = (
                        note.title
                        if note
                        else bl.source_path.rsplit("/", 1)[-1].replace(".md", "")
                    )
                    folder = note.folder if note else ""
                    nodes[bl.source_path] = {
                        "id": bl.source_path,
                        "label": label,
                        "group": "note",
                        "folder": folder,
                        "backlink_count": 0,
                    }
                edge_key = (bl.source_path, hub.path)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(
                        {
                            "from": bl.source_path,
                            "to": hub.path,
                            "type": bl.link_type,
                        }
                    )

        return {"nodes": list(nodes.values()), "edges": edges}

    @mcp.tool(
        icons=_TOOL_ICONS["vault_list"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"fastmcp": {"app": "vault"}},
        app=AppConfig(resourceUri=_VAULT_APP_URI, visibility=["app"]),
    )
    async def vault_list(
        folder: str | None = None,
        collection: Collection = Depends(get_collection),
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
            collection.list, folder=folder, include_attachments=True
        )
        folders = await asyncio.to_thread(collection.list_folders)

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
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"fastmcp": {"app": "vault"}},
        app=AppConfig(resourceUri=_VAULT_APP_URI, visibility=["app"]),
    )
    async def vault_read(
        path: str,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any] | None:
        """Read a note's full content for preview rendering (app-only).

        Called by the SPA browser view via ``app.callServerTool()``.
        Not visible to the LLM.

        Args:
            path: Relative note path.

        Returns:
            Dict with path, title, frontmatter, content (markdown body), and
            modified_at (Unix timestamp), or null if the note is not found.
        """
        note = await asyncio.to_thread(collection.read, path)
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
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        meta={"fastmcp": {"app": "vault"}},
        app=AppConfig(resourceUri=_VAULT_APP_URI, visibility=["app"]),
    )
    async def vault_search(
        query: str,
        mode: Literal["keyword", "semantic", "hybrid"] = "hybrid",
        limit: int = 20,
        collection: Collection = Depends(get_collection),
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
                collection.search, query, limit=limit, mode=mode
            )
        except ValueError as exc:
            return [{"error": str(exc)}]
        return [
            {
                "path": r.path,
                "title": r.title,
                "snippet": r.content[:200] if r.content else "",
                "score": r.score,
            }
            for r in results
        ]
