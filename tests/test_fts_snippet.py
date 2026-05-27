"""Tests for FTS5 snippet() projection in FTSIndex.search."""

from __future__ import annotations

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import HeadingChunker, parse_note


def _upsert(tmp_path, fts: FTSIndex, name: str, body: str) -> None:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    fts.upsert_note(parse_note(p, tmp_path, HeadingChunker()))


def test_snippet_words_zero_returns_full_content(tmp_path):
    """snippet_words=0 (or None) returns the full chunk content."""
    fts = FTSIndex(db_path=":memory:")
    body = "# A\n" + "alpha " * 50 + "needle " + "alpha " * 50 + "\n"
    _upsert(tmp_path, fts, "doc.md", body)

    [r] = fts.search("needle", limit=10, snippet_words=0)
    # Full chunk: ~101 words.
    assert "alpha alpha" in r.content
    assert len(r.content.split()) > 50


def test_snippet_words_caps_returned_text(tmp_path):
    """snippet_words=20 returns roughly 20 tokens centered on the match."""
    fts = FTSIndex(db_path=":memory:")
    body = "# A\n" + "alpha " * 100 + "needle " + "alpha " * 100 + "\n"
    _upsert(tmp_path, fts, "doc.md", body)

    [r] = fts.search("needle", limit=10, snippet_words=20)
    assert "needle" in r.content
    assert len(r.content.split()) <= 30  # 20 + ellipsis slack


def test_snippet_includes_ellipsis_marker_when_truncated(tmp_path):
    """Truncated snippets include the … marker."""
    fts = FTSIndex(db_path=":memory:")
    body = "# A\n" + "alpha " * 200 + "needle " + "alpha " * 200 + "\n"
    _upsert(tmp_path, fts, "doc.md", body)

    [r] = fts.search("needle", limit=10, snippet_words=10)
    assert "…" in r.content


def test_chunk_count_populated_on_fts_result(tmp_path):
    """FTSResult exposes the parent doc's chunk_count."""
    fts = FTSIndex(db_path=":memory:")
    body = "# A\nalpha needle\n## B\nbeta\n## C\ngamma\n"
    _upsert(tmp_path, fts, "doc.md", body)

    results = fts.search("needle", limit=10)
    assert results[0].chunk_count >= 1
    assert (
        results[0].chunk_count
        == fts._conn()
        .execute("SELECT chunk_count FROM documents WHERE path = 'doc.md'")
        .fetchone()["chunk_count"]
    )
