"""Automatic OKF log placement follows designated and existing ancestors."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.utils.fs import path_exists
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain


@pytest.mark.parametrize(
    ("folders", "existing", "note", "chosen"),
    [
        ((".",), ("", "projects/x"), "projects/x/a/b/note.md", "projects/x"),
        (("**",), ("",), "projects/x/a.md", "projects/x"),
        ((".", "projects/*"), ("",), "projects/x/a/b/note.md", "projects/x"),
        (("projects/*",), (), "other/note.md", ""),
        (
            (".", "projects/*"),
            ("projects/x/a",),
            "projects/x/a/b/note.md",
            "projects/x/a",
        ),
    ],
)
def test_log_routing(
    tmp_path: Path,
    folders: tuple[str, ...],
    existing: tuple[str, ...],
    note: str,
    chosen: str,
) -> None:
    root = tmp_path / "vault"
    (root / note).parent.mkdir(parents=True)
    original = "# Log\n\n## 2020-01-01\n\n- Earlier entry\n"
    for folder in existing:
        (root / folder / "log.md").write_text(original)
    caps = ContentConfig(
        okf_mode="on", okf_log="writes", okf_folders=folders
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.writer.write(note, "# Note\n")
        wait_for_writer_drain(vault)
        target = root / chosen / "log.md"
        assert f"`{note}`" in target.read_text()
        for folder in existing:
            if folder != chosen:
                assert (root / folder / "log.md").read_text() == original
        if str(Path(note).parent) != chosen:
            assert not path_exists((root / note).parent / "log.md")
        if chosen in existing:
            assert "Earlier entry" in target.read_text()
    finally:
        vault.close()


def test_selected_log_alias_is_not_written_through(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "project/deep").mkdir(parents=True)
    (root / "authored.md").write_text("# Authored\n")
    (root / "project/log.md").symlink_to(root / "authored.md")
    caps = ContentConfig(
        okf_mode="on", okf_log="writes", okf_folders=(".", "project")
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        vault.writer.write("project/deep/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert (root / "authored.md").read_text() == "# Authored\n"
        assert not path_exists(root / "log.md")
    finally:
        vault.close()
