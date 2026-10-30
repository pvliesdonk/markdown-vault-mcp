"""An index path inside a folder that does not exist yet (#1730).

The keyword store creates the folder at an owning open, as the vector
stores and the state file do; the store's own tests cover the platforms
without a writer lock. This pins it end to end through the vault.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from pathlib import Path


def test_an_index_in_a_new_folder_is_built_there(tmp_path: Path) -> None:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "a.md").write_text("# a\n\nhello\n")
    index = tmp_path / "missing" / "deeper" / "index.db"
    vault = Vault(source_dir=vault_dir, settings=VaultSettings(index_path=index))
    try:
        vault.index.build_index()
        hits = vault.reader.search("hello", mode="keyword")
    finally:
        vault.close()
    assert index.is_file()
    assert [hit.path for hit in hits] == ["a.md"]
