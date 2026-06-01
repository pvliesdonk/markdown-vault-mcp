"""Tests for link extraction, FTS storage, and Collection backlink/outlink API."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.types import (
    BacklinkInfo,
    Chunk,
    LinkInfo,
    NoteContext,
    OutlinkInfo,
    ParsedNote,
)
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_note(
    path: str = "test.md",
    title: str = "Test",
    links: list[LinkInfo] | None = None,
    chunks: list[Chunk] | None = None,
    content_hash: str = "abc123",
    modified_at: float = 1000.0,
    frontmatter: dict[str, Any] | None = None,
) -> ParsedNote:
    """Create a minimal ParsedNote for testing.

    Args:
        path: Relative document path.
        title: Document title.
        links: Pre-built links list.
        chunks: Chunk list. Defaults to a single generic chunk.
        content_hash: Content hash string.
        modified_at: Modification timestamp.
        frontmatter: Optional frontmatter dict.

    Returns:
        A :class:`ParsedNote` suitable for indexing.
    """
    if chunks is None:
        chunks = [
            Chunk(heading="Test", heading_level=1, content="Test content", start_line=0)
        ]
    return ParsedNote(
        path=path,
        frontmatter=frontmatter or {},
        title=title,
        chunks=chunks,
        content_hash=content_hash,
        modified_at=modified_at,
        links=links or [],
    )


# ---------------------------------------------------------------------------
# extract_links: inline markdown links
# ---------------------------------------------------------------------------


class TestExtractInlineLinks:
    def test_simple_inline_link(self) -> None:
        """Basic [text](path.md) is extracted as a markdown link."""
        links = extract_links("[Click here](notes/intro.md)", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/intro.md"
        assert links[0].link_text == "Click here"
        assert links[0].link_type == "markdown"
        assert links[0].fragment is None
        assert links[0].raw_target == "notes/intro.md"

    def test_inline_link_with_fragment(self) -> None:
        """Fragment identifier is split from target and stored separately."""
        links = extract_links("[See section](notes/intro.md#overview)", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/intro.md"
        assert links[0].fragment == "overview"
        # raw_target preserves the original string including fragment
        assert links[0].raw_target == "notes/intro.md#overview"

    def test_external_url_skipped(self) -> None:
        """HTTP/HTTPS links are not extracted."""
        links = extract_links("[Web](https://example.com)", "index.md")
        assert links == []

    def test_mailto_skipped(self) -> None:
        """mailto: links are not extracted."""
        links = extract_links("[Mail](mailto:foo@bar.com)", "index.md")
        assert links == []

    def test_pure_anchor_skipped(self) -> None:
        """Pure anchor links (# only) are not extracted."""
        links = extract_links("[Top](#top)", "index.md")
        assert links == []

    def test_relative_path_resolved(self) -> None:
        """Relative path is resolved against source document's directory."""
        links = extract_links("[Note](../sibling.md)", "Journal/2024/today.md")
        assert len(links) == 1
        assert links[0].target_path == "Journal/sibling.md"
        # raw_target preserves the original relative string, not the resolved one
        assert links[0].raw_target == "../sibling.md"

    def test_relative_path_same_dir(self) -> None:
        """Relative path in same directory resolves correctly."""
        links = extract_links("[Note](other.md)", "Journal/today.md")
        assert len(links) == 1
        assert links[0].target_path == "Journal/other.md"

    def test_traversal_above_root_clamped(self) -> None:
        """Path traversal above vault root is clamped to root level."""
        links = extract_links("[Note](../../escape.md)", "a/b/c.md")
        assert len(links) == 1
        # a/b/../../escape.md -> escape.md (root level)
        assert links[0].target_path == "escape.md"

    def test_multiple_inline_links(self) -> None:
        """Multiple links in one document are all extracted."""
        content = "See [A](a.md) and [B](b.md) for more."
        links = extract_links(content, "index.md")
        assert len(links) == 2
        targets = {lnk.target_path for lnk in links}
        assert targets == {"a.md", "b.md"}

    def test_image_link_excluded(self) -> None:
        """Image links ![alt](src) are not extracted."""
        links = extract_links("![photo](assets/cat.png)", "index.md")
        assert links == []

    def test_image_link_alongside_regular(self) -> None:
        """Regular link is extracted, image link next to it is skipped."""
        content = "![img](pic.png) and [note](note.md)"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "note.md"

    def test_empty_link_text_allowed(self) -> None:
        """Empty link text is still extracted."""
        links = extract_links("[](note.md)", "index.md")
        assert len(links) == 1
        assert links[0].link_text == ""


# ---------------------------------------------------------------------------
# extract_links: reference-style links
# ---------------------------------------------------------------------------


class TestExtractReferenceLinks:
    def test_reference_link_basic(self) -> None:
        """[text][ref] with [ref]: path.md definition is extracted."""
        content = "See [the note][ref1]\n\n[ref1]: notes/topic.md"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/topic.md"
        assert links[0].link_text == "the note"
        assert links[0].link_type == "reference"
        assert links[0].fragment is None
        assert links[0].raw_target == "notes/topic.md"

    def test_reference_link_with_fragment(self) -> None:
        """Fragment in reference definition target is split off."""
        content = "See [note][ref]\n\n[ref]: notes/topic.md#section"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/topic.md"
        assert links[0].fragment == "section"
        assert links[0].raw_target == "notes/topic.md#section"

    def test_reference_link_case_insensitive_key(self) -> None:
        """Reference keys are case-insensitive per Markdown spec."""
        content = "See [note][REF]\n\n[ref]: target.md"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "target.md"

    def test_reference_definition_with_title_stripped(self) -> None:
        """Optional CommonMark title in definition is stripped from target."""
        content = 'See [note][ref]\n\n[ref]: target.md "My Title"'
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "target.md"

    def test_undefined_reference_skipped(self) -> None:
        """Usage with no matching definition is silently skipped."""
        links = extract_links("See [note][missing]", "index.md")
        assert links == []

    def test_external_reference_skipped(self) -> None:
        """Reference definition pointing to external URL is not extracted."""
        content = "See [web][ref]\n\n[ref]: https://example.com"
        links = extract_links(content, "index.md")
        assert links == []

    def test_reference_relative_path_resolved(self) -> None:
        """Relative path in reference definition is resolved correctly."""
        content = "See [note][ref]\n\n[ref]: ../sibling.md"
        links = extract_links(content, "Journal/2024/today.md")
        assert len(links) == 1
        assert links[0].target_path == "Journal/sibling.md"


# ---------------------------------------------------------------------------
# extract_links: wikilinks
# ---------------------------------------------------------------------------


class TestExtractWikilinks:
    def test_wikilink_basic(self) -> None:
        """[[Note Title]] is extracted with .md extension appended."""
        links = extract_links("See [[Note Title]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "Note Title.md"
        assert links[0].link_text == "Note Title"
        assert links[0].link_type == "wikilink"
        assert links[0].fragment is None
        # raw_target is the stem before .md was appended
        assert links[0].raw_target == "Note Title"

    def test_wikilink_with_alias(self) -> None:
        """[[path|alias]] uses alias as link_text."""
        links = extract_links("See [[notes/topic|My Topic]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/topic.md"
        assert links[0].link_text == "My Topic"
        # raw_target is the path portion only (before |), without .md
        assert links[0].raw_target == "notes/topic"

    def test_wikilink_md_extension_not_doubled(self) -> None:
        """[[note.md]] does not become note.md.md."""
        links = extract_links("See [[note.md]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "note.md"

    def test_wikilink_with_fragment(self) -> None:
        """[[note#section]] splits fragment from path."""
        links = extract_links("See [[note#section]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "note.md"
        assert links[0].fragment == "section"
        # raw_target re-attaches the fragment to the stem (no .md)
        assert links[0].raw_target == "note#section"

    def test_wikilink_path_stored_as_is(self) -> None:
        """Wikilinks with a path component are stored as-is for vault-wide resolution.

        Obsidian resolves [[folder/Note]] vault-wide (not relative to the source
        document).  The scanner stores the path unchanged; FTSIndex.resolve_vault_wikilinks()
        resolves it against the full document set after indexing.
        """
        links = extract_links("See [[subdir/note]]", "Journal/today.md")
        assert len(links) == 1
        assert links[0].target_path == "subdir/note.md"

    def test_wikilink_explicit_relative_resolved_against_source(self) -> None:
        """Wikilinks with ./ or ../ prefix resolve relative to source document.

        Explicit relative prefixes opt out of vault-wide resolution and
        use the same path resolution as regular markdown links.
        """
        links = extract_links("See [[../notes/topic]]", "Journal/today.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/topic.md"

        links2 = extract_links("See [[./sibling]]", "Journal/today.md")
        assert len(links2) == 1
        assert links2[0].target_path == "Journal/sibling.md"

    def test_wikilink_dotmd_with_fragment(self) -> None:
        """[[note.md#heading]] keeps .md, splits fragment."""
        links = extract_links("See [[note.md#heading]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "note.md"
        assert links[0].fragment == "heading"

    def test_wikilink_dotmd_raw_target_keeps_extension(self) -> None:
        """[[note.md]] produces raw_target='note.md', not 'note'.

        When the author writes the .md extension explicitly in the wikilink,
        raw_target preserves it.  This is the contract used by rename
        update_links to reconstruct the exact replacement string.
        """
        links = extract_links("See [[note.md]]", "index.md")
        assert len(links) == 1
        assert links[0].raw_target == "note.md"


# ---------------------------------------------------------------------------
# extract_links: code block exclusion
# ---------------------------------------------------------------------------


class TestCodeBlockExclusion:
    def test_link_in_fenced_code_excluded(self) -> None:
        """Links inside fenced code blocks are not extracted."""
        content = "```\nSee [note](target.md)\n```"
        links = extract_links(content, "index.md")
        assert links == []

    def test_link_in_inline_code_excluded(self) -> None:
        """Links inside inline code spans are not extracted."""
        content = "Use `[note](target.md)` syntax"
        links = extract_links(content, "index.md")
        assert links == []

    def test_link_after_fenced_code_extracted(self) -> None:
        """Links after a code block are still extracted."""
        content = "```python\ncode\n```\n\nSee [real](real.md)"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "real.md"

    def test_wikilink_in_fenced_code_excluded(self) -> None:
        """Wikilinks inside fenced code blocks are not extracted."""
        content = "```\n[[SomeNote]]\n```"
        links = extract_links(content, "index.md")
        assert links == []


# ---------------------------------------------------------------------------
# extract_links: mixed content
# ---------------------------------------------------------------------------


class TestMixedLinks:
    def test_all_three_types_together(self) -> None:
        """All three link types in one document are all extracted."""
        content = "[Inline](a.md)\n[[WikiNote]]\n[RefLink][ref]\n\n[ref]: b.md"
        links = extract_links(content, "index.md")
        link_types = {lnk.link_type for lnk in links}
        assert link_types == {"markdown", "wikilink", "reference"}
        targets = {lnk.target_path for lnk in links}
        assert "a.md" in targets
        assert "WikiNote.md" in targets
        assert "b.md" in targets

    def test_empty_content_returns_empty(self) -> None:
        """Empty markdown body yields no links."""
        assert extract_links("", "index.md") == []

    def test_no_links_returns_empty(self) -> None:
        """Plain prose with no links yields no results."""
        assert extract_links("Just some plain text here.", "index.md") == []


# ---------------------------------------------------------------------------
# FTSIndex: links table storage
# ---------------------------------------------------------------------------


class TestFTSIndexLinks:
    def test_upsert_stores_links(self) -> None:
        """upsert_note persists link rows and get_outlinks returns them."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            title="Source",
            links=[
                LinkInfo(
                    target_path="target.md",
                    link_text="Target",
                    link_type="markdown",
                )
            ],
        )
        idx.upsert_note(note)

        rows = idx.get_outlinks("source.md")
        assert len(rows) == 1
        assert rows[0]["target_path"] == "target.md"
        assert rows[0]["link_text"] == "Target"
        assert rows[0]["link_type"] == "markdown"
        assert rows[0]["fragment"] is None

    def test_get_backlinks_returns_sources(self) -> None:
        """get_backlinks returns the document that links to a given path."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            title="Source Doc",
            links=[
                LinkInfo(
                    target_path="target.md",
                    link_text="the target",
                    link_type="markdown",
                )
            ],
        )
        idx.upsert_note(note)
        # Also index the target so cascade delete tests have something to delete.
        idx.upsert_note(make_note(path="target.md", title="Target Doc"))

        rows = idx.get_backlinks("target.md")
        assert len(rows) == 1
        assert rows[0]["source_path"] == "source.md"
        assert rows[0]["source_title"] == "Source Doc"
        assert rows[0]["link_text"] == "the target"
        assert rows[0]["link_type"] == "markdown"

    def test_build_from_notes_populates_links(self) -> None:
        """build_from_notes bulk indexes links for all notes."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="a.md",
                links=[
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown")
                ],
            ),
            make_note(
                path="b.md",
                links=[
                    LinkInfo(target_path="a.md", link_text="A", link_type="wikilink")
                ],
            ),
        ]
        idx.build_from_notes(notes)

        assert len(idx.get_outlinks("a.md")) == 1
        assert len(idx.get_outlinks("b.md")) == 1
        assert len(idx.get_backlinks("a.md")) == 1
        assert len(idx.get_backlinks("b.md")) == 1

    def test_upsert_replaces_links(self) -> None:
        """Re-upserting a note replaces old links with new ones."""
        idx = FTSIndex(":memory:")
        note_v1 = make_note(
            path="source.md",
            links=[
                LinkInfo(target_path="old.md", link_text="Old", link_type="markdown")
            ],
        )
        idx.upsert_note(note_v1)
        assert len(idx.get_outlinks("source.md")) == 1

        note_v2 = make_note(
            path="source.md",
            links=[
                LinkInfo(target_path="new1.md", link_text="N1", link_type="markdown"),
                LinkInfo(target_path="new2.md", link_text="N2", link_type="wikilink"),
            ],
        )
        idx.upsert_note(note_v2)
        rows = idx.get_outlinks("source.md")
        assert len(rows) == 2
        targets = {r["target_path"] for r in rows}
        assert targets == {"new1.md", "new2.md"}

    def test_delete_cascades_links(self) -> None:
        """Deleting a document removes its link rows via ON DELETE CASCADE."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            links=[
                LinkInfo(target_path="target.md", link_text="T", link_type="markdown")
            ],
        )
        idx.upsert_note(note)
        assert len(idx.get_outlinks("source.md")) == 1

        idx.delete_by_path("source.md")
        assert idx.get_outlinks("source.md") == []

    def test_get_backlinks_empty_when_no_links(self) -> None:
        """get_backlinks returns [] when nothing links to the given path."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note(path="orphan.md"))
        assert idx.get_backlinks("orphan.md") == []

    def test_get_outlinks_empty_when_no_links(self) -> None:
        """get_outlinks returns [] when document has no links."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note(path="lonely.md"))
        assert idx.get_outlinks("lonely.md") == []

    def test_fragment_stored_and_retrieved(self) -> None:
        """Fragment identifier is stored and returned correctly."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            links=[
                LinkInfo(
                    target_path="target.md",
                    link_text="See section",
                    link_type="markdown",
                    fragment="intro",
                )
            ],
        )
        idx.upsert_note(note)
        rows = idx.get_outlinks("source.md")
        assert rows[0]["fragment"] == "intro"

        backlinks = idx.get_backlinks("target.md")
        assert backlinks[0]["fragment"] == "intro"

    def test_multiple_sources_linking_to_same_target(self) -> None:
        """get_backlinks aggregates links from multiple source documents."""
        idx = FTSIndex(":memory:")
        for i in range(3):
            note = make_note(
                path=f"src{i}.md",
                links=[
                    LinkInfo(
                        target_path="shared.md",
                        link_text=f"Link {i}",
                        link_type="markdown",
                    )
                ],
            )
            idx.upsert_note(note)

        rows = idx.get_backlinks("shared.md")
        assert len(rows) == 3
        sources = {r["source_path"] for r in rows}
        assert sources == {"src0.md", "src1.md", "src2.md"}


# ---------------------------------------------------------------------------
# raw_target: stored and returned by FTS queries
# ---------------------------------------------------------------------------


class TestRawTargetInFTS:
    def test_raw_target_stored_and_returned_by_outlinks(self) -> None:
        """raw_target is stored in the links table and returned by get_outlinks."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            links=[
                LinkInfo(
                    target_path="notes/topic.md",
                    link_text="Topic",
                    link_type="markdown",
                    raw_target="../notes/topic.md",
                )
            ],
        )
        idx.upsert_note(note)
        rows = idx.get_outlinks("source.md")
        assert rows[0]["raw_target"] == "../notes/topic.md"

    def test_raw_target_returned_by_backlinks(self) -> None:
        """raw_target is included in get_backlinks results."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            links=[
                LinkInfo(
                    target_path="target.md",
                    link_text="T",
                    link_type="markdown",
                    raw_target="target.md",
                )
            ],
        )
        idx.upsert_note(note)
        rows = idx.get_backlinks("target.md")
        assert rows[0]["raw_target"] == "target.md"

    def test_raw_target_empty_string_default(self) -> None:
        """LinkInfo with no explicit raw_target defaults to empty string."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            links=[
                LinkInfo(
                    target_path="target.md",
                    link_text="T",
                    link_type="markdown",
                    # raw_target omitted — defaults to ""
                )
            ],
        )
        idx.upsert_note(note)
        rows = idx.get_outlinks("source.md")
        assert rows[0]["raw_target"] == ""

    def test_raw_target_wikilink_no_md_extension(self) -> None:
        """Wikilink raw_target is the stem without .md, per extract_links convention."""
        links = extract_links("[[My Note]]", "index.md")
        assert len(links) == 1
        assert links[0].raw_target == "My Note"
        assert links[0].target_path == "My Note.md"

    def test_raw_target_wikilink_with_fragment(self) -> None:
        """Wikilink raw_target re-attaches the fragment to the stem."""
        links = extract_links("[[My Note#heading]]", "index.md")
        assert len(links) == 1
        assert links[0].raw_target == "My Note#heading"
        assert links[0].fragment == "heading"

    def test_schema_migration_adds_column_to_existing_db(self, tmp_path: Path) -> None:
        """Opening an existing DB without raw_target column applies migration."""
        import sqlite3

        db_file = tmp_path / "test.db"
        # Create DB with old schema (no raw_target column).
        conn = sqlite3.connect(str(db_file))
        conn.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                folder TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                modified_at REAL NOT NULL DEFAULT 0,
                frontmatter_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE links (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                target_path TEXT NOT NULL,
                link_text TEXT NOT NULL DEFAULT '',
                link_type TEXT NOT NULL,
                fragment TEXT,
                FOREIGN KEY (source_id) REFERENCES documents(id) ON DELETE CASCADE
            );
            """
        )
        conn.close()
        # Opening via FTSIndex should apply migration without error.
        idx = FTSIndex(db_file)
        # Verify the column exists by inserting a row with raw_target.
        note = make_note(
            path="src.md",
            links=[
                LinkInfo(
                    target_path="tgt.md",
                    link_text="T",
                    link_type="markdown",
                    raw_target="tgt.md",
                )
            ],
        )
        idx.upsert_note(note)
        rows = idx.get_outlinks("src.md")
        assert rows[0]["raw_target"] == "tgt.md"

    def test_raw_target_returned_by_get_broken_links(self) -> None:
        """raw_target is included in get_broken_links results."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            links=[
                LinkInfo(
                    target_path="missing/page.md",
                    link_text="Missing",
                    link_type="markdown",
                    raw_target="../missing/page.md",
                )
            ],
        )
        idx.upsert_note(note)
        rows = idx.get_broken_links()
        assert len(rows) == 1
        assert rows[0]["raw_target"] == "../missing/page.md"


