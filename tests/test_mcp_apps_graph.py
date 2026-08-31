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
from tests.conftest import _CLEAR_VARS, get_app_html, wait_for_mcp_writer_drain
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path


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
        # Edge color/style by type
        assert "edgeStyle" in html
        # Orphan dashed border
        assert "borderDashes" in html
        # Edge styling applied via styleEdges
        assert "styleEdges" in html

    async def test_bfs_distance_computed(self) -> None:
        html = await get_app_html()
        assert "computeDepths" in html
        # BFS from the center over the current edge set.
        assert "graphCenterPath" in html

    async def test_node_roles_pills_and_dots(self) -> None:
        html = await get_app_html()
        assert "styleNodes" in html
        # Focus/neighbor nodes are box pills; distant nodes are dots.
        assert "'box'" in html
        assert "shapeProperties" in html
        # Folder colors preserved for node fills.
        assert "_folderColor" in html
        # Label gate: distant nodes get an empty label.
        assert "label: ''" in html or "label:''" in html

    async def test_focus_node_accent_pill(self) -> None:
        html = await get_app_html()
        assert "background: c.accent, border: c.accent" in html

    async def test_lod_zoom_reveal(self) -> None:
        html = await get_app_html()
        assert "network.on('zoom'" in html
        assert "LABEL_ZOOM_THRESHOLD" in html
        assert "1.35" in html

    async def test_lod_hover_reveal(self) -> None:
        html = await get_app_html()
        assert "network.on('hoverNode'" in html
        assert "network.on('blurNode'" in html
        assert "_hoveredId = params.node" in html
        assert "applyLOD" in html

    async def test_lod_graceful_font_floor(self) -> None:
        html = await get_app_html()
        # scaling.label acts as a graceful adjunct, not the mechanism.
        assert "drawThreshold: 6" in html

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

    async def test_graph_reads_paper_tokens(self) -> None:
        html = await get_app_html()
        assert "getColors" in html
        # Pin the actual getColors read sites, not the shared CSS tokens.
        assert "v('--accent'" in html
        assert "v('--edge'" in html

    async def test_graph_resolves_canvas_colors_via_probe(self) -> None:
        """#856: canvas node colors must be resolved to concrete rgb() through a
        probe element (assign ``var(token)`` to a real ``color`` property, read
        the computed value). Reading Paper tokens straight off documentElement
        via ``getPropertyValue`` returns the literal ``var(--color-...)`` for
        indirection tokens in older Chromium (Claude Desktop's Electron), which
        canvas fillStyle can't parse -> nodes paint black. ``probe`` is a
        view-specific name (not vendored-library text)."""
        html = await get_app_html()
        assert "probe.style.color" in html
        assert "getComputedStyle(probe)" in html

    async def test_graph_semantic_match_labelled(self) -> None:
        html = await get_app_html()
        # _semanticMatch drives the dashed-pill styling in styleNodes.
        assert "_semanticMatch" in html

    async def test_graph_edge_lod_fade(self) -> None:
        html = await get_app_html()
        # edgeStyle fades link edges once the farthest endpoint is >= depth 2.
        assert "depthMax >= 2" in html

    async def test_graph_hub_labels_always_visible(self) -> None:
        html = await get_app_html()
        # labelVisible must treat hub nodes as always-labelled.
        assert "n._group === 'hub'" in html

    async def test_lod_predicate_pinned(self) -> None:
        html = await get_app_html()
        # Pin the whole label-visibility decision so it can't be gutted silently.
        assert (
            "return d <= 1 || n._group === 'hub' || _zoomedIn || n.id === _hoveredId"
            in html
        )

    async def test_edge_lod_fade_outcome(self) -> None:
        html = await get_app_html()
        # Pin the fade itself, not just the `depthMax >= 2` threshold.
        assert "opacity: far ? 0.5 : 1" in html
        assert "width: far ? 1 : 1.6" in html

    async def test_semantic_match_dashed_pill_styling(self) -> None:
        html = await get_app_html()
        assert "borderDashes: [4, 3]" in html
        assert "background: c.accentSoft, border: c.accent" in html

    async def test_zoom_crossing_triggers_relabel(self) -> None:
        html = await get_app_html()
        assert "if (zoomed !== _zoomedIn) { _zoomedIn = zoomed; applyLOD(); }" in html

    async def test_legend_chip_toggle_wired(self) -> None:
        html = await get_app_html()
        assert "classList.toggle('open')" in html

    async def test_graph_dotted_grid_surface(self) -> None:
        html = await get_app_html()
        assert "radial-gradient" in html
        assert "#graph-container" in html

    async def test_zoom_control_overlay(self) -> None:
        html = await get_app_html()
        assert 'id="graph-zoom-in"' in html
        assert 'id="graph-zoom-out"' in html
        assert "network.moveTo" in html

    async def test_node_count_chip(self) -> None:
        html = await get_app_html()
        assert 'id="graph-count"' in html
        assert "updateCountChip" in html

    async def test_graph_legend_matches_drawing(self) -> None:
        html = await get_app_html()
        assert 'id="graph-legend"' in html
        # "semantic" and "more" also occur elsewhere in the bundle (Paper
        # semantic tokens, "N more properties"), so pin the exact legend
        # entry markup rather than the bare words.
        for entry_markup in (
            '<span class="lg-line lg-solid"></span>wikilink',
            '<span class="lg-line lg-dashed"></span>semantic',
            '<span class="lg-pill lg-focus"></span>focus note',
            '<span class="lg-pill lg-linked"></span>linked note',
            '<span class="lg-dot"></span>more (hover)',
        ):
            assert entry_markup in html

    async def test_graph_legend_collapse_chip(self) -> None:
        html = await get_app_html()
        assert 'id="graph-legend-chip"' in html
        assert "graph-legend-chip" in html
        assert "legend" in html


