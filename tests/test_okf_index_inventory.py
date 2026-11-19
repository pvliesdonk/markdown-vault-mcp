"""Generated navigation lists every visible note, not only those search admits."""

from __future__ import annotations

import errno
import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.fixtures.designated_index import designated_vault

if TYPE_CHECKING:
    from pathlib import Path

_ROOT_INDEX = '---\nokf_version: "0.2"\n---\n# Root\n'

needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores file permissions",
)


def _okf_vault(
    root: Path, folders: tuple[str, ...], exclude: tuple[str, ...] = ()
) -> Vault:
    """Open a writable OKF vault with index maintenance and excludes."""
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=folders
    ).okf_capabilities
    return Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            exclude_patterns=list(exclude),
        ),
    )


@pytest.mark.parametrize("built", [False, True])
def test_generate_lists_non_admitted_concept_without_excluded_paths(
    tmp_path: Path, built: bool
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
        if built:
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
        assert "[note](/note.md)" in (tmp_path / "index.md").read_text()
        assert not vault.index.is_queryable()
    finally:
        vault.close()


@needs_permissions
def test_unreadable_note_is_listed_by_filename(tmp_path: Path) -> None:
    (tmp_path / "visible.md").write_text("# Visible\n")
    secret = tmp_path / "secret.md"
    secret.write_text("# Secret\n")
    secret.chmod(0)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "[secret](/secret.md)" in body
        assert "[visible](/visible.md)" in body
    finally:
        secret.chmod(0o644)
        vault.close()


def test_note_read_failure_keeps_existing_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.bundle_inventory as inventory
    import markdown_vault_mcp.utils.fs as fs

    (tmp_path / "note.md").write_text("# Note\n")
    (tmp_path / "index.md").write_text("# Existing\n\n- [Note](/note.md)\n")
    original = fs.read_regular_nofollow

    def fail_note(parent: int, name: str, limit: int) -> bytes:
        if name == "note.md":
            raise OSError(errno.EIO, "fixture I/O failure")
        return original(parent, name, limit)

    monkeypatch.setattr(inventory, "read_regular_nofollow", fail_note)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.writer.okf_generate_index()
        assert "](/note.md)" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


@needs_permissions
def test_unlistable_visible_folder_refuses_without_writes(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "note.md").write_text("# Locked\n")
    old = b"# Existing navigation\n"
    (tmp_path / "index.md").write_bytes(old)
    locked.chmod(0)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError, match="Cannot list"):
            vault.writer.okf_generate_index()
        assert (tmp_path / "index.md").read_bytes() == old
    finally:
        locked.chmod(0o755)
        vault.close()


@needs_permissions
def test_parent_refusal_is_checked_before_child_write(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    (tmp_path / "child/index.md").write_text("# Existing child\n")
    (tmp_path / "child/note.md").write_text("# Note\n")
    locked = tmp_path / "other"
    locked.mkdir()
    locked.chmod(0)
    vault = designated_vault(tmp_path, (".", "child"))
    try:
        with pytest.raises(InvalidRequestError, match="Cannot list"):
            vault.writer.okf_generate_index(folder="child")
        assert (tmp_path / "child/index.md").read_text() == "# Existing child\n"
        assert (tmp_path / "index.md").read_text() == _ROOT_INDEX
    finally:
        locked.chmod(0o755)
        vault.close()


@needs_permissions
def test_hidden_and_excluded_deep_trees_neither_block_nor_appear(
    tmp_path: Path,
) -> None:
    (tmp_path / "visible.md").write_text("# Visible\n")
    for top in (".cache", "build"):
        deep = tmp_path / top
        for _ in range(33):
            deep = deep / "d"
        deep.mkdir(parents=True)
        (deep / "secret.md").write_text("# Hidden\n")
        deep.chmod(0)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, exclude_patterns=["build/**"]),
    )
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "[visible](/visible.md)" in body
        assert ".cache" not in body
        assert "build" not in body
    finally:
        for top in (".cache", "build"):
            (tmp_path / top / "/".join(["d"] * 33)).chmod(0o755)
        vault.close()


def test_hidden_nested_repository_gets_no_pointer(
    tmp_path: Path,
    isolated_git: None,  # noqa: ARG001
) -> None:
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    nested = tmp_path / ".nested"
    nested.mkdir()
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    (nested / "index.md").write_text("# Nested\n")
    (tmp_path / "visible.md").write_text("# Visible\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False),
        git_strategy=GitWriteStrategy(),
    )
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "[visible](/visible.md)" in body
        assert ".nested" not in body
    finally:
        vault.close()


