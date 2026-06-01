"""Tests for the Note Context Card MCP App view.

Covers issue #274: _vault_context tool, show_context tool, and
HTML context card rendering.
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
# Context card HTML content
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestContextCardHTML:
    """Verify context card sections exist in the SPA HTML."""

    async def test_context_card_container(self) -> None:
        html = await get_app_html()
        assert 'id="context-card"' in html

    async def test_context_header_elements(self) -> None:
        html = await get_app_html()
        assert 'id="ctx-title"' in html
        assert 'id="ctx-path"' in html
        assert 'id="ctx-folder"' in html

    async def test_collapsible_sections(self) -> None:
        html = await get_app_html()
        for section_id in [
            "ctx-frontmatter",
            "ctx-tags",
            "ctx-backlinks",
            "ctx-outlinks",
            "ctx-similar",
            "ctx-peers",
        ]:
            assert f'id="{section_id}"' in html

    async def test_send_to_claude_button(self) -> None:
        html = await get_app_html()
        assert 'id="ctx-send-btn"' in html
        assert "sendToLLM" in html

    async def test_call_server_tool_for_context(self) -> None:
        html = await get_app_html()
        assert "callServerTool" in html
        assert _hashed("vault_context") in html

    async def test_clickable_link_items(self) -> None:
        html = await get_app_html()
        assert "link-item" in html
        assert "data-path" in html

    async def test_host_css_variables(self) -> None:
        html = await get_app_html()
        assert "var(--color-text-info" in html
        assert "var(--color-border-primary" in html
        assert "var(--color-text-secondary" in html

    async def test_update_model_context_on_view(self) -> None:
        html = await get_app_html()
        assert "updateContext" in html
        assert "'context card'" in html

    async def test_link_type_badges(self) -> None:
        html = await get_app_html()
        assert "link-type-badge" in html

    async def test_similar_score_bar(self) -> None:
        html = await get_app_html()
        assert "similar-score-fill" in html

    async def test_tag_pills(self) -> None:
        html = await get_app_html()
        assert "tag-pill" in html

    async def test_frontmatter_table(self) -> None:
        html = await get_app_html()
        assert "fm-table" in html


# ---------------------------------------------------------------------------
# _vault_context tool data
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestVaultContextToolData:
    """Verify _vault_context returns complete NoteContext fields."""

    async def test_all_context_fields_present(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                _hashed("vault_context"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "simple.md"
            assert "title" in data
            assert "folder" in data
            assert "frontmatter" in data
            assert "modified_at" in data
            assert "backlinks" in data
            assert "outlinks" in data
            assert "similar" in data
            assert "folder_notes" in data
            assert "tags" in data

    async def test_context_with_frontmatter(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                _hashed("vault_context"), {"path": "full_frontmatter.md"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "full_frontmatter.md"
            assert isinstance(data["frontmatter"], dict)


# ---------------------------------------------------------------------------
# show_context tool (AC2)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestShowContextTool:
    """Verify show_context primary tool behaviour (AC2)."""

    async def test_returns_view_and_summary(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("show_context", {"path": "simple.md"})
            data = _parse_tool_data(result)
            assert data["view"] == "context"
            assert data["path"] == "simple.md"
            assert "Backlinks:" in data["summary"]

    async def test_summary_contains_relationship_counts(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("show_context", {"path": "simple.md"})
            data = _parse_tool_data(result)
            summary = data["summary"]
            assert "Outlinks:" in summary
            assert "Similar notes:" in summary
            assert "Folder peers:" in summary

    async def test_show_context_visible_to_llm(self) -> None:
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert "show_context" in names
            # vault_context has visibility=["app"] — hidden from LLM tool list
            assert "vault_context" not in names

    async def test_missing_note_returns_error_summary(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool("show_context", {"path": "nonexistent.md"})
            data = _parse_tool_data(result)
            assert data["view"] == "context"
            assert "not found" in data["summary"].lower()


# ---------------------------------------------------------------------------
# No hardcoded colors (AC7)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestNoHardcodedColors:
    """Verify context card CSS uses variables instead of hardcoded hex colours."""

    async def test_success_color_is_variable(self) -> None:
        html = await get_app_html()
        assert "var(--color-text-success" in html

    async def test_error_color_is_variable(self) -> None:
        html = await get_app_html()
        assert "var(--color-text-danger" in html

    async def test_accent_fg_color_is_variable(self) -> None:
        html = await get_app_html()
        assert "var(--color-text-inverse" in html
