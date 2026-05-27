"""Tests for Collection initialization semantics (issue #513)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_ensure_initialized_does_not_build_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_ensure_initialized()` must only flip the flag, not call `build_index()`.

    Previously `_ensure_initialized` called `build_index` on first access,
    which blocked the MCP handshake on cold start. The new contract: it
    flips ``_initialized`` and nothing else; callers see whatever the FTS
    DB currently contains, which may be empty. A monkeypatched
    `build_index` raising on call directly proves the invariant.
    """
    (tmp_path / "note.md").write_text("# Title\n\nSome body.\n", encoding="utf-8")

    collection = Collection(
        source_dir=tmp_path, state_path=tmp_path / ".state" / "state.json"
    )
    try:
        assert collection._initialized is False

        def fail_build_index(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(
                "build_index must not be called by _ensure_initialized"
            )

        monkeypatch.setattr(collection, "build_index", fail_build_index)

        results = collection.search("Title")

        assert collection._initialized is True
        assert results == []
    finally:
        collection.close()
