"""Generated navigation lists every visible note, not only those search admits."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.utils.fs import path_exists
from markdown_vault_mcp.vault import Vault, VaultSettings
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


def test_listing_keeps_readable_names_and_drops_non_files(tmp_path: Path) -> None:
    (tmp_path / "real.md").write_text("# Real\n")
    (tmp_path / "alias.md").symlink_to("real.md")
    (tmp_path / "dangling.md").symlink_to("gone.md")
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "pipe.md")
    secret = tmp_path / "secret.md"
    secret.write_text("# Secret\n")
    secret.chmod(0)
    vault = _vault(tmp_path)
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        for listed in ("real", "alias", "secret"):
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
def test_unlistable_folder_refuses_by_name_before_any_write(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    (tmp_path / "child/index.md").write_text("# Existing child\n")
    (tmp_path / "child/note.md").write_text("# Note\n")
    locked = tmp_path / "docs/locked[1]"
    locked.mkdir(parents=True)
    locked.chmod(0)
    vault = designated_vault(tmp_path, (".", "child"))
    try:
        with pytest.raises(InvalidRequestError) as refused:
            vault.writer.okf_generate_index(folder="child")
        assert "'docs/locked[1]'" in str(refused.value)
        assert "restore access" in str(refused.value)
        assert "exclude" not in str(refused.value)
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


@pytest.mark.parametrize("folder", [".", "guides//nested", "../escape"])
def test_invalid_folder_is_request_error(tmp_path: Path, folder: str) -> None:
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    vault = _vault(tmp_path)
    try:
        with pytest.raises(InvalidRequestError, match="Invalid folder"):
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


@pytest.mark.parametrize(
    ("case", "designated"),
    [("excluded", False), ("owned", False), ("owned", True)],
    ids=["excluded", "server-owned", "server-owned-designated"],
)
def test_unlisted_existing_index_is_not_a_boundary(
    tmp_path: Path, case: str, designated: bool
) -> None:
    (tmp_path / "child").mkdir()
    (tmp_path / "child/note.md").write_text("# Note\n")
    (tmp_path / "index.md").write_text(_ROOT_INDEX)
    index = tmp_path / "child/index.md"
    if case == "excluded":
        index.write_text("# Authored\n")
        vault = _vault(tmp_path, exclude_patterns=["child/index.md"])
    elif designated:
        vault = designated_vault(tmp_path, (".",), index_path=index)
    else:
        vault = _vault(tmp_path, index_path=index)
    try:
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "/child/index.md" not in body
        assert "(/child/note.md)" in body
        if case == "excluded":
            assert index.read_text() == "# Authored\n"
        else:
            assert index.read_bytes().startswith(b"SQLite format 3")
    finally:
        vault.close()


@pytest.mark.parametrize("designated", [False, True])
def test_outside_vault_links_are_omitted_without_outside_writes(
    tmp_path: Path, designated: bool
) -> None:
    root = tmp_path / "vault"
    (root / "a").mkdir(parents=True)
    (root / "a/n.md").write_text("# A\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "confidential.md").write_text("# Outside\n")
    (root / "z").symlink_to(outside, target_is_directory=True)
    (root / "leaf.md").symlink_to(outside / "confidential.md")
    (root / "index.md").write_text(_ROOT_INDEX)
    vault = designated_vault(root, (".",)) if designated else _vault(root)
    try:
        vault.writer.okf_generate_index()
        body = (root / "index.md").read_text()
        assert "/a/" in body
        assert vault.reader.read("leaf.md") is None
        for absent in ("/z/", "confidential", "leaf"):
            assert absent not in body
        assert sorted(p.name for p in outside.iterdir()) == ["confidential.md"]
    finally:
        vault.close()


@pytest.mark.parametrize("explicit", [False, True], ids=["settings", "policy"])
def test_server_owned_paths_are_not_listed(tmp_path: Path, explicit: bool) -> None:
    from markdown_vault_mcp.bundle_inventory import InspectionPolicy

    (tmp_path / "note.md").write_text("# Note\n")
    index = tmp_path / "search.md"
    if explicit:
        owned = tmp_path / "state"
        owned.mkdir()
        (owned / "inside.md").write_text("# Inside\n")
        policy = InspectionPolicy(state_paths=(owned,), index_path=index)
        vault = Vault(
            source_dir=tmp_path,
            settings=VaultSettings(read_only=False, index_path=index),
            inspection_policy=policy,
        )
        hidden = ("state", "inside", "search", "twin")
    else:
        vault = _vault(tmp_path, index_path=index, state_path=tmp_path / "state.md")
        hidden = ("search", "state", "alias")
    try:
        vault.index.build_index()
        if explicit:
            os.link(index, tmp_path / "twin.md")
        else:
            (tmp_path / "alias.md").symlink_to("search.md")
        vault.writer.okf_generate_index()
        body = (tmp_path / "index.md").read_text()
        assert "[Note](/note.md)" in body
        for internal in hidden:
            assert internal not in body
    finally:
        vault.close()


def test_uninspectable_vault_root_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from markdown_vault_mcp.bundle_inventory import BundleInspector

    (tmp_path / "a.md").write_text("# A\n")
    monkeypatch.setattr(
        BundleInspector, "inspect", lambda *_: SimpleNamespace(state="unavailable")
    )
    vault = _vault(tmp_path)
    try:
        with pytest.raises(InvalidRequestError, match=r"Cannot list '\.'"):
            vault.writer.okf_generate_index()
        assert not path_exists(tmp_path / "index.md")
    finally:
        vault.close()


def test_missing_folder_is_listed_empty(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    try:
        result = vault.writer.okf_generate_index(folder="new")
        assert result.entries == 0
        assert (tmp_path / "new/index.md").read_text().startswith("# new")
    finally:
        vault.close()
