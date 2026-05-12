"""Tests for the Graph Explorer MCP App view.

Covers issue #275: vis-network integration, _vault_graph_neighborhood and
_vault_graph_hubs tools, and HTML graph view content.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_apps import _hashed
from markdown_vault_mcp.server import make_server
from tests.conftest import get_app_html

if TYPE_CHECKING:
    from pathlib import Path


_CLEAR_VARS = (
    "MARKDOWN_VAULT_MCP_INDEX_PATH",
    "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
    "MARKDOWN_VAULT_MCP_STATE_PATH",
    "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
    "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
    "MARKDOWN_VAULT_MCP_EXCLUDE",
    "MARKDOWN_VAULT_MCP_GIT_TOKEN",
    "MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER",
    "MARKDOWN_VAULT_MCP_SERVER_NAME",
    "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_AUTH_MODE",
    "MARKDOWN_VAULT_MCP_BASE_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
    "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
    "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
    "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
    "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
    "MARKDOWN_VAULT_MCP_APP_DOMAIN",
)


@pytest.fixture
def _mcp_env(vault_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


def _parse_tool_data(result: Any) -> Any:
    data = result.data
    if isinstance(data, list) and data and not isinstance(data[0], (dict, str)):
        raw = result.content[0].text if result.content else "[]"
        return json.loads(raw)
    return data


# ---------------------------------------------------------------------------
# Graph HTML content
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestGraphExplorerHTML:
    """Verify graph explorer elements exist in the SPA HTML."""

    async def test_vis_network_vendored(self) -> None:
        html = await get_app_html()
        assert "vis-network@" in html
        assert "(vendored)" in html

    async def test_graph_container(self) -> None:
        html = await get_app_html()
        assert 'id="graph-container"' in html

    async def test_vis_network_initialization(self) -> None:
        html = await get_app_html()
        assert "vis.Network" in html
        assert "vis.DataSet" in html

    async def test_click_handler(self) -> None:
        html = await get_app_html()
        assert "network.on('click'" in html

    async def test_hover_tooltip(self) -> None:
        html = await get_app_html()
        # Tooltip via node.title property
        assert "tooltipDelay" in html

    async def test_double_click_handler(self) -> None:
        html = await get_app_html()
        assert "doubleClick" in html
        # Double-click triggers focus mode (clear + reload for this node only)
        assert "loadGraph(nodeId)" in html

    async def test_dynamic_expansion(self) -> None:
        html = await get_app_html()
        assert "expandNode" in html
        assert _hashed("vault_graph_neighborhood") in html

    async def test_hub_view(self) -> None:
        html = await get_app_html()
        assert _hashed("vault_graph_hubs") in html
        assert "loadHubs" in html

    async def test_node_visual_encoding(self) -> None:
        html = await get_app_html()
        # Node size proportional to backlink_count via value
        assert "backlink_count" in html
        # Edge color by type
        assert "edgeColorByType" in html
        # Orphan dashed border
        assert "borderDashes" in html

    async def test_send_to_claude_button(self) -> None:
        html = await get_app_html()
        assert 'id="graph-send-btn"' in html

    async def test_fullscreen_button(self) -> None:
        html = await get_app_html()
        assert 'id="graph-fullscreen-btn"' in html

    async def test_mini_context_card(self) -> None:
        html = await get_app_html()
        assert 'id="graph-mini-card"' in html
        assert "showMiniCard" in html
        assert "Full Context" in html
        assert "Open in Browser" in html

    async def test_xss_protection_eschtml(self) -> None:
        html = await get_app_html()
        assert "escHtml" in html

    async def test_cdn_crash_guard(self) -> None:
        html = await get_app_html()
        # loadGraph must check nodesDS before calling clear()
        assert "vis CDN failed" in html or "!nodesDS" in html

    async def test_host_css_variables_in_graph(self) -> None:
        html = await get_app_html()
        assert "getColors" in html
        assert "--color-text-info" in html


# ---------------------------------------------------------------------------
# Graph data tools
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestGraphDataTools:
    """Verify graph tools return valid node/edge structures."""

    async def test_neighborhood_returns_graph(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            assert "nodes" in data
            assert "edges" in data
            node_ids = [n["id"] for n in data["nodes"]]
            assert "simple.md" in node_ids
            for node in data["nodes"]:
                assert "id" in node
                assert "label" in node
                assert "group" in node
                assert "folder" in node

    async def test_neighborhood_with_depth(self) -> None:
        server = make_server()
        async with Client(server) as client:
            r1 = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md", "depth": 1}
            )
            d1 = _parse_tool_data(r1)
            r2 = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md", "depth": 2}
            )
            d2 = _parse_tool_data(r2)
            assert "nodes" in d2
            # depth=2 should return at least as many nodes as depth=1
            assert len(d2["nodes"]) >= len(d1["nodes"])

    async def test_hubs_returns_graph(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
            assert "nodes" in data
            assert "edges" in data

    async def test_hubs_does_not_read_hub_documents(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """vault_graph_hubs uses MostLinkedNote.folder, not per-hub collection.read."""
        from markdown_vault_mcp.collection import Collection

        original_read = Collection.read
        read_paths: list[str] = []

        def _spy(self: Collection, path: str) -> Any:
            read_paths.append(path)
            return original_read(self, path)

        monkeypatch.setattr(Collection, "read", _spy)
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
        hub_paths = {n["id"] for n in data["nodes"] if n["group"] == "hub"}
        assert hub_paths, "fixture should produce at least one hub"
        assert not (hub_paths & set(read_paths)), (
            f"hub documents should not be read directly; read={read_paths}"
        )

    async def test_edges_have_type(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            for edge in data["edges"]:
                assert "from" in edge
                assert "to" in edge
                assert "type" in edge

    async def test_neighborhood_nodes_have_backlink_count(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            for node in data["nodes"]:
                assert "backlink_count" in node
                assert isinstance(node["backlink_count"], int)

    async def test_edges_deduplicated(self) -> None:
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            edge_keys = [(e["from"], e["to"]) for e in data["edges"]]
            assert len(edge_keys) == len(set(edge_keys))

    async def test_include_semantic_false_by_default(self) -> None:
        """Default call returns no semantic edges."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
            semantic_edges = [e for e in data["edges"] if e.get("type") == "semantic"]
            assert semantic_edges == []

    async def test_include_semantic_true_no_embeddings(self) -> None:
        """include_semantic=True without embeddings returns graph without semantic edges."""
        server = make_server()
        async with Client(server) as client:
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"),
                {"path": "simple.md", "include_semantic": True},
            )
            data = _parse_tool_data(result)
            # Without embeddings configured, get_similar returns [] — no crash
            assert "nodes" in data
            assert "edges" in data
            # All edge types are explicit link types, not semantic
            for edge in data["edges"]:
                assert edge.get("type") != "semantic"


