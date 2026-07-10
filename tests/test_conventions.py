"""Unit tests for the folder-conventions resolver (conventions.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.conventions import (
    _MAX_ENTRY_CHARS,
    ConventionsResolver,
)


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """A small vault with root, nested, and deep convention files."""
    (tmp_path / "_conventions.md").write_text("Root rules.", encoding="utf-8")
    resources = tmp_path / "3-Resources"
    resources.mkdir()
    (resources / "_conventions.md").write_text(
        "---\ndescription: reference\n---\nResource rules.", encoding="utf-8"
    )
    (resources / "CRA.md").write_text("# CRA", encoding="utf-8")
    deep = resources / "Regulations" / "EU"
    deep.mkdir(parents=True)
    projects = tmp_path / "1-Projects"
    projects.mkdir()
    return tmp_path


class TestForPath:
    def test_accumulates_root_first(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        entries = resolver.for_path("3-Resources/CRA.md")
        assert [e.folder for e in entries] == ["", "3-Resources"]
        assert entries[0].content == "Root rules."
        assert entries[0].path == "_conventions.md"
        assert entries[1].path == "3-Resources/_conventions.md"

    def test_note_path_resolves_to_parent_folder(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        by_note = resolver.for_path("3-Resources/CRA.md")
        by_folder = resolver.for_path("3-Resources")
        assert by_note == by_folder

    def test_root_note_gets_root_conventions_only(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        entries = resolver.for_path("inbox-note.md")
        assert [e.folder for e in entries] == [""]

    def test_deep_folder_skips_missing_intermediate_files(
        self, vault_dir: Path
    ) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        entries = resolver.for_path("3-Resources/Regulations/EU/gdpr.md")
        assert [e.folder for e in entries] == ["", "3-Resources"]

    def test_folder_without_conventions_gets_root_only(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        entries = resolver.for_path("1-Projects/plan.md")
        assert [e.folder for e in entries] == [""]

    def test_no_files_anywhere_returns_empty(self, tmp_path: Path) -> None:
        resolver = ConventionsResolver(tmp_path, "_conventions.md")
        assert resolver.for_path("notes/a.md") == []

    def test_frontmatter_is_stripped(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        entries = resolver.for_path("3-Resources")
        assert entries[1].content == "Resource rules."

    def test_invalid_frontmatter_falls_back_to_raw(self, tmp_path: Path) -> None:
        raw = "---\n: bad: [yaml\n---\nBody text."
        (tmp_path / "_conventions.md").write_text(raw, encoding="utf-8")
        resolver = ConventionsResolver(tmp_path, "_conventions.md")
        entries = resolver.for_path("")
        assert entries[0].content == raw.strip()

    def test_backslashes_and_slashes_normalized(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        entries = resolver.for_path("\\3-Resources\\CRA.md")
        assert [e.folder for e in entries] == ["", "3-Resources"]

    def test_traversal_rejected(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        with pytest.raises(ValueError, match="traversal"):
            resolver.for_path("../outside/note.md")

    def test_disabled_returns_empty(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, None)
        assert resolver.enabled is False
        assert resolver.for_path("3-Resources/CRA.md") == []

    def test_oversized_content_truncated(self, tmp_path: Path) -> None:
        (tmp_path / "_conventions.md").write_text(
            "x" * (_MAX_ENTRY_CHARS + 500), encoding="utf-8"
        )
        resolver = ConventionsResolver(tmp_path, "_conventions.md")
        entries = resolver.for_path("")
        assert len(entries) == 1
        assert entries[0].content.endswith("[truncated]")
        assert len(entries[0].content) < _MAX_ENTRY_CHARS + 100

    def test_custom_filename(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("Agent rules.", encoding="utf-8")
        resolver = ConventionsResolver(tmp_path, "AGENTS.md")
        entries = resolver.for_path("")
        assert entries[0].content == "Agent rules."
        assert resolver.filename == "AGENTS.md"


class TestListFolders:
    def test_lists_folders_sorted_with_root_as_empty(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        assert resolver.list_folders() == ["", "3-Resources"]

    def test_skips_dot_directories(self, vault_dir: Path) -> None:
        hidden = vault_dir / ".obsidian"
        hidden.mkdir()
        (hidden / "_conventions.md").write_text("hidden", encoding="utf-8")
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        assert resolver.list_folders() == ["", "3-Resources"]

    def test_disabled_returns_empty(self, vault_dir: Path) -> None:
        resolver = ConventionsResolver(vault_dir, None)
        assert resolver.list_folders() == []

    def test_missing_source_dir_returns_empty(self, tmp_path: Path) -> None:
        resolver = ConventionsResolver(tmp_path / "nope", "_conventions.md")
        assert resolver.list_folders() == []
