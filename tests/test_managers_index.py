"""Tests for IndexManager in isolation (no Collection dependency)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.index import IndexManager
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.types import IndexStats, ParsedNote, ReindexResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def index_vault(tmp_path: Path) -> Path:
    """Create a small vault suitable for index tests.

    Contains:
        alpha.md   (root, tags: [a, b])
        beta.md    (root, tags: [b])
        notes/gamma.md (subfolder, tags: [c])
        notes/delta.md (subfolder, no tags)
    """
    alpha = tmp_path / "alpha.md"
    alpha.write_text(
        "---\ntitle: Alpha\ntags:\n  - a\n  - b\n---\n# Alpha\n\nHello world.\n",
        encoding="utf-8",
    )
    beta = tmp_path / "beta.md"
    beta.write_text(
        "---\ntitle: Beta\ntags:\n  - b\n---\n# Beta\n\nGoodbye world.\n",
        encoding="utf-8",
    )
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    gamma = notes_dir / "gamma.md"
    gamma.write_text(
        "---\ntitle: Gamma\ntags:\n  - c\n---\n# Gamma\n\nUnique gamma content.\n",
        encoding="utf-8",
    )
    delta = notes_dir / "delta.md"
    delta.write_text(
        "---\ntitle: Delta\n---\n# Delta\n\nDelta content.\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_index_mgr(
    vault: Path,
    state_dir: Path,
    **overrides,
) -> tuple[IndexManager, FTSIndex, dict]:
    """Build an IndexManager with default wiring.

    Returns (index_mgr, fts, vectors_holder) so tests can inspect state.
    """
    fts = overrides.pop("fts", None) or FTSIndex(db_path=":memory:")
    tracker = overrides.pop("tracker", None) or ChangeTracker(
        state_dir / ".state" / "state.json"
    )
    vectors_holder: dict = {"vectors": None}
    defaults = {
        "fts": fts,
        "tracker": tracker,
        "source_dir": vault,
        "write_lock": threading.RLock(),
        "chunk_strategy": HeadingChunker(),
        "get_vectors": lambda: vectors_holder["vectors"],
        "set_vectors": lambda v: vectors_holder.__setitem__("vectors", v),
    }
    defaults.update(overrides)
    mgr = IndexManager(**defaults)
    return mgr, fts, vectors_holder


@pytest.fixture()
def index_mgr(index_vault: Path, tmp_path: Path) -> tuple[IndexManager, FTSIndex, dict]:
    """Build an IndexManager from the test vault."""
    return _make_index_mgr(index_vault, tmp_path)


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------


class TestBuildIndex:
    """Tests for IndexManager.build_index()."""

    def test_returns_index_stats(self, index_mgr):
        mgr, _fts, _ = index_mgr
        result = mgr.build_index()
        assert isinstance(result, IndexStats)
        assert result.documents_indexed >= 4

    def test_document_count(self, index_mgr):
        mgr, fts, _ = index_mgr
        mgr.build_index()
        notes = fts.list_notes()
        assert len(notes) == 4

    def test_idempotent_rebuild(self, index_mgr):
        """Second build_index without force re-scans (no early exit at mgr level)."""
        mgr, _fts, _ = index_mgr
        result1 = mgr.build_index()
        result2 = mgr.build_index()
        # IndexManager always rescans; the Collection wrapper handles the no-op.
        assert result2.documents_indexed == result1.documents_indexed

    def test_force_rebuild(self, index_mgr):
        mgr, _fts, _ = index_mgr
        mgr.build_index()
        result = mgr.build_index(force=True)
        assert result.documents_indexed >= 4

    def test_respects_exclude_patterns(self, index_vault: Path, tmp_path: Path):
        mgr, fts, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            exclude_patterns=["notes/*"],
        )
        mgr.build_index()
        notes = fts.list_notes()
        paths = {n["path"] for n in notes}
        assert "alpha.md" in paths
        assert "beta.md" in paths
        assert not any(p.startswith("notes/") for p in paths)

    def test_continues_on_upsert_error(self, index_vault: Path, tmp_path: Path):
        """If one document fails to upsert, others still get indexed."""
        fts = FTSIndex(db_path=":memory:")
        original_upsert = fts.upsert_note
        call_count = {"n": 0}

        def failing_upsert(note):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Simulated upsert failure")
            return original_upsert(note)

        fts.upsert_note = failing_upsert  # type: ignore[assignment]
        mgr, _, _ = _make_index_mgr(index_vault, tmp_path, fts=fts)
        result = mgr.build_index()
        # One errored, rest succeeded.
        assert result.documents_indexed == 3

    def test_updates_tracker_state(self, index_mgr):
        """build_index updates the change tracker baseline."""
        mgr, _fts, _ = index_mgr
        mgr.build_index()
        # After build_index, detect_changes should report no changes.
        changes = mgr._tracker.detect_changes(mgr._source_dir)
        assert len(changes.added) == 0
        assert len(changes.modified) == 0
        assert len(changes.deleted) == 0

    def test_resolves_wikilinks(self, index_vault: Path, tmp_path: Path):
        """build_index calls resolve_vault_wikilinks on the FTS index."""
        fts = MagicMock(spec=FTSIndex)
        fts.list_notes.return_value = []
        mgr, _, _ = _make_index_mgr(index_vault, tmp_path, fts=fts)
        mgr.build_index()
        fts.resolve_vault_wikilinks.assert_called_once()


# ---------------------------------------------------------------------------
# reindex
# ---------------------------------------------------------------------------


class TestReindex:
    """Tests for IndexManager.reindex()."""

    def test_detects_new_file(self, index_vault: Path, tmp_path: Path):
        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()

        # Add a new file.
        new_file = index_vault / "new_note.md"
        new_file.write_text(
            "---\ntitle: New\n---\n# New\n\nNew content.\n",
            encoding="utf-8",
        )

        result = mgr.reindex()
        assert isinstance(result, ReindexResult)
        assert result.added >= 1

        paths = {n["path"] for n in fts.list_notes()}
        assert "new_note.md" in paths

    def test_detects_deleted_file(self, index_vault: Path, tmp_path: Path):
        mgr, fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()

        # Delete a file.
        (index_vault / "beta.md").unlink()

        result = mgr.reindex()
        assert result.deleted >= 1

        paths = {n["path"] for n in fts.list_notes()}
        assert "beta.md" not in paths

    def test_detects_modified_file(self, index_vault: Path, tmp_path: Path):
        mgr, _fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()

        # Modify a file.
        (index_vault / "alpha.md").write_text(
            "---\ntitle: Alpha Updated\n---\n# Alpha\n\nUpdated.\n",
            encoding="utf-8",
        )

        result = mgr.reindex()
        assert result.modified >= 1


# ---------------------------------------------------------------------------
# build_embeddings
# ---------------------------------------------------------------------------


class TestBuildEmbeddings:
    """Tests for IndexManager.build_embeddings()."""

    def test_returns_zero_without_provider(self, index_vault: Path, tmp_path: Path):
        """build_embeddings raises ValueError when no provider is configured."""
        mgr, _fts, _ = _make_index_mgr(index_vault, tmp_path)
        mgr.build_index()
        with pytest.raises(ValueError, match="Embeddings require"):
            mgr.build_embeddings()

    def test_builds_with_provider(self, index_vault: Path, tmp_path: Path):
        """build_embeddings returns chunk count when provider is configured."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        embeddings_path = tmp_path / "embeddings"
        vectors_holder: dict = {"vectors": None}
        mgr, _fts, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=provider,
            get_vectors=lambda: vectors_holder["vectors"],
            set_vectors=lambda v: vectors_holder.__setitem__("vectors", v),
        )
        mgr.build_index()
        count = mgr.build_embeddings()
        assert count >= 4
        assert vectors_holder["vectors"] is not None


