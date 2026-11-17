"""Custom title field wiring through document and vault operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from pathlib import Path


def test_vault_rename_refreshes_custom_title(tmp_path: Path) -> None:
    (tmp_path / "original.md").write_text(
        "---\nname: Custom Title\ntitle: Other Title\n---\n# Heading Title\n\nbody\n",
        encoding="utf-8",
    )
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=False, title_field="name")
    )
    try:
        vault.index.build_index()
        vault.writer.rename("original.md", "renamed.md")
        wait_for_writer_drain(vault)
        note = vault.reader.read("renamed.md")
        assert note is not None
        assert note.title == "Custom Title"
        results = vault.reader.search("body", mode="keyword")
        assert [(result.path, result.title) for result in results] == [
            ("renamed.md", "Custom Title")
        ]
    finally:
        vault.close()
