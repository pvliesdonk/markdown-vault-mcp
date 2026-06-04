"""Unit tests for GraphFacet (facade-decomposition PR3a, issue #604)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.facets.graph import GraphFacet

if TYPE_CHECKING:
    from pathlib import Path


class TestGraphFacetAccessor:
    def test_accessor_returns_graph_facet(self, built: Collection) -> None:
        assert isinstance(built.graph, GraphFacet)

    def test_accessor_is_stable(self, built: Collection) -> None:
        assert built.graph is built.graph


class TestGraphFacetBehaviour:
    def test_backlinks_returns_list(self, built: Collection) -> None:
        assert isinstance(built.graph.get_backlinks("full_frontmatter.md"), list)

    def test_outlinks_returns_list(self, built: Collection) -> None:
        assert isinstance(built.graph.get_outlinks("full_frontmatter.md"), list)

    def test_broken_links_returns_list(self, built: Collection) -> None:
        assert isinstance(built.graph.get_broken_links(), list)

    def test_orphan_notes_returns_list(self, built: Collection) -> None:
        assert isinstance(built.graph.get_orphan_notes(), list)

    def test_most_linked_returns_list(self, built: Collection) -> None:
        assert isinstance(built.graph.get_most_linked(), list)

    def test_connection_path_returns_none_or_list(self, built: Collection) -> None:
        result = built.graph.get_connection_path("full_frontmatter.md", "simple.md")
        assert result is None or isinstance(result, list)


class TestGraphFacetReadinessGate:
    def test_bucket3_methods_raise_on_cold_index(self, vault_path: Path) -> None:
        col = Collection(source_dir=vault_path)  # never built
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
        col = Collection(source_dir=vault)
        col.build_index()
        try:
            # target.md has 2 backlinks (a.md, b.md); a.md has 2 outlinks.
            assert len(col.graph.get_backlinks("target.md")) == 2
            assert len(col.graph.get_backlinks("target.md", limit=1)) == 1
            assert len(col.graph.get_backlinks("target.md", limit=None)) == 2
            assert len(col.graph.get_outlinks("a.md")) == 2
            assert len(col.graph.get_outlinks("a.md", limit=1)) == 1
        finally:
            col.close()
