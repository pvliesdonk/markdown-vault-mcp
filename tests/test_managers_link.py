"""Tests for LinkManager in isolation (no Vault dependency)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.link import LinkManager
from markdown_vault_mcp.scanner import scan_directory
from markdown_vault_mcp.types import (
    BacklinkInfo,
    BrokenLinkInfo,
    MostLinkedNote,
    NoteInfo,
    OutlinkInfo,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def linked_vault(tmp_path: Path) -> Path:
    """Create a small vault with interlinked notes.

    Graph:
        alpha.md -> beta.md  (markdown link)
        alpha.md -> gamma.md (wikilink)
        beta.md  -> alpha.md (markdown link)
        gamma.md -> (no outlinks)
        orphan.md -> (no outlinks, no inlinks)
        alpha.md -> missing.md (broken link)
    """
    alpha = tmp_path / "alpha.md"
    alpha.write_text(
        "---\ntitle: Alpha\n---\n"
        "# Alpha\n\n"
        "Link to [beta](beta.md) and [[gamma]].\n"
        "Also a broken link to [missing](missing.md).\n",
        encoding="utf-8",
    )
    beta = tmp_path / "beta.md"
    beta.write_text(
        "---\ntitle: Beta\n---\n# Beta\n\nBack to [alpha](alpha.md).\n",
        encoding="utf-8",
    )
    gamma = tmp_path / "gamma.md"
    gamma.write_text(
        "---\ntitle: Gamma\n---\n# Gamma\n\nNo outgoing links here.\n",
        encoding="utf-8",
    )
    orphan = tmp_path / "orphan.md"
    orphan.write_text(
        "---\ntitle: Orphan\n---\n# Orphan\n\nCompletely disconnected.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def link_mgr(linked_vault: Path) -> LinkManager:
    """Build a LinkManager from a scanned vault."""
    fts = FTSIndex(db_path=":memory:")
    for note in scan_directory(linked_vault):
        fts.upsert_note(note)
    fts.resolve_vault_wikilinks()
    return LinkManager(fts=fts, source_dir=linked_vault)


# ---------------------------------------------------------------------------
# get_backlinks
# ---------------------------------------------------------------------------


class TestGetBacklinks:
    def test_returns_backlink_info(self, link_mgr: LinkManager) -> None:
        """get_backlinks returns BacklinkInfo objects."""
        results = link_mgr.get_backlinks("beta.md")
        assert len(results) >= 1
        assert all(isinstance(r, BacklinkInfo) for r in results)
        sources = [r.source_path for r in results]
        assert "alpha.md" in sources

    def test_finds_correct_sources(self, link_mgr: LinkManager) -> None:
        """alpha.md has a backlink from beta.md."""
        results = link_mgr.get_backlinks("alpha.md")
        sources = [r.source_path for r in results]
        assert "beta.md" in sources

    def test_nonexistent_raises_value_error(self, link_mgr: LinkManager) -> None:
        """Querying backlinks for a nonexistent note raises ValueError."""
        with pytest.raises(ValueError, match="Document not found"):
            link_mgr.get_backlinks("nonexistent.md")

    def test_path_traversal_rejected(self, link_mgr: LinkManager) -> None:
        """Path traversal attempt raises ValueError."""
        with pytest.raises(ValueError, match="Path traversal detected"):
            link_mgr.get_backlinks("../../etc/passwd.md")

    def test_non_md_rejected(self, link_mgr: LinkManager) -> None:
        """Non-.md path raises ValueError."""
        with pytest.raises(ValueError, match=r"Path must end with '\.md'"):
            link_mgr.get_backlinks("notes.txt")

    def test_limit_parameter(self, link_mgr: LinkManager) -> None:
        """limit parameter caps the number of results."""
        # gamma has backlinks from alpha; use limit=1
        all_results = link_mgr.get_backlinks("gamma.md")
        limited = link_mgr.get_backlinks("gamma.md", limit=1)
        assert len(limited) <= 1
        assert len(limited) <= len(all_results)


# ---------------------------------------------------------------------------
# get_outlinks
# ---------------------------------------------------------------------------


class TestGetOutlinks:
    def test_returns_outlink_info(self, link_mgr: LinkManager) -> None:
        """get_outlinks returns OutlinkInfo objects."""
        results = link_mgr.get_outlinks("alpha.md")
        assert len(results) >= 2
        assert all(isinstance(r, OutlinkInfo) for r in results)

    def test_finds_correct_targets(self, link_mgr: LinkManager) -> None:
        """alpha.md links to beta.md and gamma.md."""
        results = link_mgr.get_outlinks("alpha.md")
        targets = [r.target_path for r in results]
        assert "beta.md" in targets
        assert "gamma.md" in targets

    def test_exists_flag_correct(self, link_mgr: LinkManager) -> None:
        """Existing targets have exists=True, missing have exists=False."""
        results = link_mgr.get_outlinks("alpha.md")
        by_target = {r.target_path: r for r in results}
        assert by_target["beta.md"].exists is True
        assert by_target["missing.md"].exists is False

    def test_nonexistent_raises_value_error(self, link_mgr: LinkManager) -> None:
        """Querying outlinks for a nonexistent note raises ValueError."""
        with pytest.raises(ValueError, match="Document not found"):
            link_mgr.get_outlinks("nonexistent.md")

    def test_limit_parameter(self, link_mgr: LinkManager) -> None:
        """limit parameter caps the number of results."""
        limited = link_mgr.get_outlinks("alpha.md", limit=1)
        assert len(limited) == 1


# ---------------------------------------------------------------------------
# get_broken_links
# ---------------------------------------------------------------------------


class TestGetBrokenLinks:
    def test_returns_broken_link_info(self, link_mgr: LinkManager) -> None:
        """get_broken_links returns BrokenLinkInfo objects."""
        results = link_mgr.get_broken_links()
        assert len(results) >= 1
        assert all(isinstance(r, BrokenLinkInfo) for r in results)

    def test_finds_broken_links(self, link_mgr: LinkManager) -> None:
        """The link from alpha.md to missing.md is broken."""
        results = link_mgr.get_broken_links()
        targets = [r.target_path for r in results]
        assert "missing.md" in targets

    def test_folder_filter(self, link_mgr: LinkManager) -> None:
        """Folder filter restricts results (empty folder yields no results)."""
        results = link_mgr.get_broken_links(folder="nonexistent_folder")
        assert results == []


# ---------------------------------------------------------------------------
# get_orphan_notes
# ---------------------------------------------------------------------------


class TestGetOrphanNotes:
    def test_returns_note_info(self, link_mgr: LinkManager) -> None:
        """get_orphan_notes returns NoteInfo objects."""
        results = link_mgr.get_orphan_notes()
        assert all(isinstance(r, NoteInfo) for r in results)

    def test_finds_orphans(self, link_mgr: LinkManager) -> None:
        """orphan.md has no links in or out, so it is an orphan."""
        results = link_mgr.get_orphan_notes()
        paths = [r.path for r in results]
        assert "orphan.md" in paths

    def test_connected_notes_not_orphans(self, link_mgr: LinkManager) -> None:
        """alpha.md, beta.md, gamma.md are all connected — not orphans."""
        results = link_mgr.get_orphan_notes()
        paths = [r.path for r in results]
        assert "alpha.md" not in paths
        assert "beta.md" not in paths
        assert "gamma.md" not in paths


# ---------------------------------------------------------------------------
# get_most_linked
# ---------------------------------------------------------------------------


class TestGetMostLinked:
    def test_returns_most_linked_note(self, link_mgr: LinkManager) -> None:
        """get_most_linked returns MostLinkedNote objects."""
        results = link_mgr.get_most_linked()
        assert all(isinstance(r, MostLinkedNote) for r in results)

    def test_ordering(self, link_mgr: LinkManager) -> None:
        """Results are ordered by backlink count descending."""
        results = link_mgr.get_most_linked()
        if len(results) >= 2:
            assert results[0].backlink_count >= results[1].backlink_count

    def test_limit_parameter(self, link_mgr: LinkManager) -> None:
        """limit parameter caps the number of results."""
        results = link_mgr.get_most_linked(limit=1)
        assert len(results) <= 1


# ---------------------------------------------------------------------------
# get_connection_path
# ---------------------------------------------------------------------------


class TestGetConnectionPath:
    def test_direct_connection(self, link_mgr: LinkManager) -> None:
        """alpha -> beta is a direct connection."""
        path = link_mgr.get_connection_path("alpha.md", "beta.md")
        assert path is not None
        assert path[0] == "alpha.md"
        assert path[-1] == "beta.md"

    def test_indirect_connection(self, link_mgr: LinkManager) -> None:
        """beta -> gamma goes through alpha (beta->alpha->gamma)."""
        path = link_mgr.get_connection_path("beta.md", "gamma.md")
        assert path is not None
        assert path[0] == "beta.md"
        assert path[-1] == "gamma.md"
        assert len(path) >= 2

    def test_no_connection_returns_none(self, link_mgr: LinkManager) -> None:
        """orphan.md has no connections to anyone."""
        path = link_mgr.get_connection_path("orphan.md", "alpha.md")
        assert path is None

    def test_path_traversal_rejected(self, link_mgr: LinkManager) -> None:
        """Path traversal in source raises ValueError."""
        with pytest.raises(ValueError, match="Path traversal detected"):
            link_mgr.get_connection_path("../../etc/passwd.md", "alpha.md")

    def test_path_traversal_rejected_target(self, link_mgr: LinkManager) -> None:
        """Path traversal in target raises ValueError."""
        with pytest.raises(ValueError, match="Path traversal detected"):
            link_mgr.get_connection_path("alpha.md", "../../etc/passwd.md")

    def test_non_md_rejected(self, link_mgr: LinkManager) -> None:
        """Non-.md path raises ValueError."""
        with pytest.raises(ValueError, match=r"Path must end with '\.md'"):
            link_mgr.get_connection_path("alpha.txt", "beta.md")
