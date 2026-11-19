"""Automatic index folding and designated folder boundaries."""

from pathlib import Path

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.utils.fs import path_exists
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain


def _vault(root: Path, folders: tuple[str, ...]) -> Vault:
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="off", okf_folders=folders
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    vault.index.build_index()
    return vault


def test_folds_deep_notes_and_preserves_authored_boundary(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p/deep/more").mkdir(parents=True)
    (root / "projects/p/other").mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    authored = "# Handwritten\n"
    (root / "projects/p/other/index.md").write_text(authored)
    (root / "projects/p/other/hidden.md").write_text("# Hidden\n")
    vault = _vault(root, (".", "projects/*"))
    try:
        vault.writer.write("projects/p/deep/more/note.md", "# Deep note\n")
        wait_for_writer_drain(vault)
        text = (root / "projects/p/index.md").read_text()
        assert "## deep/more" in text
        assert "[Deep note](/projects/p/deep/more/note.md)" in text
        assert "[other/](/projects/p/other/index.md)" in text
        assert "hidden.md" not in text
        assert (root / "projects/p/other/index.md").read_text() == authored
        assert not path_exists(root / "projects/p/deep/index.md")
        assert not path_exists(root / "projects/p/deep/more/index.md")
        vault.writer.delete("projects/p/deep/more/note.md")
        wait_for_writer_drain(vault)
        assert "note.md" not in (root / "projects/p/index.md").read_text()
        vault.writer.write("root.md", "# Root note\n")
        wait_for_writer_drain(vault)
        root_index = (root / "index.md").read_text()
        assert 'okf_version: "0.2"' in root_index
        assert "/root.md" in root_index
    finally:
        vault.close()


def test_all_folders_retains_recursive_golden(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "a/b").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = _vault(root, ("**",))
    try:
        vault.writer.write("a/b/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert (
            (root / "index.md").read_text()
            == '---\nokf_version: "0.2"\n---\n# Index\n\n- [a/](/a/index.md)\n'
        )
        assert (root / "a/index.md").read_text() == "# a\n\n- [b/](/a/b/index.md)\n"
        assert (root / "a/b/index.md").read_text() == "# b\n\n- [Note](/a/b/note.md)\n"
    finally:
        vault.close()
