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
        # The private loader's defensive guard mirrors the public behavior.
        assert resolver._load("") is None

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

    def test_non_utf8_file_skipped_without_breaking_chain(
        self, vault_dir: Path
    ) -> None:
        # A latin-1/binary conventions file must not error the lookup or
        # drop valid ancestor entries.
        (vault_dir / "3-Resources" / "_conventions.md").write_bytes(
            b"caf\xe9 rules \xff\xfe"
        )
        resolver = ConventionsResolver(vault_dir, "_conventions.md")
        entries = resolver.for_path("3-Resources/CRA.md")
        assert [e.folder for e in entries] == [""]


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

    def test_skips_folders_under_exclude_patterns(self, vault_dir: Path) -> None:
        archive = vault_dir / "4-Archive"
        archive.mkdir()
        (archive / "_conventions.md").write_text("archived", encoding="utf-8")
        resolver = ConventionsResolver(
            vault_dir, "_conventions.md", exclude_patterns=["4-Archive/**"]
        )
        assert resolver.list_folders() == ["", "3-Resources"]

    def test_file_level_exclude_pattern_filters_unpruned_walk(
        self, vault_dir: Path
    ) -> None:
        # A file-exact pattern cannot be directory-pruned; the per-file
        # correctness filter must catch it.
        resolver = ConventionsResolver(
            vault_dir,
            "_conventions.md",
            exclude_patterns=["3-Resources/_conventions.md"],
        )
        assert resolver.list_folders() == [""]


class TestVaultDerivedExclusion:
    def test_vault_derives_exclude_patterns(self, tmp_path: Path) -> None:
        """Direct Vault construction excludes convention files by itself."""
        from markdown_vault_mcp.vault import Vault

        (tmp_path / "_conventions.md").write_text("Rules.", encoding="utf-8")
        (tmp_path / "note.md").write_text("# Note\nBody.", encoding="utf-8")
        col = Vault(source_dir=tmp_path, exclude_patterns=[".trash/**"])
        try:
            assert col.exclude_patterns == [
                ".trash/**",
                "_conventions.md",
                "**/_conventions.md",
            ]
            col.index.build_index()
            paths = [n.path for n in col.reader.list_documents()]
            assert paths == ["note.md"]
            assert [e.content for e in col.conventions.for_path("note.md")] == [
                "Rules."
            ]
        finally:
            col.close()

    def test_vault_rejects_glob_metachars_in_filename(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        with pytest.raises(ValueError, match="metacharacters"):
            Vault(source_dir=tmp_path, conventions_file="notes*.md")

    def test_vault_disabled_conventions_adds_nothing(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        col = Vault(source_dir=tmp_path, conventions_file=None)
        try:
            assert col.exclude_patterns is None
            assert col.conventions.enabled is False
        finally:
            col.close()
