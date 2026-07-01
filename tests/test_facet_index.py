"""Unit tests for IndexFacet (facade-decomposition PR3a, issue #604)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.facets.index import IndexFacet
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path


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

    def test_reindex_runs(self, vault_path: Path) -> None:
        # Own vault: reindex() mutates index state -> must not share `built` (#618).
        col = Vault(source_dir=vault_path)
        try:
            col.index.build_index()
            result = col.index.reindex()
            assert result is not None
        finally:
            col.close()

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


class TestIndexFacetSkippedFiles:
    """get_index_status surfaces skipped_files (#775)."""

    def test_clean_vault_has_empty_skipped_files(self, tmp_path: Path) -> None:
        # A fresh tmp_path (not the shared `built` fixture, which
        # deliberately includes malformed_yaml.md) has nothing to skip.
        (tmp_path / "good.md").write_text("---\ntitle: ok\n---\nbody", encoding="utf-8")
        col = Vault(source_dir=tmp_path)
        try:
            col.index.build_index()
            status = col.index.get_index_status()
        finally:
            col.close()
        assert status["skipped_files"] == []

    def test_reports_parse_skip(self, vault_path: Path) -> None:
        col = Vault(source_dir=vault_path)
        try:
            col.index.build_index()
            (vault_path / "bad.md").write_text(
                "---\ntitle: [unclosed\n---\n", encoding="utf-8"
            )
            col.index.reindex()
            status = col.index.get_index_status()
        finally:
            col.close()
        entries = {e["path"]: e for e in status["skipped_files"]}
        assert entries["bad.md"]["category"] == "parse_error"
        assert set(entries["bad.md"]) == {"path", "category", "detail"}
