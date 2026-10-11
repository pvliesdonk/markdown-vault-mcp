"""Tests for FTS5 optimize after bulk purges (dead-segment hygiene).

Deleting rows from an FTS5 table only marks their tokens as deleted; the
dead entries stay in the on-disk segment b-trees until a merge. These tests
cover ``should_optimize`` thresholding, ``FTSIndex.optimize()`` behaviour,
and the bulk-purge call sites in ``IndexManager`` (including the issue #255
exclusion upgrade path).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.fts_index import (
    FTSIndex,
    should_optimize,
)
from markdown_vault_mcp.managers.index import IndexManager
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.types import Chunk, ParsedNote

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_note(
    path: str = "test.md",
    title: str = "Test",
    chunks: list[Chunk] | None = None,
    content_hash: str = "abc123",
) -> ParsedNote:
    """Create a ParsedNote for testing.

    Args:
        path: Relative document path including ``.md`` extension.
        title: Document title.
        chunks: List of chunks. Defaults to a single generic chunk.
        content_hash: Hash string stored in the note.

    Returns:
        A fully-populated :class:`ParsedNote` suitable for indexing.
    """
    if chunks is None:
        chunks = [
            Chunk(heading="Test", heading_level=1, content="Test content", start_line=0)
        ]
    return ParsedNote(
        path=path,
        frontmatter={},
        title=title,
        chunks=chunks,
        content_hash=content_hash,
        modified_at=1000.0,
    )


class _FailingConnection:
    """Stand-in connection whose every execute raises OperationalError.

    ``sqlite3.Connection.execute`` cannot be patched on an instance (the
    attribute is read-only), so contention tests swap the whole connection
    for this stub instead.
    """

    def __init__(self, message: str) -> None:
        self._message = message

    def __enter__(self) -> _FailingConnection:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def execute(self, *_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError(self._message)


def _make_index_mgr(
    vault: Path,
    state_dir: Path,
    **overrides: object,
) -> tuple[IndexManager, FTSIndex]:
    """Build an IndexManager with default wiring.

    Args:
        vault: Source directory containing markdown files.
        state_dir: Directory for the change-tracker state file.
        **overrides: Keyword overrides for the IndexManager constructor;
            ``fts`` may be passed to share an index between managers.

    Returns:
        Tuple of (manager, fts index).
    """
    fts = overrides.pop("fts", None) or FTSIndex(db_path=":memory:")
    defaults: dict = {
        "fts": fts,
        "tracker": ChangeTracker(state_dir / ".state" / "state.json"),
        "source_dir": vault,
        "chunk_strategy": HeadingChunker(),
    }
    defaults.update(overrides)
    return IndexManager(**defaults), fts


def _write_docs(directory: Path, count: int, prefix: str = "doc") -> list[Path]:
    """Write *count* small markdown files into *directory*.

    Args:
        directory: Target directory (created if missing).
        count: Number of files to create.
        prefix: Filename prefix.

    Returns:
        List of created file paths.
    """
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(count):
        f = directory / f"{prefix}{i}.md"
        f.write_text(
            f"# {prefix} {i}\n\nUnique content {prefix}{i} body text.\n",
            encoding="utf-8",
        )
        files.append(f)
    return files


def _dbstat_fts_data_size(db_path: Path) -> int | None:
    """Return SUM(pgsize) of the notes_fts_data shadow table via dbstat.

    Returns ``None`` when this SQLite build lacks the dbstat virtual table,
    so callers can skip size assertions in environments without it.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT SUM(pgsize) FROM dbstat WHERE name = 'notes_fts_data'"
        ).fetchone()
        return int(row[0] or 0)
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# should_optimize thresholds
# ---------------------------------------------------------------------------


