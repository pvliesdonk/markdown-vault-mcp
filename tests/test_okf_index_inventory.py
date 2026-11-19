"""Generated navigation lists every visible note, not only those search admits."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain
from tests.fixtures.designated_index import designated_vault

if TYPE_CHECKING:
    from pathlib import Path

_ROOT_INDEX = '---\nokf_version: "0.2"\n---\n# Root\n'

needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores file permissions",
)


def _vault(root: Path, **settings: object) -> Vault:
    return Vault(source_dir=root, settings=VaultSettings(read_only=False, **settings))


@pytest.mark.parametrize("built", [False, True])
def test_generate_lists_non_admitted_concept_without_excluded_paths(
    tmp_path: Path, built: bool
) -> None:
    (tmp_path / "missing.md").write_text("---\ntype: concept\n---\nbody\n")
    (tmp_path / "titled.md").write_text("---\ntitle: Titled\ndescription: Kept\n---\n")
    (tmp_path / "excluded.md").write_text("# Excluded\n")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden/note.md").write_text("# Hidden\n")
    vault = _vault(
        tmp_path, required_frontmatter=["title"], exclude_patterns=["excluded.md"]
    )
    try:
        if built:
            vault.index.build_index()
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "[missing](/missing.md)" in body
        label = "[Titled](/titled.md) - Kept" if built else "[titled](/titled.md)"
        assert label in body
        assert "excluded.md" not in body
        assert ".hidden" not in body
        assert vault.index.is_queryable() is built
    finally:
        vault.close()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_listing_follows_scanner_file_rules(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "real.md").write_text("# Real\n")
    (root / "alias.md").symlink_to("real.md")
    (root / "dangling.md").symlink_to("gone.md")
    (tmp_path / "outside.md").write_text("# Outside\n")
    (root / "outside.md").symlink_to(tmp_path / "outside.md")
    os.mkfifo(root / "pipe.md")
    secret = root / "secret.md"
    secret.write_text("# Secret\n")
    secret.chmod(0)
    vault = _vault(root)
    try:
        vault.writer.okf_generate_index()
        body = (root / "index.md").read_text()
        for listed in ("real", "alias", "outside", "secret"):
            assert f"[{listed}](/{listed}.md)" in body
        assert "dangling" not in body
        assert "pipe" not in body
    finally:
        secret.chmod(0o644)
        vault.close()


def test_relative_vault_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    (tmp_path / "sub").mkdir()
    (tmp_path / "a.md").write_text("# A\n")
    (tmp_path / "sub/b.md").write_text("# B\n")
    monkeypatch.chdir(tmp_path)
    vault = _vault(Path())
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "[a](/a.md)" in body
        assert "[sub/](/sub/index.md)" in body
        assert "[b](/sub/b.md)" in (tmp_path / "sub/index.md").read_text()
    finally:
        vault.close()


@pytest.mark.skipif(
    sys.version_info < (3, 13),
    reason="the scanner follows directory links from Python 3.13 (#508)",
)
def test_directory_alias_is_listed(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real/n.md").write_text("# Linked\n")
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    vault = _vault(tmp_path)
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="alias")
        assert "[Linked](/alias/n.md)" in (tmp_path / "real/index.md").read_text()
        vault.writer.okf_generate_index()
        assert "[alias/](/alias/index.md)" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


@needs_permissions
def test_unlistable_visible_folder_refuses_before_any_write(tmp_path: Path) -> None:
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
def test_unreadable_hidden_and_excluded_trees_neither_block_nor_appear(
    tmp_path: Path,
) -> None:
    (tmp_path / "visible.md").write_text("# Visible\n")
    for top in (".cache", "build"):
        (tmp_path / top / "d").mkdir(parents=True)
        (tmp_path / top / "d/secret.md").write_text("# Hidden\n")
        (tmp_path / top / "d").chmod(0)
    vault = _vault(tmp_path, exclude_patterns=["build/**"])
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "[visible](/visible.md)" in body
        assert ".cache" not in body
        assert "build" not in body
    finally:
        for top in (".cache", "build"):
            (tmp_path / top / "d").chmod(0o755)
        vault.close()


def test_just_written_note_is_titled(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = designated_vault(tmp_path, (".",))
    try:
        vault.index.build_index()
        vault.writer.write("notes/new.md", "# New title\n")
        wait_for_writer_drain(vault)
        assert "[New title](/notes/new.md)" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


@pytest.mark.parametrize("folder", [".", "guides//nested", "../escape"])
def test_invalid_folder_is_request_error(tmp_path: Path, folder: str) -> None:
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = _vault(tmp_path)
    try:
        with pytest.raises(InvalidRequestError, match="folder"):
            vault.writer.okf_generate_index(folder=folder)
        assert (tmp_path / "index.md").read_text() == _ROOT_INDEX
    finally:
        vault.close()


def test_empty_new_designated_child_connects_to_parent(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = designated_vault(tmp_path, (".", "child"))
    try:
        vault.writer.okf_generate_index(folder="child")
        assert "[child/](/child/index.md)" in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("child", "exclude"),
    [(".drafts", ()), ("drafts", ("drafts/**",)), ("drafts", ("drafts/index.md",))],
    ids=["dot-folder", "excluded-folder", "excluded-index"],
)
def test_invisible_designated_child_gets_no_parent_pointer(
    tmp_path: Path, child: str, exclude: tuple[str, ...]
) -> None:
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = designated_vault(tmp_path, (".", child), exclude)
    try:
        vault.writer.okf_generate_index(folder=child)
        assert (tmp_path / child / "index.md").read_text().startswith("# ")
        assert child not in (tmp_path / "index.md").read_text()
    finally:
        vault.close()


def test_excluded_child_index_is_not_a_boundary(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    (tmp_path / "child/note.md").write_text("---\ntype: concept\n---\nBody\n")
    (tmp_path / "child/index.md").write_text("# Authored\n")
    vault = _vault(
        tmp_path, required_frontmatter=["title"], exclude_patterns=["child/index.md"]
    )
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "/child/index.md" not in body
        assert "[note](/child/note.md)" in body
        assert (tmp_path / "child/index.md").read_text() == "# Authored\n"
    finally:
        vault.close()