# ---------------------------------------------------------------------------
# Semantic graph HTML checks
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestSemanticGraphHTML:
    """Verify semantic similarity graph features in the SPA HTML."""

    async def test_semantic_toggle_button(self) -> None:
        html = await get_app_html()
        assert 'id="graph-semantic-btn"' in html

    async def test_include_semantic_passed_to_tool(self) -> None:
        html = await get_app_html()
        assert "include_semantic" in html
        assert "semanticEnabled" in html

    async def test_semantic_edge_color_constant(self) -> None:
        html = await get_app_html()
        assert "_SEMANTIC_EDGE_COLOR" in html

    async def test_semantic_edge_dashed(self) -> None:
        html = await get_app_html()
        # Semantic edges rendered as dashed lines
        assert "isSemantic" in html
        assert "dashes" in html

    async def test_folder_color_palette(self) -> None:
        html = await get_app_html()
        assert "_FOLDER_COLORS" in html
        assert "_folderColor" in html

    async def test_cross_view_currentpath(self) -> None:
        html = await get_app_html()
        assert "currentPath" in html


# ---------------------------------------------------------------------------
# Semantic edges with embeddings enabled
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestIncludeSemanticEdges:
    """Verify _vault_graph_neighborhood semantic edges with embeddings configured."""

    async def test_semantic_edges_returned_with_embeddings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """include_semantic=True with embeddings configured adds semantic edges."""
        from .conftest import MockEmbeddingProvider

        embeddings_path = str(tmp_path / "embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", embeddings_path)

        mock_prov = MockEmbeddingProvider()
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = make_server()
            async with Client(server) as client:
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {"path": "simple.md", "include_semantic": True},
                )
        data = _parse_tool_data(result)
        assert "nodes" in data
        assert "edges" in data
        semantic_edges = [e for e in data["edges"] if e.get("type") == "semantic"]
        # With embeddings configured the vault has similar notes — at least one
        # semantic edge should appear
        assert len(semantic_edges) > 0
        for edge in semantic_edges:
            assert "from" in edge
            assert "to" in edge
            assert edge["from"] != edge["to"]

    async def test_semantic_edges_no_duplicate_pairs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Semantic edges are deduplicated — A↔B appears only once."""
        from .conftest import MockEmbeddingProvider

        embeddings_path = str(tmp_path / "embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", embeddings_path)

        mock_prov = MockEmbeddingProvider()
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = make_server()
            async with Client(server) as client:
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {"path": "simple.md", "include_semantic": True},
                )
        data = _parse_tool_data(result)
        sem_pairs = [
            frozenset({e["from"], e["to"]})
            for e in data["edges"]
            if e.get("type") == "semantic"
        ]
        assert len(sem_pairs) == len(set(sem_pairs))

    async def test_semantic_adds_nodes_outside_neighborhood(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With depth=0 only the center node is in the graph; similar notes are
        added as new nodes (exercises the `if sr.path not in nodes` branch)."""
        from .conftest import MockEmbeddingProvider

        embeddings_path = str(tmp_path / "embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", embeddings_path)

        mock_prov = MockEmbeddingProvider()
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = make_server()
            async with Client(server) as client:
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {"path": "simple.md", "depth": 0, "include_semantic": True},
                )
        data = _parse_tool_data(result)
        node_ids = {n["id"] for n in data["nodes"]}
        # Semantic similar notes must have been added beyond the center node
        assert len(node_ids) > 1
        semantic_edges = [e for e in data["edges"] if e.get("type") == "semantic"]
        assert len(semantic_edges) > 0

    async def test_semantic_handles_value_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ValueError from get_similar is silently ignored (exercises except ValueError branch)."""
        from .conftest import MockEmbeddingProvider

        embeddings_path = str(tmp_path / "embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", embeddings_path)

        mock_prov = MockEmbeddingProvider()
        with (
            patch(
                "markdown_vault_mcp.providers.get_embedding_provider",
                return_value=mock_prov,
            ),
            patch(
                "markdown_vault_mcp.collection.Collection.get_similar",
                side_effect=ValueError("not found"),
            ),
        ):
            server = make_server()
            async with Client(server) as client:
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {"path": "simple.md", "include_semantic": True},
                )
        data = _parse_tool_data(result)
        assert "nodes" in data
        assert "edges" in data
        semantic_edges = [e for e in data["edges"] if e.get("type") == "semantic"]
        assert semantic_edges == []

    async def test_semantic_handles_unexpected_exception(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unexpected exceptions from get_similar are logged and skipped
        (exercises except Exception branch)."""
        from .conftest import MockEmbeddingProvider

        embeddings_path = str(tmp_path / "embeddings")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", embeddings_path)

        mock_prov = MockEmbeddingProvider()
        with (
            patch(
                "markdown_vault_mcp.providers.get_embedding_provider",
                return_value=mock_prov,
            ),
            patch(
                "markdown_vault_mcp.collection.Collection.get_similar",
                side_effect=RuntimeError("embedding backend unavailable"),
            ),
        ):
            server = make_server()
            async with Client(server) as client:
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {"path": "simple.md", "include_semantic": True},
                )
        data = _parse_tool_data(result)
        assert "nodes" in data
        assert "edges" in data
        semantic_edges = [e for e in data["edges"] if e.get("type") == "semantic"]
        assert semantic_edges == []
