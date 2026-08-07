"""Integration tests for the OKF migration transforms (#963).

Exercised through the ``vault.writer.okf_*`` facet surface with a writable
Vault, mirroring the ``tests/test_move_folder.py`` pattern
(writable fixture + ``wait_for_writer_drain``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import ReadOnlyError
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def vault(source_dir: Path) -> Iterator[Vault]:
    col = Vault(source_dir=source_dir, read_only=False)
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


def _write(vault: Vault, path: str, body: str) -> None:
    vault.writer.write(path, body)
    wait_for_writer_drain(vault)


def _outlink_targets(vault: Vault, path: str) -> set[str]:
    return {o.target_path for o in vault.graph.get_outlinks(path)}


class TestConvertLinks:
    def test_graph_round_trips(self, vault: Vault) -> None:
        _write(vault, "guides/playbook.md", "# Playbook\nSteps.\n")
        _write(
            vault,
            "guides/plain.md",
            "# Plain\nSee [[playbook]] and [[playbook|the playbook]].\n",
        )
        before = _outlink_targets(vault, "guides/plain.md")
        assert before == {"guides/playbook.md"}

        result = vault.writer.okf_convert_links()
        wait_for_writer_drain(vault)

        assert result.files_changed == 1
        assert result.links_converted == 2
        assert result.links_skipped == 0
        # The graph is preserved edge-for-edge.
        assert _outlink_targets(vault, "guides/plain.md") == before
        body = vault.reader.read("guides/plain.md").content
        assert "[[playbook]]" not in body
        assert "[playbook](/guides/playbook.md)" in body
        assert "[the playbook](/guides/playbook.md)" in body

    def test_frontmatter_preserved(self, vault: Vault) -> None:
        # read() returns the full raw file (frontmatter included) in
        # NoteContent.content, and the converter writes it back verbatim, so
        # frontmatter — including OKF conformance fields — must survive.
        _write(vault, "target.md", "# Target\n")
        _write(
            vault,
            "note.md",
            "---\ntype: Playbook\ntags: [a, b]\nstatus: stable\n---\n"
            "# Note\nSee [[target]].\n",
        )
        vault.writer.okf_convert_links()
        wait_for_writer_drain(vault)
        read = vault.reader.read("note.md")
        assert read.frontmatter == {
            "type": "Playbook",
            "tags": ["a", "b"],
            "status": "stable",
        }
        assert "[target](/target.md)" in read.content
        assert "[[target]]" not in read.content

    def test_unresolvable_wikilink_skipped(self, vault: Vault) -> None:
        _write(vault, "note.md", "# Note\nSee [[ghost]].\n")
        result = vault.writer.okf_convert_links()
        assert result.links_converted == 0
        assert result.links_skipped == 1
        assert result.files_changed == 0
        assert "[[ghost]]" in vault.reader.read("note.md").content

    def test_folder_scope(self, vault: Vault) -> None:
        _write(vault, "a/x.md", "# X\n")
        _write(vault, "a/y.md", "# Y\nSee [[x]].\n")
        _write(vault, "b/z.md", "# Z\nSee [[x]].\n")
        result = vault.writer.okf_convert_links(folder="a")
        wait_for_writer_drain(vault)
        assert result.files_changed == 1
        assert "[[x]]" in vault.reader.read("b/z.md").content

    def test_read_only_rejected(self, source_dir: Path) -> None:
        col = Vault(source_dir=source_dir, read_only=True)
        col.index.build_index()
        try:
            with pytest.raises(ReadOnlyError):
                col.writer.okf_convert_links()
        finally:
            col.close()


class TestGenerateIndex:
    def test_lists_notes_and_preserves_frontmatter(self, vault: Vault) -> None:
        _write(
            vault,
            "index.md",
            '---\nokf_version: "0.2"\n---\n# Old body\n',
        )
        _write(
            vault,
            "playbook.md",
            "---\ntitle: The Playbook\ndescription: How to migrate.\n---\n# P\n",
        )
        _write(vault, "plain.md", "# Plain Note\n")

        result = vault.writer.okf_generate_index()
        wait_for_writer_drain(vault)

        assert result.path == "index.md"
        assert result.frontmatter_preserved is True
        content = vault.reader.read("index.md")
        # okf_version survived regeneration.
        assert content.frontmatter.get("okf_version") == "0.2"
        body = content.content
        assert "- [The Playbook](/playbook.md) - How to migrate." in body
        assert "- [Plain Note](/plain.md)" in body
        # index.md does not list itself.
        assert "/index.md)" not in body

    def test_folder_index(self, vault: Vault) -> None:
        _write(vault, "guides/one.md", "# One\n")
        result = vault.writer.okf_generate_index(folder="guides")
        wait_for_writer_drain(vault)
        assert result.path == "guides/index.md"
        assert result.frontmatter_preserved is False
        assert "# guides" in vault.reader.read("guides/index.md").content

    def test_progressive_disclosure_one_level(self, vault: Vault) -> None:
        # index.md lists only immediate notes plus a pointer per immediate
        # subfolder — it does not flatten the whole subtree.
        _write(vault, "top.md", "# Top\n")
        _write(vault, "guides/one.md", "# One\n")
        _write(vault, "guides/sub/two.md", "# Two\n")

        vault.writer.okf_generate_index()
        wait_for_writer_drain(vault)
        root = vault.reader.read("index.md").content
        assert "- [Top](/top.md)" in root
        assert "- [guides/](/guides/index.md)" in root
        assert "/guides/one.md" not in root  # deferred to guides/index.md
        assert "/two.md" not in root

        vault.writer.okf_generate_index(folder="guides")
        wait_for_writer_drain(vault)
        guides = vault.reader.read("guides/index.md").content
        assert "- [One](/guides/one.md)" in guides
        assert "- [sub/](/guides/sub/index.md)" in guides
        assert "/two.md" not in guides  # deferred to guides/sub/index.md


class TestSeedLog:
    def test_empty_history_writes_empty_log(self, vault: Vault) -> None:
        # No git strategy in this vault → empty history.
        result = vault.writer.okf_seed_log()
        wait_for_writer_drain(vault)
        assert result.path == "log.md"
        assert result.commits == 0
        assert vault.reader.read("log.md").content == "# Log\n"

    def test_folder_scopes_write_path_only(self, vault: Vault) -> None:
        # folder chooses where log.md is written; its content is always the
        # whole bundle's history (a directory is not a valid git-history path).
        _write(vault, "guides/note.md", "# Note\n")
        result = vault.writer.okf_seed_log(folder="guides")
        wait_for_writer_drain(vault)
        assert result.path == "guides/log.md"
        assert vault.reader.read("guides/log.md") is not None

    def test_refuses_to_overwrite_existing_log(self, vault: Vault) -> None:
        _write(vault, "log.md", "# Log\n\n## 2026-01-01\n\n- **hand-written**\n")
        with pytest.raises(FileExistsError, match=r"log\.md"):
            vault.writer.okf_seed_log()
        # The hand-written log is untouched.
        assert "hand-written" in vault.reader.read("log.md").content


def test_facet_without_migration_manager_raises(vault: Vault) -> None:
    """WriterFacet built without a migration manager rejects okf_* calls."""
    from markdown_vault_mcp.facets.writer import WriterFacet

    facet = WriterFacet(vault._doc_mgr)
    with pytest.raises(RuntimeError, match="migration manager"):
        facet.okf_convert_links()
    with pytest.raises(RuntimeError, match="migration manager"):
        facet.okf_generate_index()
    with pytest.raises(RuntimeError, match="migration manager"):
        facet.okf_seed_log()