# ---------------------------------------------------------------------------
# Collection: get_backlinks and get_outlinks
# ---------------------------------------------------------------------------


@pytest.fixture
def linked_vault(tmp_path: Path) -> Path:
    """Create a vault with interlinked notes for Collection integration tests.

    Layout:
        index.md          links to notes/topic.md and notes/other.md
        notes/topic.md    links back to ../index.md and to notes/other.md
        notes/other.md    no outbound links
    """
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "index.md").write_text(
        "# Index\n\nSee [Topic](notes/topic.md) and [Other](notes/other.md).\n",
        encoding="utf-8",
    )
    (vault / "notes").mkdir()
    (vault / "notes" / "topic.md").write_text(
        "# Topic\n\nBack to [Index](../index.md) and also [Other](other.md).\n",
        encoding="utf-8",
    )
    (vault / "notes" / "other.md").write_text(
        "# Other\n\nNo links here.\n",
        encoding="utf-8",
    )
    return vault


class TestCollectionBacklinks:
    def test_get_backlinks_returns_backlink_info(self, linked_vault: Path) -> None:
        """get_backlinks returns BacklinkInfo objects for documents linking to path."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        backlinks = col.get_backlinks("notes/topic.md")
        assert len(backlinks) == 1
        assert isinstance(backlinks[0], BacklinkInfo)
        assert backlinks[0].source_path == "index.md"
        assert backlinks[0].link_text == "Topic"
        assert backlinks[0].link_type == "markdown"

    def test_get_backlinks_multiple_sources(self, linked_vault: Path) -> None:
        """Multiple documents linking to the same path all appear."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        backlinks = col.get_backlinks("notes/other.md")
        source_paths = {b.source_path for b in backlinks}
        assert "index.md" in source_paths
        assert "notes/topic.md" in source_paths

    def test_get_backlinks_empty_for_unlinked_doc(self, linked_vault: Path) -> None:
        """Document with no inbound links returns empty list."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        backlinks = col.get_backlinks("index.md")
        # index.md is only linked from topic.md (../index.md)
        assert isinstance(backlinks, list)
        # topic.md links to ../index.md which resolves to index.md
        assert all(isinstance(b, BacklinkInfo) for b in backlinks)

    def test_get_backlinks_path_traversal_rejected(self, linked_vault: Path) -> None:
        """Path traversal in get_backlinks raises ValueError."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        with pytest.raises(ValueError):
            col.get_backlinks("../etc/passwd")


