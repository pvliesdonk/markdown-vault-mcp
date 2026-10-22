"""Synthetic path populations shared by real local and HTTP CLI checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def path_source(discovery_source: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Add literal Unicode and sibling-folder paths with intersecting predicates."""
    for name, tag, category in [
        ("notes/alpine.md", "shared", "a=b"),
        ("notes-extra/alpha.md", "shared", "elsewhere"),
        ("notes/Straße.md", "shared", "a=b"),
        ("notes/star[1].md", "other", "a=b"),
    ]:
        path = discovery_source / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            f"---\ntitle: {path.stem}\ntags: [{tag}]\ncategory: {category}\n---\n# Note\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags,category")
    return discovery_source
