"""Tests for the Vault Browser MCP App view.

Covers issue #276: marked.js + DOMPurify, _vault_list/_vault_read/_vault_search
tools, and HTML browser view content.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_apps import _hashed
from markdown_vault_mcp.server import make_server
from tests.conftest import get_app_html, wait_for_mcp_writer_drain


def _parse_tool_data(result: Any) -> Any:
    data = result.data
    if isinstance(data, list) and data and not isinstance(data[0], (dict, str)):
        raw = result.content[0].text if result.content else "[]"
        return json.loads(raw)
    return data


# ---------------------------------------------------------------------------
# Browser HTML content
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestBrowserHTML:
    """Verify browser elements exist in the SPA HTML."""

    async def test_marked_js_vendored(self) -> None:
        html = await get_app_html()
        assert "marked@" in html
        assert "(vendored)" in html

    async def test_dompurify_vendored(self) -> None:
        html = await get_app_html()
        assert "dompurify@" in html
        assert "(vendored)" in html
        assert "DOMPurify.sanitize" in html

    async def test_browser_layout(self) -> None:
        html = await get_app_html()
        assert "browser-layout" in html
        assert "browser-sidebar" in html
        assert "browser-preview" in html

    async def test_folder_tree(self) -> None:
        html = await get_app_html()
        assert 'id="browser-tree"' in html
        assert "tree-folder" in html
        assert "tree-note" in html

    async def test_search_input(self) -> None:
        html = await get_app_html()
        assert 'id="browser-search-input"' in html
        assert "Search vault" in html

    async def test_search_clear_button(self) -> None:
        html = await get_app_html()
        assert 'id="browser-search-clear"' in html

    async def test_preview_panel(self) -> None:
        html = await get_app_html()
        assert 'id="browser-preview"' in html
        assert "preview-content" in html

    async def test_disabled_edit_button(self) -> None:
        html = await get_app_html()
        assert "edit-btn-disabled" in html
        assert "Coming soon" in html

    async def test_send_to_claude_in_preview(self) -> None:
        html = await get_app_html()
        assert "preview-send-btn" in html

    async def test_context_button_in_preview(self) -> None:
        html = await get_app_html()
        assert "preview-ctx-btn" in html

    async def test_graph_button_in_preview(self) -> None:
        html = await get_app_html()
        assert "preview-graph-btn" in html

    async def test_marked_parse_call(self) -> None:
        html = await get_app_html()
        assert "marked.parse" in html

    async def test_host_font_inheritance(self) -> None:
        html = await get_app_html()
        assert "font-family: inherit" in html or "--font-sans" in html

    async def test_update_model_context(self) -> None:
        html = await get_app_html()
        assert "'browser'" in html
        assert "updateContext" in html


# ---------------------------------------------------------------------------
# Browser data tools
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestBrowserDataTools:
    """Verify browser data tools return expected structures."""

    async def test_vault_list_root(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(_hashed("vault_list"), {})
            data = _parse_tool_data(result)
            assert "folders" in data
            assert "notes" in data
            assert isinstance(data["folders"], list)
            assert isinstance(data["notes"], list)
            # Root should contain at least some notes
            assert len(data["notes"]) > 0

    async def test_vault_list_subfolder(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                _hashed("vault_list"), {"folder": "subfolder"}
            )
            data = _parse_tool_data(result)
            assert "folders" in data
            assert "notes" in data

    async def test_vault_read_note(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_read"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "simple.md"
            assert "title" in data
            assert "content" in data
            assert "frontmatter" in data
            assert "modified_at" in data
            assert len(data["content"]) > 0

    async def test_vault_read_with_frontmatter(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_read"), {"path": "full_frontmatter.md"}
            )
            data = _parse_tool_data(result)
            assert isinstance(data["frontmatter"], dict)
            assert len(data["frontmatter"]) > 0

    async def test_vault_search_keyword(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                _hashed("vault_search"), {"query": "simple", "mode": "keyword"}
            )
            data = _parse_tool_data(result)
            assert isinstance(data, list)
            assert len(data) > 0, "Expected at least one result for query 'simple'"
            assert "path" in data[0]
            assert "title" in data[0]
            assert "snippet" in data[0]
            assert "score" in data[0]

    async def test_vault_search_respects_limit(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_search"),
                {"query": "document", "mode": "keyword", "limit": 2},
            )
            data = _parse_tool_data(result)
            assert len(data) <= 2

    async def test_notes_have_kind_field(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(_hashed("vault_list"), {})
            data = _parse_tool_data(result)
            for note in data["notes"]:
                assert "kind" in note