# ---------------------------------------------------------------------------
# Graph data tools
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_mcp_env")
class TestGraphDataTools:
    """Verify graph tools return valid node/edge structures."""

    async def test_neighborhood_returns_graph(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
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

    async def test_neighborhood_tolerates_oversize_node(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A node whose document exceeds MAX_NOTE_READ_BYTES must not crash the
        graph — node labels come from indexed metadata, not a size-capped read.

        Regression: the tool read each node's full document only for its
        title/folder, so a single oversize note raised ValueError and failed
        the whole tool call.
        """
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "10")
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"), {"path": "simple.md"}
            )
            data = _parse_tool_data(result)
        focus = next((n for n in data["nodes"] if n["id"] == "simple.md"), None)
        assert focus is not None, "focus node must be present despite the size cap"
        assert focus["label"], "label must come from metadata, not a failed read"

    async def test_neighborhood_with_depth(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
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
            # Drain the boot BuildIndex like every sibling test: the hubs
            # backlink path requires a built index, so calling before the
            # boot build lands races into IndexUnavailableError.
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
            assert "nodes" in data
            assert "edges" in data

    async def test_hubs_tolerates_oversize_backlink_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A backlink-source note over MAX_NOTE_READ_BYTES must not crash the hub
        graph — labels come from indexed metadata, not a size-capped read."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "10")
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
        assert "nodes" in data
        assert all(n["label"] for n in data["nodes"])

    async def test_hubs_does_not_read_hub_documents(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """vault_graph_hubs uses MostLinkedNote.folder, not a per-hub metadata fetch."""
        from markdown_vault_mcp.facets.reader import ReaderFacet

        original = ReaderFacet.get_metadata
        fetched: list[str] = []

        def _spy(self: ReaderFacet, path: str) -> Any:
            fetched.append(path)
            return original(self, path)

        monkeypatch.setattr(ReaderFacet, "get_metadata", _spy)
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
        hub_paths = {n["id"] for n in data["nodes"] if n["group"] == "hub"}
        assert hub_paths, "fixture should produce at least one hub"
        assert not (hub_paths & set(fetched)), (
            f"hub documents should not be fetched directly; fetched={fetched}"
        )

    async def test_hubs_reads_each_backlink_source_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#285: the parallelized hub fetch still de-duplicates per-source metadata
        lookups — a source backlinking several hubs is fetched at most once."""
        from markdown_vault_mcp.facets.reader import ReaderFacet

        original = ReaderFacet.get_metadata
        fetched: list[str] = []

        def _spy(self: ReaderFacet, path: str) -> Any:
            fetched.append(path)
            return original(self, path)

        monkeypatch.setattr(ReaderFacet, "get_metadata", _spy)
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool(_hashed("vault_graph_hubs"), {})
        assert len(fetched) == len(set(fetched)), (
            f"backlink sources should be fetched once each; fetched={fetched}"
        )

    async def test_hubs_tolerates_backlinks_valueerror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#285: a hub whose get_backlinks raises ValueError degrades to no
        connections rather than failing the whole graph."""
        from markdown_vault_mcp.facets.graph import GraphFacet

        def _boom(self: GraphFacet, path: str) -> Any:  # noqa: ARG001
            raise ValueError("no such note")

        monkeypatch.setattr(GraphFacet, "get_backlinks", _boom)
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(_hashed("vault_graph_hubs"), {})
            data = _parse_tool_data(result)
        assert "nodes" in data  # hubs still returned
        assert data["edges"] == []  # no backlinks resolved → no edges

    async def test_edges_have_type(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
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
            await wait_for_mcp_writer_drain(client)
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
            await wait_for_mcp_writer_drain(client)
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
            await wait_for_mcp_writer_drain(client)
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
            await wait_for_mcp_writer_drain(client)
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

    async def test_semantic_edge_is_dashed_accent_not_purple(self) -> None:
        html = await get_app_html()
        # The old purple semantic constant is gone; semantic reads as dashed accent.
        assert "#a855f7" not in html
        assert "_SEMANTIC_EDGE_COLOR" not in html
        assert "isSemantic" in html
        assert "dashes" in html

    async def test_semantic_edge_accent_color(self) -> None:
        html = await get_app_html()
        assert "color: c.accent, opacity: 0.75" in html

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

    async def test_graph_refreshes_on_theme_change(self) -> None:
        html = await get_app_html()
        # Pin the actual event binding (listener + dispatch), not just the
        # string, so a stub can't satisfy the test.
        assert "window.addEventListener('vault-theme-changed', refreshColors)" in html
        # Pins core.js's dispatch specifically, not only graph.js's listener.
        assert "new CustomEvent('vault-theme-changed'" in html

    async def test_semantic_toggle_active_is_accent(self) -> None:
        html = await get_app_html()
        # Pins the toggle wiring + the CSS rule (accent-soft alone is a
        # shared token used by other components).
        assert "classList.toggle('active', semanticEnabled)" in html
        assert ".action-btn.active {" in html


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
                await wait_for_mcp_writer_drain(client)
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

    async def test_semantic_tolerates_oversize_similar_node(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A similar note over MAX_NOTE_READ_BYTES must not crash semantic
        expansion — its label comes from indexed metadata, not a capped read."""
        from .conftest import MockEmbeddingProvider

        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(tmp_path / "embeddings")
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "10")
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=MockEmbeddingProvider(),
        ):
            server = make_server()
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {"path": "simple.md", "include_semantic": True},
                )
        data = _parse_tool_data(result)
        assert all(n["label"] for n in data["nodes"])

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
                await wait_for_mcp_writer_drain(client)
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
        import asyncio as _asyncio
        import json as _json

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
                # BuildEmbeddings runs on the writer FIFO; poll until chunks
                # are present so the semantic-neighborhood query sees them.
                for _ in range(50):
                    status_res = await client.call_tool_mcp("embeddings_status", {})
                    if (
                        _json.loads(status_res.content[0].text).get("chunk_count", 0)
                        > 0
                    ):
                        break
                    await _asyncio.sleep(0.1)
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
                "markdown_vault_mcp.managers.search.SearchManager.get_similar",
                side_effect=ValueError("not found"),
            ),
        ):
            server = make_server()
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
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
                "markdown_vault_mcp.managers.search.SearchManager.get_similar",
                side_effect=RuntimeError("embedding backend unavailable"),
            ),
        ):
            server = make_server()
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {"path": "simple.md", "include_semantic": True},
                )
        data = _parse_tool_data(result)
        assert "nodes" in data
        assert "edges" in data
        semantic_edges = [e for e in data["edges"] if e.get("type") == "semantic"]
        assert semantic_edges == []