class TestShouldOptimize:
    """Unit tests for the bulk-purge optimize threshold."""

    def test_purge_at_absolute_threshold_triggers(self) -> None:
        """Purging OPTIMIZE_MIN_PURGED_DOCS documents qualifies."""
        assert should_optimize(25, 1000) is True

    def test_purge_below_both_thresholds_does_not_trigger(self) -> None:
        """A small purge of a large corpus does not qualify."""
        assert should_optimize(24, 1000) is False

    def test_purge_at_fractional_threshold_triggers(self) -> None:
        """Purging >= 10% of a small corpus qualifies."""
        assert should_optimize(3, 20) is True  # 15% of corpus.

    def test_purge_below_fractional_threshold_does_not_trigger(self) -> None:
        """Purging < 10% of a small corpus does not qualify."""
        assert should_optimize(1, 20) is False  # 5% of corpus.

    def test_zero_purged_never_triggers(self) -> None:
        """No purge, no optimize."""
        assert should_optimize(0, 100) is False

    def test_empty_corpus_never_triggers(self) -> None:
        """Guard against division by zero on an empty corpus."""
        assert should_optimize(5, 0) is False


# ---------------------------------------------------------------------------
# FTSIndex.optimize()
# ---------------------------------------------------------------------------


class TestOptimize:
    """Unit tests for FTSIndex.optimize()."""

    def test_optimize_runs_and_returns_true(self) -> None:
        """optimize() executes the FTS5 optimize command and reports success."""
        idx = FTSIndex(":memory:")
        idx.build_from_notes([make_note("a.md"), make_note("b.md")])
        idx.delete_by_path("a.md")

        assert idx.optimize() is True

    def test_optimize_preserves_search_results(self) -> None:
        """Surviving documents remain searchable after optimize()."""
        idx = FTSIndex(":memory:")
        idx.build_from_notes(
            [
                make_note(
                    "keep.md",
                    chunks=[
                        Chunk(
                            heading="K",
                            heading_level=1,
                            content="zanzibar survives",
                            start_line=0,
                        )
                    ],
                ),
                make_note("drop.md"),
            ]
        )
        idx.delete_by_path("drop.md")
        idx.optimize()

        results = idx.search("zanzibar")
        assert [r.path for r in results] == ["keep.md"]

    def test_optimize_shrinks_dead_segments(self, tmp_path: Path) -> None:
        """After a bulk delete, optimize() shrinks the FTS5 segment b-trees.

        Measured via the dbstat virtual table; the size assertion is skipped
        when this SQLite build does not provide dbstat.
        """
        db_path = tmp_path / "index.db"
        idx = FTSIndex(db_path)
        # Index enough distinct-token content for measurable segments.
        notes = [
            make_note(
                f"doc{i}.md",
                chunks=[
                    Chunk(
                        heading=f"Heading {i}",
                        heading_level=1,
                        content=" ".join(f"token{i}word{j}" for j in range(200)),
                        start_line=0,
                    )
                ],
                content_hash=f"hash{i}",
            )
            for i in range(40)
        ]
        idx.build_from_notes(notes)

        for i in range(40):
            idx.delete_by_path(f"doc{i}.md")
        size_before = _dbstat_fts_data_size(db_path)

        assert idx.optimize() is True
        size_after = _dbstat_fts_data_size(db_path)
        idx.close()

        if size_before is None or size_after is None:
            pytest.skip("dbstat virtual table not available in this SQLite build")
        assert size_after < size_before

    def test_optimize_tolerates_busy_database(self, monkeypatch) -> None:
        """A busy database skips the optimize instead of raising."""
        idx = FTSIndex(":memory:")
        idx.build_from_notes([make_note("a.md")])

        monkeypatch.setattr(
            idx, "_conn", lambda: _FailingConnection("database is busy")
        )
        assert idx.optimize() is False

    def test_optimize_tolerates_locked_past_retry_budget(self, monkeypatch) -> None:
        """A lock held past the retry budget skips the optimize."""
        idx = FTSIndex(":memory:")
        idx.build_from_notes([make_note("a.md")])

        def _exhausted(_operation, **_kwargs):
            raise sqlite3.OperationalError("database table is locked: notes_fts")

        monkeypatch.setattr(
            "markdown_vault_mcp.fts_index._retry_on_sqlite_locked", _exhausted
        )
        assert idx.optimize() is False

    def test_optimize_propagates_other_operational_errors(self, monkeypatch) -> None:
        """Non-lock OperationalErrors are not swallowed."""
        idx = FTSIndex(":memory:")

        monkeypatch.setattr(idx, "_conn", lambda: _FailingConnection("disk I/O error"))
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            idx.optimize()


