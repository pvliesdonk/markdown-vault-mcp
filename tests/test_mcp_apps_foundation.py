"""Tests for MCP Apps foundation — SPA shell, domain computation, and tools.

Covers issue #273: resource registration, tool registration, domain
computation, and HTML content verification.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_apps import (
    _VAULT_APP_TOOL_NAMES,
    _app_tool_meta,
    _compute_claude_app_domain,
    _hashed,
    _rewrite_spa_app_tool_calls,
)
from markdown_vault_mcp.server import make_server
from tests.conftest import _CLEAR_VARS

if TYPE_CHECKING:
    from pathlib import Path


def _parse_tool_data(result: Any) -> Any:
    """Extract data from a CallToolResult."""
    data = result.data
    if isinstance(data, list) and data and not isinstance(data[0], (dict, str)):
        raw = result.content[0].text if result.content else "[]"
        return json.loads(raw)
    return data


# ---------------------------------------------------------------------------
# Domain computation
# ---------------------------------------------------------------------------


class TestComputeClaudeAppDomain:
    """Tests for _compute_claude_app_domain()."""

    def test_no_base_url_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_BASE_URL", raising=False)
        assert _compute_claude_app_domain() is None

    def test_empty_base_url_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "  ")
        assert _compute_claude_app_domain() is None

    def test_base_url_computes_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://vault.example.com")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_HTTP_PATH", raising=False)
        result = _compute_claude_app_domain()
        assert result is not None
        assert result.endswith(".claudemcpcontent.com")
        # Prefix should be 32-char hex
        prefix = result.split(".")[0]
        assert len(prefix) == 32
        assert all(c in "0123456789abcdef" for c in prefix)

    def test_app_domain_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """APP_DOMAIN env var is tested at the register_apps level, not here."""
        # _compute_claude_app_domain() only looks at BASE_URL
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://example.com")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_HTTP_PATH", raising=False)
        result = _compute_claude_app_domain()
        assert result is not None

    def test_custom_http_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://example.com")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_HTTP_PATH", "/custom/path")
        result1 = _compute_claude_app_domain()

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_HTTP_PATH", "/mcp")
        result2 = _compute_claude_app_domain()

        # Different paths should produce different domains
        assert result1 != result2

    def test_trailing_slash_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://example.com/")
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_HTTP_PATH", raising=False)
        result1 = _compute_claude_app_domain()

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://example.com")
        result2 = _compute_claude_app_domain()

        assert result1 == result2


# ---------------------------------------------------------------------------
# SPA shell resource
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestSPAShellResource:
    """Tests for the ui://vault/app.html resource."""

    async def test_resource_registered(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resources = await client.list_resources()
            uris = [str(r.uri) for r in resources]
            assert "ui://vault/app.html" in uris

    async def test_html_contains_ext_apps_sdk(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            assert "@modelcontextprotocol/ext-apps" in html

    async def test_html_contains_tab_navigation(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            assert 'data-tab="context"' in html
            assert 'data-tab="graph"' in html
            assert 'data-tab="browse"' in html

    async def test_html_contains_host_theming(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            assert "handleHostContext" in html
            assert "applyDocumentTheme" in html
            assert "--color-background-primary" in html
            assert "--color-text-primary" in html

    async def test_html_handlers_before_connect(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            # Handlers must be registered before app.connect()
            connect_pos = html.index("app.connect()")
            on_tool_result_pos = html.index("app.ontoolresult")
            assert on_tool_result_pos < connect_pos

    async def test_html_contains_fullscreen_toggle(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            assert "fullscreenBtn" in html
            assert "requestDisplayMode" in html

    async def test_html_contains_ontoolinput(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = resource[0].text
            assert "app.ontoolinput" in html
            assert "processToolInput" in html
            assert "pendingToolInput" in html

    async def test_html_contains_ontoolcancelled(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = resource[0].text
            assert "app.ontoolcancelled" in html

    async def test_html_contains_onteardown(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = resource[0].text
            assert "app.onteardown" in html

    async def test_html_static_import(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = resource[0].text
            # Static import via import map (vendored SDK, Android compatible)
            assert 'from "@modelcontextprotocol/ext-apps"' in html
            assert "importmap" in html
            assert "await import(" not in html

    async def test_html_parse_tool_result(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = resource[0].text
            # parseToolResult extracts JSON from CallToolResult.content[0].text
            assert "parseToolResult" in html
            assert "content.find(c => c.type === 'text')" in html
            # Must be at module scope — before any IIFE — so Browse/Graph views
            # can access it (regression guard for d9962d7).
            parse_pos = html.find("function parseToolResult")
            iife_pos = html.find("app.ontoolinput")
            assert parse_pos != -1, "parseToolResult not found in app.html"
            assert iife_pos != -1, "app.ontoolinput not found in app.html"
            assert parse_pos < iife_pos, (
                "parseToolResult must be defined at module scope before app.ontoolinput"
            )

    async def test_html_browse_fallback_when_no_tool_input(self) -> None:
        """After connect, app defaults to browse view when ontoolinput never fires."""
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = resource[0].text
            # The else branch after pendingToolInput check must switch to browse
            assert "switchTab('browse')" in html
            assert "window.loadBrowser" in html

    async def test_html_contains_error_handler(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            assert "app.onerror" in html

    async def test_html_contains_navigate_to(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            assert "navigateTo" in html

    async def test_html_contains_send_to_llm(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            assert "sendToLLM" in html
            assert "sendMessage" in html

    async def test_html_contains_update_context(self) -> None:
        server = make_server()
        async with Client(server) as client:
            resource = await client.read_resource("ui://vault/app.html")
            html = (
                resource[0].text if hasattr(resource[0], "text") else str(resource[0])
            )
            assert "updateContext" in html
            assert "updateModelContext" in html


# ---------------------------------------------------------------------------
# browse_vault tool
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestBrowseVaultTool:
    """Tests for the browse_vault primary tool."""

    async def test_tool_registered(self) -> None:
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert "browse_vault" in names

    async def test_no_args_returns_vault_summary(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("browse_vault", {})
            data = _parse_tool_data(result)
            assert data["view"] == "browse"
            assert data["path"] is None
            assert "notes" in data["summary"]

    async def test_with_path_returns_note_info(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("browse_vault", {"path": "simple.md"})
            data = _parse_tool_data(result)
            assert data["path"] == "simple.md"
            assert data["view"] == "context"

    async def test_with_view_override(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("browse_vault", {"view": "graph"})
            data = _parse_tool_data(result)
            assert data["view"] == "graph"

    async def test_missing_path(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "browse_vault", {"path": "nonexistent/path.md"}
            )
            data = _parse_tool_data(result)
            assert "not found" in data["summary"].lower()


# ---------------------------------------------------------------------------
# show_context tool
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestShowContextTool:
    """Tests for the show_context tool."""

    async def test_tool_registered(self) -> None:
        server = make_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert "show_context" in names

    async def test_returns_context_summary(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("show_context", {"path": "simple.md"})
            data = _parse_tool_data(result)
            assert data["path"] == "simple.md"
            assert data["view"] == "context"
            assert "Backlinks:" in data["summary"]
            assert "Outlinks:" in data["summary"]


# ---------------------------------------------------------------------------
# App-only tools
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestAppOnlyTools:
    """Tests for app-only tools (visibility=["app"])."""

    async def test_vault_context_returns_note_context(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_context"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "simple.md"
            assert "backlinks" in data
            assert "outlinks" in data
            assert "similar" in data
            assert "folder_notes" in data
            assert "tags" in data

    async def test_vault_graph_neighborhood(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            assert "nodes" in data
            assert "edges" in data
            assert isinstance(data["nodes"], list)
            assert isinstance(data["edges"], list)
            # Center node should be present
            node_ids = [n["id"] for n in data["nodes"]]
            assert "simple.md" in node_ids

    async def test_vault_graph_hubs(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
            assert "nodes" in data
            assert "edges" in data

    async def test_vault_list_root(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_list"), {})
            data = _parse_tool_data(result)
            assert "folders" in data
            assert "notes" in data
            assert isinstance(data["notes"], list)

    async def test_vault_list_root_notes_are_root_only(self) -> None:
        """Root listing must not include notes from subfolders."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_list"), {})
            data = _parse_tool_data(result)
            for note in data["notes"]:
                assert "/" not in note["path"], (
                    f"Root listing returned nested note: {note['path']}"
                )

    async def test_vault_list_subfolder_notes_are_direct_children(self) -> None:
        """Subfolder listing must only include direct children, not deeper nesting."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_list"), {"folder": "subfolder"}
            )
            data = _parse_tool_data(result)
            for note in data["notes"]:
                assert note["path"].startswith("subfolder/"), (
                    f"Subfolder note has wrong prefix: {note['path']}"
                )
                # Must be a direct child: subfolder/foo.md (no further slash)
                rest = note["path"][len("subfolder/") :]
                assert "/" not in rest, (
                    f"Subfolder listing returned deeply nested note: {note['path']}"
                )

    async def test_vault_list_folders_intermediate_only_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Root listing must surface intermediate folders even when list_folders()
        only returns leaf paths (e.g. 'ai/llm/note.md' with no file directly
        in 'ai/').  Regression guard for the split('/')[0] fix."""
        # Create a vault with a note only at a deeply nested path — no file
        # directly in 'ai/', so list_folders() returns only 'ai/llm'.
        ai_llm = tmp_path / "ai" / "llm"
        ai_llm.mkdir(parents=True)
        (ai_llm / "note.md").write_text("# Note\n\nContent.\n")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        for var in _CLEAR_VARS:
            monkeypatch.delenv(var, raising=False)
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_list"), {})
            data = _parse_tool_data(result)
            # 'ai' must appear even though list_folders() only returns 'ai/llm'
            assert "ai" in data["folders"], (
                f"Intermediate folder 'ai' missing from root listing: {data['folders']}"
            )
            assert "ai/llm" not in data["folders"], (
                f"Leaf path 'ai/llm' must not appear in root listing: {data['folders']}"
            )

    async def test_vault_read(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_read"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "simple.md"
            assert "content" in data
            assert "title" in data

    async def test_vault_read_missing(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_read"), {"path": "does-not-exist.md"}
            )
            data = _parse_tool_data(result)
            assert data is None

    async def test_vault_search(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_search"), {"query": "hello", "mode": "keyword"}
            )
            data = _parse_tool_data(result)
            assert isinstance(data, list)


# ---------------------------------------------------------------------------
# Domain env var override
# ---------------------------------------------------------------------------


class TestAppDomainOverride:
    """Test that APP_DOMAIN env var takes precedence over auto-compute."""

    def test_explicit_app_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When APP_DOMAIN is set, it should be used verbatim."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_APP_DOMAIN", "custom.example.com")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://vault.example.com")
        # The override happens in register_apps, not in _compute_claude_app_domain.
        # We just verify compute still works independently.
        computed = _compute_claude_app_domain()
        assert computed is not None
        assert computed != "custom.example.com"

    def test_app_domain_overrides_base_url(
        self, vault_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When both APP_DOMAIN and BASE_URL are set, APP_DOMAIN wins."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_APP_DOMAIN", "custom.example.com")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://vault.example.com")
        for var in _CLEAR_VARS:
            if (
                var != "MARKDOWN_VAULT_MCP_APP_DOMAIN"
                and var != "MARKDOWN_VAULT_MCP_BASE_URL"
            ):
                monkeypatch.delenv(var, raising=False)
        # Server creates successfully — no assertion on domain value since
        # it's internal to the resource config, but the server should not error.
        server = make_server()
        assert server is not None


# ---------------------------------------------------------------------------
# App-only tool data coverage
# ---------------------------------------------------------------------------


@pytest.fixture
def _linked_env(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create linked notes in the vault for graph traversal tests."""
    (vault_path / "linked_a.md").write_text(
        "---\ntitle: Linked A\n---\n\n# Linked A\n\n"
        "Links to [Linked B](linked_b.md) and [Simple](simple.md).\n"
    )
    (vault_path / "linked_b.md").write_text(
        "---\ntitle: Linked B\ntags:\n  - test\n---\n\n# Linked B\n\n"
        "Links back to [Linked A](linked_a.md).\n"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.usefixtures("_mcp_env")
class TestAppToolData:
    """Cover app-only tool Python paths for diff-cover."""

    async def test_browse_vault_with_path(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "browse_vault", {"path": "full_frontmatter.md", "view": "browse"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "full_frontmatter.md"
            assert data["view"] == "browse"
            assert "Frontmatter:" in data["summary"]

    async def test_browse_vault_no_path(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("browse_vault", {})
            data = _parse_tool_data(result)
            assert "Vault:" in data["summary"]

    async def test_show_context_tool(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("show_context", {"path": "simple.md"})
            data = _parse_tool_data(result)
            assert data["path"] == "simple.md"
            assert data["view"] == "context"
            assert "Backlinks:" in data["summary"]

    async def test_vault_graph_hubs(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
            assert "nodes" in data
            assert "edges" in data

    async def test_vault_list_root(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_list"), {})
            data = _parse_tool_data(result)
            assert "folders" in data
            assert "notes" in data
            # Root listing must only include root-level notes
            for note in data["notes"]:
                assert "/" not in note["path"]

    async def test_vault_read_note(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_read"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "simple.md"
            assert "content" in data

    async def test_vault_search_keyword(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_search"), {"query": "simple", "mode": "keyword"}
            )
            data = _parse_tool_data(result)
            assert isinstance(data, list)

    async def test_domain_http_path_no_leading_slash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://v.example.com")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_HTTP_PATH", "custom")
        result = _compute_claude_app_domain()
        assert result is not None
        assert result.endswith(".claudemcpcontent.com")

    async def test_vault_context_missing_path(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_context"), {"path": "does-not-exist.md"}
            )
            data = _parse_tool_data(result)
            assert "error" in data
            assert "not found" in data["error"].lower()

    async def test_show_context_missing_path(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                "show_context", {"path": "does-not-exist.md"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "does-not-exist.md"
            assert "not found" in data["summary"].lower()

    async def test_vault_search_semantic_no_embeddings(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_search"), {"query": "test", "mode": "semantic"}
            )
            data = _parse_tool_data(result)
            assert isinstance(data, list)
            assert len(data) == 1
            assert "error" in data[0]


@pytest.mark.usefixtures("_linked_env")
class TestAppToolLinkedData:
    """Cover graph traversal paths that require inter-note links."""

    async def test_vault_graph_neighborhood_with_links(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "linked_a.md", "depth": 2}
            )
            data = _parse_tool_data(result)
            assert "nodes" in data
            assert "edges" in data
            node_ids = [n["id"] for n in data["nodes"]]
            assert "linked_a.md" in node_ids
            assert len(data["edges"]) > 0

    async def test_vault_graph_neighborhood_dedup(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "linked_a.md", "depth": 2}
            )
            data = _parse_tool_data(result)
            edge_keys = [(e["from"], e["to"]) for e in data["edges"]]
            assert len(edge_keys) == len(set(edge_keys))

    async def test_vault_graph_hubs_with_links(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
            assert "nodes" in data
            assert "edges" in data
            if data["nodes"]:
                assert len(data["edges"]) > 0

    async def test_vault_context_with_links(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_context"), {"path": "linked_a.md"}
            )
            data = _parse_tool_data(result)
            assert data["path"] == "linked_a.md"
            assert "backlinks" in data
            assert "outlinks" in data
            assert len(data["outlinks"]) > 0

    async def test_show_context_with_tags(
        self, vault_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags")
        for var in _CLEAR_VARS:
            if var != "MARKDOWN_VAULT_MCP_INDEXED_FIELDS":
                monkeypatch.delenv(var, raising=False)
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool("show_context", {"path": "linked_b.md"})
            data = _parse_tool_data(result)
            assert "Tags:" in data["summary"]


class TestSPARewriteValidation:
    """Error-path coverage for `_rewrite_spa_app_tool_calls` and `_app_tool_meta`.

    These guards run at module-import time on production code paths and
    can't be exercised through the public MCP surface — direct unit
    tests are the only way to cover them.

    Regex matches are anchored to each production message's distinctive
    prefix so a future error string change in an adjacent branch can't
    silently satisfy the wrong assertion.
    """

    def test_rewrite_no_vault_calls_raises(self) -> None:
        with pytest.raises(RuntimeError, match=r"^SPA shell rewrite found zero "):
            _rewrite_spa_app_tool_calls("<html>no vault calls here</html>")

    def test_rewrite_unexpected_tool_raises(self) -> None:
        # Provide every declared name plus one typo so `missing` is empty
        # and only the `unexpected` branch can fire — isolates the test
        # from the production check ordering.
        all_valid = " ".join(f"vault___{n}" for n in _VAULT_APP_TOOL_NAMES)
        with pytest.raises(RuntimeError, match=r"^SPA references unknown vault tools:"):
            _rewrite_spa_app_tool_calls(f"{all_valid} vault___vault_typo_unknown")

    def test_rewrite_missing_declared_tool_raises(self) -> None:
        # The "missing" branch only fires when at least 2 tools are declared.
        # Assert that precondition explicitly so a future single-tool refactor
        # produces a clear error message instead of a mysterious "expected
        # RuntimeError, got nothing".
        assert len(_VAULT_APP_TOOL_NAMES) >= 2, (
            "test invariant: needs >=2 declared tools to trigger the missing branch"
        )
        name = sorted(_VAULT_APP_TOOL_NAMES)[0]
        with pytest.raises(
            RuntimeError, match=r"^SPA HTML doesn't reference declared vault tools:"
        ):
            _rewrite_spa_app_tool_calls(f"vault___{name}")

    def test_app_tool_meta_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match=r"^Unknown vault app tool "):
            _app_tool_meta("vault_unknown_tool")
