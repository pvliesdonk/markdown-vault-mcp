"""OKF enforced writes leave skill folders as the Agent Skills layout has them.

A skill folder is a folder holding ``SKILL.md``; everything below it belongs to
it, a ``SKILL.md`` nested deeper included (the outermost skill owns the
subtree). Inside one the enforced-write
layer stamps no top-level OKF keys on the skill's resource files (#2478) and
creates, refreshes or appends no ``index.md`` / ``log.md`` (#2479). Where a
skill's own ``SKILL.md`` stamp lands is #2095's, so it is not pinned here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp import okf
from markdown_vault_mcp.okf import skill_folder_of
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_ROOT_INDEX = '---\nokf_version: "0.2"\ntitle: Root\n---\n# Root\n'
_SKILL = "---\nname: demo\ndescription: Demo skill.\n---\n# Demo\n"
_NESTED = "---\nname: inner\ndescription: Inner skill.\n---\n# Inner\n"
_RESOURCE = "# Notes\n\nPlain resource file.\n"


class TestSkillFolderOf:
    def test_outermost_skill_folder_wins(self, tmp_path: Path) -> None:
        (tmp_path / "s/a/refs").mkdir(parents=True)
        (tmp_path / "s/a/SKILL.md").write_text("x", encoding="utf-8")
        (tmp_path / "s/a/refs/SKILL.md").write_text("x", encoding="utf-8")
        assert skill_folder_of(tmp_path, "s/a") == "s/a"
        assert skill_folder_of(tmp_path, "./s/a/refs/") == "s/a"

    def test_folders_outside_a_skill_and_the_root_are_none(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "s/a").mkdir(parents=True)
        (tmp_path / "SKILL.md").write_text("x", encoding="utf-8")
        assert skill_folder_of(tmp_path, "s/a") is None
        assert skill_folder_of(tmp_path, "") is None

    def test_a_skill_md_folder_is_not_a_skill(self, tmp_path: Path) -> None:
        (tmp_path / "s/SKILL.md").mkdir(parents=True)
        assert skill_folder_of(tmp_path, "s") is None

    def test_a_refused_stat_counts_as_no_skill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(_path: Path) -> bool:
            raise PermissionError("denied")

        monkeypatch.setattr(okf, "is_regular_file", refuse)
        assert skill_folder_of(tmp_path, "s/a") is None


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
    col = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    col.index.build_index()
    try:
        yield col
    finally:
        col.close()


def _files(vault: Vault) -> set[str]:
    root = vault.source_dir
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.parts)
    }


def _text(vault: Vault, path: str) -> str:
    return (vault.source_dir / path).read_text(encoding="utf-8")


def _reserved_inside(vault: Vault, skill: str) -> set[str]:
    return {
        f
        for f in _files(vault)
        if f.startswith(f"{skill}/") and f.rsplit("/", 1)[-1] in {"index.md", "log.md"}
    }


class TestSkillFolderWrites:
    def test_skill_md_write_is_listed_by_its_parent_only(self, vault: Vault) -> None:
        vault.writer.write("skills/demo/SKILL.md", _SKILL)
        wait_for_writer_drain(vault)
        assert _reserved_inside(vault, "skills/demo") == set()
        assert "skills/log.md" not in _files(vault)
        listing = _text(vault, "skills/index.md")
        assert "- [demo/](/skills/demo/SKILL.md) - Demo skill." in listing
        assert "/skills/demo/index.md" not in listing

    def test_resource_write_is_unstamped_and_unlisted(self, vault: Vault) -> None:
        vault.writer.write("skills/demo/SKILL.md", _SKILL)
        vault.writer.write("skills/demo/references/notes.md", _RESOURCE)
        vault.writer.edit("skills/demo/references/notes.md", "Plain", "Bare")
        wait_for_writer_drain(vault)
        assert _text(vault, "skills/demo/references/notes.md") == (
            "# Notes\n\nBare resource file.\n"
        )
        assert _reserved_inside(vault, "skills/demo") == set()

    def test_nested_template_skill_is_a_resource_of_the_outer(
        self, vault: Vault
    ) -> None:
        vault.writer.write("skills/demo/SKILL.md", _SKILL)
        vault.writer.write("skills/demo/templates/inner/SKILL.md", _NESTED)
        vault.writer.write("skills/demo/templates/inner/refs/x.md", _RESOURCE)
        wait_for_writer_drain(vault)
        assert _text(vault, "skills/demo/templates/inner/SKILL.md") == _NESTED
        assert _text(vault, "skills/demo/templates/inner/refs/x.md") == _RESOURCE
        assert _reserved_inside(vault, "skills/demo") == set()
        assert "inner" not in _text(vault, "skills/index.md")

    def test_note_beside_a_skill_folder_keeps_the_conventions(
        self, vault: Vault
    ) -> None:
        vault.writer.write("skills/demo/SKILL.md", _SKILL)
        vault.writer.write("skills/readme.md", "# Readme\n\nAbout skills.\n")
        wait_for_writer_drain(vault)
        assert "generated:" in _text(vault, "skills/readme.md")
        assert "wrote `skills/readme.md`" in _text(vault, "skills/log.md")
        listing = _text(vault, "skills/index.md")
        assert "[Readme](/skills/readme.md)" in listing
        assert "[demo/](/skills/demo/SKILL.md)" in listing
        assert _reserved_inside(vault, "skills/demo") == set()

    def test_delete_inside_a_skill_refreshes_nothing_there(self, vault: Vault) -> None:
        vault.writer.write("skills/demo/SKILL.md", _SKILL)
        vault.writer.write("skills/demo/references/a.md", _RESOURCE)
        vault.writer.write("skills/demo/references/b.md", _RESOURCE)
        authored = vault.source_dir / "skills/demo/references/index.md"
        authored.write_text("# Hand-kept\n", encoding="utf-8")
        vault.writer.delete("skills/demo/references/a.md")
        wait_for_writer_drain(vault)
        assert authored.read_text(encoding="utf-8") == "# Hand-kept\n"
        assert _reserved_inside(vault, "skills/demo") == {
            "skills/demo/references/index.md"
        }