def test_contained_file_alias_is_listed_and_escapes_are_not(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "real.md").write_text("# Real\n")
    (root / "alias.md").symlink_to("real.md")
    (root / "dangling.md").symlink_to("gone.md")
    (tmp_path / "outside.md").write_text("# Outside\n")
    (root / "outside.md").symlink_to(tmp_path / "outside.md")
    vault = Vault(source_dir=root, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        assert any(note.path == "alias.md" for note in vault._search_mgr.list())
        vault.writer.okf_generate_index()
        body = (root / "index.md").read_text()
        assert "](/alias.md)" in body
        assert "dangling" not in body
        assert "outside" not in body
    finally:
        vault.close()


def test_paging_and_malformed_fallback(tmp_path: Path) -> None:
    for number in range(1001):
        (tmp_path / f"note-{number:04}.md").write_text("body\n")
    (tmp_path / "bad.md").write_text("---\ntitle: [\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert body.count("](/note-") == 1001
        assert "[bad](/bad.md)" in body
    finally:
        vault.close()


def test_indexed_large_note_keeps_title_and_description(tmp_path: Path) -> None:
    (tmp_path / "topic.md").write_text(
        "---\ndescription: Original\n---\n# First\n" + "x" * 1000
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, max_note_read_bytes=128),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index()
        assert "[First](/topic.md) - Original" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


def test_generation_reads_and_parses_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.scanner as scanner

    (tmp_path / "note.md").write_text("---\ndescription: Kept\n---\n# Titled\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()

        def no_parse(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("generation parsed a note")

        monkeypatch.setattr(scanner, "parse_frontmatter", no_parse)
        monkeypatch.setattr(vault._doc_mgr, "inspect_bundle", no_parse)
        vault.writer.okf_generate_index()
        assert "[Titled](/note.md) - Kept" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


def test_just_written_note_is_titled(tmp_path: Path) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = designated_vault(tmp_path, (".",))
    try:
        vault.index.build_index()
        vault.writer.write("notes/new.md", "# New title\n")
        wait_for_writer_drain(vault)
        assert "[New title](/notes/new.md)" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


def test_directory_alias_does_not_overwrite_index(tmp_path: Path) -> None:
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


@pytest.mark.parametrize("folder", [".", "guides//nested", "../escape"])
def test_invalid_folder_is_request_error(tmp_path: Path, folder: str) -> None:
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError, match="folder"):
            vault.writer.okf_generate_index(folder=folder)
    finally:
        vault.close()


def test_empty_new_designated_child_connects_to_parent(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = designated_vault(tmp_path, (".", "child"))
    try:
        vault.writer.okf_generate_index(folder="child")
        assert "/child/index.md" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("child", "exclude"),
    [
        (".drafts", ()),
        ("drafts", ("drafts/**",)),
        ("drafts", ("drafts/index.md",)),
    ],
    ids=["dot-folder", "excluded-folder", "excluded-index"],
)
def test_invisible_designated_child_gets_no_parent_pointer(
    tmp_path: Path, child: str, exclude: tuple[str, ...]
) -> None:
    (tmp_path / child).mkdir()
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = _okf_vault(tmp_path, (".", "**"), exclude)
    try:
        vault.writer.okf_generate_index(folder=child)
        assert (tmp_path / child / "index.md").exists()
        assert child not in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("child", "exclude"),
    [
        (".drafts", ()),
        ("drafts", ("drafts/**",)),
        ("drafts", ("drafts/*",)),
    ],
    ids=["dot-folder", "excluded-folder", "excluded-index"],
)
def test_maintenance_gives_invisible_child_no_parent_pointer(
    tmp_path: Path, child: str, exclude: tuple[str, ...]
) -> None:
    from tests.conftest import wait_for_writer_drain

    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = _okf_vault(tmp_path, (".", "**"), exclude)
    try:
        vault.writer.write(f"{child}/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert child not in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


def test_missing_folder_lists_nothing(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("# Note\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        assert vault._doc_mgr.index_note_paths("absent") == []
        assert vault._doc_mgr.index_note_paths("") == ["note.md"]
    finally:
        vault.close()


@needs_permissions
def test_is_symlink_reports_absence_and_refusal(tmp_path: Path) -> None:
    from markdown_vault_mcp.utils.fs import is_symlink

    locked = tmp_path / "locked"
    locked.mkdir()
    (tmp_path / "link.md").symlink_to("gone.md")
    assert is_symlink(tmp_path / "link.md")
    assert not is_symlink(tmp_path / "absent.md")
    locked.chmod(0)
    try:
        with pytest.raises(PermissionError):
            is_symlink(locked / "note.md")
    finally:
        locked.chmod(0o755)
