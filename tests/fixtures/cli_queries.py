"""Synthetic corpus distinguishing native query scopes and projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def query_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep body-only and metadata-only matches in independently checked notes."""
    source = tmp_path / "notes"
    source.mkdir()
    notes = {
        "a.md": "---\ntitle: Alpha\ntags: [shared, red]\n---\n# Alpha\n\nbodyword one two\n\n## More\n\nbodyword three four\n",
        "b.md": "---\ntitle: Beta\ntags: [shared, blue]\n---\n# Beta\n\nbodyword five six\n",
        "c.md": "---\ntitle: bodyword\ntags: [other]\n---\n# Other\n\nDifferent content\n",
    }
    for path, text in notes.items():
        (source / path).write_text(text, encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", ":memory:")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags")
    return source


@pytest.fixture
def query_index(
    query_notes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Seed a completed index independently of the commands being verified."""
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings

    index = tmp_path / "completed.sqlite"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    config = ProjectConfig.from_env()
    vault = Vault(
        source_dir=query_notes, settings=VaultSettings.from_project_config(config)
    )
    try:
        vault.index.build_index()
    finally:
        vault.close()
    return index
