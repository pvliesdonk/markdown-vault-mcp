"""Generated navigation uses live inventory rather than search admission."""

from pathlib import Path

import pytest

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


def test_incomplete_inventory_preserves_existing_index(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    deep = tmp_path
    for _ in range(32):
        deep = deep / "d"
        deep.mkdir()
    (deep / "note.md").write_text("# Deep\n")
    old = "# Existing navigation\n"
    (tmp_path / "index.md").write_text(old)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError):
            vault.writer.okf_generate_index()
        assert (tmp_path / "index.md").read_text() == old
    finally:
        vault.close()


@pytest.mark.parametrize("folder", [".", "guides//nested", "../escape"])
def test_invalid_folder_is_request_error(tmp_path: Path, folder: str) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError, match="folder"):
            vault.writer.okf_generate_index(folder=folder)
    finally:
        vault.close()


@pytest.mark.parametrize("built", [False, True])
def test_large_note_metadata_and_disk_edits(tmp_path: Path, built: bool) -> None:
    note = tmp_path / "topic.md"
    note.write_text(
        "---\ntype: concept\ndescription: Original\n---\n# First\n" + "x" * 1000
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, max_note_read_bytes=128, required_frontmatter=["title"]
        ),
    )
    try:
        if built:
            vault.index.build_index()
        note.write_text(
            "---\ntype: concept\ndescription: Current\n---\n# Changed\n" + "x" * 1000
        )
        vault.writer.okf_generate_index()
        assert "[Changed](/topic.md) - Current" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


def test_directory_alias_does_not_overwrite_index(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    real = tmp_path / "real"
    real.mkdir()
    (real / "index.md").write_text("# Authored\n")
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError, match="alias"):
            vault.writer.okf_generate_index(folder="alias")
        assert (real / "index.md").read_text() == "# Authored\n"
    finally:
        vault.close()


def test_paging_and_malformed_fallback(tmp_path: Path) -> None:
    for number in range(1001):
        (tmp_path / f"note-{number:04}.md").write_text("body\n")
    (tmp_path / "bad.md").write_text("---\ntitle: [\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert body.count("](/note-") == 1001
        assert "[bad](/bad.md)" in body
    finally:
        vault.close()


def test_unavailable_parent_is_checked_before_child_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError

    (tmp_path / "child").mkdir()
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "child/index.md").write_text("# Existing child\n")
    (tmp_path / "child/note.md").write_text("# Note\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".", "child")
    ).okf_capabilities
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    try:
        inspect = vault._doc_mgr.inspect_bundle

        def unavailable_parent(request):
            page = inspect(request)
            return (
                replace(page, enumeration="unavailable") if request.root == "" else page
            )

        monkeypatch.setattr(vault._doc_mgr, "inspect_bundle", unavailable_parent)
        with pytest.raises(InvalidRequestError, match="inspect"):
            vault.writer.okf_generate_index(folder="child")
        assert (tmp_path / "child/index.md").read_text() == "# Existing child\n"
    finally:
        vault.close()
