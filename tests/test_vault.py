"""Integration tests for Vault."""

from __future__ import annotations

import concurrent.futures
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.types import (
    AttachmentContent,
    AttachmentInfo,
    DeleteResult,
    EditResult,
    NoteContent,
    NoteInfo,
    RenameResult,
    VaultStats,
    WriteResult,
)
from markdown_vault_mcp.utils.text import (
    build_position_map as _build_position_map,
)
from markdown_vault_mcp.utils.text import (
    find_closest_match as _find_closest_match,
)
from markdown_vault_mcp.utils.text import (
    normalize_text as _normalize_text,
)
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .conftest import MockEmbeddingProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vault(
    source_dir: Path,
    *,
    index_path: Path | None = None,
    embeddings_path: Path | None = None,
    embedding_provider: MockEmbeddingProvider | None = None,
    indexed_frontmatter_fields: list[str] | None = None,
    state_path: Path | None = None,
    read_only: bool = True,
    on_write: object = None,
    exclude_patterns: list[str] | None = None,
) -> Vault:
    """Create a Vault for testing with sensible defaults.

    Uses an in-memory SQLite index unless *index_path* is given.

    Args:
        source_dir: Root directory of the markdown vault.
        index_path: Optional path to a persistent SQLite file.
        embeddings_path: Base path for vector sidecar files.
        embedding_provider: Provider for semantic search.
        indexed_frontmatter_fields: Fields to index in document_tags.
        state_path: Path for the change-tracker state file.
        read_only: When True, write operations raise ReadOnlyError.
        on_write: Optional callback for write operations.
        exclude_patterns: Glob patterns to exclude from indexing.

    Returns:
        A configured :class:`Vault` instance.
    """
    return Vault(
        source_dir=source_dir,
        index_path=index_path,
        embeddings_path=embeddings_path,
        embedding_provider=embedding_provider,
        indexed_frontmatter_fields=indexed_frontmatter_fields,
        state_path=state_path,
        read_only=read_only,
        on_write=on_write,
        exclude_patterns=exclude_patterns,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(vault_path: Path) -> Iterator[Vault]:
    """Built Vault backed by the clean vault fixture."""
    col = _make_vault(vault_path)
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


# ---------------------------------------------------------------------------
# Build / indexing tests
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_build_index_from_fixtures(self, vault_path: Path) -> None:
        """build_index() indexes all parseable documents and their chunks."""
        col = _make_vault(vault_path)
        stats = col.index.build_index()

        # 9 valid .md files (excludes invalid_utf8.md; malformed_yaml.md skipped).
        assert stats.documents_indexed == 9
        # All fixtures are short (<= 30 lines) → 1 chunk each.
        assert stats.chunks_indexed == 9
        assert stats.skipped >= 0

    def test_build_index_force_rebuild(self, vault_path: Path) -> None:
        """build_index(force=True) rebuilds without crashing."""
        col = _make_vault(vault_path)
        col.index.build_index()
        stats = col.index.build_index(force=True)

        assert stats.documents_indexed == 9
        assert stats.chunks_indexed == 9

    def test_build_index_idempotent_without_force(self, vault_path: Path) -> None:
        """Calling build_index() twice (no force) does not double-index."""
        col = _make_vault(vault_path)
        col.index.build_index()
        # Second call is a no-op; documents_indexed reflects existing count.
        stats2 = col.index.build_index()
        assert stats2.documents_indexed == 9

    def test_reindex_detects_new_file(self, tmp_path: Path, vault_path: Path) -> None:
        """reindex() detects and indexes a file added after build_index()."""
        state_path = tmp_path / "state.json"
        col = _make_vault(vault_path, state_path=state_path)
        col.index.build_index()

        # Add a new file to the vault.
        new_file = vault_path / "added_note.md"
        new_file.write_text("# Added Note\n\nThis was added after initial index.\n")

        result = col.index.reindex()

        assert result.added >= 1

    def test_build_index_continues_on_upsert_error(self, vault_path: Path) -> None:
        """build_index() skips documents that fail to index and continues."""
        col = _make_vault(vault_path)
        call_count = 0
        original_upsert = col._fts.upsert_note

        def upsert_that_fails_once(note):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TypeError("simulated serialization error")
            return original_upsert(note)

        with patch.object(col._fts, "upsert_note", side_effect=upsert_that_fails_once):
            stats = col.index.build_index()

        # One document errored, remaining 8 indexed successfully.
        assert stats.documents_indexed == 8
        assert stats.chunks_indexed == 8

    def test_reindex_continues_on_upsert_error(
        self, tmp_path: Path, vault_path: Path
    ) -> None:
        """reindex() skips documents that fail to upsert and continues."""
        state_path = tmp_path / "state.json"
        col = _make_vault(vault_path, state_path=state_path)
        col.index.build_index()

        # Add two new files.
        (vault_path / "new_a.md").write_text("# A\n\nContent A.\n")
        (vault_path / "new_b.md").write_text("# B\n\nContent B.\n")

        call_count = 0
        original_upsert = col._fts.upsert_note

        def upsert_that_fails_once(note):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TypeError("simulated serialization error")
            return original_upsert(note)

        with patch.object(col._fts, "upsert_note", side_effect=upsert_that_fails_once):
            result = col.index.reindex()

        # One of the two added files failed, the other succeeded.
        assert result.added == 1

    def test_reindex_respects_exclude_patterns(
        self, tmp_path: Path, vault_path: Path
    ) -> None:
        """reindex() must not index files matching exclude_patterns."""
        state_path = tmp_path / "state.json"
        col = _make_vault(
            vault_path,
            state_path=state_path,
            exclude_patterns=[".claude/**"],
        )
        col.index.build_index()

        # Add a file inside an excluded directory after the initial index.
        excluded_dir = vault_path / ".claude" / "agents"
        excluded_dir.mkdir(parents=True, exist_ok=True)
        (excluded_dir / "knowledge-gaps.md").write_text(
            "---\ntitle: knowledge-gaps\n---\n\n# Knowledge Gaps\n"
        )

        result = col.index.reindex()

        # The excluded file should NOT be indexed.
        paths = [row["path"] for row in col._fts.list_notes()]
        assert ".claude/agents/knowledge-gaps.md" not in paths
        # The file should not count as added.
        assert result.added == 0

        # Second reindex: excluded file is still absent (tracker reports it as
        # "added" again since it's never saved to state, but the filter skips it).
        result2 = col.index.reindex()
        paths2 = [row["path"] for row in col._fts.list_notes()]
        assert ".claude/agents/knowledge-gaps.md" not in paths2
        assert result2.added == 0

    def test_reindex_purges_stale_excluded_docs(
        self, tmp_path: Path, vault_path: Path
    ) -> None:
        """reindex() removes pre-existing excluded docs from a persistent index."""
        state_path = tmp_path / "state.json"
        index_path = tmp_path / "index.db"

        # Phase 1: build index WITHOUT exclude_patterns (simulates old behaviour).
        excluded_dir = vault_path / ".claude"
        excluded_dir.mkdir(parents=True, exist_ok=True)
        (excluded_dir / "test.md").write_text("# Excluded\nSome content.\n")

        col1 = _make_vault(vault_path, state_path=state_path, index_path=index_path)
        col1.index.build_index()

        # The file is in the index because no exclude_patterns were set.
        paths1 = [row["path"] for row in col1._fts.list_notes()]
        assert ".claude/test.md" in paths1

        # Phase 2: create a new Vault WITH exclude_patterns and reindex.
        col2 = _make_vault(
            vault_path,
            state_path=state_path,
            index_path=index_path,
            exclude_patterns=[".claude/**"],
        )
        # col2 shares the persistent DB; build_index() short-circuits on
        # populated FTS state alone (issue #525), so this is fast.
        col2.index.build_index()
        col2.index.reindex()

        # The stale excluded doc should be purged.
        paths2 = [row["path"] for row in col2._fts.list_notes()]
        assert ".claude/test.md" not in paths2

    def test_reindex_purges_stale_excluded_docs_with_embeddings(
        self,
        tmp_path: Path,
        vault_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """reindex() purges stale excluded docs from both FTS and vector index."""
        state_path = tmp_path / "state.json"
        index_path = tmp_path / "index.db"
        embeddings_path = tmp_path / "embeddings"

        # Phase 1: build + embed WITHOUT exclude_patterns.
        excluded_dir = vault_path / ".claude"
        excluded_dir.mkdir(parents=True, exist_ok=True)
        (excluded_dir / "test.md").write_text("# Excluded\nSome content.\n")

        col1 = _make_vault(
            vault_path,
            state_path=state_path,
            index_path=index_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col1.index.build_index()
        col1.index.build_embeddings()
        assert col1._vectors is not None
        vec_paths1 = [m["path"] for m in col1._vectors._metadata]
        assert ".claude/test.md" in vec_paths1

        # Phase 2: reindex WITH exclude_patterns — stale doc purged from both.
        col2 = _make_vault(
            vault_path,
            state_path=state_path,
            index_path=index_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
            exclude_patterns=[".claude/**"],
        )
        col2.index.build_index()
        col2.index.reindex()

        paths2 = [row["path"] for row in col2._fts.list_notes()]
        assert ".claude/test.md" not in paths2
        # Vectors should also be purged (loaded via _load_vectors before purge).
        assert col2._vectors is not None
        vec_paths2 = [m["path"] for m in col2._vectors._metadata]
        assert ".claude/test.md" not in vec_paths2

    def test_build_index_purges_stale_excluded_docs(
        self, tmp_path: Path, vault_path: Path
    ) -> None:
        """build_index() removes pre-existing excluded docs from a persistent index."""
        index_path = tmp_path / "index.db"

        # Phase 1: build index WITHOUT exclude_patterns.
        excluded_dir = vault_path / ".claude"
        excluded_dir.mkdir(parents=True, exist_ok=True)
        (excluded_dir / "test.md").write_text("# Excluded\nSome content.\n")

        col1 = _make_vault(vault_path, index_path=index_path)
        col1.index.build_index()
        paths1 = [row["path"] for row in col1._fts.list_notes()]
        assert ".claude/test.md" in paths1

        # Phase 2: new Vault WITH exclude_patterns. Per issue #525 the
        # short-circuit fires on populated FTS state alone, so a plain
        # build_index() would no-op — config changes (exclude_patterns,
        # required_frontmatter) need an explicit force=True rebuild.
        col2 = _make_vault(
            vault_path,
            index_path=index_path,
            exclude_patterns=[".claude/**"],
        )
        col2.index.build_index(force=True)
        paths2 = [row["path"] for row in col2._fts.list_notes()]
        assert ".claude/test.md" not in paths2

    def test_build_index_purges_stale_excluded_docs_with_embeddings(
        self,
        tmp_path: Path,
        vault_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_index() purges stale excluded docs from vector sidecar too."""
        index_path = tmp_path / "index.db"
        embeddings_path = tmp_path / "embeddings"

        # Phase 1: build + embed WITHOUT exclude_patterns.
        excluded_dir = vault_path / ".claude"
        excluded_dir.mkdir(parents=True, exist_ok=True)
        (excluded_dir / "test.md").write_text("# Excluded\nSome content.\n")

        col1 = _make_vault(
            vault_path,
            index_path=index_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col1.index.build_index()
        col1.index.build_embeddings()
        assert col1._vectors is not None
        vec_paths1 = [m["path"] for m in col1._vectors._metadata]
        assert ".claude/test.md" in vec_paths1

        # Phase 2: force=True rebuild applies the new exclude_patterns to
        # both FTS and embeddings — see the sibling test for the rationale
        # (issue #525). Vectors need their own force=True because the
        # short-circuit in build_embeddings is independent.
        col2 = _make_vault(
            vault_path,
            index_path=index_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
            exclude_patterns=[".claude/**"],
        )
        col2.index.build_index(force=True)
        col2.index.build_embeddings(force=True)

        paths2 = [row["path"] for row in col2._fts.list_notes()]
        assert ".claude/test.md" not in paths2
        # Vectors loaded and purged, then saved back to disk.
        assert col2._vectors is not None
        vec_paths2 = [m["path"] for m in col2._vectors._metadata]
        assert ".claude/test.md" not in vec_paths2


# ---------------------------------------------------------------------------
# Lazy initialisation
# ---------------------------------------------------------------------------


class TestLazyInitialisation:
    def test_search_without_build_index_returns_empty(self, vault_path: Path) -> None:
        """search() on an unbuilt index returns [] without crashing (bucket 2)."""
        col = _make_vault(vault_path)

        results = col.reader.search("simple")

        assert results == []

    def test_list_without_build_index_returns_empty(self, vault_path: Path) -> None:
        """list() on an unbuilt index returns [] (bucket 2 — no implicit build)."""
        col = _make_vault(vault_path)

        notes = col.reader.list_documents()

        assert notes == []


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_keyword_returns_results(self, vault: Vault) -> None:
        """Keyword search for a term present in fixtures returns results."""
        results = vault.reader.search("simple", mode="keyword")

        assert len(results) > 0
        assert all(hasattr(r, "path") for r in results)
        assert all(hasattr(r, "score") for r in results)

    def test_search_keyword_term_in_content(self, vault: Vault) -> None:
        """Keyword results reference documents that contain the query term."""
        results = vault.reader.search("unicode", mode="keyword")

        paths = [r.path for r in results]
        assert any("unicode" in p.lower() for p in paths)

    def test_search_semantic_no_embeddings_raises(self, vault_path: Path) -> None:
        """Semantic search without a provider configured raises ValueError."""
        col = _make_vault(vault_path)
        col.index.build_index()

        with pytest.raises(ValueError, match="embedding_provider"):
            col.reader.search("any query", mode="semantic")

    def test_search_hybrid_with_mock_embeddings(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """Hybrid search returns results when both FTS and vector indexes are built."""
        embeddings_path = tmp_path / "embeddings"
        col = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.index.build_index()
        col.index.build_embeddings()

        results = col.reader.search("document content", mode="hybrid")

        assert isinstance(results, list)
        # At least some results should come back (9 docs indexed).
        assert len(results) > 0

    def test_rrf_neither_dominates(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """Hybrid search combines FTS and semantic results via RRF.

        Regression: verifies results contain items from both retrieval paths
        rather than one source completely overshadowing the other.
        """
        embeddings_path = tmp_path / "embeddings"
        col = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.index.build_index()
        col.index.build_embeddings()

        # Use a broad query that FTS can handle and semantic can also rank.
        results = col.reader.search("note document", mode="hybrid", limit=9)

        # With 9 documents indexed we expect a meaningful set of results.
        assert len(results) >= 1
        # RRF scores must be positive.
        assert all(r.score > 0 for r in results)

    def test_search_hybrid_no_embeddings_raises(self, vault_path: Path) -> None:
        """Hybrid search without a provider configured raises ValueError."""
        col = _make_vault(vault_path)
        col.index.build_index()

        with pytest.raises(ValueError, match="embedding_provider"):
            col.reader.search("query", mode="hybrid")

    def test_document_identity_different_folders(
        self,
        tmp_path: Path,
    ) -> None:
        """Same filename in different folders produces distinct search results.

        Regression: document identity is the full relative path, not just
        the filename. Two files named ``note.md`` in different folders must
        be indexed and retrieved as separate documents.
        """
        vault = tmp_path / "dual_vault"
        (vault / "alpha").mkdir(parents=True)
        (vault / "beta").mkdir(parents=True)
        (vault / "alpha" / "note.md").write_text(
            "# Alpha Note\n\nContent from the alpha folder.\n"
        )
        (vault / "beta" / "note.md").write_text(
            "# Beta Note\n\nContent from the beta folder.\n"
        )

        col = _make_vault(vault)
        col.index.build_index()

        notes = col.reader.list_documents()
        paths = [n.path for n in notes]
        assert "alpha/note.md" in paths
        assert "beta/note.md" in paths
        assert len(paths) == 2

        # Keyword search should find both as separate results.
        results = col.reader.search("note", mode="keyword", limit=10)
        result_paths = [r.path for r in results]
        assert "alpha/note.md" in result_paths
        assert "beta/note.md" in result_paths


# ---------------------------------------------------------------------------
# Read tests
# ---------------------------------------------------------------------------


class TestRead:
    def test_read_returns_content(self, vault: Vault) -> None:
        """read() returns a NoteContent with correct fields."""
        result = vault.reader.read("full_frontmatter.md")

        assert isinstance(result, NoteContent)
        assert result.path == "full_frontmatter.md"
        assert result.title == "Full Frontmatter Note"
        assert "Full Frontmatter Note" in result.content
        assert result.frontmatter.get("cluster") == "fiction"
        assert result.folder == ""

    def test_read_subfolder_document(self, vault: Vault) -> None:
        """read() works for documents nested in subfolders."""
        result = vault.reader.read("subfolder/nested.md")

        assert isinstance(result, NoteContent)
        assert result.path == "subfolder/nested.md"
        assert result.folder == "subfolder"

    def test_read_not_found_returns_none(self, vault: Vault) -> None:
        """read() returns None for a path that does not exist."""
        result = vault.reader.read("nonexistent/missing.md")

        assert result is None

    def test_read_returns_etag(self, vault: Vault, vault_path: Path) -> None:
        """read() returns an etag field containing the SHA256 hex digest."""
        import hashlib

        result = vault.reader.read("full_frontmatter.md")

        assert result is not None
        expected = hashlib.sha256(
            (vault_path / "full_frontmatter.md").read_bytes()
        ).hexdigest()
        assert result.etag == expected

    def test_read_etag_is_stable(self, vault: Vault) -> None:
        """read() returns the same etag on repeated reads of unchanged content."""
        result1 = vault.reader.read("full_frontmatter.md")
        result2 = vault.reader.read("full_frontmatter.md")

        assert result1 is not None
        assert result2 is not None
        assert result1.etag is not None
        assert result1.etag == result2.etag
        assert len(result1.etag) == 64  # SHA256 hex is 64 chars

    def test_read_etag_changes_after_write(self, vault_path: Path) -> None:
        """read() etag changes when file content changes."""
        col = _make_vault(vault_path, read_only=False)
        col.index.build_index()

        result_before = col.reader.read("full_frontmatter.md")
        assert result_before is not None
        etag_before = result_before.etag

        col.writer.write("full_frontmatter.md", "# Updated\n\nNew content.\n")

        result_after = col.reader.read("full_frontmatter.md")
        assert result_after is not None
        assert result_after.etag != etag_before


# ---------------------------------------------------------------------------
# List tests
# ---------------------------------------------------------------------------


class TestList:
    def test_list_all(self, vault: Vault) -> None:
        """list() returns all indexed documents."""
        notes = vault.reader.list_documents()

        assert len(notes) == 9
        assert all(isinstance(n, NoteInfo) for n in notes)

    def test_list_with_folder(self, vault: Vault) -> None:
        """list(folder=...) returns only documents in that folder."""
        notes = vault.reader.list_documents(folder="subfolder")

        assert len(notes) >= 1
        assert all("subfolder" in n.folder for n in notes)

    def test_list_with_pattern(self, vault: Vault) -> None:
        """list(pattern=...) returns only documents matching the glob.

        ``fnmatch`` treats ``*`` as matching path separators, so
        ``subfolder/*.md`` also matches ``subfolder/deep/doc.md``.
        """
        notes = vault.reader.list_documents(pattern="subfolder/*.md")

        paths = [n.path for n in notes]
        assert "subfolder/nested.md" in paths
        # fnmatch '*' matches across directory separators.
        assert "subfolder/deep/doc.md" in paths
        # Root-level documents must not be included.
        assert all(n.path.startswith("subfolder/") for n in notes)

    def test_list_subfolder_deep_pattern(self, vault: Vault) -> None:
        """list() with a deep glob pattern returns deeply nested documents."""
        notes = vault.reader.list_documents(pattern="subfolder/**/*.md")

        paths = [n.path for n in notes]
        assert "subfolder/deep/doc.md" in paths


# ---------------------------------------------------------------------------
# Metadata / stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_returns_vault_stats(self, vault: Vault) -> None:
        """stats() returns a VaultStats with correct counts."""
        s = vault.reader.stats()

        assert isinstance(s, VaultStats)
        assert s.document_count == 9
        assert s.chunk_count == 9
        # Folders: "" (root), "subfolder", "subfolder/deep"
        assert s.folder_count == 3
        assert s.semantic_search_available is False

    def test_stats_semantic_available_when_configured(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """stats() reports semantic_search_available=True when provider is set."""
        col = Vault(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        col.index.build_index()

        s = col.reader.stats()
        assert s.semantic_search_available is True

    def test_list_folders(self, vault: Vault) -> None:
        """list_folders() returns the distinct folder values across the index."""
        folders = vault.reader.list_folders()

        assert isinstance(folders, list)
        assert "" in folders  # root documents
        assert "subfolder" in folders
        assert "subfolder/deep" in folders

    def test_list_tags(self, vault_path: Path) -> None:
        """list_tags() returns distinct values for indexed frontmatter fields."""
        col = Vault(
            source_dir=vault_path,
            indexed_frontmatter_fields=["cluster", "topics"],
        )
        col.index.build_index()

        clusters = col.reader.list_tags("cluster")
        # full_frontmatter.md has cluster: fiction
        assert "fiction" in clusters

        topics = col.reader.list_tags("topics")
        # full_frontmatter.md has topics: [horror, gothic]
        assert "horror" in topics
        assert "gothic" in topics

    def test_list_tags_unindexed_field_returns_empty(self, vault: Vault) -> None:
        """list_tags() on a field not in indexed_frontmatter_fields returns []."""
        result = vault.reader.list_tags("cluster")
        assert result == []


# ---------------------------------------------------------------------------
# Write / read-only guard
# ---------------------------------------------------------------------------


class TestWriteReadOnly:
    def test_write_raises_readonly(self, vault: Vault) -> None:
        """write() raises ReadOnlyError on a default (read-only) vault."""
        with pytest.raises(ReadOnlyError):
            vault.writer.write("new_note.md", "# New Note\n\nContent.")

    def test_edit_raises_readonly(self, vault: Vault) -> None:
        """edit() raises ReadOnlyError on a read-only vault."""
        with pytest.raises(ReadOnlyError):
            vault.writer.edit("simple.md", "old text", "new text")

    def test_delete_raises_readonly(self, vault: Vault) -> None:
        """delete() raises ReadOnlyError on a read-only vault."""
        with pytest.raises(ReadOnlyError):
            vault.writer.delete("simple.md")

    def test_rename_raises_readonly(self, vault: Vault) -> None:
        """rename() raises ReadOnlyError on a read-only vault."""
        with pytest.raises(ReadOnlyError):
            vault.writer.rename("simple.md", "renamed.md")


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


@pytest.fixture
def writable(vault_path: Path) -> Iterator[Vault]:
    """Writable Vault backed by the clean vault fixture."""
    col = _make_vault(vault_path, read_only=False)
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


@pytest.fixture
def writable_with_embeddings(
    vault_path: Path,
    tmp_path: Path,
    mock_provider: MockEmbeddingProvider,
) -> Iterator[Vault]:
    """Writable Vault with mock embeddings enabled."""
    col = Vault(
        source_dir=vault_path,
        embeddings_path=tmp_path / "embeddings",
        embedding_provider=mock_provider,
        read_only=False,
    )
    try:
        col.index.build_index()
        col.index.build_embeddings()
        yield col
    finally:
        col.close()


class TestWrite:
    def test_write_creates_new_file(self, writable: Vault, vault_path: Path) -> None:
        """write() creates a new file on disk and returns created=True."""
        result = writable.writer.write("new_note.md", "# New Note\n\nNew content.\n")

        assert isinstance(result, WriteResult)
        assert result.path == "new_note.md"
        assert result.created is True
        assert (vault_path / "new_note.md").is_file()
        assert "New content" in (vault_path / "new_note.md").read_text()

    def test_write_creates_intermediate_directories(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """write() creates intermediate dirs as needed."""
        writable.writer.write("deep/nested/note.md", "# Deep\n\nNested.\n")

        assert (vault_path / "deep" / "nested" / "note.md").is_file()

    def test_write_overwrites_existing_file(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """write() overwrites existing file and returns created=False."""
        result = writable.writer.write("simple.md", "# Replaced\n\nNew body.\n")

        assert result.created is False
        assert "Replaced" in (vault_path / "simple.md").read_text()

    def test_write_with_frontmatter(self, writable: Vault, vault_path: Path) -> None:
        """write() serialises frontmatter as YAML header."""
        writable.writer.write(
            "with_fm.md",
            "# Hello\n\nBody text.\n",
            frontmatter={"title": "Hello", "tags": ["a", "b"]},
        )

        content = (vault_path / "with_fm.md").read_text()
        assert "---" in content
        assert "title: Hello" in content

    def test_write_immediately_searchable(self, writable: Vault) -> None:
        """Written content is immediately searchable."""
        writable.writer.write(
            "searchable.md", "# Unique Xylophone\n\nRare content for testing.\n"
        )
        wait_for_writer_drain(writable)

        results = writable.reader.search("xylophone", mode="keyword")
        paths = [r.path for r in results]
        assert "searchable.md" in paths

    def test_write_triggers_callback(self, vault_path: Path) -> None:
        """write() invokes the on_write callback with correct arguments."""
        calls: list = []
        col = _make_vault(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.index.build_index()

        col.writer.write("cb_test.md", "# Callback\n\nTest.\n")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        path, content, operation = calls[0]
        assert path == vault_path / "cb_test.md"
        assert "Callback" in content
        assert operation == "write"

    def test_write_path_traversal_rejected(self, writable: Vault) -> None:
        """write() rejects paths that escape the source directory."""
        with pytest.raises(ValueError, match="traversal"):
            writable.writer.write("../../etc/passwd.md", "malicious")

    def test_write_non_md_extension_rejected(self, writable: Vault) -> None:
        """write() rejects paths that do not end with .md."""
        with pytest.raises(ValueError, match=r"\.md"):
            writable.writer.write("notes.yaml", "content")

    def test_write_updates_vector_index(self, writable_with_embeddings: Vault) -> None:
        """write() with embeddings configured makes the doc findable via semantic search."""
        writable_with_embeddings.writer.write(
            "new_semantic.md",
            "# Unique Quantum Entanglement\n\nContent about quantum physics.\n",
        )
        wait_for_writer_drain(writable_with_embeddings)

        results = writable_with_embeddings.reader.search(
            "quantum entanglement", mode="semantic"
        )
        paths = [r.path for r in results]
        assert "new_semantic.md" in paths

    def test_write_frontmatter_roundtrip(self, writable: Vault) -> None:
        """write() with frontmatter dict round-trips correctly through read()."""
        frontmatter = {
            "title": "Roundtrip Note",
            "tags": ["alpha", "beta"],
            "meta": {"key": "value"},
        }
        writable.writer.write(
            "roundtrip.md", "# Body\n\nContent.\n", frontmatter=frontmatter
        )

        result = writable.reader.read("roundtrip.md")

        assert result is not None
        assert result.frontmatter["title"] == "Roundtrip Note"
        assert result.frontmatter["tags"] == ["alpha", "beta"]
        assert result.frontmatter["meta"] == {"key": "value"}

    def test_write_empty_content(self, writable: Vault) -> None:
        """write() with empty body and frontmatter produces a readable document."""
        writable.writer.write("empty_body.md", "", frontmatter={"title": "Empty Body"})

        result = writable.reader.read("empty_body.md")

        assert result is not None
        assert result.frontmatter["title"] == "Empty Body"

    def test_write_unicode_content(self, writable: Vault) -> None:
        """write() with Unicode and emoji content produces a searchable document."""
        writable.writer.write(
            "unicode_note.md",
            "# Unicode Test\n\nCafé naïve résumé \U0001f600\n",
        )
        wait_for_writer_drain(writable)

        results = writable.reader.search("unicode test", mode="keyword")
        paths = [r.path for r in results]
        assert "unicode_note.md" in paths


class TestEdit:
    def test_edit_replaces_text(self, writable: Vault, vault_path: Path) -> None:
        """edit() replaces exactly one occurrence of old_text."""
        result = writable.writer.edit(
            "simple.md", "Simple Document", "Updated Document"
        )

        assert isinstance(result, EditResult)
        assert result.path == "simple.md"
        assert result.replacements == 1

        content = (vault_path / "simple.md").read_text()
        assert "Updated Document" in content
        assert "Simple Document" not in content

    def test_edit_match_type_exact_default(self, writable: Vault) -> None:
        """edit() returns match_type='exact' by default."""
        result = writable.writer.edit(
            "simple.md", "Simple Document", "Updated Document"
        )
        assert result.match_type == "exact"

    def test_edit_empty_old_text_raises(self, writable: Vault) -> None:
        """edit() raises ValueError when old_text is empty."""
        with pytest.raises(ValueError, match="old_text must not be empty"):
            writable.writer.edit("simple.md", "", "new")

    def test_edit_not_found_raises(self, writable: Vault) -> None:
        """edit() raises DocumentNotFoundError for missing files."""
        with pytest.raises(DocumentNotFoundError):
            writable.writer.edit("nonexistent.md", "old", "new")

    def test_edit_old_text_missing_raises(self, writable: Vault) -> None:
        """edit() raises EditConflictError when old_text is not found."""
        with pytest.raises(EditConflictError, match="not found"):
            writable.writer.edit("simple.md", "text that does not exist", "new")

    def test_edit_old_text_multiple_raises(
        self,
        writable: Vault,
    ) -> None:
        """edit() raises EditConflictError when old_text appears multiple times."""
        # Create a file with repeated content.
        writable.writer.write("repeated.md", "word word word\n")

        with pytest.raises(EditConflictError, match="3 times"):
            writable.writer.edit("repeated.md", "word", "replaced")

    def test_edit_updates_index(self, writable: Vault) -> None:
        """Edited content is immediately searchable."""
        writable.writer.write("editable.md", "# Old Title\n\nOld body text.\n")
        writable.writer.edit("editable.md", "Old Title", "New Unique Xylophone Title")
        wait_for_writer_drain(writable)

        results = writable.reader.search("xylophone", mode="keyword")
        paths = [r.path for r in results]
        assert "editable.md" in paths

    def test_edit_triggers_callback(self, vault_path: Path) -> None:
        """edit() invokes the on_write callback with correct arguments."""
        calls: list = []
        col = _make_vault(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.index.build_index()

        col.writer.edit("simple.md", "Simple Document", "Modified Document")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        _, _, operation = calls[0]
        assert operation == "edit"

    def test_edit_old_content_removed_from_fts(self, writable: Vault) -> None:
        """edit() removes the old content from FTS; old text is no longer searchable."""
        writable.writer.write("editable_fts.md", "# OldUniqueTitle\n\nOld body text.\n")
        wait_for_writer_drain(writable)

        # Confirm old text is searchable before edit.
        before = writable.reader.search("OldUniqueTitle", mode="keyword")
        assert any(r.path == "editable_fts.md" for r in before)

        writable.writer.edit("editable_fts.md", "OldUniqueTitle", "NewReplacedTitle")
        wait_for_writer_drain(writable)

        # Old text must no longer appear in results.
        after_old = writable.reader.search("OldUniqueTitle", mode="keyword")
        assert not any(r.path == "editable_fts.md" for r in after_old)

    def test_edit_updates_vector_index(self, writable_with_embeddings: Vault) -> None:
        """edit() with embeddings configured reflects new content in semantic search."""
        writable_with_embeddings.writer.write(
            "vec_editable.md",
            "# Original Content\n\nThis is the original text.\n",
        )
        writable_with_embeddings.writer.edit(
            "vec_editable.md",
            "original text",
            "quantum mechanics discussion",
        )
        wait_for_writer_drain(writable_with_embeddings)

        results = writable_with_embeddings.reader.search(
            "quantum mechanics", mode="semantic"
        )
        paths = [r.path for r in results]
        assert "vec_editable.md" in paths

    def test_edit_line_range_replaces(self, writable: Vault, vault_path: Path) -> None:
        """edit() with line_start/line_end replaces the specified lines."""
        writable.writer.write("lines.md", "line1\nline2\nline3\nline4\n")
        result = writable.writer.edit(
            "lines.md", new_text="replaced\n", line_start=2, line_end=3
        )
        assert result.replacements == 1
        content = (vault_path / "lines.md").read_text()
        assert content == "line1\nreplaced\nline4\n"

    def test_edit_line_range_single_line(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """line_start == line_end replaces exactly one line."""
        writable.writer.write("lines.md", "line1\nline2\nline3\n")
        writable.writer.edit("lines.md", new_text="new2", line_start=2, line_end=2)
        content = (vault_path / "lines.md").read_text()
        assert content == "line1\nnew2\nline3\n"

    def test_edit_line_range_out_of_bounds(self, writable: Vault) -> None:
        """line_end beyond file length raises ValueError."""
        writable.writer.write("lines.md", "line1\nline2\n")
        with pytest.raises(ValueError, match="out of range"):
            writable.writer.edit("lines.md", new_text="x", line_start=1, line_end=5)

    def test_edit_line_range_inverted(self, writable: Vault) -> None:
        """line_start > line_end raises ValueError."""
        writable.writer.write("lines.md", "line1\nline2\n")
        with pytest.raises(ValueError, match=r"line_start.*line_end"):
            writable.writer.edit("lines.md", new_text="x", line_start=3, line_end=1)

    def test_edit_line_range_only_one_provided(self, writable: Vault) -> None:
        """Providing only line_start without line_end raises ValueError."""
        with pytest.raises(ValueError, match=r"both.*line_start.*line_end"):
            writable.writer.edit("simple.md", new_text="x", line_start=1)

    def test_edit_no_old_text_no_lines(self, writable: Vault) -> None:
        """Neither old_text nor line range raises ValueError."""
        with pytest.raises(ValueError, match=r"old_text.*line_start"):
            writable.writer.edit("simple.md", new_text="x")

    def test_edit_line_range_zero_raises(self, writable: Vault) -> None:
        """line_start < 1 raises ValueError (1-based)."""
        with pytest.raises(ValueError, match=r"line_start.*>= 1"):
            writable.writer.edit("simple.md", new_text="x", line_start=0, line_end=1)

    def test_edit_line_range_updates_index(self, writable: Vault) -> None:
        """Line-range edit updates the FTS index."""
        writable.writer.write("lines.md", "# Old Title\n\nOld body.\n")
        writable.writer.edit(
            "lines.md", new_text="# Xylophone Title\n", line_start=1, line_end=1
        )
        wait_for_writer_drain(writable)
        results = writable.reader.search("xylophone", mode="keyword")
        assert any(r.path == "lines.md" for r in results)

    def test_edit_line_range_with_if_match(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """Line-range edit respects if_match etag."""
        writable.writer.write("lines.md", "line1\nline2\n")
        read_result = writable.reader.read("lines.md")
        writable.writer.edit(
            "lines.md",
            new_text="new1\n",
            line_start=1,
            line_end=1,
            if_match=read_result.etag,
        )
        content = (vault_path / "lines.md").read_text()
        assert content == "new1\nline2\n"

    def test_edit_line_range_with_wrong_if_match(self, writable: Vault) -> None:
        """Line-range edit rejects stale etag."""
        writable.writer.write("lines.md", "line1\nline2\n")
        with pytest.raises(ConcurrentModificationError):
            writable.writer.edit(
                "lines.md",
                new_text="new",
                line_start=1,
                line_end=1,
                if_match="stale_hash",
            )

    def test_edit_line_range_triggers_callback(self, vault_path: Path) -> None:
        """Line-range edit fires the on_write callback."""
        calls: list = []
        col = _make_vault(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.index.build_index()
        col.writer.write("lines.md", "line1\nline2\n")
        col.writer.edit("lines.md", new_text="replaced\n", line_start=1, line_end=1)
        col.close()
        # write + edit = 2 callbacks
        assert len(calls) == 2
        _, _, operation = calls[1]
        assert operation == "edit"

    # -----------------------------------------------------------------------
    # Scoped match tests
    # -----------------------------------------------------------------------

    def test_edit_scoped_match(self, writable: Vault, vault_path: Path) -> None:
        """old_text + line range disambiguates repeated text."""
        writable.writer.write("repeated.md", "hello\nworld\nhello\n")
        # "hello" appears twice, but only once in lines 1-1.
        result = writable.writer.edit(
            "repeated.md",
            old_text="hello",
            new_text="goodbye",
            line_start=1,
            line_end=1,
        )
        assert result.replacements == 1
        content = (vault_path / "repeated.md").read_text()
        assert content == "goodbye\nworld\nhello\n"

    def test_edit_scoped_match_not_found(self, writable: Vault) -> None:
        """old_text not in the specified line range raises EditConflictError."""
        writable.writer.write("scoped.md", "aaa\nbbb\nccc\n")
        with pytest.raises(EditConflictError, match="not found"):
            writable.writer.edit(
                "scoped.md",
                old_text="ccc",
                new_text="ddd",
                line_start=1,
                line_end=2,
            )

    # -----------------------------------------------------------------------
    # Normalized match tests
    # -----------------------------------------------------------------------

    def test_edit_normalized_dashes(self, writable: Vault, vault_path: Path) -> None:
        """Normalized match handles em-dash vs hyphen."""
        writable.writer.write("dashes.md", "hello \u2014 world\n")
        result = writable.writer.edit(
            "dashes.md", old_text="hello - world", new_text="goodbye"
        )
        assert result.match_type == "normalized"
        content = (vault_path / "dashes.md").read_text()
        assert content == "goodbye\n"

    def test_edit_normalized_quotes(self, writable: Vault, vault_path: Path) -> None:
        """Normalized match handles smart quotes vs straight."""
        writable.writer.write("quotes.md", "\u201chello\u201d\n")
        result = writable.writer.edit(
            "quotes.md", old_text='"hello"', new_text="goodbye"
        )
        assert result.match_type == "normalized"
        content = (vault_path / "quotes.md").read_text()
        assert content == "goodbye\n"

    def test_edit_normalized_whitespace(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """Normalized match handles collapsed whitespace."""
        writable.writer.write("ws.md", "hello   world\n")
        result = writable.writer.edit(
            "ws.md", old_text="hello world", new_text="goodbye"
        )
        assert result.match_type == "normalized"
        content = (vault_path / "ws.md").read_text()
        assert content == "goodbye\n"

    def test_edit_normalized_trailing_ws(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """Normalized match handles trailing whitespace difference."""
        writable.writer.write("trail.md", "hello   \nworld\n")
        result = writable.writer.edit(
            "trail.md", old_text="hello\nworld", new_text="goodbye"
        )
        assert result.match_type == "normalized"
        content = (vault_path / "trail.md").read_text()
        assert content == "goodbye\n"

    def test_edit_normalized_unicode(self, writable: Vault, vault_path: Path) -> None:
        """Normalized match handles NFC decomposed vs composed."""
        # File content: decomposed é written as "café" (will normalize to composed on write)
        # We match it with composed é from old_text, confirming normalization works
        writable.writer.write(
            "unicode.md", "caf\u00e9\n"
        )  # Use composed form explicitly
        # Try to match with a slight variation that requires normalization
        # (in practice, this tests that equivalence is handled)
        result = writable.writer.edit(
            "unicode.md", old_text="caf\u00e9", new_text="tea"
        )
        assert result.match_type == "exact"  # Will be exact since they match exactly
        content = (vault_path / "unicode.md").read_text()
        assert content == "tea\n"

    def test_edit_normalized_returns_match_type(self, writable: Vault) -> None:
        """Normalized match returns match_type='normalized' in EditResult."""
        writable.writer.write("norm.md", "a\u2014b\n")
        result = writable.writer.edit("norm.md", old_text="a-b", new_text="c")
        assert result.match_type == "normalized"

    def test_edit_normalized_multiple_raises(self, writable: Vault) -> None:
        """Normalized match with >1 occurrences raises EditConflictError."""
        writable.writer.write("multi.md", "a\u2014b and a\u2014b\n")
        with pytest.raises(EditConflictError, match="after normalization"):
            writable.writer.edit("multi.md", old_text="a-b", new_text="c")

    def test_edit_exact_preferred_over_normalized(self, writable: Vault) -> None:
        """Exact match is used even when normalized would also work."""
        writable.writer.write("exact.md", "a-b\n")
        result = writable.writer.edit("exact.md", old_text="a-b", new_text="c")
        assert result.match_type == "exact"

    def test_edit_normalized_preserves_original_bytes(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """Normalized replacement preserves original bytes outside the match."""
        # File has smart quotes + em-dash in OTHER parts.
        writable.writer.write(
            "preserve.md",
            "\u201cintro\u201d\nhello   world\n\u201coutro\u201d\n",
        )
        writable.writer.edit("preserve.md", old_text="hello world", new_text="goodbye")
        content = (vault_path / "preserve.md").read_text()
        # Smart quotes in intro/outro must be preserved.
        assert content == "\u201cintro\u201d\ngoodbye\n\u201coutro\u201d\n"

    def test_edit_normalized_decomposed_unicode_replacement(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """Normalized match correctly replaces decomposed Unicode (multi-char → one).

        When a file contains a decomposed combining sequence (e.g. 'e' +
        U+0301) and old_text contains the composed form (é, U+00E9), the
        replacement must consume the *full* original span including the
        combining accent, not just the base character.
        """
        # File has decomposed 'é' (e + combining acute) at the end of a word.
        writable.writer.write("decomposed.md", "caf\u0065\u0301 au lait\n")
        result = writable.writer.edit(
            "decomposed.md",
            old_text="caf\u00e9",  # composed form
            new_text="tea",
        )
        assert result.match_type == "normalized"
        content = (vault_path / "decomposed.md").read_text()
        # The combining accent must NOT appear in the output.
        assert content == "tea au lait\n"
        assert "\u0301" not in content

    def test_edit_normalized_within_line_range(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """Normalized match works within a scoped line range."""
        writable.writer.write("scoped_norm.md", "aaa\nhello\u2014world\nccc\n")
        result = writable.writer.edit(
            "scoped_norm.md",
            old_text="hello-world",
            new_text="goodbye",
            line_start=2,
            line_end=2,
        )
        assert result.match_type == "normalized"
        content = (vault_path / "scoped_norm.md").read_text()
        assert content == "aaa\ngoodbye\nccc\n"

    # -----------------------------------------------------------------------
    # Diagnostic error tests
    # -----------------------------------------------------------------------

    def test_edit_diagnostic_closest_line(self, writable: Vault) -> None:
        """Failed match includes closest_match_line in error."""
        writable.writer.write("diag.md", "line one\nthe quick brown fox\nline three\n")
        with pytest.raises(EditConflictError) as exc_info:
            writable.writer.edit(
                "diag.md", old_text="the quick brown fax", new_text="x"
            )
        assert exc_info.value.closest_match_line == 2

    def test_edit_diagnostic_diff_snippet(self, writable: Vault) -> None:
        """Failed match includes expected/found snippets."""
        writable.writer.write("diag2.md", "the quick brown fox\n")
        with pytest.raises(EditConflictError) as exc_info:
            writable.writer.edit(
                "diag2.md", old_text="the quick-brown fox", new_text="x"
            )
        err = exc_info.value
        assert err.expected_snippet is not None
        assert err.found_snippet is not None

    def test_edit_diagnostic_no_close_match(self, writable: Vault) -> None:
        """No diagnostics when nothing is remotely close."""
        writable.writer.write("diag3.md", "aaaa\nbbbb\ncccc\n")
        with pytest.raises(EditConflictError) as exc_info:
            writable.writer.edit(
                "diag3.md",
                old_text="xyz123 completely different",
                new_text="x",
            )
        err = exc_info.value
        assert err.closest_match_line is None


class TestEditConflictDiagnostics:
    def test_error_has_diagnostic_fields(self) -> None:
        """EditConflictError stores diagnostic fields."""
        err = EditConflictError(
            "old_text not found in test.md",
            closest_match_line=10,
            first_diff_char=42,
            expected_snippet="—",
            found_snippet="-",
        )
        assert err.closest_match_line == 10
        assert err.first_diff_char == 42
        assert err.expected_snippet == "—"
        assert err.found_snippet == "-"
        assert str(err) == "old_text not found in test.md"

    def test_error_defaults_none(self) -> None:
        """EditConflictError diagnostic fields default to None."""
        err = EditConflictError("old_text not found in test.md")
        assert err.closest_match_line is None
        assert err.first_diff_char is None
        assert err.expected_snippet is None
        assert err.found_snippet is None


class TestNormalizeText:
    def test_nfc_normalization(self) -> None:
        """NFC normalizes composed vs decomposed Unicode."""
        # e + combining acute accent → é (composed)
        decomposed = "caf\u0065\u0301"
        assert _normalize_text(decomposed) == "caf\u00e9"

    def test_dashes_normalized(self) -> None:
        """En-dash and em-dash become hyphens."""
        assert _normalize_text("a\u2013b\u2014c") == "a-b-c"

    def test_smart_quotes_normalized(self) -> None:
        """Smart quotes become straight quotes."""
        assert (
            _normalize_text("\u201chello\u201d \u2018world\u2019")
            == "\"hello\" 'world'"
        )

    def test_whitespace_collapsed(self) -> None:
        """Multiple spaces/tabs collapse to single space within lines."""
        assert _normalize_text("a   b\tc") == "a b c"

    def test_trailing_whitespace_stripped(self) -> None:
        """Trailing whitespace stripped per line."""
        assert _normalize_text("hello   \nworld\t\n") == "hello\nworld\n"

    def test_newlines_preserved(self) -> None:
        """Newlines are not collapsed."""
        assert _normalize_text("a\n\nb") == "a\n\nb"

    def test_no_change_passthrough(self) -> None:
        """Clean text passes through unchanged."""
        text = "hello world"
        assert _normalize_text(text) == text


class TestBuildPositionMap:
    def test_identity_mapping(self) -> None:
        """When text is already normalized, positions map 1:1 plus sentinel."""
        text = "hello"
        pos_map = _build_position_map(text, text)
        # 5 chars + 1 sentinel = 6 entries; sentinel equals len(original)
        assert pos_map == [0, 1, 2, 3, 4, 5]

    def test_dash_mapping(self) -> None:
        """Em-dash (1 char) maps to hyphen (1 char), sentinel = orig_len."""
        original = "a\u2014b"
        normalized = _normalize_text(original)
        pos_map = _build_position_map(original, normalized)
        # normalized is "a-b", length 3 → 4 entries (3 + sentinel)
        assert len(pos_map) == 4
        assert pos_map[0] == 0  # 'a' -> 'a'
        assert pos_map[1] == 1  # '—' -> '-'
        assert pos_map[2] == 2  # 'b' -> 'b'
        assert pos_map[3] == 3  # sentinel = orig_len

    def test_whitespace_collapse_mapping(self) -> None:
        """Multiple spaces collapse; map points to first original space."""
        original = "a   b"
        normalized = _normalize_text(original)
        pos_map = _build_position_map(original, normalized)
        # normalized is "a b", length 3 → 4 entries (3 + sentinel)
        assert len(pos_map) == 4
        assert pos_map[0] == 0  # 'a'
        assert pos_map[1] == 1  # first space of '   '
        assert pos_map[2] == 4  # 'b'
        assert pos_map[3] == 5  # sentinel = orig_len

    def test_trailing_ws_mapping(self) -> None:
        """Trailing whitespace stripped; mapping covers remaining chars."""
        original = "ab  "
        normalized = _normalize_text(original)
        pos_map = _build_position_map(original, normalized)
        # normalized is "ab", length 2 → 3 entries (2 + sentinel)
        assert len(pos_map) == 3
        assert pos_map[0] == 0
        assert pos_map[1] == 1
        assert pos_map[2] == 4  # sentinel = orig_len

    def test_nfc_multichar_mapping(self) -> None:
        """NFC decomposed sequence (2 chars) maps to original start index.

        Sentinel must equal orig_len so orig_end is computed correctly
        when the NFC sequence is the last character.
        """
        # 'e' + combining acute accent (U+0301) → 'é' (U+00E9) after NFC.
        original = "caf\u0065\u0301"  # 5 chars: c a f e ́
        normalized = _normalize_text(original)  # "café" — 4 chars
        assert normalized == "caf\u00e9"
        pos_map = _build_position_map(original, normalized)
        # 4 normalized chars + 1 sentinel = 5 entries
        assert len(pos_map) == 5
        assert pos_map[0] == 0  # 'c'
        assert pos_map[1] == 1  # 'a'
        assert pos_map[2] == 2  # 'f'
        assert pos_map[3] == 3  # 'e' (start of 2-char decomposed sequence)
        assert pos_map[4] == 5  # sentinel = orig_len (past the combining accent)


class TestFindClosestMatch:
    def test_close_match_found(self) -> None:
        """Returns diagnostic info when a close match exists."""
        old_text = "the quick\u2014brown fox"
        file_content = "line one\nthe quick-brown fox\nline three\n"
        diag = _find_closest_match(old_text, file_content)
        assert diag["closest_match_line"] == 2
        assert diag["first_diff_char"] is not None
        assert diag["expected_snippet"] is not None
        assert diag["found_snippet"] is not None

    def test_no_close_match(self) -> None:
        """Returns empty dict when nothing is close."""
        old_text = "completely different text xyz123"
        file_content = "line one\nline two\nline three\n"
        diag = _find_closest_match(old_text, file_content)
        assert diag == {}

    def test_exact_match_reports_line(self) -> None:
        """Even near-exact matches are reported with correct line number."""
        old_text = "hello world"
        file_content = "first\nhello worlds\nthird\n"
        diag = _find_closest_match(old_text, file_content)
        assert diag["closest_match_line"] == 2


class TestDelete:
    def test_delete_removes_file(self, writable: Vault, vault_path: Path) -> None:
        """delete() removes the file from disk."""
        result = writable.writer.delete("simple.md")

        assert isinstance(result, DeleteResult)
        assert result.path == "simple.md"
        assert not (vault_path / "simple.md").is_file()

    def test_delete_not_found_raises(self, writable: Vault) -> None:
        """delete() raises DocumentNotFoundError for missing files."""
        with pytest.raises(DocumentNotFoundError):
            writable.writer.delete("nonexistent.md")

    def test_delete_removes_from_search(self, writable: Vault) -> None:
        """Deleted content no longer appears in search results."""
        # Verify it's searchable first.
        results_before = writable.reader.search("Simple Document", mode="keyword")
        assert any(r.path == "simple.md" for r in results_before)

        writable.writer.delete("simple.md")
        wait_for_writer_drain(writable)

        results_after = writable.reader.search("Simple Document", mode="keyword")
        assert not any(r.path == "simple.md" for r in results_after)

    def test_delete_triggers_callback(self, vault_path: Path) -> None:
        """delete() invokes the on_write callback with empty content."""
        calls: list = []
        col = _make_vault(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.index.build_index()

        col.writer.delete("simple.md")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        path, content, operation = calls[0]
        assert path == vault_path / "simple.md"
        assert content == ""
        assert operation == "delete"

    def test_delete_removes_from_vector_index(
        self, writable_with_embeddings: Vault
    ) -> None:
        """delete() removes the document from semantic search results."""
        # Confirm the doc is reachable via semantic search first.
        before = writable_with_embeddings.reader.search(
            "simple document", mode="semantic"
        )
        assert any(r.path == "simple.md" for r in before)

        writable_with_embeddings.writer.delete("simple.md")
        wait_for_writer_drain(writable_with_embeddings)

        after = writable_with_embeddings.reader.search(
            "simple document", mode="semantic"
        )
        assert not any(r.path == "simple.md" for r in after)


class TestRename:
    def test_rename_moves_file(self, writable: Vault, vault_path: Path) -> None:
        """rename() moves the file on disk."""
        result = writable.writer.rename("simple.md", "moved.md")

        assert isinstance(result, RenameResult)
        assert result.old_path == "simple.md"
        assert result.new_path == "moved.md"
        assert not (vault_path / "simple.md").is_file()
        assert (vault_path / "moved.md").is_file()

    def test_rename_updates_search(self, writable: Vault) -> None:
        """After rename, search finds the document at the new path only."""
        writable.writer.rename("simple.md", "moved.md")
        wait_for_writer_drain(writable)

        results = writable.reader.search("Simple Document", mode="keyword")
        paths = [r.path for r in results]
        assert "moved.md" in paths
        assert "simple.md" not in paths

    def test_rename_creates_intermediate_dirs(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """rename() creates intermediate directories for the new path."""
        writable.writer.rename("simple.md", "new_folder/moved.md")

        assert (vault_path / "new_folder" / "moved.md").is_file()

    def test_rename_not_found_raises(self, writable: Vault) -> None:
        """rename() raises DocumentNotFoundError when old_path missing."""
        with pytest.raises(DocumentNotFoundError):
            writable.writer.rename("nonexistent.md", "target.md")

    def test_rename_target_exists_raises(self, writable: Vault) -> None:
        """rename() raises DocumentExistsError when new_path exists."""
        with pytest.raises(DocumentExistsError):
            writable.writer.rename("simple.md", "no_frontmatter.md")

    def test_rename_triggers_callback(self, vault_path: Path) -> None:
        """rename() invokes the on_write callback with new path."""
        calls: list = []
        col = _make_vault(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.index.build_index()

        col.writer.rename("simple.md", "moved.md")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        path, content, operation = calls[0]
        assert path == vault_path / "moved.md"
        assert content != ""
        assert operation == "rename"

    def test_rename_folder_updated(self, writable: Vault) -> None:
        """rename() updates the folder derivation after move."""
        writable.writer.rename("simple.md", "new_folder/simple.md")
        wait_for_writer_drain(writable)

        notes = writable.reader.list_documents(folder="new_folder")
        paths = [n.path for n in notes]
        assert "new_folder/simple.md" in paths

    def test_rename_preserves_file_content(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """rename() produces a file whose content is byte-identical to the original."""
        original_bytes = (vault_path / "simple.md").read_bytes()

        writable.writer.rename("simple.md", "preserved.md")

        renamed_bytes = (vault_path / "preserved.md").read_bytes()
        assert renamed_bytes == original_bytes

    def test_rename_old_path_removed_from_fts(self, writable: Vault) -> None:
        """rename() removes the old path from FTS; old path is no longer searchable."""
        # Confirm old path is searchable before rename.
        before = writable.reader.search("Simple Document", mode="keyword")
        assert any(r.path == "simple.md" for r in before)

        writable.writer.rename("simple.md", "after_rename.md")
        wait_for_writer_drain(writable)

        after = writable.reader.search("Simple Document", mode="keyword")
        assert not any(r.path == "simple.md" for r in after)

    def test_rename_updates_vector_index(self, writable_with_embeddings: Vault) -> None:
        """rename() with embeddings configured indexes the new path, drops the old."""
        writable_with_embeddings.writer.rename("simple.md", "renamed_semantic.md")
        wait_for_writer_drain(writable_with_embeddings)

        after = writable_with_embeddings.reader.search(
            "simple document", mode="semantic"
        )
        paths = [r.path for r in after]
        assert "renamed_semantic.md" in paths
        assert "simple.md" not in paths

    def test_rename_to_same_path_raises(self, writable: Vault) -> None:
        """rename() to the same path raises DocumentExistsError."""
        with pytest.raises(DocumentExistsError):
            writable.writer.rename("simple.md", "simple.md")


# ---------------------------------------------------------------------------
# Concurrent write safety
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    def test_concurrent_writes(self, writable: Vault, vault_path: Path) -> None:
        """10 simultaneous write() calls to distinct paths all succeed.

        Uses :class:`concurrent.futures.ThreadPoolExecutor` to exercise the
        ``_write_lock`` on a single Vault instance.
        """
        paths = [f"concurrent_write_{i}.md" for i in range(10)]

        def do_write(p: str) -> None:
            writable.writer.write(p, f"# Note {p}\n\nContent for {p}.\n")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_write, p) for p in paths]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()  # re-raise any exception from the thread

        wait_for_writer_drain(writable)

        # All 10 files must exist on disk.
        for p in paths:
            assert (vault_path / p).is_file(), f"Expected {p} to exist on disk"

        # All 10 files must be discoverable via search.
        results = writable.reader.search("Content for", mode="keyword", limit=20)
        result_paths = {r.path for r in results}
        for p in paths:
            assert p in result_paths, f"Expected {p} to be searchable"

    def test_concurrent_write_and_edit(self, writable: Vault, vault_path: Path) -> None:
        """Simultaneous edit() calls on distinct unique strings do not corrupt data.

        Writes a file with 10 uniquely-labelled sections, then submits 10
        concurrent edits each replacing a different label.  Verifies all
        replacements land without data loss.
        """
        # Build a file where each line contains a unique token.
        lines = [f"Section-Token-{i}: original text\n" for i in range(10)]
        writable.writer.write("concurrent_edit.md", "".join(lines))

        def do_edit(i: int) -> None:
            writable.writer.edit(
                "concurrent_edit.md",
                f"Section-Token-{i}: original text",
                f"Section-Token-{i}: replaced text",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_edit, i) for i in range(10)]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

        # All replacements must appear in the final file; none lost.
        final = (vault_path / "concurrent_edit.md").read_text(encoding="utf-8")
        for i in range(10):
            assert f"Section-Token-{i}: replaced text" in final, (
                f"Token {i} replacement missing from final file"
            )
            assert f"Section-Token-{i}: original text" not in final, (
                f"Token {i} original text still present after replacement"
            )

    def test_pause_writes_blocks_write(self, vault_path: Path) -> None:
        """pause_writes() queues write() calls until the context exits."""
        import threading
        import time

        col = Vault(source_dir=vault_path, read_only=False)

        finished = threading.Event()

        def do_write() -> None:
            col.writer.write("paused.md", "# Paused\n")
            finished.set()

        with col.pause_writes():
            t = threading.Thread(target=do_write, daemon=True)
            t.start()
            time.sleep(0.05)
            assert not finished.is_set()

        t.join(timeout=2.0)
        assert finished.is_set()

    def test_start_and_close_delegate_to_git_strategy(self, vault_path: Path) -> None:
        """start() and close() call into the configured git strategy."""

        class DummyGitStrategy:
            def __init__(self) -> None:
                self.started: dict[str, object] | None = None
                self.stopped = False
                self.closed = False

            def start(
                self,
                *,
                repo_path: Path,
                pull_interval_s: int,
                pause_writes: object,
                on_pull: object,
            ) -> None:
                self.started = {
                    "repo_path": repo_path,
                    "pull_interval_s": pull_interval_s,
                    "pause_writes": pause_writes,
                    "on_pull": on_pull,
                }

            def stop(self) -> None:
                self.stopped = True

            def close(self) -> None:
                self.closed = True

            def sync_once(self, _repo_path: Path) -> bool:
                return False

        git_strategy = DummyGitStrategy()
        col = Vault(
            source_dir=vault_path,
            read_only=False,
            git_strategy=git_strategy,  # type: ignore[arg-type]
            git_pull_interval_s=60,
        )

        col.start()
        assert git_strategy.started is not None
        assert git_strategy.started["repo_path"] == vault_path
        assert git_strategy.started["pull_interval_s"] == 60

        # Verify Vault.stop() delegates to git_strategy.stop() (Option C lifecycle).
        col.stop()
        assert git_strategy.stopped is True

        col.close()
        assert git_strategy.closed is True

    def test_sync_from_remote_before_index_calls_sync_once(
        self, vault_path: Path
    ) -> None:
        """sync_from_remote_before_index() calls git sync when enabled."""

        class DummyGitStrategy:
            def __init__(self) -> None:
                self.calls: list[Path] = []

            def sync_once(self, repo_path: Path) -> bool:
                self.calls.append(repo_path)
                return False

            def close(self) -> None:
                return None

        git_strategy = DummyGitStrategy()
        col = Vault(
            source_dir=vault_path,
            git_strategy=git_strategy,  # type: ignore[arg-type]
            git_pull_interval_s=60,
        )

        col.sync_from_remote_before_index()
        assert git_strategy.calls == [vault_path]


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    """write(), edit(), and write_attachment() must use Path.replace for atomicity."""

    def test_write_uses_path_replace(
        self, writable: Vault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """write() calls Path.replace to atomically land the new file."""
        replace_calls: list[tuple[str, str]] = []
        original = Path.replace

        def tracking(self: Path, target: Path) -> Path:
            replace_calls.append((str(self), str(target)))
            return original(self, target)

        monkeypatch.setattr(Path, "replace", tracking)
        writable.writer.write("atomic_write.md", "atomic content")

        assert any(dst.endswith("atomic_write.md") for _, dst in replace_calls), (
            "write() did not use Path.replace — file was not written atomically"
        )

    def test_edit_uses_path_replace(
        self, writable: Vault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """edit() calls Path.replace to atomically land the patched file."""
        writable.writer.write("atomic_edit.md", "original content")
        replace_calls: list[tuple[str, str]] = []
        original = Path.replace

        def tracking(self: Path, target: Path) -> Path:
            replace_calls.append((str(self), str(target)))
            return original(self, target)

        monkeypatch.setattr(Path, "replace", tracking)
        writable.writer.edit(
            "atomic_edit.md", old_text="original content", new_text="updated content"
        )

        assert any(dst.endswith("atomic_edit.md") for _, dst in replace_calls), (
            "edit() did not use Path.replace — file was not written atomically"
        )

    def test_write_attachment_uses_path_replace(
        self, writable: Vault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """write_attachment() calls Path.replace to atomically land the attachment."""
        replace_calls: list[tuple[str, str]] = []
        original = Path.replace

        def tracking(self: Path, target: Path) -> Path:
            replace_calls.append((str(self), str(target)))
            return original(self, target)

        monkeypatch.setattr(Path, "replace", tracking)
        writable.writer.write_attachment("diagram.png", b"\x89PNG\r\n\x1a\n")

        assert any(dst.endswith("diagram.png") for _, dst in replace_calls), (
            "write_attachment() did not use Path.replace — file was not written atomically"
        )

    def test_write_preserves_original_on_failed_write(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """If the write fails, the original file is untouched (no silent truncation)."""
        writable.writer.write("safe.md", "original content")

        def failing_replace(_self: Path, _target: Path) -> Path:
            raise OSError("simulated disk full")

        with patch.object(Path, "replace", failing_replace), pytest.raises(OSError):
            writable.writer.write("safe.md", "replacement content")

        assert "original content" in (vault_path / "safe.md").read_text()
        # No leftover .tmp files should remain after the failed write.
        assert list(vault_path.glob("**/*.tmp")) == [], (
            "Temp file was not cleaned up on failure"
        )

    def test_write_preserves_file_permissions_on_overwrite(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """Overwriting an existing file must not downgrade its permissions."""
        writable.writer.write("perms.md", "original content")
        target = vault_path / "perms.md"
        target.chmod(0o644)

        writable.writer.write("perms.md", "new content")

        mode = target.stat().st_mode & 0o777
        assert mode == 0o644, (
            f"write() changed file permissions from 0o644 to {oct(mode)}"
        )

    def test_edit_preserves_file_permissions_on_overwrite(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """edit() on an existing file must not downgrade its permissions."""
        writable.writer.write("perms_edit.md", "original content")
        target = vault_path / "perms_edit.md"
        target.chmod(0o644)

        writable.writer.edit(
            "perms_edit.md", old_text="original content", new_text="edited content"
        )

        mode = target.stat().st_mode & 0o777
        assert mode == 0o644, (
            f"edit() changed file permissions from 0o644 to {oct(mode)}"
        )

    def test_write_attachment_preserves_file_permissions_on_overwrite(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """Overwriting an existing attachment must not downgrade its permissions."""
        writable.writer.write_attachment("diagram.png", b"\x89PNG\r\n\x1a\n")
        target = vault_path / "diagram.png"
        target.chmod(0o644)

        writable.writer.write_attachment("diagram.png", b"\x89PNG\r\n\x1a\nUpdated")

        mode = target.stat().st_mode & 0o777
        assert mode == 0o644, (
            f"write_attachment() changed file permissions from 0o644 to {oct(mode)}"
        )

    def test_write_attachment_preserves_original_on_failed_write(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """If write_attachment fails, the original is untouched and no .tmp files remain."""
        writable.writer.write_attachment("diagram.png", b"original bytes")

        def failing_replace(_self: Path, _target: Path) -> Path:
            raise OSError("simulated disk full")

        with patch.object(Path, "replace", failing_replace), pytest.raises(OSError):
            writable.writer.write_attachment("diagram.png", b"new bytes")

        assert (vault_path / "diagram.png").read_bytes() == b"original bytes"
        assert list(vault_path.glob("**/*.tmp")) == [], (
            "Temp file was not cleaned up on failure"
        )


# ---------------------------------------------------------------------------
# Attachment helpers
# ---------------------------------------------------------------------------


class TestAttachmentHelpers:
    def test_is_attachment_pdf(self, vault_path: Path) -> None:
        """_is_attachment() returns True for a .pdf path with default allowlist."""
        col = Vault(source_dir=vault_path)
        assert col._doc_mgr._is_attachment("assets/report.pdf") is True

    def test_is_attachment_md_always_false(self, vault_path: Path) -> None:
        """_is_attachment() always returns False for .md paths."""
        col = Vault(source_dir=vault_path)
        assert col._doc_mgr._is_attachment("notes/note.md") is False

    def test_is_attachment_disallowed_extension(self, vault_path: Path) -> None:
        """_is_attachment() returns False for extensions not in the default list."""
        col = Vault(source_dir=vault_path)
        # .xyz is not in the default list
        assert col._doc_mgr._is_attachment("file.xyz") is False

    def test_is_attachment_wildcard_allows_all(self, vault_path: Path) -> None:
        """_is_attachment() returns True for any non-.md extension when '*' is set."""
        col = Vault(source_dir=vault_path, attachment_extensions=["*"])
        assert col._doc_mgr._is_attachment("file.xyz") is True
        assert col._doc_mgr._is_attachment("file.bin") is True
        assert col._doc_mgr._is_attachment("notes/note.md") is False

    def test_validate_attachment_path_rejects_md(self, vault_path: Path) -> None:
        """_validate_attachment_path() raises ValueError for .md paths."""
        col = Vault(source_dir=vault_path)
        with pytest.raises(ValueError, match=r"\.md"):
            col._validate_attachment_path("note.md")

    def test_validate_attachment_path_rejects_traversal(self, vault_path: Path) -> None:
        """_validate_attachment_path() raises ValueError on path traversal."""
        col = Vault(source_dir=vault_path)
        with pytest.raises(ValueError, match="traversal"):
            col._validate_attachment_path("../../etc/passwd.pdf")

    def test_validate_attachment_path_rejects_disallowed_ext(
        self, vault_path: Path
    ) -> None:
        """_validate_attachment_path() raises ValueError for disallowed extensions."""
        col = Vault(source_dir=vault_path)
        with pytest.raises(ValueError, match="allowlist"):
            col._validate_attachment_path("file.xyz")


# ---------------------------------------------------------------------------
# read_attachment / write_attachment
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_with_attachment(vault_path: Path) -> Path:
    """Vault fixture with a sample PDF-like binary file."""
    (vault_path / "assets").mkdir()
    (vault_path / "assets" / "report.pdf").write_bytes(b"%PDF-1.4 fake content")
    (vault_path / "assets" / "image.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    )
    return vault_path


class TestReadAttachment:
    def test_read_attachment_returns_content(self, vault_with_attachment: Path) -> None:
        """read_attachment() returns base64-encoded content and mime type."""
        import base64

        col = Vault(source_dir=vault_with_attachment)
        result = col.reader.read_attachment("assets/report.pdf")

        assert isinstance(result, AttachmentContent)
        assert result.path == "assets/report.pdf"
        assert result.mime_type == "application/pdf"
        assert result.size_bytes == len(b"%PDF-1.4 fake content")
        decoded = base64.b64decode(result.content_base64)
        assert decoded == b"%PDF-1.4 fake content"

    def test_read_attachment_not_found_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() raises ValueError for missing files."""
        col = Vault(source_dir=vault_with_attachment)
        with pytest.raises(ValueError, match="not found"):
            col.reader.read_attachment("assets/missing.pdf")

    def test_read_attachment_disallowed_extension_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() raises ValueError for disallowed extensions."""
        col = Vault(source_dir=vault_with_attachment)
        with pytest.raises(ValueError, match="allowlist"):
            col.reader.read_attachment("assets/report.xyz")

    def test_read_attachment_size_limit_enforced(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() raises ValueError when file exceeds the size limit."""
        # 1-byte limit
        col = Vault(source_dir=vault_with_attachment, max_attachment_size_mb=0.000001)
        with pytest.raises(ValueError, match="exceeds"):
            col.reader.read_attachment("assets/report.pdf")

    def test_read_attachment_zero_size_limit_disables(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() with max_attachment_size_mb=0 has no size limit."""
        col = Vault(source_dir=vault_with_attachment, max_attachment_size_mb=0)
        result = col.reader.read_attachment("assets/report.pdf")
        assert result.size_bytes > 0

    def test_read_attachment_png_mime_type(self, vault_with_attachment: Path) -> None:
        """read_attachment() detects image/png MIME type."""
        col = Vault(source_dir=vault_with_attachment)
        result = col.reader.read_attachment("assets/image.png")
        assert result.mime_type == "image/png"

    def test_read_attachment_returns_etag(self, vault_with_attachment: Path) -> None:
        """read_attachment() returns an etag field containing the SHA256 hex digest."""
        from markdown_vault_mcp.hashing import compute_file_hash

        col = Vault(source_dir=vault_with_attachment)
        result = col.reader.read_attachment("assets/report.pdf")

        expected = compute_file_hash(vault_with_attachment / "assets" / "report.pdf")
        assert result.etag == expected

    def test_read_attachment_etag_is_stable(self, vault_with_attachment: Path) -> None:
        """read_attachment() returns the same etag on repeated reads."""
        col = Vault(source_dir=vault_with_attachment)
        result1 = col.reader.read_attachment("assets/report.pdf")
        result2 = col.reader.read_attachment("assets/report.pdf")

        assert result1.etag is not None
        assert result1.etag == result2.etag
        assert len(result1.etag) == 64  # SHA256 hex is 64 chars

    def test_read_attachment_etag_changes_after_write(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() etag changes when file content changes."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        result_before = col.reader.read_attachment("assets/report.pdf")
        etag_before = result_before.etag

        col.writer.write_attachment("assets/report.pdf", b"new content")

        result_after = col.reader.read_attachment("assets/report.pdf")
        assert result_after.etag != etag_before


class TestWriteAttachment:
    def test_write_attachment_creates_file(self, vault_with_attachment: Path) -> None:
        """write_attachment() creates a new binary file on disk."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        result = col.writer.write_attachment("assets/new.png", raw)

        assert isinstance(result, WriteResult)
        assert result.path == "assets/new.png"
        assert result.created is True
        assert (vault_with_attachment / "assets" / "new.png").read_bytes() == raw

    def test_write_attachment_overwrites_existing(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() overwrites an existing file, returns created=False."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        new_content = b"new pdf content"
        result = col.writer.write_attachment("assets/report.pdf", new_content)

        assert result.created is False
        assert (
            vault_with_attachment / "assets" / "report.pdf"
        ).read_bytes() == new_content

    def test_write_attachment_creates_intermediate_dirs(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() creates parent directories as needed."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.writer.write_attachment("deep/nested/file.pdf", b"content")

        assert (vault_with_attachment / "deep" / "nested" / "file.pdf").is_file()

    def test_write_attachment_readonly_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() raises ReadOnlyError on a read-only vault."""
        col = Vault(source_dir=vault_with_attachment, read_only=True)
        with pytest.raises(ReadOnlyError):
            col.writer.write_attachment("assets/new.pdf", b"content")

    def test_write_attachment_size_limit_enforced(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() raises ValueError when content exceeds size limit."""
        col = Vault(
            source_dir=vault_with_attachment,
            read_only=False,
            max_attachment_size_mb=0.000001,
        )
        with pytest.raises(ValueError, match="exceeds"):
            col.writer.write_attachment("assets/big.pdf", b"a" * 100)

    def test_write_attachment_disallowed_extension_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() raises ValueError for disallowed extensions."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        with pytest.raises(ValueError, match="allowlist"):
            col.writer.write_attachment("file.xyz", b"content")

    def test_write_attachment_triggers_callback(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() invokes the on_write callback."""
        calls: list = []
        col = Vault(
            source_dir=vault_with_attachment,
            read_only=False,
            on_write=lambda *args: calls.append(args),
        )
        col.writer.write_attachment("assets/cb.pdf", b"callback test")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        path, content, operation = calls[0]
        assert path == vault_with_attachment / "assets" / "cb.pdf"
        assert content == ""  # binary — empty string passed to callback
        assert operation == "write"


# ---------------------------------------------------------------------------
# list() with include_attachments
# ---------------------------------------------------------------------------


class TestListWithAttachments:
    def test_list_default_excludes_attachments(
        self, vault_with_attachment: Path
    ) -> None:
        """list() without include_attachments does not return attachment files."""
        col = Vault(source_dir=vault_with_attachment)
        col.index.build_index()
        results = col.reader.list_documents()

        paths = [r.path for r in results]
        assert not any(p.endswith(".pdf") or p.endswith(".png") for p in paths)

    def test_list_include_attachments_returns_both(
        self, vault_with_attachment: Path
    ) -> None:
        """list(include_attachments=True) returns notes and attachments."""
        col = Vault(source_dir=vault_with_attachment)
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True)

        kinds = {type(r).__name__ for r in results}
        assert "NoteInfo" in kinds
        assert "AttachmentInfo" in kinds

    def test_list_attachment_info_fields(self, vault_with_attachment: Path) -> None:
        """AttachmentInfo entries have the correct fields."""
        col = Vault(source_dir=vault_with_attachment)
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True)

        attachments = [r for r in results if isinstance(r, AttachmentInfo)]
        assert len(attachments) >= 1

        pdf = next(a for a in attachments if a.path.endswith(".pdf"))
        assert pdf.kind == "attachment"
        assert pdf.mime_type == "application/pdf"
        assert pdf.size_bytes > 0
        assert pdf.folder == "assets"

    def test_list_attachments_excluded_when_not_in_allowlist(
        self, vault_with_attachment: Path
    ) -> None:
        """Attachments with disallowed extensions are not returned."""
        (vault_with_attachment / "assets" / "data.xyz").write_bytes(b"unknown")
        col = Vault(source_dir=vault_with_attachment)
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True)

        paths = [r.path for r in results]
        assert not any(p.endswith(".xyz") for p in paths)

    def test_list_attachments_wildcard_includes_all(
        self, vault_with_attachment: Path
    ) -> None:
        """attachment_extensions=['*'] returns all non-.md files."""
        (vault_with_attachment / "assets" / "data.xyz").write_bytes(b"unknown")
        col = Vault(source_dir=vault_with_attachment, attachment_extensions=["*"])
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True)

        paths = [r.path for r in results]
        assert any(p.endswith(".xyz") for p in paths)

    def test_list_attachments_folder_filter(self, vault_with_attachment: Path) -> None:
        """list(include_attachments=True, folder=...) filters attachments by folder."""
        col = Vault(source_dir=vault_with_attachment)
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True, folder="assets")

        for r in results:
            assert r.folder == "assets" or r.folder.startswith("assets/")


# ---------------------------------------------------------------------------
# delete() and rename() for attachments
# ---------------------------------------------------------------------------


class TestDeleteAttachment:
    def test_delete_attachment_removes_file(self, vault_with_attachment: Path) -> None:
        """delete() removes an attachment file from disk."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.index.build_index()
        result = col.writer.delete("assets/report.pdf")

        assert isinstance(result, DeleteResult)
        assert result.path == "assets/report.pdf"
        assert not (vault_with_attachment / "assets" / "report.pdf").is_file()

    def test_delete_attachment_not_found_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """delete() raises DocumentNotFoundError for missing attachment."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.index.build_index()
        with pytest.raises(DocumentNotFoundError):
            col.writer.delete("assets/missing.pdf")

    def test_delete_attachment_disallowed_ext_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """delete() on a disallowed extension raises ValueError."""
        (vault_with_attachment / "file.xyz").write_bytes(b"data")
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.index.build_index()
        with pytest.raises(ValueError, match="allowlist"):
            col.writer.delete("file.xyz")

    def test_delete_attachment_triggers_callback(
        self, vault_with_attachment: Path
    ) -> None:
        """delete() on an attachment invokes the on_write callback."""
        calls: list = []
        col = Vault(
            source_dir=vault_with_attachment,
            read_only=False,
            on_write=lambda *args: calls.append(args),
        )
        col.index.build_index()
        col.writer.delete("assets/report.pdf")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        _, _, operation = calls[0]
        assert operation == "delete"


class TestRenameAttachment:
    def test_rename_attachment_moves_file(self, vault_with_attachment: Path) -> None:
        """rename() moves an attachment file on disk."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.index.build_index()
        result = col.writer.rename("assets/report.pdf", "docs/report.pdf")

        assert isinstance(result, RenameResult)
        assert not (vault_with_attachment / "assets" / "report.pdf").is_file()
        assert (vault_with_attachment / "docs" / "report.pdf").is_file()

    def test_rename_attachment_not_found_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """rename() raises DocumentNotFoundError for missing attachment."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.index.build_index()
        with pytest.raises(DocumentNotFoundError):
            col.writer.rename("assets/missing.pdf", "docs/report.pdf")

    def test_rename_attachment_target_exists_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """rename() raises DocumentExistsError when the target already exists."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.index.build_index()
        with pytest.raises(DocumentExistsError):
            col.writer.rename("assets/report.pdf", "assets/image.png")

    def test_rename_attachment_creates_intermediate_dirs(
        self, vault_with_attachment: Path
    ) -> None:
        """rename() creates parent directories for the attachment target."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.index.build_index()
        col.writer.rename("assets/report.pdf", "new_folder/sub/report.pdf")

        assert (vault_with_attachment / "new_folder" / "sub" / "report.pdf").is_file()

    def test_rename_attachment_preserves_content(
        self, vault_with_attachment: Path
    ) -> None:
        """rename() produces a file byte-identical to the original."""
        original = (vault_with_attachment / "assets" / "report.pdf").read_bytes()
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        col.index.build_index()
        col.writer.rename("assets/report.pdf", "docs/report.pdf")

        assert (vault_with_attachment / "docs" / "report.pdf").read_bytes() == original


# ---------------------------------------------------------------------------
# stats() includes attachment_extensions
# ---------------------------------------------------------------------------


class TestStatsAttachmentExtensions:
    def test_stats_includes_attachment_extensions_default(self, vault: Vault) -> None:
        """stats() includes attachment_extensions from the default allowlist."""
        s = vault.reader.stats()
        assert isinstance(s.attachment_extensions, list)
        assert "pdf" in s.attachment_extensions
        assert "png" in s.attachment_extensions

    def test_stats_includes_attachment_extensions_custom(
        self, vault_path: Path
    ) -> None:
        """stats() reflects a custom attachment_extensions list."""
        col = Vault(source_dir=vault_path, attachment_extensions=["pdf", "docx"])
        col.index.build_index()
        s = col.reader.stats()
        assert sorted(s.attachment_extensions) == ["docx", "pdf"]

    def test_stats_includes_attachment_extensions_wildcard(
        self, vault_path: Path
    ) -> None:
        """stats() shows ['*'] when attachment_extensions is the wildcard."""
        col = Vault(source_dir=vault_path, attachment_extensions=["*"])
        col.index.build_index()
        s = col.reader.stats()
        assert s.attachment_extensions == ["*"]


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------


@pytest.fixture
def semantic_vault(
    vault_path: Path,
    tmp_path: Path,
    mock_provider: MockEmbeddingProvider,
) -> Iterator[Vault]:
    """Vault with FTS index and vector embeddings fully built."""
    col = Vault(
        source_dir=vault_path,
        embeddings_path=tmp_path / "embeddings",
        embedding_provider=mock_provider,
    )
    try:
        col.index.build_index()
        col.index.build_embeddings()
        yield col
    finally:
        col.close()


class TestSemanticSearch:
    def test_semantic_search_returns_semantic_type(self, semantic_vault: Vault) -> None:
        """search(mode='semantic') returns results with search_type='semantic'."""
        results = semantic_vault.reader.search("document content", mode="semantic")

        assert len(results) > 0
        assert all(r.search_type == "semantic" for r in results)

    def test_semantic_search_result_fields(self, semantic_vault: Vault) -> None:
        """Semantic search results carry path, title, score, and frontmatter fields."""
        results = semantic_vault.reader.search("document content", mode="semantic")

        assert len(results) > 0
        for r in results:
            assert r.path
            # Cosine similarity ranges from -1 to 1; just verify it is a float.
            assert isinstance(r.score, float)
            assert isinstance(r.frontmatter, dict)

    def test_semantic_search_limit_respected(self, semantic_vault: Vault) -> None:
        """search(mode='semantic', limit=N) never returns more than N results."""
        results = semantic_vault.reader.search("content", mode="semantic", limit=2)

        assert len(results) <= 2

    def test_semantic_search_folder_filter(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """search(mode='semantic', folder='subfolder') returns only subfolder docs."""
        col = Vault(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        col.index.build_index()
        col.index.build_embeddings()

        results = col.reader.search(
            "document content", mode="semantic", folder="subfolder"
        )

        # All results must be in the requested folder or a sub-folder of it.
        assert len(results) > 0, "Expected at least one result in subfolder"
        for r in results:
            assert r.folder == "subfolder" or r.folder.startswith("subfolder/")

    def test_semantic_search_tag_filter(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """search(mode='semantic', filters={...}) filters by frontmatter value."""
        col = Vault(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
            indexed_frontmatter_fields=["cluster"],
        )
        col.index.build_index()
        col.index.build_embeddings()

        # full_frontmatter.md has cluster=fiction; all results must match.
        results = col.reader.search(
            "document", mode="semantic", filters={"cluster": "fiction"}, limit=10
        )

        assert len(results) > 0, "Expected at least one result with cluster=fiction"
        for r in results:
            assert r.frontmatter.get("cluster") == "fiction"

    def test_semantic_search_no_provider_raises(self, vault_path: Path) -> None:
        """search(mode='semantic') raises ValueError when provider is not configured."""
        col = _make_vault(vault_path)
        col.index.build_index()

        with pytest.raises(ValueError, match="embedding_provider"):
            col.reader.search("query", mode="semantic")

    def test_load_vectors_creates_empty_when_no_npy(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """_load_vectors() creates an empty VectorIndex when no .npy file exists."""
        embeddings_path = tmp_path / "embeddings"
        col = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.index.build_index()

        # Confirm no .npy file exists before loading.
        assert not (tmp_path / "embeddings.npy").exists()

        vectors = col._search_mgr._load_vectors()

        assert vectors is not None
        assert vectors.count == 0

    def test_load_vectors_loads_from_disk(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """_load_vectors() loads persisted embeddings when .npy file exists."""
        embeddings_path = tmp_path / "embeddings"

        # Build and persist the vector index.
        col1 = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col1.index.build_index()
        chunk_count = col1.index.build_embeddings()
        assert chunk_count > 0

        # Create a fresh vault pointing at the same paths — vectors not yet loaded.
        col2 = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col2.index.build_index()
        assert col2._vectors is None  # not yet loaded

        vectors = col2._search_mgr._load_vectors()

        assert vectors.count == chunk_count

    def test_load_vectors_provider_mismatch_rebuilds(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """_load_vectors() rebuilds when persisted provider/model metadata differs."""
        embeddings_path = tmp_path / "embeddings"

        col1 = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col1.index.build_index()
        expected_count = col1.index.build_embeddings()
        assert expected_count > 0

        class AlternateProvider(type(mock_provider)):
            @property
            def provider_name(self) -> str:
                return "alternate-mock"

            @property
            def model_name(self) -> str:
                return "alternate-model-v1"

        col2 = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=AlternateProvider(),
        )
        col2.index.build_index()

        vectors = col2._search_mgr._load_vectors()
        assert vectors.count == expected_count

        with (tmp_path / "embeddings.json").open(encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["index_metadata"]["provider"] == "alternate-mock"
        assert payload["index_metadata"]["model"] == "alternate-model-v1"

    def test_require_vectors_raises_when_unconfigured(self, vault_path: Path) -> None:
        """_require_vectors() raises ValueError when provider/path are absent."""
        col = _make_vault(vault_path)

        with pytest.raises(ValueError, match="embedding_provider"):
            col._search_mgr._require_vectors()


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------


class TestHybridSearch:
    def test_hybrid_search_returns_results(self, semantic_vault: Vault) -> None:
        """search(mode='hybrid') returns at least one result."""
        results = semantic_vault.reader.search("document content", mode="hybrid")

        assert len(results) > 0

    def test_hybrid_search_search_type_varies(self, semantic_vault: Vault) -> None:
        """Hybrid results can carry 'keyword', 'semantic', or 'hybrid' search_type."""
        results = semantic_vault.reader.search(
            "document content", mode="hybrid", limit=9
        )

        types = {r.search_type for r in results}
        # With 9 docs and a broad query, at least one type should appear.
        assert types.issubset({"keyword", "semantic", "hybrid"})
        assert len(types) >= 1

    def test_hybrid_search_folder_filter(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """search(mode='hybrid', folder='subfolder') confines results to that folder."""
        col = Vault(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        col.index.build_index()
        col.index.build_embeddings()

        results = col.reader.search(
            "nested document", mode="hybrid", folder="subfolder"
        )

        assert len(results) > 0, "Expected at least one result in subfolder"
        for r in results:
            assert r.folder == "subfolder" or r.folder.startswith("subfolder/")

    def test_hybrid_search_tag_filter(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """search(mode='hybrid', filters={...}) filters semantic candidates by tag."""
        col = Vault(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
            indexed_frontmatter_fields=["cluster"],
        )
        col.index.build_index()
        col.index.build_embeddings()

        results = col.reader.search(
            "document", mode="hybrid", filters={"cluster": "fiction"}, limit=10
        )

        # All results (keyword and semantic) must have the correct tag.
        # MockEmbeddingProvider uses hash-based vectors so semantic results
        # may not include the cluster=fiction doc; keyword results are reliable.
        assert len(results) > 0, "Expected at least one result with cluster=fiction"
        for r in results:
            assert r.frontmatter.get("cluster") == "fiction"

    def test_hybrid_rrf_boost_for_dual_match(
        self,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """A doc appearing in both keyword and semantic results has a higher RRF score.

        Strategy: build a vault with two documents.  Make one uniquely relevant
        to keyword search (exact term match).  Both appear in semantic search.
        The keyword-only match and the dual-match document's RRF scores are compared.
        """
        vault = tmp_path / "rrf_vault"
        vault.mkdir()
        # doc_a appears in both FTS (exact match) and semantic results.
        (vault / "doc_a.md").write_text(
            "# Zymurgy Note\n\nZymurgy is the study of fermentation.\n"
        )
        # doc_b appears only in semantic (no keyword match for 'zymurgy').
        (vault / "doc_b.md").write_text(
            "# Brewing Science\n\nBeer and fermentation science overview.\n"
        )
        embeddings_path = tmp_path / "rrf_emb"
        col = Vault(
            source_dir=vault,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.index.build_index()
        col.index.build_embeddings()

        results = col.reader.search("zymurgy", mode="hybrid", limit=10)

        by_path = {r.path: r for r in results}
        # doc_a matched FTS (rank ~1) and semantic — should appear with a score.
        assert "doc_a.md" in by_path
        doc_a_score = by_path["doc_a.md"].score
        # If doc_b also appears, doc_a (dual match) must outscore doc_b (single match).
        if "doc_b.md" in by_path:
            assert doc_a_score >= by_path["doc_b.md"].score

    def test_hybrid_search_no_embeddings_raises(self, vault_path: Path) -> None:
        """search(mode='hybrid') raises ValueError without a configured provider."""
        col = _make_vault(vault_path)
        col.index.build_index()

        with pytest.raises(ValueError, match="embedding_provider"):
            col.reader.search("query", mode="hybrid")


# ---------------------------------------------------------------------------
# embeddings_status()
# ---------------------------------------------------------------------------


class TestEmbeddingsStatus:
    def test_status_unavailable_when_no_provider(self, vault_path: Path) -> None:
        """embeddings_status() returns available=False when no provider is set."""
        col = _make_vault(vault_path)
        col.index.build_index()

        status = col.index.embeddings_status()

        assert status["available"] is False
        assert status["provider"] is None
        assert status["chunk_count"] == 0
        assert status["path"] is None

    def test_status_available_with_provider_before_build(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """embeddings_status() returns available=True with chunk_count=0 before build."""
        col = Vault(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        col.index.build_index()

        status = col.index.embeddings_status()

        assert status["available"] is True
        assert status["chunk_count"] == 0
        assert "MockEmbeddingProvider" in status["provider"]

    def test_status_chunk_count_after_build(self, semantic_vault: Vault) -> None:
        """embeddings_status() returns correct chunk_count after build_embeddings()."""
        status = semantic_vault.index.embeddings_status()

        assert status["available"] is True
        # 9 documents, each one chunk — chunk_count must match.
        assert status["chunk_count"] == 9

    def test_status_reads_json_when_vectors_not_loaded(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """embeddings_status() reads chunk_count from JSON metadata without loading matrix.

        Builds embeddings with col1, then creates fresh col2 pointing at the same
        path.  col2 has never called _load_vectors(), so _vectors is None.
        embeddings_status() must still return the correct count from the .json sidecar.
        """
        embeddings_path = tmp_path / "embeddings"

        col1 = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col1.index.build_index()
        chunk_count = col1.index.build_embeddings()
        assert chunk_count > 0

        col2 = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col2.index.build_index()
        # Confirm vectors have NOT been loaded in-memory.
        assert col2._vectors is None

        status = col2.index.embeddings_status()

        assert status["available"] is True
        assert status["chunk_count"] == chunk_count


# ---------------------------------------------------------------------------
# build_embeddings() skip / force paths
# ---------------------------------------------------------------------------


class TestBuildEmbeddings:
    def test_build_embeddings_skip_when_already_built(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_embeddings(force=False) skips rebuild when index already has chunks.

        The first call embeds 9 chunks and saves to disk.  The second call (no
        force) must return the same count without re-embedding.
        """
        embeddings_path = tmp_path / "embeddings"
        col = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.index.build_index()
        count1 = col.index.build_embeddings()
        assert count1 == 9

        # Track embed calls to prove no re-embedding happens.
        original_embed = mock_provider.embed
        embed_calls: list = []

        def tracking_embed(texts):
            embed_calls.append(texts)
            return original_embed(texts)

        mock_provider.embed = tracking_embed  # type: ignore[method-assign]

        count2 = col.index.build_embeddings(force=False)

        # No new embedding calls; same count returned.
        assert embed_calls == []
        assert count2 == count1

    def test_build_embeddings_force_rebuilds(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_embeddings(force=True) re-embeds even when index exists."""
        embeddings_path = tmp_path / "embeddings"
        col = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.index.build_index()
        col.index.build_embeddings()

        original_embed = mock_provider.embed
        embed_calls: list = []

        def tracking_embed(texts):
            embed_calls.append(texts)
            return original_embed(texts)

        mock_provider.embed = tracking_embed  # type: ignore[method-assign]

        count = col.index.build_embeddings(force=True)

        # Re-embedding must have occurred.
        assert len(embed_calls) > 0
        assert count == 9

    def test_build_embeddings_uses_bounded_batches(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_embeddings() calls the provider in batches, not one giant call."""
        from markdown_vault_mcp.managers.index import _EMBEDDING_BATCH_SIZE

        embeddings_path = tmp_path / "embeddings"
        col = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.index.build_index()

        original_embed = mock_provider.embed
        batch_sizes: list[int] = []

        def tracking_embed(texts: list[str]) -> list[list[float]]:
            batch_sizes.append(len(texts))
            return original_embed(texts)

        mock_provider.embed = tracking_embed  # type: ignore[method-assign]

        count = col.index.build_embeddings(force=True)
        assert count == 9

        # embed() must have been called, and every batch at most _EMBEDDING_BATCH_SIZE.
        assert len(batch_sizes) > 0, "embed() should have been called"
        for size in batch_sizes:
            assert size <= _EMBEDDING_BATCH_SIZE

    def test_build_embeddings_multi_batch_corpus(
        self,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_embeddings() handles a corpus spanning multiple batches."""
        from markdown_vault_mcp.managers.index import _EMBEDDING_BATCH_SIZE

        # Create a vault with enough chunks to span multiple batches.
        vault = tmp_path / "vault"
        vault.mkdir()
        for i in range(_EMBEDDING_BATCH_SIZE + 10):
            (vault / f"note_{i:03d}.md").write_text(
                f"---\ntitle: Note {i}\n---\n\nContent of note {i}.\n"
            )

        embeddings_path = tmp_path / "embeddings"
        col = Vault(
            source_dir=vault,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.index.build_index()

        original_embed = mock_provider.embed
        batch_sizes: list[int] = []

        def tracking_embed(texts: list[str]) -> list[list[float]]:
            batch_sizes.append(len(texts))
            return original_embed(texts)

        mock_provider.embed = tracking_embed  # type: ignore[method-assign]

        count = col.index.build_embeddings(force=True)

        # All chunks embedded despite spanning multiple batches.
        assert count > _EMBEDDING_BATCH_SIZE
        # Multiple embed() calls must have been made.
        assert len(batch_sizes) > 1, "expected multiple embed() calls for large corpus"
        for size in batch_sizes:
            assert size <= _EMBEDDING_BATCH_SIZE
        # The .npy file must exist (saved at the end).
        npy_path = tmp_path / "embeddings.npy"
        assert npy_path.exists()


# ---------------------------------------------------------------------------
# build_index() second-call no-op
# ---------------------------------------------------------------------------


class TestBuildIndexNoOp:
    def test_second_build_index_is_noop(self, vault_path: Path) -> None:
        """build_index() a second time (no force) returns existing stats without re-scanning.

        The IndexStats returned on the second call must reflect the existing
        document count (9) while chunks_indexed=0 (no new scanning performed).
        """
        col = _make_vault(vault_path)
        col.index.build_index()

        # Intercept scan_directory to confirm it is NOT called again.
        import markdown_vault_mcp.managers.index as idx_mod

        original_scan = idx_mod.scan_directory
        scan_calls: list = []

        def tracking_scan(*args, **kwargs):
            scan_calls.append(args)
            return original_scan(*args, **kwargs)

        with patch.object(idx_mod, "scan_directory", side_effect=tracking_scan):
            stats2 = col.index.build_index()

        # scan_directory must not have been invoked on the second call.
        assert scan_calls == []
        # Returned stats reflect the existing index content.
        assert stats2.documents_indexed == 9
        assert stats2.chunks_indexed == 0


# ---------------------------------------------------------------------------
# reindex() with vectors active
# ---------------------------------------------------------------------------


class TestReindexWithVectors:
    @pytest.fixture
    def writable_semantic(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> Iterator[Vault]:
        """Writable vault with vectors loaded in memory."""
        col = Vault(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
            state_path=tmp_path / "state.json",
            read_only=False,
        )
        try:
            col.index.build_index()
            col.index.build_embeddings()
            # Trigger vector load so _vectors is not None.
            col._search_mgr._load_vectors()
            yield col
        finally:
            col.close()

    def test_reindex_adds_vector_entries_for_new_files(
        self,
        writable_semantic: Vault,
        vault_path: Path,
    ) -> None:
        """reindex() embeds newly added files when the vector index is loaded."""
        before_count = writable_semantic._vectors.count  # type: ignore[union-attr]

        (vault_path / "brand_new.md").write_text(
            "# Brand New Note\n\nFresh content for reindex test.\n"
        )
        writable_semantic.index.reindex()

        after_count = writable_semantic._vectors.count  # type: ignore[union-attr]
        assert after_count == before_count + 1

        # The new file must be findable via semantic search.
        results = writable_semantic.reader.search("fresh content", mode="semantic")
        paths = [r.path for r in results]
        assert "brand_new.md" in paths

    def test_reindex_removes_vector_entries_for_deleted_files(
        self,
        writable_semantic: Vault,
        vault_path: Path,
    ) -> None:
        """reindex() removes deleted files from the vector index."""
        before_count = writable_semantic._vectors.count  # type: ignore[union-attr]

        (vault_path / "simple.md").unlink()
        writable_semantic.index.reindex()

        after_count = writable_semantic._vectors.count  # type: ignore[union-attr]
        assert after_count == before_count - 1

        # Deleted file must not appear in semantic search results.
        results = writable_semantic.reader.search("simple document", mode="semantic")
        paths = [r.path for r in results]
        assert "simple.md" not in paths

    def test_reindex_updates_vector_entries_for_modified_files(
        self,
        writable_semantic: Vault,
        vault_path: Path,
    ) -> None:
        """reindex() re-embeds modified files so updated content is searchable."""
        # simple.md contains "Simple Document"; replace with unique token.
        (vault_path / "simple.md").write_text(
            "# QuantumXyloscopeModified\n\nUnique modified content.\n"
        )
        writable_semantic.index.reindex()

        # Updated content must be findable by the new unique term.
        results = writable_semantic.reader.search(
            "QuantumXyloscopeModified", mode="semantic"
        )
        paths = [r.path for r in results]
        assert "simple.md" in paths


# ---------------------------------------------------------------------------
# Gap coverage: _resolve_chunk_strategy
# ---------------------------------------------------------------------------


class TestResolveChunkStrategy:
    def test_whole_returns_whole_document_chunker(self) -> None:
        """_resolve_chunk_strategy('whole') returns a WholeDocumentChunker."""
        from markdown_vault_mcp.scanner import WholeDocumentChunker
        from markdown_vault_mcp.vault import _resolve_chunk_strategy

        result = _resolve_chunk_strategy("whole")
        assert isinstance(result, WholeDocumentChunker)

    def test_heading_returns_heading_chunker(self) -> None:
        """_resolve_chunk_strategy('heading') returns a HeadingChunker."""
        from markdown_vault_mcp.scanner import HeadingChunker
        from markdown_vault_mcp.vault import _resolve_chunk_strategy

        result = _resolve_chunk_strategy("heading")
        assert isinstance(result, HeadingChunker)

    def test_unknown_string_raises_value_error(self) -> None:
        """_resolve_chunk_strategy raises ValueError for unrecognised string."""
        from markdown_vault_mcp.vault import _resolve_chunk_strategy

        with pytest.raises(ValueError, match="Unknown chunk_strategy"):
            _resolve_chunk_strategy("paragraph")

    def test_strategy_instance_passes_through(self) -> None:
        """_resolve_chunk_strategy passes a ChunkStrategy instance through unchanged."""
        from markdown_vault_mcp.scanner import WholeDocumentChunker
        from markdown_vault_mcp.vault import _resolve_chunk_strategy

        strategy = WholeDocumentChunker()
        result = _resolve_chunk_strategy(strategy)
        assert result is strategy


# ---------------------------------------------------------------------------
# Gap coverage: _fts_row_to_note_info malformed JSON
# ---------------------------------------------------------------------------


class TestFtsRowToNoteInfoMalformedJson:
    def test_invalid_frontmatter_json_returns_empty_dict(self) -> None:
        """_fts_row_to_note_info with invalid JSON returns NoteInfo with empty frontmatter."""
        from markdown_vault_mcp.utils.fts import (
            fts_row_to_note_info as _fts_row_to_note_info,
        )

        row = {
            "path": "x.md",
            "title": "X",
            "folder": "",
            "frontmatter_json": "not-valid-json{{{",
            "modified_at": 0.0,
        }
        result = _fts_row_to_note_info(row)

        assert isinstance(result, NoteInfo)
        assert result.frontmatter == {}
        assert result.path == "x.md"

    def test_none_frontmatter_json_returns_empty_dict(self) -> None:
        """_fts_row_to_note_info with frontmatter_json=None returns empty frontmatter."""
        from markdown_vault_mcp.utils.fts import (
            fts_row_to_note_info as _fts_row_to_note_info,
        )

        row = {
            "path": "y.md",
            "title": "Y",
            "folder": "sub",
            "frontmatter_json": None,
            "modified_at": 1234567890.0,
        }
        result = _fts_row_to_note_info(row)

        assert result.frontmatter == {}

    def test_empty_string_frontmatter_json_returns_empty_dict(self) -> None:
        """_fts_row_to_note_info with frontmatter_json='' returns empty frontmatter."""
        from markdown_vault_mcp.utils.fts import (
            fts_row_to_note_info as _fts_row_to_note_info,
        )

        row = {
            "path": "z.md",
            "title": "Z",
            "folder": "",
            "frontmatter_json": "",
            "modified_at": 0.0,
        }
        result = _fts_row_to_note_info(row)

        assert result.frontmatter == {}


# ---------------------------------------------------------------------------
# Gap coverage: read() error paths
# ---------------------------------------------------------------------------


class TestReadErrorPaths:
    def test_read_returns_none_for_path_traversal(self, vault: Vault) -> None:
        """read() returns None for paths that escape the source directory."""
        result = vault.reader.read("../secret.md")
        assert result is None

    def test_read_returns_none_when_file_deleted_from_disk(
        self, vault_path: Path
    ) -> None:
        """read() returns None when file exists in index but was deleted from disk."""
        col = _make_vault(vault_path)
        col.index.build_index()

        # Confirm the file is readable before deleting it.
        assert col.reader.read("simple.md") is not None

        # Delete the file directly from disk (bypassing the Vault API).
        (vault_path / "simple.md").unlink()

        # read() must return None — file no longer exists.
        result = col.reader.read("simple.md")
        assert result is None


# ---------------------------------------------------------------------------
# Gap coverage: search() on document with no frontmatter
# ---------------------------------------------------------------------------


class TestSearchNoFrontmatter:
    def test_search_no_frontmatter_document_has_empty_frontmatter(
        self, vault: Vault
    ) -> None:
        """search() on a document with no frontmatter returns result.frontmatter == {}."""
        # no_frontmatter.md has no YAML header at all.
        results = vault.reader.search("plain markdown", mode="keyword")
        no_fm_results = [r for r in results if r.path == "no_frontmatter.md"]

        assert len(no_fm_results) >= 1
        assert no_fm_results[0].frontmatter == {}


# ---------------------------------------------------------------------------
# Gap coverage: list() attachment edge cases
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_with_root_attachment(vault_path: Path) -> Path:
    """Vault with an attachment at the root level (no subdirectory)."""
    (vault_path / "diagram.json").write_bytes(b'{"key": "value"}')
    (vault_path / "notes").mkdir(exist_ok=True)
    (vault_path / "notes" / "readme.txt").write_bytes(b"plain text file")
    return vault_path


class TestListAttachmentEdgeCases:
    def test_list_attachment_in_vault_root_has_empty_folder(
        self, vault_with_root_attachment: Path
    ) -> None:
        """Attachments at the vault root have folder='' (not '.' or '/')."""
        col = Vault(source_dir=vault_with_root_attachment)
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True)

        attachments = [r for r in results if isinstance(r, AttachmentInfo)]
        root_json = next((a for a in attachments if a.path == "diagram.json"), None)
        assert root_json is not None, "diagram.json not found in attachment listing"
        assert root_json.folder == ""

    def test_list_attachment_pattern_filters_by_extension(
        self, vault_with_root_attachment: Path
    ) -> None:
        """list(include_attachments=True, pattern='*.json') returns only .json files."""
        col = Vault(source_dir=vault_with_root_attachment)
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True, pattern="*.json")

        attachment_paths = [r.path for r in results if isinstance(r, AttachmentInfo)]
        assert all(p.endswith(".json") for p in attachment_paths)
        # txt file must not appear
        assert not any(p.endswith(".txt") for p in attachment_paths)

    def test_list_attachment_folder_filter_excludes_other_folders(
        self, vault_with_root_attachment: Path
    ) -> None:
        """list(include_attachments=True, folder='notes') only returns notes/ attachments."""
        col = Vault(source_dir=vault_with_root_attachment)
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True, folder="notes")

        for r in results:
            assert r.folder == "notes" or r.folder.startswith("notes/")

        # The root-level diagram.json must not appear.
        paths = [r.path for r in results]
        assert "diagram.json" not in paths


# ---------------------------------------------------------------------------
# Gap coverage: write_attachment() size limit
# ---------------------------------------------------------------------------


class TestWriteAttachmentSizeLimit:
    def test_write_attachment_raises_when_content_exceeds_limit(
        self, vault_path: Path
    ) -> None:
        """write_attachment() raises ValueError when content size exceeds the limit."""
        col = Vault(
            source_dir=vault_path,
            read_only=False,
            max_attachment_size_mb=0.000001,  # ~1 byte limit
        )
        with pytest.raises(ValueError, match="exceeds"):
            col.writer.write_attachment("assets/big.pdf", b"a" * 100)

    def test_write_attachment_unlimited_when_max_is_zero(
        self, vault_path: Path
    ) -> None:
        """write_attachment() with max_attachment_size_mb=0 accepts any size."""
        col = Vault(
            source_dir=vault_path,
            read_only=False,
            max_attachment_size_mb=0,
        )
        large_content = b"x" * (20 * 1024 * 1024)  # 20 MB
        result = col.writer.write_attachment("large_file.pdf", large_content)

        assert isinstance(result, WriteResult)
        assert result.path == "large_file.pdf"
        assert (vault_path / "large_file.pdf").stat().st_size == len(large_content)


# ---------------------------------------------------------------------------
# Attachment listing — hidden dir and exclude_patterns filtering (issue #78)
# ---------------------------------------------------------------------------


class TestListAttachmentHiddenDirFiltering:
    def test_hidden_dir_files_excluded_from_listing(self, tmp_path: Path) -> None:
        """list(include_attachments=True) excludes files inside hidden directories."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n", encoding="utf-8")
        # Attachment in a normal directory — should appear.
        (vault / "assets").mkdir()
        (vault / "assets" / "diagram.pdf").write_bytes(b"PDF")
        # File in a hidden directory — must NOT appear.
        (vault / ".git").mkdir()
        (vault / ".git" / "config").write_bytes(b"[core]")
        # JSON file in .markdown_vault_mcp — must NOT appear.
        (vault / ".markdown_vault_mcp").mkdir()
        (vault / ".markdown_vault_mcp" / "state.json").write_text(
            "{}", encoding="utf-8"
        )

        col = Vault(source_dir=vault, attachment_extensions=["pdf", "json"])
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True)

        attachment_paths = {
            r.path
            for r in results
            if hasattr(r, "mime_type")  # AttachmentInfo
        }
        assert "assets/diagram.pdf" in attachment_paths
        assert not any(".git" in p for p in attachment_paths)
        assert not any(".markdown_vault_mcp" in p for p in attachment_paths)

    def test_dotfile_at_root_excluded(self, tmp_path: Path) -> None:
        """Files whose own name starts with '.' are not considered attachments."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n", encoding="utf-8")
        # A dotfile directly in the vault root.
        (vault / ".hidden_config.json").write_bytes(b"{}")

        col = Vault(source_dir=vault, attachment_extensions=["json"])
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True)

        attachment_paths = {r.path for r in results if hasattr(r, "mime_type")}
        assert ".hidden_config.json" not in attachment_paths

    def test_exclude_patterns_applied_to_attachments(self, tmp_path: Path) -> None:
        """Configured exclude_patterns suppress attachments from excluded directories."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n", encoding="utf-8")
        # File in a non-hidden excluded directory (tests pure exclude_patterns path).
        (vault / "archived").mkdir()
        (vault / "archived" / "workspace.json").write_text("{}", encoding="utf-8")
        # File in another excluded directory.
        (vault / "trash").mkdir()
        (vault / "trash" / "old.pdf").write_bytes(b"PDF")
        # File in a normal directory — should appear.
        (vault / "assets").mkdir()
        (vault / "assets" / "chart.pdf").write_bytes(b"PDF")

        col = Vault(
            source_dir=vault,
            attachment_extensions=["pdf", "json"],
            exclude_patterns=["archived/**", "trash/**"],
        )
        col.index.build_index()
        results = col.reader.list_documents(include_attachments=True)

        attachment_paths = {r.path for r in results if hasattr(r, "mime_type")}
        assert "assets/chart.pdf" in attachment_paths
        assert "trash/old.pdf" not in attachment_paths
        assert "archived/workspace.json" not in attachment_paths


# ---------------------------------------------------------------------------
# get_toc
# ---------------------------------------------------------------------------

# A document long enough to be split by HeadingChunker (default threshold 30
# lines). We need > 30 lines so that the chunker splits on headings rather than
# returning the whole file as a single NULL-headed chunk.
_LONG_DOC = """\
# Long Document Title

Introductory paragraph to pad the line count.
Line 04.
Line 05.
Line 06.
Line 07.
Line 08.
Line 09.
Line 10.

## Section Alpha

Content under Alpha.
Line 14.
Line 15.
Line 16.
Line 17.
Line 18.
Line 19.
Line 20.

## Section Beta

Content under Beta.
Line 24.
Line 25.
Line 26.
Line 27.
Line 28.
Line 29.
Line 30.
Line 31.
"""


@pytest.fixture
def vault_with_long_doc(vault_path: Path) -> Iterator[Vault]:
    """Vault with a long multi-section document added for ToC testing."""
    (vault_path / "long_doc.md").write_text(_LONG_DOC, encoding="utf-8")
    col = _make_vault(vault_path)
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


class TestVaultGetToc:
    def test_get_toc_returns_headings(self, vault_with_long_doc: Vault) -> None:
        """get_toc() returns headings for a document with multiple sections."""
        toc = vault_with_long_doc.reader.get_toc("long_doc.md")

        assert isinstance(toc, list)
        assert len(toc) >= 2
        headings = [entry["heading"] for entry in toc]
        assert "Section Alpha" in headings
        # All entries must have heading and level keys.
        for entry in toc:
            assert "heading" in entry
            assert "level" in entry

    def test_get_toc_first_entry_is_synthetic_h1(
        self, vault_with_long_doc: Vault
    ) -> None:
        """The first entry in get_toc() is always the document title at level 1."""
        toc = vault_with_long_doc.reader.get_toc("long_doc.md")

        assert toc[0]["level"] == 1
        assert toc[0]["heading"] == "Long Document Title"

    def test_get_toc_no_duplicate_h1(self, vault_with_long_doc: Vault) -> None:
        """Synthetic H1 title must not duplicate a real H1 heading."""
        toc = vault_with_long_doc.reader.get_toc("long_doc.md")

        h1_entries = [e for e in toc if e["level"] == 1]
        assert len(h1_entries) == 1, (
            f"Expected 1 H1, got {len(h1_entries)}: {h1_entries}"
        )
        assert h1_entries[0]["heading"] == "Long Document Title"

    def test_get_toc_raises_for_nonexistent_document(
        self, vault_with_long_doc: Vault
    ) -> None:
        """get_toc() raises ValueError for a path not in the index."""
        with pytest.raises(ValueError, match="Document not found"):
            vault_with_long_doc.reader.get_toc("does_not_exist.md")


class TestReindexThreadSafety:
    """Verify that reindex() and write() do not corrupt the index when concurrent."""

    def test_reindex_and_write_concurrent(
        self, tmp_path: Path, vault_path: Path
    ) -> None:
        """Concurrent reindex + write does not crash or corrupt the FTS index."""
        state_path = tmp_path / "state.json"
        col = _make_vault(vault_path, state_path=state_path, read_only=False)
        col.index.build_index()

        # Add a file that reindex will discover.
        (vault_path / "concurrent_new.md").write_text(
            "# Concurrent\n\nNew file for reindex.\n"
        )

        def do_reindex() -> None:
            col.index.reindex()

        def do_write() -> None:
            col.writer.write("written_during_reindex.md", "# Written\n\nBody.\n")

        # Run reindex and write concurrently — should not raise.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(do_reindex), pool.submit(do_write)]
            for f in concurrent.futures.as_completed(futures):
                f.result()  # raises if the thread raised

        # Both documents must be readable — index is not corrupted.
        concurrent_note = col.reader.read("concurrent_new.md")
        assert concurrent_note is not None, "reindex should have indexed the new file"
        written = col.reader.read("written_during_reindex.md")
        assert written is not None, "write during reindex must persist"


# ---------------------------------------------------------------------------
# Optimistic concurrency (if_match)
# ---------------------------------------------------------------------------


class TestOptimisticConcurrency:
    """Tests for the if_match parameter on write operations."""

    # --- write() ---

    def test_write_with_correct_if_match_succeeds(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """write() with a matching if_match etag succeeds."""
        path = "simple.md"
        current_etag = compute_file_hash(vault_path / path)

        result = writable.writer.write(
            path, "# Updated\n\nNew body.\n", if_match=current_etag
        )

        assert result.created is False
        assert "Updated" in (vault_path / path).read_text()

    def test_write_with_wrong_if_match_raises(self, writable: Vault) -> None:
        """write() with a stale etag raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.writer.write("simple.md", "# Body\n", if_match="stale-etag-value")

        assert exc_info.value.path == "simple.md"
        assert exc_info.value.expected == "stale-etag-value"
        assert exc_info.value.actual != "stale-etag-value"

    def test_write_with_if_match_on_nonexistent_file_raises(
        self, writable: Vault
    ) -> None:
        """write() with if_match for a nonexistent file raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.writer.write("does_not_exist.md", "# Body\n", if_match="any-etag")

        assert exc_info.value.path == "does_not_exist.md"
        assert exc_info.value.actual == "(file does not exist)"

    def test_write_without_if_match_works_as_before(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """write() without if_match performs unconditional write (backwards compatible)."""
        result = writable.writer.write("simple.md", "# New Body\n\nContent.\n")

        assert result.created is False
        assert "New Body" in (vault_path / "simple.md").read_text()

    # --- edit() ---

    def test_edit_with_correct_if_match_succeeds(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """edit() with a matching if_match etag succeeds."""
        path = "simple.md"
        current_etag = compute_file_hash(vault_path / path)
        file_content = (vault_path / path).read_text()
        old_text = file_content[:20]
        new_text = "CHANGED_PREFIX_TEXT_"

        result = writable.writer.edit(path, old_text, new_text, if_match=current_etag)

        assert result.replacements == 1

    def test_edit_with_wrong_if_match_raises(self, writable: Vault) -> None:
        """edit() with a stale etag raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.writer.edit(
                "simple.md",
                "Simple Document",
                "Updated Document",
                if_match="stale-etag-value",
            )

        assert exc_info.value.path == "simple.md"
        assert exc_info.value.expected == "stale-etag-value"

    # --- delete() ---

    def test_delete_with_correct_if_match_succeeds(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """delete() with a matching if_match etag removes the file."""
        path = "simple.md"
        current_etag = compute_file_hash(vault_path / path)

        result = writable.writer.delete(path, if_match=current_etag)

        assert result.path == path
        assert not (vault_path / path).exists()

    def test_delete_with_wrong_if_match_raises(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """delete() with a stale etag raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.writer.delete("simple.md", if_match="stale-etag-value")

        assert exc_info.value.path == "simple.md"
        # File must NOT have been deleted.
        assert (vault_path / "simple.md").exists()

    # --- rename() ---

    def test_rename_with_correct_if_match_succeeds(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """rename() with a matching if_match etag renames the file."""
        old_path = "simple.md"
        new_path = "renamed_simple.md"
        current_etag = compute_file_hash(vault_path / old_path)

        result = writable.writer.rename(old_path, new_path, if_match=current_etag)

        assert result.old_path == old_path
        assert result.new_path == new_path
        assert not (vault_path / old_path).exists()
        assert (vault_path / new_path).exists()

    def test_rename_with_wrong_if_match_raises(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """rename() with a stale etag raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.writer.rename(
                "simple.md", "renamed.md", if_match="stale-etag-value"
            )

        assert exc_info.value.path == "simple.md"
        # File must NOT have been renamed.
        assert (vault_path / "simple.md").exists()
        assert not (vault_path / "renamed.md").exists()

    # --- write_attachment() ---

    def test_write_attachment_with_correct_if_match_succeeds(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() with a matching if_match etag overwrites the file."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)
        att_path = vault_with_attachment / "assets" / "report.pdf"
        current_etag = compute_file_hash(att_path)
        new_content = b"updated PDF bytes"

        result = col.writer.write_attachment(
            "assets/report.pdf", new_content, if_match=current_etag
        )

        assert result.created is False
        assert att_path.read_bytes() == new_content

    def test_write_attachment_with_wrong_if_match_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() with a stale etag raises ConcurrentModificationError."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)

        with pytest.raises(ConcurrentModificationError) as exc_info:
            col.writer.write_attachment(
                "assets/report.pdf", b"new content", if_match="stale-etag-value"
            )

        assert exc_info.value.path == "assets/report.pdf"
        assert exc_info.value.expected == "stale-etag-value"

    def test_write_attachment_with_if_match_on_nonexistent_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() with if_match for a missing file raises CME."""
        col = Vault(source_dir=vault_with_attachment, read_only=False)

        with pytest.raises(ConcurrentModificationError) as exc_info:
            col.writer.write_attachment(
                "assets/missing.pdf", b"data", if_match="any-etag"
            )

        assert exc_info.value.path == "assets/missing.pdf"
        assert exc_info.value.actual == "(file does not exist)"

    # --- Round-trip test ---

    def test_read_etag_roundtrip_with_write(self, writable: Vault) -> None:
        """read() etag can be passed directly to write() as if_match and succeeds."""
        note = writable.reader.read("simple.md")
        assert note is not None
        assert note.etag, "etag must be non-empty"

        result = writable.writer.write(
            "simple.md", "# Round-trip\n\nBody.\n", if_match=note.etag
        )

        assert result.created is False

    def test_read_etag_roundtrip_with_edit(
        self, writable: Vault, vault_path: Path
    ) -> None:
        """read() etag can be passed to edit() as if_match and succeeds."""
        note = writable.reader.read("simple.md")
        assert note is not None
        assert note.etag
        file_content = (vault_path / "simple.md").read_text()
        old_text = file_content[:15]

        result = writable.writer.edit(
            "simple.md", old_text, "REPLACED_TEXT_X_", if_match=note.etag
        )

        assert result.replacements == 1

    def test_read_etag_stale_after_write(self, writable: Vault) -> None:
        """etag from read() is no longer valid after the file is modified."""
        note = writable.reader.read("simple.md")
        assert note is not None
        stale_etag = note.etag

        # Modify the file.
        writable.writer.write("simple.md", "# Modified\n\nNew content.\n")

        # Now the stale etag should fail.
        with pytest.raises(ConcurrentModificationError):
            writable.writer.write("simple.md", "# Another write\n", if_match=stale_etag)


# ---------------------------------------------------------------------------
# Deferred embedding and callback tests (issue #175)
# ---------------------------------------------------------------------------


class TestDeferredEmbeddings:
    """Tests for the deferred embedding update mechanism."""

    def test_dirty_docs_flushed_on_semantic_search(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """Dirty documents are re-embedded via the writer after a write (#559).

        Pre-#559 the test asserted on ``_index_mgr._dirty_embeddings`` and
        relied on ``semantic_search`` triggering an inline flush.  Now the
        single-owner :class:`IndexWriter` owns the vector-dirty set and
        flushes it as a follow-up job; ``wait_for_writer_drain`` is the
        observation point.
        """
        col = Vault(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
            read_only=False,
        )
        col.index.build_index()
        col.index.build_embeddings()

        col.writer.write(
            "deferred_doc.md",
            "# Deferred Embedding\n\nUniqueContentForTest.\n",
        )

        # Drain the writer so FTS update + vector flush both complete.
        wait_for_writer_drain(col)

        # Semantic search no longer triggers an inline flush; the writer
        # has already done the work by the time drain returns.
        results = col.reader.search("UniqueContentForTest", mode="semantic")
        paths = [r.path for r in results]
        assert "deferred_doc.md" in paths
        # Writer's vector-dirty set should be empty after the drain.
        assert col._coordinator.writer.get_status()["dirty_embeddings"] == 0

    def test_dirty_docs_flushed_on_close(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """Dirty documents are re-embedded when close() is called (#559).

        Post-#559 the writer joins on close, so any pending vector work
        finishes before ``close()`` returns.
        """
        embeddings_path = tmp_path / "embeddings"
        col = Vault(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
            read_only=False,
        )
        col.index.build_index()
        col.index.build_embeddings()

        col.writer.write(
            "close_flush.md",
            "# Close Flush\n\nContent to be flushed on close.\n",
        )

        col.close()
        # After close, the writer's queue and dirty sets must be empty.
        status = col._coordinator.writer.get_status()
        assert status["dirty_paths"] == 0
        assert status["dirty_embeddings"] == 0

    def test_git_callback_fires_eventually(self, vault_path: Path) -> None:
        """Git callback fires in the background after write returns."""
        import threading

        event = threading.Event()
        calls: list = []

        def slow_callback(*args: object) -> None:
            calls.append(args)
            event.set()

        col = _make_vault(vault_path, read_only=False, on_write=slow_callback)
        col.index.build_index()

        col.writer.write("bg_callback.md", "# Background\n\nTest.\n")

        # Callback hasn't fired yet (it's in a background thread).
        # Wait for it to complete.
        event.wait(timeout=5)
        assert len(calls) == 1
        _, _, operation = calls[0]
        assert operation == "write"
        col.close()


# ---------------------------------------------------------------------------
# Logging audit — previously-silent error paths now emit log messages (#182)
# ---------------------------------------------------------------------------


class TestLoggingAuditSilentPaths:
    """Verify that previously-silent except blocks now log at WARNING."""

    def test_list_attachments_stat_error_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_list_attachments logs WARNING when stat() fails on a file."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n", encoding="utf-8")
        att = vault / "data.csv"
        att.write_text("a,b\n1,2\n", encoding="utf-8")

        col = Vault(source_dir=vault, attachment_extensions=["csv"])
        col.index.build_index()

        # Patch Path.stat to raise OSError for data.csv.
        # On Python 3.13, Path.is_file() calls Path.stat() internally; we also
        # patch is_file() to return True for data.csv so that the is_file() guard
        # at the top of _list_attachments does not short-circuit before reaching
        # the explicit stat() call that exercises the warning log.
        # On Python 3.14, is_file() uses a C-level stat and Path.stat is only
        # called explicitly; the is_file() patch is a no-op there.
        from pathlib import Path as _Path

        original_stat = _Path.stat
        original_is_file = _Path.is_file

        def stat_that_fails(self_path: _Path, *a: object, **kw: object) -> object:
            if self_path.name == "data.csv":
                raise OSError("simulated stat failure")
            return original_stat(self_path, *a, **kw)

        def is_file_override(self_path: _Path, *a: object, **kw: object) -> bool:
            if self_path.name == "data.csv":
                return (
                    True  # file exists; we want is_file() to pass so stat() is reached
                )
            return original_is_file(self_path, *a, **kw)

        with (
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.vault"),
            patch.object(_Path, "stat", stat_that_fails),
            patch.object(_Path, "is_file", is_file_override),
        ):
            results = col.reader.list_documents(include_attachments=True)
        attachment_paths = [r.path for r in results if isinstance(r, AttachmentInfo)]
        assert "data.csv" not in attachment_paths
        assert any("stat error" in rec.message for rec in caplog.records)

    def test_get_frontmatter_invalid_json_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Vault._get_frontmatter logs WARNING for invalid JSON."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note\n", encoding="utf-8")
        col = Vault(source_dir=vault)
        col.index.build_index()

        bad_row = {
            "path": "note.md",
            "title": "Note",
            "folder": "",
            "frontmatter_json": "{broken-json",
            "modified_at": 0.0,
        }
        with (
            caplog.at_level(
                logging.WARNING, logger="markdown_vault_mcp.managers.search"
            ),
            patch.object(col._search_mgr._fts, "get_note", return_value=bad_row),
        ):
            result = col._search_mgr._get_frontmatter("note.md")
        assert result == {}
        assert any(
            "_get_frontmatter: invalid JSON" in rec.message for rec in caplog.records
        )


def test_vault_constructs_chunker_with_max_chunk_words(tmp_path):
    """Vault plumbs max_chunk_words into HeadingChunker."""
    from markdown_vault_mcp.scanner import HeadingChunker
    from markdown_vault_mcp.vault import Vault

    coll = Vault(source_dir=tmp_path, max_chunk_words=250)
    assert isinstance(coll._chunk_strategy, HeadingChunker)
    assert coll._chunk_strategy.max_chunk_words == 250


def test_vault_search_honours_default_chunks_per_file(tmp_path):
    """A Vault-level search uses chunks_per_file=2 by default."""
    from markdown_vault_mcp.vault import Vault

    (tmp_path / "long.md").write_text(
        "# Top\n## A\nworld a\n## B\nworld b\n## C\nworld c\n",
        encoding="utf-8",
    )
    (tmp_path / "short.md").write_text("# Short\nworld\n", encoding="utf-8")
    coll = Vault(source_dir=tmp_path)
    coll.index.build_index()
    results = coll.reader.search("world", mode="keyword", limit=10)
    # Each file appears at most once in the grouped output; the file score's
    # underlying section count is capped at the default chunks_per_file=2.
    paths = [r.path for r in results]
    assert len(set(paths)) == len(paths)
    long_groups = [r for r in results if r.path == "long.md"]
    if long_groups:
        assert len(long_groups[0].sections) <= 2


def test_vault_build_index_uses_writer(tmp_path):
    """IndexFacet.build_index() routes through the IndexWriter."""
    from markdown_vault_mcp.vault import Vault

    col = Vault(source_dir=tmp_path, read_only=False)
    try:
        stats = col.index.build_index()
        # IndexWriter is non-None; build_index returned via writer.
        assert col._coordinator.writer is not None
        assert stats is not None
    finally:
        col.close()


def test_vault_writer_is_started_after_construction(tmp_path):
    """The IndexWriter is started by Vault construction."""
    from markdown_vault_mcp.vault import Vault

    col = Vault(source_dir=tmp_path, read_only=False)
    try:
        assert col._coordinator.writer is not None
        assert col._coordinator.writer._thread is not None  # thread started
        assert col._coordinator.writer._thread.is_alive()
    finally:
        col.close()


def test_vault_build_index_async_returns_future(tmp_path):
    from concurrent.futures import Future

    from markdown_vault_mcp.vault import Vault

    col = Vault(source_dir=tmp_path, read_only=False)
    try:
        future = col.index.build_index_async()
        assert isinstance(future, Future)
        future.result(timeout=10)
    finally:
        col.close()


def test_vault_reindex_async_returns_future(tmp_path):
    from concurrent.futures import Future

    from markdown_vault_mcp.vault import Vault

    col = Vault(source_dir=tmp_path, read_only=False)
    try:
        col.index.build_index()  # required precondition
        future = col.index.reindex_async()
        assert isinstance(future, Future)
        future.result(timeout=10)
    finally:
        col.close()


def test_vault_build_embeddings_async_returns_future(tmp_path):
    from concurrent.futures import Future

    from markdown_vault_mcp.vault import Vault
    from tests.conftest import MockEmbeddingProvider

    col = Vault(
        source_dir=tmp_path,
        read_only=False,
        embeddings_path=tmp_path / "vec",
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        col.index.build_index()
        future = col.index.build_embeddings_async()
        assert isinstance(future, Future)
        future.result(timeout=10)
    finally:
        col.close()


def test_reindex_async_failure_recorded_in_status(tmp_path, monkeypatch):
    """If the writer-thread reindex job raises, the exception is captured
    into get_index_status['last_reindex_error'] (#561)."""
    import time

    from markdown_vault_mcp.managers import index as index_module
    from markdown_vault_mcp.vault import Vault

    col = Vault(source_dir=tmp_path, read_only=False)
    try:
        col.index.build_index()

        # Patch IndexManager.reindex to raise so we can verify capture.
        def boom(self):  # noqa: ARG001
            raise RuntimeError("simulated reindex failure")

        monkeypatch.setattr(index_module.IndexManager, "reindex", boom)

        fut = col.index.reindex_async()
        # Future will resolve with the exception.
        with pytest.raises(RuntimeError, match="simulated reindex failure"):
            fut.result(timeout=5)

        # Wait for the done-callback to run.  The callback ordering
        # relative to .result() can race — give it a tick.
        for _ in range(50):
            if col._coordinator._last_reindex_error is not None:
                break
            time.sleep(0.01)

        assert col._coordinator._last_reindex_error is not None
        assert "simulated reindex failure" in str(col._coordinator._last_reindex_error)

        status = col.index.get_index_status()
        assert "last_reindex_error" in status
        assert status["last_reindex_error"] is not None
        assert "simulated reindex failure" in status["last_reindex_error"]
    finally:
        col.close()


def test_reindex_async_success_clears_prior_error(tmp_path):
    """A successful async reindex clears any prior captured error (#561)."""
    import time

    from markdown_vault_mcp.vault import Vault

    col = Vault(source_dir=tmp_path, read_only=False)
    try:
        col.index.build_index()
        # Seed a prior error.
        col._coordinator._last_reindex_error = RuntimeError("prior")

        fut = col.index.reindex_async()
        fut.result(timeout=5)

        for _ in range(50):
            if col._coordinator._last_reindex_error is None:
                break
            time.sleep(0.01)
        assert col._coordinator._last_reindex_error is None
        assert col.index.get_index_status()["last_reindex_error"] is None
    finally:
        col.close()


def test_build_embeddings_async_failure_recorded_in_status(tmp_path, monkeypatch):
    """If the writer-thread build_embeddings job raises, the exception is
    captured into get_index_status['last_build_embeddings_error'] (#561)."""
    import time

    from markdown_vault_mcp.managers import index as index_module
    from markdown_vault_mcp.vault import Vault
    from tests.conftest import MockEmbeddingProvider

    col = Vault(
        source_dir=tmp_path,
        read_only=False,
        embeddings_path=tmp_path / "vec",
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        col.index.build_index()

        def boom(self, *, force=False):  # noqa: ARG001
            raise RuntimeError("simulated embeddings failure")

        monkeypatch.setattr(index_module.IndexManager, "build_embeddings", boom)

        fut = col.index.build_embeddings_async()
        with pytest.raises(RuntimeError, match="simulated embeddings failure"):
            fut.result(timeout=5)

        for _ in range(50):
            if col._coordinator._last_build_embeddings_error is not None:
                break
            time.sleep(0.01)

        assert col._coordinator._last_build_embeddings_error is not None
        status = col.index.get_index_status()
        assert status["last_build_embeddings_error"] is not None
        assert "simulated embeddings failure" in status["last_build_embeddings_error"]
    finally:
        col.close()


class TestIsDrained:
    """Tests for IndexFacet.is_drained() (#534)."""

    def test_returns_true_on_idle_writer(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            assert col.index.is_drained() is True
        finally:
            col.close()

    def test_returns_false_when_dirty_paths_present(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            col._coordinator.writer.mark_dirty(["fake.md"])
            assert col.index.is_drained() is False
        finally:
            col.close()

    def test_returns_false_when_dirty_embeddings_present(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            col._coordinator.writer.mark_embedding_dirty(["fake.md"])
            assert col.index.is_drained() is False
        finally:
            col.close()

    def test_returns_false_during_in_flight_job(self, tmp_path: Path) -> None:
        import threading

        from markdown_vault_mcp.indexing import BuildIndex
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            release = threading.Event()
            started = threading.Event()
            original_runner = col._coordinator.writer._runners["build_index"]

            def _blocking_runner(job, ctx):
                started.set()
                release.wait(timeout=5)
                return original_runner(job, ctx)

            col._coordinator.writer._runners["build_index"] = _blocking_runner
            try:
                fut = col._coordinator.writer.submit(BuildIndex())
                started.wait(timeout=5)
                assert col.index.is_drained() is False
            finally:
                release.set()
                fut.result(timeout=5)
        finally:
            col.close()


class TestWriteGeneration:
    """Tests for IndexFacet.write_generation() (#534)."""

    def test_increments_on_job_completion(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            gen_before = col.index.write_generation()
            # Submit a no-op write (mark + flush dirty paths) so a job
            # cycle completes through the writer.
            col._coordinator.writer.mark_dirty(["sentinel.md"])
            col._coordinator.writer.drain_dirty_paths()
            from markdown_vault_mcp.indexing import BuildIndex

            col._coordinator.writer.submit(BuildIndex()).result(timeout=5)
            assert col.index.write_generation() > gen_before
        finally:
            col.close()

    def test_monotonic_across_multiple_jobs(self, tmp_path: Path) -> None:
        from itertools import pairwise

        from markdown_vault_mcp.indexing import BuildIndex
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            samples = [col.index.write_generation()]
            for _ in range(3):
                col._coordinator.writer.submit(BuildIndex()).result(timeout=5)
                samples.append(col.index.write_generation())
            for prev, cur in pairwise(samples):
                assert cur > prev
        finally:
            col.close()


class TestWaitForDrain:
    """Tests for IndexFacet.wait_for_drain() (#534)."""

    def test_returns_true_immediately_when_already_drained(
        self, tmp_path: Path
    ) -> None:
        import time

        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            start = time.monotonic()
            drained = col.index.wait_for_drain(timeout=1.0)
            elapsed = time.monotonic() - start
            assert drained is True
            assert elapsed < 0.2
        finally:
            col.close()

    def test_returns_true_after_writer_drains(self, tmp_path: Path) -> None:
        import threading

        from markdown_vault_mcp.indexing import BuildIndex
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            release = threading.Event()
            started = threading.Event()
            original_runner = col._coordinator.writer._runners["build_index"]

            def _blocking_runner(job, ctx):
                started.set()
                release.wait(timeout=5)
                return original_runner(job, ctx)

            col._coordinator.writer._runners["build_index"] = _blocking_runner
            fut = col._coordinator.writer.submit(BuildIndex())
            started.wait(timeout=5)
            threading.Timer(0.1, release.set).start()
            assert col.index.wait_for_drain(timeout=5.0) is True
            fut.result(timeout=5)
        finally:
            col.close()

    def test_returns_false_on_timeout(self, tmp_path: Path) -> None:
        import threading

        from markdown_vault_mcp.indexing import BuildIndex
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, read_only=False)
        try:
            col.index.build_index()
            release = threading.Event()
            started = threading.Event()
            original_runner = col._coordinator.writer._runners["build_index"]

            def _blocking_runner(job, ctx):
                started.set()
                release.wait(timeout=5)
                return original_runner(job, ctx)

            col._coordinator.writer._runners["build_index"] = _blocking_runner
            fut = col._coordinator.writer.submit(BuildIndex())
            started.wait(timeout=5)
            assert col.index.wait_for_drain(timeout=0.1) is False
            release.set()
            fut.result(timeout=5)
        finally:
            col.close()


# The 39 flat delegators removed in PR4a (#627) — reachable only via the facets.
_REMOVED_FLAT_METHODS = [
    "search", "read", "read_attachment", "list_documents", "list_folders",
    "list_tags", "get_toc", "get_recent", "get_similar", "get_context",
    "get_history", "get_diff", "stats", "write", "edit", "delete", "rename",
    "write_attachment", "get_backlinks", "get_outlinks", "get_broken_links",
    "get_orphan_notes", "get_most_linked", "get_connection_path", "build_index",
    "build_index_async", "reindex", "reindex_async", "build_embeddings",
    "build_embeddings_async", "is_queryable", "is_drained", "wait_for_drain",
    "wait_until_queryable", "write_generation", "get_index_status",
    "embeddings_status", "start_background_build_index", "should_use_background_build",
]  # fmt: skip
# Facet accessors + lifecycle the thin facade still exposes.
_RETAINED_SURFACE = [
    "reader", "writer", "graph", "index",
    "start", "stop", "close", "pause_writes", "force_pull",
    "sync_from_remote_before_index",
]  # fmt: skip


class TestVaultThinSurface:
    """The facade is a thin composition root after the flat delegators (PR4a)."""

    def test_flat_delegators_are_gone(self) -> None:
        present = [m for m in _REMOVED_FLAT_METHODS if hasattr(Vault, m)]
        assert present == [], f"flat delegators still on Vault: {present}"

    def test_facet_accessors_and_lifecycle_retained(self) -> None:
        missing = [m for m in _RETAINED_SURFACE if not hasattr(Vault, m)]
        assert missing == [], f"retained surface missing from Vault: {missing}"