# ---------------------------------------------------------------------------
# embeddings_status
# ---------------------------------------------------------------------------


class TestEmbeddingsStatus:
    """Tests for IndexManager.embeddings_status()."""

    def test_not_configured(self, index_mgr):
        mgr, _, _ = index_mgr
        status = mgr.embeddings_status()
        assert status["available"] is False
        assert status["provider"] is None
        assert status["chunk_count"] == 0
        assert status["path"] is None

    def test_configured(self, index_vault: Path, tmp_path: Path):
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        embeddings_path = tmp_path / "embeddings"
        mgr, _, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=embeddings_path,
            embedding_provider=provider,
        )
        status = mgr.embeddings_status()
        assert status["available"] is True
        assert status["provider"] == "MockEmbeddingProvider"
        assert status["path"] == str(embeddings_path)


# ---------------------------------------------------------------------------
# update_vector_index / mark_dirty / remove_from_dirty
# ---------------------------------------------------------------------------


class TestDeferredEmbeddings:
    """Tests for deferred embedding methods."""

    def test_update_vector_index_noop_without_embeddings(self, index_mgr):
        """update_vector_index is a no-op when embeddings are not configured."""
        mgr, _, _ = index_mgr
        note = ParsedNote(
            path="test.md",
            frontmatter={},
            title="Test",
            chunks=[],
            content_hash="abc123",
            modified_at=0.0,
        )
        mgr.update_vector_index(note)
        assert len(mgr._dirty_embeddings) == 0

    def test_update_vector_index_adds_to_dirty(self, index_vault: Path, tmp_path: Path):
        """update_vector_index adds path to dirty set when embeddings configured."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        mgr, _, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=provider,
        )
        note = ParsedNote(
            path="test.md",
            frontmatter={},
            title="Test",
            chunks=[],
            content_hash="abc123",
            modified_at=0.0,
        )
        mgr.update_vector_index(note)
        # Cancel the timer to avoid background flush.
        mgr.cancel_flush_timer()
        assert "test.md" in mgr._dirty_embeddings

    def test_mark_dirty(self, index_vault: Path, tmp_path: Path):
        """mark_dirty adds path to dirty set."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        mgr, _, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=provider,
        )
        mgr.mark_dirty("some/path.md")
        mgr.cancel_flush_timer()
        assert "some/path.md" in mgr._dirty_embeddings

    def test_remove_from_dirty(self, index_vault: Path, tmp_path: Path):
        """remove_from_dirty removes path from dirty set."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        mgr, _, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=provider,
        )
        mgr._dirty_embeddings.add("target.md")
        mgr.remove_from_dirty("target.md")
        assert "target.md" not in mgr._dirty_embeddings

    def test_remove_from_dirty_nonexistent(self, index_mgr):
        """remove_from_dirty is safe when path is not in set."""
        mgr, _, _ = index_mgr
        mgr.remove_from_dirty("nonexistent.md")

    def test_flush_noop_when_nothing_dirty(self, index_mgr):
        """flush_dirty_embeddings is a no-op when nothing is dirty."""
        mgr, _, _ = index_mgr
        # Should not raise.
        mgr.flush_dirty_embeddings()

    def test_flush_noop_without_provider(self, index_mgr):
        """flush_dirty_embeddings is a no-op without embedding provider."""
        mgr, _, _ = index_mgr
        mgr._dirty_embeddings.add("something.md")
        mgr.flush_dirty_embeddings()
        # Still in dirty set because provider check skips the flush.
        assert "something.md" in mgr._dirty_embeddings

    def test_cancel_flush_timer(self, index_vault: Path, tmp_path: Path):
        """cancel_flush_timer cancels without flushing."""
        from tests.conftest import MockEmbeddingProvider

        provider = MockEmbeddingProvider()
        mgr, _, _ = _make_index_mgr(
            index_vault,
            tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=provider,
        )
        mgr.mark_dirty("test.md")
        mgr.cancel_flush_timer()
        # Timer cancelled but dirty set still has the entry.
        assert "test.md" in mgr._dirty_embeddings
        assert mgr._embedding_flush_timer is None


# ---------------------------------------------------------------------------
# _is_path_excluded
# ---------------------------------------------------------------------------


class TestIsPathExcluded:
    """Tests for IndexManager._is_path_excluded()."""

    def test_no_patterns(self, index_mgr):
        mgr, _, _ = index_mgr
        assert mgr._is_path_excluded("anything.md") is False

    def test_matching_pattern(self, index_vault: Path, tmp_path: Path):
        mgr, _, _ = _make_index_mgr(index_vault, tmp_path, exclude_patterns=["notes/*"])
        assert mgr._is_path_excluded("notes/gamma.md") is True
        assert mgr._is_path_excluded("alpha.md") is False
