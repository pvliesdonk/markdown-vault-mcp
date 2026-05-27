"""Tests for Collection lazy-initialization semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from pathlib import Path


def test_ensure_initialized_does_not_build_index(tmp_path: Path) -> None:
    """Calling a tool method before build_index() must not trigger a scan.

    The `_ensure_initialized` method historically called `build_index()` when
    the index had not been built yet. After the fast-probe change, it must
    only mark the collection as initialized so tool calls operate on
    whatever the DB currently has (possibly empty), never triggering a
    filesystem scan.
    """
    (tmp_path / "note.md").write_text("# Title\n\nSome body.\n", encoding="utf-8")

    collection = Collection(
        source_dir=tmp_path, state_path=tmp_path / ".state" / "state.json"
    )
    try:
        assert collection._initialized is False

        results = collection.search("Title")

        assert collection._initialized is True
        assert results == []
    finally:
        collection.close()