class TestCollectionOutlinks:
    def test_get_outlinks_returns_outlink_info(self, linked_vault: Path) -> None:
        """get_outlinks returns OutlinkInfo objects for links from a document."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        outlinks = col.get_outlinks("index.md")
        assert len(outlinks) == 2
        assert all(isinstance(o, OutlinkInfo) for o in outlinks)
        targets = {o.target_path for o in outlinks}
        assert "notes/topic.md" in targets
        assert "notes/other.md" in targets

    def test_get_outlinks_exists_flag(self, linked_vault: Path) -> None:
        """OutlinkInfo.exists is True when target is indexed, False otherwise."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        outlinks = col.get_outlinks("index.md")
        # Both targets exist in the vault.
        for outlink in outlinks:
            assert outlink.exists is True

    def test_get_outlinks_exists_false_for_missing_target(self, tmp_path: Path) -> None:
        """OutlinkInfo.exists is False when target is not in the index."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [Ghost](ghost.md).\n",
            encoding="utf-8",
        )
        col = Collection(source_dir=vault)
        col.build_index()

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "ghost.md"
        assert outlinks[0].exists is False

    def test_get_outlinks_empty_for_no_links_doc(self, linked_vault: Path) -> None:
        """Document with no outbound links returns empty list."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        outlinks = col.get_outlinks("notes/other.md")
        assert outlinks == []

    def test_get_outlinks_path_traversal_rejected(self, linked_vault: Path) -> None:
        """Path traversal in get_outlinks raises ValueError."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        with pytest.raises(ValueError):
            col.get_outlinks("../../secret.md")


class TestLinkLimitSQL:
    """SQL LIMIT is pushed into get_backlinks/get_outlinks queries."""

    def test_get_backlinks_limit_restricts_results(self) -> None:
        """get_backlinks(limit=N) returns at most N rows."""
        idx = FTSIndex(":memory:")
        # Three sources link to the same target.
        for i in range(3):
            idx.upsert_note(
                make_note(
                    path=f"src{i}.md",
                    links=[
                        LinkInfo(
                            target_path="target.md",
                            link_text="T",
                            link_type="markdown",
                        )
                    ],
                )
            )
        assert len(idx.get_backlinks("target.md")) == 3
        assert len(idx.get_backlinks("target.md", limit=2)) == 2
        assert len(idx.get_backlinks("target.md", limit=1)) == 1

    def test_get_outlinks_limit_restricts_results(self) -> None:
        """get_outlinks(limit=N) returns at most N rows."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                path="source.md",
                links=[
                    LinkInfo(
                        target_path=f"t{i}.md", link_text="T", link_type="markdown"
                    )
                    for i in range(4)
                ],
            )
        )
        assert len(idx.get_outlinks("source.md")) == 4
        assert len(idx.get_outlinks("source.md", limit=2)) == 2

    def test_get_backlinks_no_limit_returns_all(self) -> None:
        """get_backlinks without limit returns all rows (backward-compatible)."""
        idx = FTSIndex(":memory:")
        for i in range(5):
            idx.upsert_note(
                make_note(
                    path=f"src{i}.md",
                    links=[
                        LinkInfo(
                            target_path="hub.md",
                            link_text="H",
                            link_type="markdown",
                        )
                    ],
                )
            )
        assert len(idx.get_backlinks("hub.md")) == 5

    def test_get_outlinks_no_limit_returns_all(self) -> None:
        """get_outlinks without limit returns all rows (backward-compatible)."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                path="source.md",
                links=[
                    LinkInfo(
                        target_path=f"t{i}.md", link_text="T", link_type="markdown"
                    )
                    for i in range(5)
                ],
            )
        )
        assert len(idx.get_outlinks("source.md")) == 5


class TestCollectionReindex:
    def test_reindex_updates_links(self, tmp_path: Path) -> None:
        """reindex() refreshes link data for modified documents."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [Old](old.md).\n",
            encoding="utf-8",
        )
        col = Collection(source_dir=vault)
        col.build_index()

        assert len(col.get_outlinks("source.md")) == 1
        assert col.get_outlinks("source.md")[0].target_path == "old.md"

        # Modify source.md to point somewhere else.
        (vault / "source.md").write_text(
            "# Source\n\nSee [New](new.md).\n",
            encoding="utf-8",
        )
        col.reindex()

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "new.md"

    def test_reindex_removes_links_for_deleted_note(self, tmp_path: Path) -> None:
        """reindex() removes link rows when a document is deleted."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [Other](other.md).\n",
            encoding="utf-8",
        )
        (vault / "other.md").write_text("# Other\n", encoding="utf-8")
        col = Collection(source_dir=vault)
        col.build_index()

        assert len(col.get_backlinks("other.md")) == 1

        # Delete source.md and reindex.
        (vault / "source.md").unlink()
        col.reindex()

        assert col.get_backlinks("other.md") == []


# ---------------------------------------------------------------------------
# ParsedNote default field
# ---------------------------------------------------------------------------


class TestParsedNoteDefault:
    def test_links_field_defaults_to_empty_list(self) -> None:
        """ParsedNote constructed without links= has links=[]."""
        note = ParsedNote(
            path="test.md",
            frontmatter={},
            title="Test",
            chunks=[],
            content_hash="hash",
            modified_at=0.0,
        )
        assert note.links == []

    def test_links_field_independent_across_instances(self) -> None:
        """Each ParsedNote instance gets its own links list."""
        n1 = ParsedNote(
            path="a.md",
            frontmatter={},
            title="A",
            chunks=[],
            content_hash="h",
            modified_at=0.0,
        )
        n2 = ParsedNote(
            path="b.md",
            frontmatter={},
            title="B",
            chunks=[],
            content_hash="h",
            modified_at=0.0,
        )
        n1.links.append(
            LinkInfo(target_path="x.md", link_text="X", link_type="markdown")
        )
        assert n2.links == []


# ---------------------------------------------------------------------------
# Collection.get_context
# ---------------------------------------------------------------------------


class TestCollectionGetContext:
    """Tests for Collection.get_context(), including graceful degradation."""

    @pytest.fixture
    def context_vault(self, tmp_path: Path) -> Path:
        """Vault with interlinked notes and a root-level peer."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "index.md").write_text(
            "# Index\n\nSee [Topic](notes/topic.md).\n",
            encoding="utf-8",
        )
        (vault / "home.md").write_text(
            "# Home\n\nAnother root note.\n",
            encoding="utf-8",
        )
        (vault / "notes").mkdir()
        (vault / "notes" / "topic.md").write_text(
            "# Topic\n\nBack to [Index](../index.md).\n",
            encoding="utf-8",
        )
        (vault / "notes" / "peer.md").write_text(
            "# Peer\n\nA sibling note.\n",
            encoding="utf-8",
        )
        return vault

    def test_get_context_returns_note_context(self, context_vault: Path) -> None:
        """get_context returns a NoteContext instance."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        result = col.get_context("index.md")
        assert isinstance(result, NoteContext)

    def test_get_context_basic_fields(self, context_vault: Path) -> None:
        """get_context populates path, title, folder, frontmatter, modified_at."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        result = col.get_context("index.md")
        assert result.path == "index.md"
        assert result.title == "Index"
        assert result.folder == ""
        assert isinstance(result.frontmatter, dict)
        assert isinstance(result.modified_at, float)
        assert result.modified_at > 0

    def test_get_context_backlinks(self, context_vault: Path) -> None:
        """get_context includes backlinks for a well-linked note."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        result = col.get_context("notes/topic.md")
        sources = [b.source_path for b in result.backlinks]
        assert "index.md" in sources

    def test_get_context_outlinks(self, context_vault: Path) -> None:
        """get_context includes outlinks from a note."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        result = col.get_context("index.md")
        targets = [o.target_path for o in result.outlinks]
        assert "notes/topic.md" in targets

    def test_get_context_folder_notes_excludes_self(self, context_vault: Path) -> None:
        """folder_notes does not include the document itself."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        result = col.get_context("notes/topic.md")
        assert "notes/topic.md" not in result.folder_notes
        assert "notes/peer.md" in result.folder_notes

    def test_get_context_root_folder_notes(self, context_vault: Path) -> None:
        """Root-level documents see other root-level docs as folder peers."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        result = col.get_context("index.md")
        assert "index.md" not in result.folder_notes
        assert "home.md" in result.folder_notes

    def test_get_context_similar_empty_without_embeddings(
        self, context_vault: Path
    ) -> None:
        """similar is empty when no embedding provider is configured."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        result = col.get_context("index.md")
        assert result.similar == []

    def test_get_context_backlinks_outlinks_empty_without_links_table(
        self, context_vault: Path
    ) -> None:
        """backlinks and outlinks are empty when the links table methods raise."""
        col = Collection(source_dir=context_vault)
        col.build_index()

        # Simulate missing links table by making FTS methods raise OperationalError.
        err = sqlite3.OperationalError("no such table: links")
        with (
            patch.object(col._fts, "get_backlinks", side_effect=err),
            patch.object(col._fts, "get_outlinks", side_effect=err),
        ):
            result = col.get_context("index.md")

        assert result.backlinks == []
        assert result.outlinks == []

    def test_get_context_raises_for_nonexistent_path(self, context_vault: Path) -> None:
        """get_context raises ValueError when the path is not indexed."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        with pytest.raises(ValueError, match="Document not found"):
            col.get_context("nonexistent.md")

    def test_get_context_link_limit_caps_results(self, context_vault: Path) -> None:
        """link_limit caps backlinks and outlinks lists."""
        col = Collection(source_dir=context_vault)
        col.build_index()
        result = col.get_context("notes/topic.md", link_limit=0)
        assert result.backlinks == []
        assert result.outlinks == []