# ---------------------------------------------------------------------------
# IndexManager bulk-purge call sites
# ---------------------------------------------------------------------------


class TestBulkPurgeOptimize:
    """Integration tests for the optimize trigger in IndexManager."""

    def test_reindex_bulk_delete_triggers_optimize(self, tmp_path: Path) -> None:
        """Deleting >= threshold docs in one reindex pass runs FTS optimize."""
        vault = tmp_path / "vault"
        files = _write_docs(vault, 30)
        mgr, fts = _make_index_mgr(vault, tmp_path)
        mgr.build_index()

        # Remove 26 files (>= OPTIMIZE_MIN_PURGED_DOCS) from disk.
        for f in files[:26]:
            f.unlink()

        with patch.object(fts, "optimize", wraps=fts.optimize) as spy:
            result = mgr.reindex()

        assert result.deleted == 26
        assert spy.call_count == 1

    def test_reindex_small_delete_does_not_optimize(self, tmp_path: Path) -> None:
        """A purge below both thresholds skips the FTS optimize."""
        vault = tmp_path / "vault"
        files = _write_docs(vault, 30)
        mgr, fts = _make_index_mgr(vault, tmp_path)
        mgr.build_index()

        # Remove 1 of 30 files: below 25 docs and below 10% of the corpus.
        files[0].unlink()

        with patch.object(fts, "optimize", wraps=fts.optimize) as spy:
            result = mgr.reindex()

        assert result.deleted == 1
        assert spy.call_count == 0

    def test_exclusion_upgrade_purge_triggers_optimize(self, tmp_path: Path) -> None:
        """Newly-configured exclude patterns purging >= threshold docs
        (issue #255 upgrade path) trigger an FTS optimize on build_index."""
        vault = tmp_path / "vault"
        _write_docs(vault, 5)
        _write_docs(vault / ".claude", 26, prefix="transcript")

        # Phase 1: index WITHOUT exclude_patterns (old configuration).
        mgr1, fts = _make_index_mgr(vault, tmp_path / "s1")
        mgr1.build_index()
        assert len(fts.list_notes()) == 31

        # Phase 2: rebuild WITH exclude_patterns on the same index.
        mgr2, _ = _make_index_mgr(
            vault,
            tmp_path / "s2",
            fts=fts,
            exclude_patterns=[".claude/**"],
        )
        with patch.object(fts, "optimize", wraps=fts.optimize) as spy:
            mgr2.build_index()

        assert len(fts.list_notes()) == 5
        assert spy.call_count == 1

    def test_exclusion_upgrade_small_purge_does_not_optimize(
        self, tmp_path: Path
    ) -> None:
        """An exclusion purge below both thresholds skips the FTS optimize."""
        vault = tmp_path / "vault"
        _write_docs(vault, 30)
        _write_docs(vault / ".claude", 1, prefix="transcript")

        mgr1, fts = _make_index_mgr(vault, tmp_path / "s1")
        mgr1.build_index()

        mgr2, _ = _make_index_mgr(
            vault,
            tmp_path / "s2",
            fts=fts,
            exclude_patterns=[".claude/**"],
        )
        with patch.object(fts, "optimize", wraps=fts.optimize) as spy:
            mgr2.build_index()

        assert len(fts.list_notes()) == 30
        assert spy.call_count == 0
