"""Tests for data types."""

from __future__ import annotations

import dataclasses
from dataclasses import asdict

from markdown_vault_mcp.types import SKIP_CATEGORIES, MoveFolderResult, SkippedFile


def test_move_folder_result_fields():
    r = MoveFolderResult(
        old_dir="drafts",
        new_dir="archive/2026",
        files_moved=3,
        updated_links=2,
        failed_links=["notes/bad.md"],
    )
    assert r.old_dir == "drafts"
    assert r.new_dir == "archive/2026"
    assert r.files_moved == 3
    assert r.updated_links == 2
    assert r.failed_links == ["notes/bad.md"]


def test_move_folder_result_failed_links_defaults_empty():
    r = MoveFolderResult(old_dir="a", new_dir="b", files_moved=0, updated_links=0)
    assert r.failed_links == []
    # asdict works (the MCP tool serializes via dataclasses.asdict)
    assert dataclasses.asdict(r)["failed_links"] == []


class TestSkippedFile:
    def test_serializes_to_plain_dict(self) -> None:
        sf = SkippedFile(
            path="notes/bad.md",
            category="parse_error",
            detail="while scanning a simple key",
        )
        assert asdict(sf) == {
            "path": "notes/bad.md",
            "category": "parse_error",
            "detail": "while scanning a simple key",
        }

    def test_is_frozen(self) -> None:
        sf = SkippedFile(path="a.md", category="parse_error", detail="x")
        try:
            sf.path = "b.md"  # type: ignore[misc]
        except AttributeError:
            return
        raise AssertionError("SkippedFile should be frozen")

    def test_skip_categories_are_the_three_legal_values(self) -> None:
        assert {
            "parse_error",
            "encoding_error",
            "missing_frontmatter",
        } == SKIP_CATEGORIES
