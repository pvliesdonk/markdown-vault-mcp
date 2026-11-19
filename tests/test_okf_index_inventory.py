"""Generated navigation uses live inventory rather than search admission."""

from pathlib import Path

from markdown_vault_mcp.vault import Vault, VaultSettings


def test_generate_lists_non_admitted_concept_without_excluded_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "missing.md").write_text("---\ntype: concept\n---\nbody\n")
    (tmp_path / "excluded.md").write_text("# Excluded\n")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden/note.md").write_text("# Hidden\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            required_frontmatter=["title"],
            exclude_patterns=["excluded.md"],
        ),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "[missing](/missing.md)" in body
        assert "excluded.md" not in body
        assert ".hidden" not in body
    finally:
        vault.close()


def test_generate_before_first_index_build(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("# A note\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer.okf_generate_index()
        assert "[A note](/note.md)" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()
