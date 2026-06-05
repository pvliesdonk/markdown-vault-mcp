"""Unit tests for IndexFacet (facade-decomposition PR3a, issue #604)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.facets.index import IndexFacet

if TYPE_CHECKING:
    from markdown_vault_mcp.vault import Vault


class TestIndexFacetAccessor:
    def test_accessor_returns_index_facet(self, built: Vault) -> None:
        assert isinstance(built.index, IndexFacet)

    def test_accessor_is_stable(self, built: Vault) -> None:
        assert built.index is built.index


class TestIndexFacetBehaviour:
    def test_is_queryable_after_build(self, built: Vault) -> None:
        assert built.index.is_queryable() is True

    def test_get_index_status_reports_queryable(self, built: Vault) -> None:
        assert built.index.get_index_status()["status"] == "queryable"

    def test_is_drained_after_build(self, built: Vault) -> None:
        assert built.index.is_drained() is True

    def test_write_generation_is_int(self, built: Vault) -> None:
        assert isinstance(built.index.write_generation(), int)

    def test_reindex_runs(self, built: Vault) -> None:
        # ReindexResult; just exercise the delegation end to end.
        result = built.index.reindex()
        assert result is not None

    def test_embeddings_status_returns_dict(self, built: Vault) -> None:
        assert isinstance(built.index.embeddings_status(), dict)


class TestIndexFacetEncapsulation:
    def test_hides_coordinator_internals(self, built: Vault) -> None:
        """The wrapper must NOT surface the coordinator's internal methods."""
        for internal in (
            "close",
            "writer",
            "require_built",
            "mark_paths_dirty",
            "rebuild_embeddings",
        ):
            assert not hasattr(built.index, internal), internal