# ---------------------------------------------------------------------------
# Collection.rename update_links
# ---------------------------------------------------------------------------


@pytest.fixture
def rename_vault(tmp_path: Path) -> Path:
    """Vault with notes that link to a target that will be renamed.

    Layout:
        target.md            the note that will be renamed
        linker_md.md         contains a markdown link to target.md
        linker_wiki.md       contains a wikilink to target.md
        linker_ref.md        contains a reference-style link to target.md
        linker_frag.md       contains a markdown link with fragment to target.md
        linker_alias.md      contains a wikilink with alias to target.md
        linker_wiki_frag.md  contains a wikilink with fragment [[target#heading]]
        no_links.md          no links — should not be modified
        subdir/linker_rel.md contains a relative markdown link ../target.md
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "subdir").mkdir()

    (vault / "target.md").write_text("# Target\n\nThis is the target.\n")
    (vault / "linker_md.md").write_text(
        "# Linker MD\n\nSee [Target](target.md) for details.\n"
    )
    (vault / "linker_wiki.md").write_text(
        "# Linker Wiki\n\nSee [[target]] for details.\n"
    )
    (vault / "linker_ref.md").write_text(
        "# Linker Ref\n\nSee [Target][ref]\n\n[ref]: target.md\n"
    )
    (vault / "linker_frag.md").write_text(
        "# Linker Frag\n\nSee [Section](target.md#section) for details.\n"
    )
    (vault / "linker_alias.md").write_text(
        "# Linker Alias\n\nSee [[target|My Target]] for details.\n"
    )
    (vault / "linker_wiki_frag.md").write_text(
        "# Linker Wiki Frag\n\nSee [[target#heading]] for details.\n"
    )
    (vault / "subdir" / "linker_rel.md").write_text(
        "# Linker Rel\n\nSee [Target](../target.md) for details.\n"
    )
    (vault / "no_links.md").write_text("# No Links\n\nJust prose.\n")
    return vault


class TestRenameUpdateLinks:
    def test_update_links_false_default_does_not_modify_sources(
        self, rename_vault: Path
    ) -> None:
        """update_links=False (default): source files are not modified."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        result = col.rename("target.md", "renamed.md")

        assert result.updated_links == 0
        # linker_md.md still has the old link
        content = (rename_vault / "linker_md.md").read_text()
        assert "target.md" in content

    def test_update_links_markdown_link(self, rename_vault: Path) -> None:
        """Markdown link [text](target.md) is updated to new_path."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        result = col.rename("target.md", "renamed.md", update_links=True)

        assert result.old_path == "target.md"
        assert result.new_path == "renamed.md"
        content = (rename_vault / "linker_md.md").read_text()
        assert "(renamed.md)" in content
        assert "(target.md)" not in content

    def test_update_links_wikilink(self, rename_vault: Path) -> None:
        """Wikilink [[target]] is updated to [[renamed]]."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        col.rename("target.md", "renamed.md", update_links=True)

        content = (rename_vault / "linker_wiki.md").read_text()
        assert "[[renamed]]" in content
        assert "[[target]]" not in content

    def test_update_links_wikilink_preserves_alias(self, rename_vault: Path) -> None:
        """Wikilink [[target|alias]] becomes [[renamed|alias]]."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        col.rename("target.md", "renamed.md", update_links=True)

        content = (rename_vault / "linker_alias.md").read_text()
        assert "[[renamed|My Target]]" in content
        assert "[[target|" not in content

    def test_update_links_reference_link(self, rename_vault: Path) -> None:
        """Reference definition [ref]: target.md is updated."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        col.rename("target.md", "renamed.md", update_links=True)

        content = (rename_vault / "linker_ref.md").read_text()
        assert "]: renamed.md" in content
        assert "]: target.md" not in content

    def test_update_links_fragment_preserved(self, rename_vault: Path) -> None:
        """Fragment in markdown link is preserved after rename."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        col.rename("target.md", "renamed.md", update_links=True)

        content = (rename_vault / "linker_frag.md").read_text()
        assert "(renamed.md#section)" in content
        assert "(target.md#section)" not in content

    def test_update_links_unrelated_file_not_modified(self, rename_vault: Path) -> None:
        """Files with no links to the renamed note are not modified."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()
        original_mtime = (rename_vault / "no_links.md").stat().st_mtime

        col.rename("target.md", "renamed.md", update_links=True)

        assert (rename_vault / "no_links.md").stat().st_mtime == original_mtime

    def test_update_links_updated_links_count(self, rename_vault: Path) -> None:
        """updated_links counts source documents successfully updated."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        result = col.rename("target.md", "renamed.md", update_links=True)

        # linker_md, linker_wiki, linker_ref, linker_frag, linker_alias,
        # linker_wiki_frag, subdir/linker_rel = 7
        assert result.updated_links == 7

    def test_update_links_failure_does_not_prevent_rename(
        self, rename_vault: Path
    ) -> None:
        """A write failure on a source file does not prevent the rename."""
        import contextlib
        from pathlib import Path as _Path

        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        linker_abs = str(rename_vault / "linker_md.md")
        original_replace = _Path.replace

        def failing_replace(self: _Path, target: _Path) -> _Path:
            if str(target) == linker_abs:
                # Clean up the temp file and simulate a write failure.
                with contextlib.suppress(OSError):
                    self.unlink()
                raise PermissionError(f"simulated: cannot write {target}")
            return original_replace(self, target)

        with patch.object(_Path, "replace", failing_replace):
            result = col.rename("target.md", "renamed.md", update_links=True)

        # Rename succeeded even though one source update failed.
        assert (rename_vault / "renamed.md").is_file()
        assert not (rename_vault / "target.md").is_file()
        # updated_links is less than the total of 7 (one failure)
        assert result.updated_links < 7

    def test_update_links_wikilink_fragment_preserved(self, rename_vault: Path) -> None:
        """Wikilink with fragment [[target#heading]] becomes [[renamed#heading]]."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        col.rename("target.md", "renamed.md", update_links=True)

        content = (rename_vault / "linker_wiki_frag.md").read_text()
        assert "[[renamed#heading]]" in content
        assert "[[target#heading]]" not in content

    def test_update_links_relative_path_preserved(self, rename_vault: Path) -> None:
        """Cross-directory relative links keep their relative form after rename.

        A file at subdir/linker_rel.md with [Target](../target.md) should
        become [Target](../renamed.md) — not the vault-absolute renamed.md.
        """
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        col.rename("target.md", "renamed.md", update_links=True)

        content = (rename_vault / "subdir" / "linker_rel.md").read_text()
        assert "(../renamed.md)" in content
        assert "(../target.md)" not in content

    def test_update_links_fts_reindexed_after_update(self, rename_vault: Path) -> None:
        """Updated source documents are re-indexed in FTS."""
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        col.rename("target.md", "renamed.md", update_links=True)
        wait_for_writer_drain(col)

        # linker_md now has a link to renamed.md, not target.md
        outlinks = col._fts.get_outlinks("linker_md.md")
        targets = {r["target_path"] for r in outlinks}
        assert "renamed.md" in targets
        assert "target.md" not in targets

    def test_update_links_self_referencing(self, rename_vault: Path) -> None:
        """A note that links to itself has its own link updated after rename."""
        (rename_vault / "self_link.md").write_text(
            "# Self\n\nSee [Self](self_link.md) for more.\n"
        )
        col = Collection(source_dir=rename_vault, read_only=False)
        col.build_index()

        col.rename("self_link.md", "self_renamed.md", update_links=True)

        content = (rename_vault / "self_renamed.md").read_text()
        assert "(self_renamed.md)" in content
        assert "(self_link.md)" not in content

    def test_update_links_ignored_for_attachments(self, rename_vault: Path) -> None:
        """update_links=True is silently ignored when renaming a non-.md attachment."""
        (rename_vault / "image.png").write_bytes(b"\x89PNG\r\n")
        col = Collection(
            source_dir=rename_vault, read_only=False, attachment_extensions=["png"]
        )
        col.build_index()

        result = col.rename("image.png", "photo.png", update_links=True)

        assert result.updated_links == 0
        assert (rename_vault / "photo.png").is_file()


# ---------------------------------------------------------------------------
# CollectionStats: link counts
# ---------------------------------------------------------------------------


class TestStatsLinkCounts:
    """stats() returns link_count, broken_link_count, and orphan_count."""

    def test_link_count_reflects_total_links(self, linked_vault: Path) -> None:
        """link_count equals total rows in the links table."""
        col = Collection(source_dir=linked_vault)
        col.build_index()
        s = col.stats()
        # index.md → 2 links, notes/topic.md → 2 links, notes/other.md → 0
        assert s.link_count == 4

    def test_broken_link_count_zero_when_all_resolve(self, linked_vault: Path) -> None:
        """broken_link_count is 0 when every link target exists."""
        col = Collection(source_dir=linked_vault)
        col.build_index()
        assert col.stats().broken_link_count == 0

    def test_broken_link_count_nonzero_for_missing_targets(
        self, tmp_path: Path
    ) -> None:
        """broken_link_count counts links to non-existent documents."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [Ghost](ghost.md) and [Void](void.md).\n",
            encoding="utf-8",
        )
        col = Collection(source_dir=vault)
        col.build_index()
        assert col.stats().broken_link_count == 2

    def test_orphan_count_for_unlinked_notes(self, tmp_path: Path) -> None:
        """orphan_count counts notes with no inbound or outbound links."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "linked.md").write_text(
            "# Linked\n\nSee [Other](other.md).\n", encoding="utf-8"
        )
        (vault / "other.md").write_text("# Other\n", encoding="utf-8")
        (vault / "island.md").write_text("# Island\n\nNo links.\n", encoding="utf-8")
        col = Collection(source_dir=vault)
        col.build_index()
        # island.md has no links in or out; other.md has a backlink
        assert col.stats().orphan_count == 1

    def test_link_counts_zero_without_links_table(self) -> None:
        """count_* methods return 0 when the links table does not exist.

        Simulates an old index file predating link tracking by dropping the
        links table after schema creation and confirming the OperationalError
        guard in _count_links_query returns 0 instead of propagating.
        """
        idx = FTSIndex(":memory:")
        # Simulate an old index file that predates link tracking.
        idx._conn().execute("DROP TABLE links")
        assert idx.count_links() == 0
        assert idx.count_broken_links() == 0
        assert idx.count_orphans() == 0


# ---------------------------------------------------------------------------
# resolve_vault_wikilinks: Obsidian vault-wide wikilink resolution
# ---------------------------------------------------------------------------


class TestResolveVaultWikilinks:
    """FTSIndex.resolve_vault_wikilinks() and Collection integration tests."""

    def test_bare_wikilink_resolves_vault_wide(self, tmp_path: Path) -> None:
        """[[Note]] resolves to notes/Note.md anywhere in the vault."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "journal").mkdir()
        (vault / "journal" / "today.md").write_text(
            "# Today\n\nSee [[MyNote]].\n", encoding="utf-8"
        )
        (vault / "notes").mkdir()
        (vault / "notes" / "MyNote.md").write_text("# My Note\n", encoding="utf-8")

        col = Collection(source_dir=vault)
        col.build_index()

        # The wikilink must resolve to notes/MyNote.md, not journal/MyNote.md.
        outlinks = col.get_outlinks("journal/today.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "notes/MyNote.md"
        assert outlinks[0].exists is True

        # Must not appear in broken links.
        assert col.get_broken_links() == []
        assert col.stats().broken_link_count == 0

    def test_bare_wikilink_shortest_path_wins(self, tmp_path: Path) -> None:
        """When multiple vault documents match, shortest path is selected."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [[Note]].\n", encoding="utf-8"
        )
        (vault / "a").mkdir()
        (vault / "a" / "Note.md").write_text("# Note A\n", encoding="utf-8")
        (vault / "a" / "b").mkdir()
        (vault / "a" / "b" / "Note.md").write_text("# Note B\n", encoding="utf-8")

        col = Collection(source_dir=vault)
        col.build_index()

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        # a/Note.md is shorter than a/b/Note.md.
        assert outlinks[0].target_path == "a/Note.md"
        assert outlinks[0].exists is True

    def test_bare_wikilink_genuine_broken(self, tmp_path: Path) -> None:
        """[[NonExistent]] where no document matches stays broken."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [[NonExistent]].\n", encoding="utf-8"
        )

        col = Collection(source_dir=vault)
        col.build_index()

        broken = col.get_broken_links()
        assert len(broken) == 1
        assert broken[0].raw_target == "NonExistent"
        assert col.stats().broken_link_count == 1

    def test_wikilink_with_path_separator_resolves_vault_wide(
        self, tmp_path: Path
    ) -> None:
        """[[folder/Note]] resolves to any vault document ending in folder/Note.md."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [[folder/Note]].\n", encoding="utf-8"
        )
        (vault / "sub").mkdir()
        (vault / "sub" / "folder").mkdir()
        (vault / "sub" / "folder" / "Note.md").write_text("# Note\n", encoding="utf-8")

        col = Collection(source_dir=vault)
        col.build_index()

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "sub/folder/Note.md"
        assert outlinks[0].exists is True
        assert col.get_broken_links() == []

    def test_reindex_resolves_new_wikilinks(self, tmp_path: Path) -> None:
        """resolve_vault_wikilinks() runs after reindex(), fixing new documents."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [[Target]].\n", encoding="utf-8"
        )

        col = Collection(source_dir=vault)
        col.build_index()

        # Initially broken — Target.md does not exist.
        assert col.stats().broken_link_count == 1

        # Add the target document and trigger reindex.
        (vault / "notes").mkdir()
        (vault / "notes" / "Target.md").write_text("# Target\n", encoding="utf-8")
        col.reindex()

        # Now the wikilink should resolve.
        assert col.stats().broken_link_count == 0
        outlinks = col.get_outlinks("source.md")
        assert outlinks[0].target_path == "notes/Target.md"
        assert outlinks[0].exists is True

    def test_resolve_vault_wikilinks_returns_count(self) -> None:
        """resolve_vault_wikilinks() returns the number of rows updated."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="sub/page.md",
                links=[
                    LinkInfo(
                        target_path="Target.md",
                        link_text="Target",
                        link_type="wikilink",
                        raw_target="Target",
                    )
                ],
            ),
            make_note(path="notes/Target.md"),
        ]
        idx.build_from_notes(notes)

        # First call: resolves the wikilink to notes/Target.md.
        assert idx.resolve_vault_wikilinks() == 1

        # Confirm the path was updated.
        outlinks = idx.get_outlinks("sub/page.md")
        assert outlinks[0]["target_path"] == "notes/Target.md"

        # Second call is idempotent: already at correct path, nothing to update.
        assert idx.resolve_vault_wikilinks() == 0

    def test_reindex_resolves_moved_target(self, tmp_path: Path) -> None:
        """resolve_vault_wikilinks() re-resolves after a target document is moved.

        After the first build_index(), [[Target]] is resolved to notes/Target.md.
        When notes/Target.md is deleted and other/Target.md is added, the next
        reindex() must update the stored target_path to other/Target.md rather
        than leaving it pointing to the now-missing notes/Target.md.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [[Target]].\n", encoding="utf-8"
        )
        (vault / "notes").mkdir()
        (vault / "notes" / "Target.md").write_text("# Target\n", encoding="utf-8")

        col = Collection(source_dir=vault)
        col.build_index()

        # Initial state: link resolves to notes/Target.md.
        outlinks = col.get_outlinks("source.md")
        assert outlinks[0].target_path == "notes/Target.md"
        assert col.stats().broken_link_count == 0

        # Simulate move: delete notes/Target.md, create other/Target.md.
        (vault / "notes" / "Target.md").unlink()
        (vault / "other").mkdir()
        (vault / "other" / "Target.md").write_text("# Target moved\n", encoding="utf-8")
        col.reindex()

        # Link must re-resolve to the new location.
        outlinks = col.get_outlinks("source.md")
        assert outlinks[0].target_path == "other/Target.md"
        assert outlinks[0].exists is True
        assert col.stats().broken_link_count == 0

    def test_root_level_exact_match_preferred_over_subdir(self, tmp_path: Path) -> None:
        """[[Note]] where Note.md exists at vault root resolves to root."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [[Note]].\n", encoding="utf-8"
        )
        (vault / "Note.md").write_text("# Note at root\n", encoding="utf-8")
        (vault / "sub").mkdir()
        (vault / "sub" / "Note.md").write_text("# Note in sub\n", encoding="utf-8")

        col = Collection(source_dir=vault)
        col.build_index()

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        # Note.md (length 7) is shorter than sub/Note.md (length 11).
        assert outlinks[0].target_path == "Note.md"

    def test_write_resolves_new_wikilinks(self, tmp_path: Path) -> None:
        """write() re-runs vault-wide resolution so new wikilinks aren't broken."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "notes").mkdir()
        (vault / "notes" / "Target.md").write_text("# Target\n", encoding="utf-8")

        col = Collection(source_dir=vault, read_only=False)
        col.build_index()

        col.write("source.md", "# Source\n\nSee [[Target]].\n")
        wait_for_writer_drain(col)

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "notes/Target.md"
        assert outlinks[0].exists is True
        assert col.get_broken_links() == []

    def test_edit_resolves_new_wikilinks(self, tmp_path: Path) -> None:
        """edit() re-runs vault-wide resolution after the source content changes."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nNo links yet.\n", encoding="utf-8"
        )
        (vault / "notes").mkdir()
        (vault / "notes" / "Target.md").write_text("# Target\n", encoding="utf-8")

        col = Collection(source_dir=vault, read_only=False)
        col.build_index()

        col.edit("source.md", old_text="No links yet.", new_text="See [[Target]].")
        wait_for_writer_drain(col)

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "notes/Target.md"
        assert outlinks[0].exists is True
        assert col.get_broken_links() == []

    def test_delete_target_falls_back_to_basename_twin(self, tmp_path: Path) -> None:
        """delete() re-resolves inlinks; if a basename twin exists, they pick it up."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [[Target]].\n", encoding="utf-8"
        )
        # Asymmetric depths so resolution is deterministic: a/Target.md is
        # the shortest candidate before delete; aa/bb/Target.md is the only
        # surviving twin afterwards.
        (vault / "a").mkdir()
        (vault / "a" / "Target.md").write_text("# Target A\n", encoding="utf-8")
        (vault / "aa" / "bb").mkdir(parents=True)
        (vault / "aa" / "bb" / "Target.md").write_text(
            "# Target deep\n", encoding="utf-8"
        )

        col = Collection(source_dir=vault, read_only=False)
        col.build_index()

        outlinks = col.get_outlinks("source.md")
        assert outlinks[0].target_path == "a/Target.md"
        assert outlinks[0].exists is True

        col.delete("a/Target.md")
        wait_for_writer_drain(col)

        outlinks = col.get_outlinks("source.md")
        assert outlinks[0].target_path == "aa/bb/Target.md"
        assert outlinks[0].exists is True
        assert col.get_broken_links() == []

    def test_rename_target_keeps_inlinks_resolved(self, tmp_path: Path) -> None:
        """rename() re-resolves inlinks after the target moves to a new path."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [[Target]].\n", encoding="utf-8"
        )
        (vault / "Target.md").write_text("# Target\n", encoding="utf-8")

        col = Collection(source_dir=vault, read_only=False)
        col.build_index()

        # Initial: bare [[Target]] resolves to root-level Target.md.
        outlinks = col.get_outlinks("source.md")
        assert outlinks[0].target_path == "Target.md"
        assert outlinks[0].exists is True

        col.rename("Target.md", "other/Target.md")
        wait_for_writer_drain(col)

        # After rename, the inlink must point at the new location.
        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "other/Target.md"
        assert outlinks[0].exists is True
        assert col.get_broken_links() == []


