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
