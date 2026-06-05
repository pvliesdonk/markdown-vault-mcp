"""Tool icons (Lucide SVGs as data URIs) for MCP tool/resource/prompt decorators.

SVG sources live in ``static/icons/*.svg`` and are base64-encoded at import time
so each tool gets a ``data:image/svg+xml;base64,...`` URI suitable for MCP Icon.
"""

from __future__ import annotations

import base64
import importlib.resources

from mcp.types import Icon

_ICONS_DIR = importlib.resources.files("markdown_vault_mcp").joinpath("static/icons")


def _load_icon(name: str) -> list[Icon]:
    """Load a single SVG icon from *static/icons/{name}.svg* as a data URI.

    Called at module level to populate :data:`_TOOL_ICONS`.  Raises
    ``FileNotFoundError`` if the SVG file is missing — this indicates a
    broken or partial package installation.
    """
    svg_bytes = _ICONS_DIR.joinpath(f"{name}.svg").read_bytes().rstrip(b"\n")
    b64 = base64.b64encode(svg_bytes).decode("ascii")
    return [Icon(src=f"data:image/svg+xml;base64,{b64}", mimeType="image/svg+xml")]


_SERVER_ICON: list[Icon] = _load_icon("server")

_TOOL_ICONS: dict[str, list[Icon]] = {
    name: _load_icon(name)
    for name in [
        "search",
        "read",
        "list_documents",
        "list_folders",
        "list_tags",
        "stats",
        "embeddings_status",
        "reindex",
        "build_embeddings",
        "write",
        "edit",
        "delete",
        "rename",
        "get_backlinks",
        "get_outlinks",
        "get_recent",
        "get_similar",
        "get_broken_links",
        "get_context",
        "get_orphan_notes",
        "get_most_linked",
        "get_connection_path",
        "fetch",
        "create_download_link",
        "create_upload_link",
        "browse_vault",
        "show_context",
        "get_history",
        "get_diff",
        "git_sync",
        "vault_graph_neighborhood",
        "vault_graph_hubs",
    ]
}

# Status tools reuse existing icons rather than introducing new SVG files.
_TOOL_ICONS["get_index_status"] = _TOOL_ICONS["embeddings_status"]

# App-only tools reuse existing icons rather than introducing new SVG files.
_TOOL_ICONS["vault_context"] = _TOOL_ICONS["get_context"]
_TOOL_ICONS["vault_list"] = _TOOL_ICONS["list_documents"]
_TOOL_ICONS["vault_read"] = _TOOL_ICONS["read"]
_TOOL_ICONS["vault_search"] = _TOOL_ICONS["search"]