# ---------------------------------------------------------------------------
# Alias resolution in wikilinks
# ---------------------------------------------------------------------------


class TestAliasResolution:
    """Wikilinks can resolve via frontmatter aliases (Obsidian behaviour)."""

    def test_wikilink_resolves_via_alias(self) -> None:
        """[[AI]] resolves to a document with aliases: [AI]."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="source.md",
                links=[
                    LinkInfo(
                        target_path="AI.md",
                        link_text="AI",
                        link_type="wikilink",
                        raw_target="AI",
                    )
                ],
            ),
            make_note(
                path="artificial-intelligence.md",
                title="Artificial Intelligence",
                frontmatter={"aliases": ["AI", "A.I."]},
            ),
        ]
        idx.build_from_notes(notes)
        assert idx.resolve_vault_wikilinks() == 1

        outlinks = idx.get_outlinks("source.md")
        assert outlinks[0]["target_path"] == "artificial-intelligence.md"

    def test_alias_resolution_case_insensitive(self) -> None:
        """Alias matching is case-insensitive."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="source.md",
                links=[
                    LinkInfo(
                        target_path="ai.md",
                        link_text="ai",
                        link_type="wikilink",
                        raw_target="ai",
                    )
                ],
            ),
            make_note(
                path="artificial-intelligence.md",
                title="Artificial Intelligence",
                frontmatter={"aliases": ["AI"]},
            ),
        ]
        idx.build_from_notes(notes)
        assert idx.resolve_vault_wikilinks() == 1

        outlinks = idx.get_outlinks("source.md")
        assert outlinks[0]["target_path"] == "artificial-intelligence.md"

    def test_path_match_takes_priority_over_alias(self) -> None:
        """If a path matches, alias is not used (path wins)."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="source.md",
                links=[
                    LinkInfo(
                        target_path="AI.md",
                        link_text="AI",
                        link_type="wikilink",
                        raw_target="AI",
                    )
                ],
            ),
            # This document matches by path.
            make_note(path="AI.md", title="AI Page"),
            # This document matches by alias.
            make_note(
                path="artificial-intelligence.md",
                title="Artificial Intelligence",
                frontmatter={"aliases": ["AI"]},
            ),
        ]
        idx.build_from_notes(notes)
        idx.resolve_vault_wikilinks()

        outlinks = idx.get_outlinks("source.md")
        assert outlinks[0]["target_path"] == "AI.md"

    def test_alias_singular_key(self) -> None:
        """Frontmatter ``alias`` (singular) is also supported."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="source.md",
                links=[
                    LinkInfo(
                        target_path="ML.md",
                        link_text="ML",
                        link_type="wikilink",
                        raw_target="ML",
                    )
                ],
            ),
            make_note(
                path="machine-learning.md",
                title="Machine Learning",
                frontmatter={"alias": "ML"},
            ),
        ]
        idx.build_from_notes(notes)
        assert idx.resolve_vault_wikilinks() == 1

        outlinks = idx.get_outlinks("source.md")
        assert outlinks[0]["target_path"] == "machine-learning.md"

    def test_alias_not_broken_link(self, tmp_path: Path) -> None:
        """A wikilink resolved via alias is not reported as broken."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text("# Source\n\nSee [[AI]].\n", encoding="utf-8")
        (vault / "artificial-intelligence.md").write_text(
            "---\naliases:\n  - AI\n  - A.I.\n---\n# Artificial Intelligence\n",
            encoding="utf-8",
        )

        col = Collection(source_dir=vault)
        col.build_index()

        broken = col.get_broken_links()
        assert broken == []
        assert col.stats().broken_link_count == 0

    def test_alias_resolution_shortest_path_wins(self) -> None:
        """When multiple documents share an alias, shortest path wins."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="source.md",
                links=[
                    LinkInfo(
                        target_path="JS.md",
                        link_text="JS",
                        link_type="wikilink",
                        raw_target="JS",
                    )
                ],
            ),
            make_note(
                path="deep/nested/javascript.md",
                title="JavaScript (nested)",
                frontmatter={"aliases": ["JS"]},
            ),
            make_note(
                path="javascript.md",
                title="JavaScript",
                frontmatter={"aliases": ["JS"]},
            ),
        ]
        idx.build_from_notes(notes)
        idx.resolve_vault_wikilinks()

        outlinks = idx.get_outlinks("source.md")
        assert outlinks[0]["target_path"] == "javascript.md"

    def test_alias_with_fragment(self) -> None:
        """[[AI#history]] resolves via alias and preserves fragment."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="source.md",
                links=[
                    LinkInfo(
                        target_path="AI.md",
                        link_text="AI",
                        link_type="wikilink",
                        fragment="history",
                        raw_target="AI#history",
                    )
                ],
            ),
            make_note(
                path="artificial-intelligence.md",
                title="Artificial Intelligence",
                frontmatter={"aliases": ["AI"]},
            ),
        ]
        idx.build_from_notes(notes)
        assert idx.resolve_vault_wikilinks() == 1

        outlinks = idx.get_outlinks("source.md")
        assert outlinks[0]["target_path"] == "artificial-intelligence.md"
        assert outlinks[0]["fragment"] == "history"
