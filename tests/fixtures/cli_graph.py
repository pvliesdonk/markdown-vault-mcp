"""Synthetic indexed link topology for local and real HTTP CLI checks."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest


@pytest.fixture
def graph_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a cycle, hub, orphan, Unicode path and three broken-link scopes."""
    source = tmp_path / "graph"
    notes = {
        "alpha.md": "# Alpha\n[Beta](notes/beta.md)\n[Lost](missing-root.md)\n",
        "notes/beta.md": "# Beta\n[Gamma](ø.md)\n[Lost](missing.md)\n",
        "notes/ø.md": "# Gamma\n[Alpha](../alpha.md)\n",
        "notes/deep/delta.md": "# Delta\n[Beta](../beta.md)\n",
        "notes-extra/epsilon.md": "# Epsilon\n[Lost](missing-sibling.md)\n",
        "island.md": "# Island\nDisconnected.\n",
    }
    for relative, content in notes.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (1700000000, 1700000000))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    return source
