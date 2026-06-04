"""Unit tests for ReaderFacet (facade-decomposition PR3a, issue #604)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.facets.reader import ReaderFacet
from markdown_vault_mcp.types import CollectionStats, NoteContext

if TYPE_CHECKING:
    from pathlib import Path


class TestReaderFacetAccessor:
    def test_accessor_returns_reader_facet(self, built: Collection) -> None:
        assert isinstance(built.reader, ReaderFacet)

    def test_accessor_is_stable(self, built: Collection) -> None:
        assert built.reader is built.reader


class TestReaderFacetBehaviour:
    def test_search_returns_list(self, built: Collection) -> None:
        assert isinstance(built.reader.search("frontmatter"), list)

    def test_read_existing_document(self, built: Collection) -> None:
        note = built.reader.read("full_frontmatter.md")
        assert note is not None
        assert note.path == "full_frontmatter.md"

    def test_read_missing_returns_none(self, built: Collection) -> None:
        assert built.reader.read("does/not/exist.md") is None

    def test_list_documents_returns_list(self, built: Collection) -> None:
        assert isinstance(built.reader.list_documents(), list)

    def test_list_folders_returns_list(self, built: Collection) -> None:
        assert isinstance(built.reader.list_folders(), list)

    def test_list_tags_returns_list(self, built: Collection) -> None:
        assert isinstance(built.reader.list_tags(), list)

    def test_get_recent_returns_list(self, built: Collection) -> None:
        assert isinstance(built.reader.get_recent(), list)

    def test_get_toc_returns_list(self, built: Collection) -> None:
        assert isinstance(built.reader.get_toc("full_frontmatter.md"), list)

    def test_get_similar_empty_without_embeddings(self, built: Collection) -> None:
        # No embedding provider configured -> semantic similarity degrades to [].
        assert built.reader.get_similar("full_frontmatter.md") == []

    def test_get_context_returns_note_context(self, built: Collection) -> None:
        ctx = built.reader.get_context("full_frontmatter.md")
        assert isinstance(ctx, NoteContext)
        assert ctx.path == "full_frontmatter.md"

    def test_stats_returns_collection_stats(self, built: Collection) -> None:
        stats = built.reader.stats()
        assert isinstance(stats, CollectionStats)
        assert stats.document_count >= 1

    def test_get_history_empty_without_git(self, built: Collection) -> None:
        # vault fixture is not a git repo -> git strategy is None -> [].
        assert built.reader.get_history() == []

    def test_get_diff_empty_without_git(self, built: Collection) -> None:
        # No git strategy -> empty string before any argument validation.
        assert built.reader.get_diff("full_frontmatter.md") == ""


class TestReaderFacetAttachments:
    """Writable-collection attachment round-trip; owns its own lifecycle."""

    def test_read_attachment_round_trips(self, vault_path: Path) -> None:
        col = Collection(
            source_dir=vault_path, read_only=False, attachment_extensions=["*"]
        )
        col.index.build_index()
        try:
            col.writer.write_attachment("facet_read.bin", b"\x01\x02\x03")
            att = col.reader.read_attachment("facet_read.bin")
            assert att.path == "facet_read.bin"
            assert att.size_bytes == 3
        finally:
            col.close()


class TestReaderFacetReadinessGate:
    def test_bucket3_methods_raise_on_cold_index(self, vault_path: Path) -> None:
        col = Collection(source_dir=vault_path)  # never built
        try:
            with pytest.raises(IndexUnavailableError):
                col.reader.get_toc("full_frontmatter.md")
            with pytest.raises(IndexUnavailableError):
                col.reader.get_similar("full_frontmatter.md")
            with pytest.raises(IndexUnavailableError):
                col.reader.get_context("full_frontmatter.md")
        finally:
            col.close()
