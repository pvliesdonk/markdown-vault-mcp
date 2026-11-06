"""The outline lists every heading a note has, however it was chunked (#2033).

``get_toc`` used to read headings from the chunk table, so every heading the
chunker does not give a chunk of its own went missing. Each case below is
one shape the chunker drops, built with the chunker settings that drop it.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path  # noqa: TC003

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
    extract_section,
    parse_note,
)
from markdown_vault_mcp.types import TocEntry

_PARA = "word " * 40 + "\n\n"
_FILLER = "\n".join(["filler"] * 30) + "\n\n"

# (id, body, chunker, expected outline)
_CASES: list[tuple[str, str, ChunkStrategy, list[TocEntry]]] = [
    (
        "parent_resplit_without_intro",
        _FILLER
        + "## What is known\n### Standards\n"
        + _PARA
        + "### Second\n"
        + _PARA
        + "## Other\nshort\n",
        HeadingChunker(max_chunk_words=50),
        [
            TocEntry("What is known", 2),
            TocEntry("Standards", 3),
            TocEntry("Second", 3),
            TocEntry("Other", 2),
        ],
    ),
    (
        "short_note",
        "## A\n" + "x " * 500 + "\n## B\n### B1\ny\n",
        HeadingChunker(),
        [TocEntry("A", 2), TocEntry("B", 2), TocEntry("B1", 3)],
    ),
    (
        "subheadings_within_budget",
        _FILLER + "## A\n### A1\nfoo\n### A2\nbar\n",
        HeadingChunker(),
        [TocEntry("A", 2), TocEntry("A1", 3), TocEntry("A2", 3)],
    ),
    (
        "heading_without_body",
        _FILLER + "## Empty\n## Full\ntext\n",
        HeadingChunker(),
        [TocEntry("Empty", 2), TocEntry("Full", 2)],
    ),
    (
        "whole_document_chunker",
        "## A\nx\n## B\ny\n",
        WholeDocumentChunker(),
        [TocEntry("A", 2), TocEntry("B", 2)],
    ),
]


def _index(tmp_path: Path, body: str, chunker: ChunkStrategy) -> FTSIndex:
    note_path = tmp_path / "note.md"
    note_path.write_text(body, encoding="utf-8")
    fts = FTSIndex(db_path=":memory:")
    fts.upsert_note(parse_note(note_path, tmp_path, chunk_strategy=chunker))
    return fts


@pytest.mark.parametrize(
    ("body", "chunker", "expected"),
    [case[1:] for case in _CASES],
    ids=[case[0] for case in _CASES],
)
def test_outline_lists_every_heading(
    tmp_path: Path, body: str, chunker: ChunkStrategy, expected: list[TocEntry]
) -> None:
    fts = _index(tmp_path, body, chunker)
    assert fts.get_toc("note.md") == expected
    notes, _ = fts.get_subtree_toc("")
    assert [n.headings for n in notes] == [expected]


@pytest.mark.parametrize(
    ("body", "chunker"),
    [case[1:3] for case in _CASES],
    ids=[case[0] for case in _CASES],
)
def test_every_outline_heading_is_readable_as_a_section(
    tmp_path: Path, body: str, chunker: ChunkStrategy
) -> None:
    fts = _index(tmp_path, body, chunker)
    for entry in fts.get_toc("note.md"):
        assert extract_section(body, entry.heading) is not None, entry


def test_repeated_heading_is_listed_once_per_occurrence(tmp_path: Path) -> None:
    fts = _index(tmp_path, "## Notes\na\n## Other\nb\n## Notes\nc\n", HeadingChunker())
    assert fts.get_toc("note.md") == [
        TocEntry("Notes", 2),
        TocEntry("Other", 2),
        TocEntry("Notes", 2),
    ]


def test_max_level_filters_stored_headings(tmp_path: Path) -> None:
    fts = _index(tmp_path, "## A\n### A1\n#### A1a\nx\n", HeadingChunker())
    assert fts.get_toc("note.md", max_level=3) == [
        TocEntry("A", 2),
        TocEntry("A1", 3),
    ]


def test_reindexing_a_note_replaces_its_headings(tmp_path: Path) -> None:
    fts = _index(tmp_path, "## Old\nx\n", HeadingChunker())
    (tmp_path / "note.md").write_text("## New\ny\n", encoding="utf-8")
    fts.upsert_note(parse_note(tmp_path / "note.md", tmp_path))
    assert fts.get_toc("note.md") == [TocEntry("New", 2)]
    fts.delete_by_path("note.md")
    rows = fts._conn().execute("SELECT COUNT(*) FROM note_headings").fetchone()
    assert rows[0] == 0


def test_read_only_index_without_heading_table_falls_back_to_chunks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = tmp_path / "index.db"
    (tmp_path / "vault").mkdir()
    note_path = tmp_path / "vault" / "note.md"
    note_path.write_text(_FILLER + "## A\n### A1\nx\n## B\ny\n", encoding="utf-8")
    writer = FTSIndex(db)
    writer.upsert_note(parse_note(note_path, tmp_path / "vault"))
    writer.close()
    # An index an older version built has no note_headings table.
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE note_headings")
    conn.commit()
    conn.close()

    reader = FTSIndex(db, read_only=True)
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.fts_index"):
        toc = reader.get_toc("note.md")
        reader.get_toc("note.md")
    reader.close()

    assert toc == [TocEntry("A", 2), TocEntry("B", 2)]
    warnings = [
        r for r in caplog.records if "toc_heading_table_missing" in r.getMessage()
    ]
    assert len(warnings) == 1
