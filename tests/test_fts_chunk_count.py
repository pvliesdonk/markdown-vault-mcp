"""Tests for the chunk_count column on documents and its migration path."""

from __future__ import annotations

import sqlite3

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import HeadingChunker, parse_note


def _make_note(tmp_path, name: str, body: str):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return parse_note(
        p, tmp_path, HeadingChunker(short_doc_lines=0, max_chunk_words=200)
    )


def test_chunk_count_populated_on_upsert(tmp_path):
    """upsert_note populates documents.chunk_count from len(note.chunks)."""
    fts = FTSIndex(db_path=":memory:")
    body = "\n".join(
        ["# A", "alpha body " * 10, "## B", "beta body " * 10, "## C", "gamma " * 10]
    )
    note = _make_note(tmp_path, "doc.md", body)
    fts.upsert_note(note)

    row = (
        fts._conn()
        .execute("SELECT chunk_count FROM documents WHERE path = ?", (note.path,))
        .fetchone()
    )
    assert row is not None
    assert row["chunk_count"] == len(note.chunks)
    # Sanity: the helper bypasses the 30-line short-doc rule so this fixture
    # actually splits.
    assert len(note.chunks) > 1


def test_chunk_count_populated_on_build_from_notes(tmp_path):
    """build_from_notes populates chunk_count for every note."""
    fts = FTSIndex(db_path=":memory:")
    notes = [
        _make_note(tmp_path, "a.md", "# A\nalpha\n## B\nbeta\n"),
        _make_note(tmp_path, "b.md", "# A\nonly one chunk\n"),
    ]
    fts.build_from_notes(notes)

    counts = dict(
        fts._conn().execute("SELECT path, chunk_count FROM documents").fetchall()
    )
    assert counts["a.md"] == len(notes[0].chunks)
    assert counts["b.md"] == len(notes[1].chunks)


def test_chunk_count_updates_on_reupsert(tmp_path):
    """When a doc is re-upserted with a different chunk count, the column updates."""
    fts = FTSIndex(db_path=":memory:")
    note_v1 = _make_note(tmp_path, "doc.md", "# A\nbody\n")
    fts.upsert_note(note_v1)
    v1_count = (
        fts._conn()
        .execute("SELECT chunk_count FROM documents WHERE path = ?", (note_v1.path,))
        .fetchone()["chunk_count"]
    )

    note_v2 = _make_note(
        tmp_path,
        "doc.md",
        "# A\nbody\n## B\nmore\n## C\neven more\n",
    )
    fts.upsert_note(note_v2)
    v2_count = (
        fts._conn()
        .execute("SELECT chunk_count FROM documents WHERE path = ?", (note_v2.path,))
        .fetchone()["chunk_count"]
    )

    assert v2_count != v1_count
    assert v2_count == len(note_v2.chunks)


def test_migration_no_crash_when_column_already_added(tmp_path):
    """If a concurrent process added chunk_count first, our migration is a no-op."""
    db_path = tmp_path / "race.sqlite3"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            folder TEXT NOT NULL DEFAULT '',
            frontmatter_json TEXT,
            content_hash TEXT NOT NULL,
            modified_at REAL NOT NULL,
            chunk_count INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    legacy.commit()
    legacy.close()

    # FTSIndex must not crash even though the column already exists.
    fts = FTSIndex(db_path=db_path)
    assert fts is not None


def test_migration_adds_column_to_pre_existing_db(tmp_path):
    """Opening an FTS DB created without chunk_count adds the column."""
    db_path = tmp_path / "old.sqlite3"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            folder TEXT NOT NULL DEFAULT '',
            frontmatter_json TEXT,
            content_hash TEXT NOT NULL,
            modified_at REAL NOT NULL
        );
        INSERT INTO documents (path, title, content_hash, modified_at)
        VALUES ('legacy.md', 'Legacy', 'x', 0.0);
        """
    )
    legacy.commit()
    legacy.close()

    fts = FTSIndex(db_path=db_path)
    cols = [
        r["name"]
        for r in fts._conn().execute("PRAGMA table_info(documents)").fetchall()
    ]
    assert "chunk_count" in cols
    # Existing legacy row gets the default value.
    row = (
        fts._conn()
        .execute("SELECT chunk_count FROM documents WHERE path = 'legacy.md'")
        .fetchone()
    )
    assert row["chunk_count"] == 1