# ---------------------------------------------------------------------------
# max_nodes BFS cap
# ---------------------------------------------------------------------------


@pytest.fixture
def _star_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Star-pattern vault: hub.md links to 20 spokes; each spoke links back.
    vault = tmp_path / "star_vault"
    vault.mkdir()
    spokes = "\n".join(f"- [s{i}](spoke{i}.md)" for i in range(20))
    (vault / "hub.md").write_text(f"# Hub\n\n{spokes}\n")
    for i in range(20):
        (vault / f"spoke{i}.md").write_text(f"# Spoke {i}\n\n[hub](hub.md)\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    return vault


class TestGraphNeighborhoodMaxNodes:
    """Verify max_nodes caps BFS output and sets the truncated flag."""

    async def test_max_nodes_caps_node_count(self, _star_vault: Path) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"),
                {"path": "hub.md", "depth": 2, "max_nodes": 5},
            )
        data = _parse_tool_data(result)
        assert len(data["nodes"]) <= 5
        assert data["truncated"] is True

    async def test_truncated_false_when_under_cap(self, _star_vault: Path) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"),
                {"path": "hub.md", "depth": 2, "max_nodes": 500},
            )
        data = _parse_tool_data(result)
        assert data["truncated"] is False

    async def test_max_nodes_caps_semantic_expansion(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """max_nodes also bounds the semantic-expansion phase, not just BFS."""
        from .conftest import MockEmbeddingProvider

        # Star vault: forces BFS to hit the cap; semantic phase must not bypass it
        vault = tmp_path / "sem_star"
        vault.mkdir()
        spokes = "\n".join(f"- [s{i}](spoke{i}.md)" for i in range(20))
        (vault / "hub.md").write_text(f"# Hub\n\n{spokes}\n")
        for i in range(20):
            (vault / f"spoke{i}.md").write_text(f"# Spoke {i}\n\n[hub](hub.md)\n")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(tmp_path / "embeddings")
        )
        for var in _CLEAR_VARS:
            if var != "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH":
                monkeypatch.delenv(var, raising=False)

        mock_prov = MockEmbeddingProvider()
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = make_server()
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {
                        "path": "hub.md",
                        "depth": 2,
                        "max_nodes": 5,
                        "include_semantic": True,
                    },
                )
        data = _parse_tool_data(result)
        assert len(data["nodes"]) <= 5
        assert data["truncated"] is True

    async def test_max_nodes_caps_semantic_inner_branch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Inner semantic-cap fires when expansion fills the cap mid-iteration (PR #478)."""
        from .conftest import MockEmbeddingProvider

        # Vault forces the *inner* cap branch in _vault_graph_neighborhood:
        # BFS from center returns {center, B} = 2 nodes (below cap=4).
        # Semantic expansion for `center` then adds enough fresh candidates
        # to reach the cap mid-loop; the next non-member candidate must
        # trigger the inner ``len(nodes) >= max_nodes`` guard (lines 564-566).
        vault = tmp_path / "sem_inner"
        vault.mkdir()
        # center links only to B (BFS yields exactly {center, B} at depth=1)
        (vault / "center.md").write_text("# Center\n\n[B](B.md)\n")
        (vault / "B.md").write_text("# B\n\n[center](center.md)\n")
        # Four semantic-only notes (unlinked from center/B). With max_nodes=4
        # and nodes already at 2, the loop adds two and the third trips the
        # inner cap regardless of MockEmbeddingProvider's similarity ordering.
        for label in ("C", "D", "E", "F"):
            (vault / f"{label}.md").write_text(
                f"# {label}\n\nstandalone note {label}\n"
            )

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(tmp_path / "embeddings")
        )
        for var in _CLEAR_VARS:
            if var != "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH":
                monkeypatch.delenv(var, raising=False)

        mock_prov = MockEmbeddingProvider()
        with patch(
            "markdown_vault_mcp.providers.get_embedding_provider",
            return_value=mock_prov,
        ):
            server = make_server()
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
                result = await client.call_tool(
                    _hashed("vault_graph_neighborhood"),
                    {
                        "path": "center.md",
                        "depth": 1,
                        "max_nodes": 4,
                        "include_semantic": True,
                    },
                )
        data = _parse_tool_data(result)
        assert len(data["nodes"]) == 4
        assert data["truncated"] is True
        # Inner branch fired: at least one semantic candidate was rejected
        # because the cap was reached mid-loop, so not every standalone
        # note (C/D/E/F) ended up in the result set.
        node_ids = {n["id"] for n in data["nodes"]}
        standalone = {"C.md", "D.md", "E.md", "F.md"}
        assert len(standalone & node_ids) < len(standalone)


@pytest.mark.usefixtures("_mcp_env")
class TestGraphNeighborhoodMaxNodesDefault:
    """Default max_nodes preserves prior behavior on small fixture vault."""

    async def test_default_does_not_truncate_small_vault(self) -> None:
        server = make_server()
        async with Client(server) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                _hashed("vault_graph_neighborhood"),
                {"path": "simple.md", "depth": 2},
            )
        data = _parse_tool_data(result)
        assert data["truncated"] is False
        assert len(data["nodes"]) < 200
