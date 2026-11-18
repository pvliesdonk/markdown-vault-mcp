"""Tests for field-collapsed search output (issue #469)."""

from __future__ import annotations

import pytest

from markdown_vault_mcp.types import GroupedResult


def test_search_groups_same_file_chunks_under_one_result(populated_vault):
    """Two chunks of the same doc collapse into one GroupedResult."""
    results = populated_vault.reader.search("foo", limit=10, chunks_per_file=3)
    paths = [r.path for r in results]
    assert len(paths) == len(set(paths)), f"duplicate paths in {paths}"
    assert all(isinstance(r, GroupedResult) for r in results)
    for r in results:
        assert r.sections, f"result for {r.path} has no sections"
        assert r.score == max(s.score for s in r.sections)


def test_search_chunks_per_file_rejects_zero(populated_vault):
    with pytest.raises(ValueError, match="chunks_per_file"):
        populated_vault.reader.search("foo", limit=10, chunks_per_file=0)


def test_distinct_chunks_keeps_the_best_row_of_each_chunk_in_first_position():
    from markdown_vault_mcp.managers.search import _distinct_chunks as distinct_chunks

    def row(path, content, score, heading=None, start_line=0):
        return {
            "path": path,
            "heading": heading,
            "start_line": start_line,
            "content": content,
            "score": score,
        }

    rows = [
        row("a.md", "body", 0.2),
        row("b.md", "other", 0.9),
        row("a.md", "body", 0.8),  # a listing row copying the chunk's metadata
        row("a.md", "body", 0.8, heading="H"),
        row("a.md", "body", 0.8, start_line=3),
        row("a.md", "fragment", 0.5),
    ]
    kept = distinct_chunks(rows)
    assert [(r["path"], r["content"], r["score"]) for r in kept] == [
        ("a.md", "body", 0.8),
        ("b.md", "other", 0.9),
        ("a.md", "body", 0.8),
        ("a.md", "body", 0.8),
        ("a.md", "fragment", 0.5),
    ]
    assert len(kept) == 5
    assert distinct_chunks([]) == []
