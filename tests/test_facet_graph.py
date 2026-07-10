"""Unit tests for GraphFacet (facade-decomposition PR3a, issue #604)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.facets.graph import GraphFacet
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path


class TestGraphFacetAccessor:
    def test_accessor_returns_graph_facet(self, built: Vault) -> None:
        assert isinstance(built.graph, GraphFacet)

    def test_accessor_is_stable(self, built: Vault) -> None:
        assert built.graph is built.graph


class TestGraphFacetBehaviour:
    def test_backlinks_returns_list(self, built: Vault) -> None:
        assert isinstance(built.graph.get_backlinks("full_frontmatter.md"), list)

    def test_outlinks_returns_list(self, built: Vault) -> None:
        assert isinstance(built.graph.get_outlinks("full_frontmatter.md"), list)

    def test_broken_links_returns_list(self, built: Vault) -> None:
        assert isinstance(built.graph.get_broken_links(), list)

    def test_orphan_notes_returns_list(self, built: Vault) -> None:
        assert isinstance(built.graph.get_orphan_notes(), list)

    def test_most_linked_returns_list(self, built: Vault) -> None:
        assert isinstance(built.graph.get_most_linked(), list)

    def test_connection_path_returns_none_or_list(self, built: Vault) -> None:
        result = built.graph.get_connection_path("full_frontmatter.md", "simple.md")
        assert result is None or isinstance(result, list)


class TestGraphFacetReadinessGate:
    def test_bucket3_methods_raise_on_cold_index(self, vault_path: Path) -> None:
        col = Vault(source_dir=vault_path)  # never built
        try:
            with pytest.raises(IndexUnavailableError):
                col.graph.get_backlinks("full_frontmatter.md")
            with pytest.raises(IndexUnavailableError):
                col.graph.get_outlinks("full_frontmatter.md")
            with pytest.raises(IndexUnavailableError):
                col.graph.get_connection_path("full_frontmatter.md", "simple.md")
        finally:
            col.close()


class TestGraphFacetLimit:
    """``limit`` caps backlinks/outlinks (forwarded to LinkManager); built
    inline since the shared vault fixture has no multi-link document."""

    def test_limit_caps_backlinks_and_outlinks(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "target.md").write_text("# Target\n", encoding="utf-8")
        (vault / "a.md").write_text(
            "# A\n\nSee [t](target.md) and [b](b.md).\n", encoding="utf-8"
        )
        (vault / "b.md").write_text("# B\n\nSee [t](target.md).\n", encoding="utf-8")
        col = Vault(source_dir=vault)
        col.index.build_index()
        try:
            # target.md has 2 backlinks (a.md, b.md); a.md has 2 outlinks.
            assert len(col.graph.get_backlinks("target.md")) == 2
            assert len(col.graph.get_backlinks("target.md", limit=1)) == 1
            assert len(col.graph.get_backlinks("target.md", limit=None)) == 2
            assert len(col.graph.get_outlinks("a.md")) == 2
            assert len(col.graph.get_outlinks("a.md", limit=1)) == 1
        finally:
            col.close()


class TestGraphViews:
    """Direct facet-level coverage of the graph-view assembly (#880)."""

    @staticmethod
    def _linked_vault(tmp_path: Path) -> Path:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "center.md").write_text(
            "# Center\n\nSee [a](a.md).\n", encoding="utf-8"
        )
        (vault / "a.md").write_text(
            "# A\n\nBack to [c](center.md).\n", encoding="utf-8"
        )
        (vault / "lonely.md").write_text("# Lonely\n\nno links\n", encoding="utf-8")
        return vault

    def test_neighborhood_nodes_edges_and_groups(self, tmp_path: Path) -> None:
        col = Vault(source_dir=self._linked_vault(tmp_path))
        col.index.build_index()
        try:
            view = col.graph.get_neighborhood("center.md", depth=1)
            ids = {n.id for n in view.nodes}
            assert "center.md" in ids
            assert "a.md" in ids
            by_id = {n.id: n for n in view.nodes}
            assert by_id["center.md"].group == "note"
            assert by_id["center.md"].label == "Center"
            edge_pairs = {(e.source, e.target) for e in view.edges}
            assert ("center.md", "a.md") in edge_pairs
            assert ("a.md", "center.md") in edge_pairs
            assert view.truncated is False
        finally:
            col.close()

    def test_neighborhood_orphan_group(self, tmp_path: Path) -> None:
        col = Vault(source_dir=self._linked_vault(tmp_path))
        col.index.build_index()
        try:
            view = col.graph.get_neighborhood("lonely.md", depth=1)
            by_id = {n.id: n for n in view.nodes}
            assert by_id["lonely.md"].group == "orphan"
            assert view.edges == []
        finally:
            col.close()

    def test_neighborhood_max_nodes_truncates(self, tmp_path: Path) -> None:
        col = Vault(source_dir=self._linked_vault(tmp_path))
        col.index.build_index()
        try:
            view = col.graph.get_neighborhood("center.md", depth=2, max_nodes=1)
            assert len(view.nodes) == 1
            assert view.truncated is True
        finally:
            col.close()

    def test_neighborhood_boundary_nodes_not_expanded(self, tmp_path: Path) -> None:
        col = Vault(source_dir=self._linked_vault(tmp_path))
        col.index.build_index()
        try:
            view = col.graph.get_neighborhood("center.md", depth=1)
            by_id = {n.id: n for n in view.nodes}
            # a.md sits at the depth boundary: present, but not expanded.
            assert by_id["a.md"].backlink_count == 0
        finally:
            col.close()

    def test_neighborhood_semantic_skipped_without_embeddings(
        self, tmp_path: Path
    ) -> None:
        col = Vault(source_dir=self._linked_vault(tmp_path))
        col.index.build_index()
        try:
            view = col.graph.get_neighborhood(
                "center.md", depth=1, include_semantic=True
            )
            assert all(e.link_type != "semantic" for e in view.edges)
        finally:
            col.close()

    def test_neighborhood_gates_on_cold_index(self, tmp_path: Path) -> None:
        # depth=0 + include_semantic never reaches the gated link queries;
        # the up-front gate must still surface the documented error (#891).
        col = Vault(source_dir=self._linked_vault(tmp_path))
        try:
            with pytest.raises(IndexUnavailableError):
                col.graph.get_neighborhood("center.md", depth=0, include_semantic=True)
        finally:
            col.close()

    def test_hub_graph_nodes_and_edges(self, tmp_path: Path) -> None:
        col = Vault(source_dir=self._linked_vault(tmp_path))
        col.index.build_index()
        try:
            view = col.graph.get_hub_graph(limit=5)
            by_id = {n.id: n for n in view.nodes}
            # center.md and a.md each have one backlink -> both are hubs.
            assert by_id["center.md"].group == "hub"
            assert by_id["center.md"].backlink_count == 1
            edge_pairs = {(e.source, e.target) for e in view.edges}
            assert ("a.md", "center.md") in edge_pairs
            assert view.truncated is False
        finally:
            col.close()
