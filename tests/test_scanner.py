"""Tests for markdown_vault_mcp.scanner module."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
    extract_section,
    list_section_headings,
    normalize_heading,
    parse_note,
    scan_directory,
)
from markdown_vault_mcp.types import Chunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_folder(path: str) -> str:
    """Derive the folder string from a relative document path.

    Mirrors the logic in fts_index._derive_folder: parent directory of the
    path, with "." replaced by "" for root-level documents.
    """
    parent = Path(path).parent.as_posix()
    return "" if parent == "." else parent


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_scan_directory_discovers_all_md_files(fixtures_path: Path) -> None:
    """scan_directory yields all .md files except invalid_utf8 and malformed_yaml.

    invalid_utf8.md is skipped due to UnicodeDecodeError.
    malformed_yaml.md is skipped due to YAML parse error.
    Both are handled gracefully without aborting the scan.
    """
    notes = list(
        scan_directory(
            fixtures_path,
        )
    )
    paths = {n.path for n in notes}

    expected = {
        "simple.md",
        "full_frontmatter.md",
        "minimal_frontmatter.md",
        "no_frontmatter.md",
        "deep_headings.md",
        "unicode.md",
        "empty.md",
        "subfolder/nested.md",
        "subfolder/deep/doc.md",
    }
    assert paths == expected


@pytest.mark.skipif(
    sys.version_info < (3, 13),
    reason="recurse_symlinks kwarg added in 3.13",
)
def test_scan_directory_follows_symlinked_subdirs(tmp_path: Path) -> None:
    # Python 3.13 changed Path.glob to skip symlinked subdirs by default; this
    # would silently zero-index symlink-farm vault layouts (issue #508).
    real = tmp_path / "real"
    real.mkdir()
    (real / "linked.md").write_text("# Linked\nBody.\n")

    vault = tmp_path / "vault"
    vault.mkdir()
    try:
        (vault / "via_symlink").symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not supported here: {exc}")

    paths = {n.path for n in scan_directory(vault)}
    assert "via_symlink/linked.md" in paths


# ---------------------------------------------------------------------------
# Frontmatter and title resolution
# ---------------------------------------------------------------------------


def test_parse_note_with_frontmatter(fixtures_path: Path) -> None:
    """parse_note reads frontmatter fields and derives title from them."""
    note = parse_note(fixtures_path / "full_frontmatter.md", fixtures_path)

    assert note.title == "Full Frontmatter Note"
    assert note.frontmatter["cluster"] == "fiction"
    assert note.frontmatter["author"] == "Test Author"
    assert "horror" in note.frontmatter["topics"]
    assert "gothic" in note.frontmatter["topics"]
    assert "dark" in note.frontmatter["tags"]
    # Must produce at least one chunk.
    assert len(note.chunks) >= 1


def test_parse_note_without_frontmatter(fixtures_path: Path) -> None:
    """parse_note returns an empty frontmatter dict when no YAML block exists."""
    note = parse_note(fixtures_path / "no_frontmatter.md", fixtures_path)

    assert note.frontmatter == {}


def test_parse_note_minimal_frontmatter(fixtures_path: Path) -> None:
    """parse_note correctly parses a frontmatter block with only a title field."""
    note = parse_note(fixtures_path / "minimal_frontmatter.md", fixtures_path)

    assert note.title == "Minimal Note"
    assert list(note.frontmatter.keys()) == ["title"]


def test_title_from_h1(fixtures_path: Path) -> None:
    """When there is no frontmatter title, the first H1 heading becomes the title."""
    note = parse_note(fixtures_path / "simple.md", fixtures_path)

    # simple.md has no frontmatter; its first line is "# Simple Document".
    assert note.title == "Simple Document"
    assert note.frontmatter == {}


def test_title_from_filename(fixtures_path: Path) -> None:
    """When there is no frontmatter title and no H1, the filename stem is the title."""
    note = parse_note(fixtures_path / "no_frontmatter.md", fixtures_path)

    # no_frontmatter.md has no frontmatter and no H1 heading.
    assert note.title == "no_frontmatter"


# ---------------------------------------------------------------------------
# HeadingChunker
# ---------------------------------------------------------------------------


def test_heading_chunker_splits_on_h1_h2(fixtures_path: Path) -> None:
    """HeadingChunker splits simple.md into two chunks at H1 and H2 boundaries.

    short_doc_lines=0 disables the short-document bypass so the 7-line fixture
    is actually split.
    """
    chunker = HeadingChunker(short_doc_lines=0)
    note = parse_note(fixtures_path / "simple.md", fixtures_path, chunker)

    assert len(note.chunks) == 2

    h1_chunk = note.chunks[0]
    assert h1_chunk.heading == "Simple Document"
    assert h1_chunk.heading_level == 1

    h2_chunk = note.chunks[1]
    assert h2_chunk.heading == "Section Two"
    assert h2_chunk.heading_level == 2


def test_heading_chunker_deep_headings(fixtures_path: Path) -> None:
    """HeadingChunker only splits on H1/H2; H3 and H4 do not create new chunks.

    deep_headings.md has H1, H2, H3, H4 headings.  Only the H1 and H2 form
    chunk boundaries, so the result must have exactly 2 chunks.
    """
    chunker = HeadingChunker(short_doc_lines=0)
    note = parse_note(fixtures_path / "deep_headings.md", fixtures_path, chunker)

    assert len(note.chunks) == 2

    levels = [c.heading_level for c in note.chunks]
    assert levels == [1, 2]

    # H3/H4 content must appear inside the H2 chunk, not as a separate chunk.
    h2_chunk = note.chunks[1]
    assert "### Heading 3" in h2_chunk.content
    assert "#### Heading 4" in h2_chunk.content


def test_heading_chunker_short_doc_bypass(fixtures_path: Path) -> None:
    """HeadingChunker returns a single chunk for documents under short_doc_lines."""
    # simple.md has 7 lines, well under the default 30-line threshold.
    chunker = HeadingChunker()  # default short_doc_lines=30
    note = parse_note(fixtures_path / "simple.md", fixtures_path, chunker)

    assert len(note.chunks) == 1
    assert note.chunks[0].heading is None
    assert note.chunks[0].heading_level == 0


# ---------------------------------------------------------------------------
# WholeDocumentChunker
# ---------------------------------------------------------------------------


def test_whole_document_chunker(fixtures_path: Path) -> None:
    """WholeDocumentChunker always returns exactly one chunk for any document."""
    chunker = WholeDocumentChunker()
    note = parse_note(fixtures_path / "full_frontmatter.md", fixtures_path, chunker)

    assert len(note.chunks) == 1
    chunk = note.chunks[0]
    assert chunk.heading is None
    assert chunk.heading_level == 0
    # The single chunk must include the body content.
    assert "Full Frontmatter Note" in chunk.content


# ---------------------------------------------------------------------------
# Required-frontmatter filtering
# ---------------------------------------------------------------------------


def test_required_frontmatter_filters(fixtures_path: Path) -> None:
    """scan_directory excludes documents missing required frontmatter fields.

    Requiring both 'title' and 'cluster' should match only full_frontmatter.md,
    since it is the only fixture that contains a 'cluster' field.
    """
    notes = list(
        scan_directory(
            fixtures_path,
            required_frontmatter=["title", "cluster"],
        )
    )

    assert len(notes) == 1
    assert notes[0].path == "full_frontmatter.md"


# ---------------------------------------------------------------------------
# UTF-8 fault tolerance
# ---------------------------------------------------------------------------


def test_utf8_fault_tolerance(fixtures_path: Path) -> None:
    """scan_directory skips files that cannot be decoded as UTF-8.

    invalid_utf8.md contains raw non-UTF-8 bytes.  The scan must complete
    without raising, and that file must be absent from the results.
    """
    notes = list(
        scan_directory(
            fixtures_path,
        )
    )
    paths = {n.path for n in notes}
    assert "invalid_utf8.md" not in paths
    assert "malformed_yaml.md" not in paths


def test_parse_note_invalid_utf8_raises(fixtures_path: Path) -> None:
    """parse_note propagates UnicodeDecodeError for non-UTF-8 files."""
    with pytest.raises(UnicodeDecodeError):
        parse_note(fixtures_path / "invalid_utf8.md", fixtures_path)


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


def test_exclude_patterns(fixtures_path: Path) -> None:
    """scan_directory excludes files whose relative path matches exclude_patterns.

    Using patterns 'subfolder/*' and 'subfolder/**/*' excludes both direct
    children and deeper descendants of subfolder/.
    """
    notes = list(
        scan_directory(
            fixtures_path,
            exclude_patterns=[
                "subfolder/*",
                "subfolder/**/*",
            ],
        )
    )
    paths = {n.path for n in notes}

    assert "subfolder/nested.md" not in paths
    assert "subfolder/deep/doc.md" not in paths
    # Root-level documents are unaffected.
    assert "simple.md" in paths


# ---------------------------------------------------------------------------
# Folder derivation
# ---------------------------------------------------------------------------


def test_folder_derivation(fixtures_path: Path) -> None:
    """Folder is derived as the parent directory of the relative path.

    Root-level documents have folder '', nested documents have their parent
    directory as folder (e.g. 'subfolder').
    """
    note_root = parse_note(fixtures_path / "simple.md", fixtures_path)
    note_sub = parse_note(fixtures_path / "subfolder/nested.md", fixtures_path)

    assert _derive_folder(note_root.path) == ""
    assert _derive_folder(note_sub.path) == "subfolder"


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


def test_content_hash_is_sha256(fixtures_path: Path) -> None:
    """content_hash is the SHA-256 hex digest of the raw file bytes (64 chars)."""
    target = fixtures_path / "simple.md"
    note = parse_note(target, fixtures_path)

    expected = hashlib.sha256(target.read_bytes()).hexdigest()

    assert len(note.content_hash) == 64
    assert note.content_hash == expected


# ---------------------------------------------------------------------------
# Unicode content
# ---------------------------------------------------------------------------


def test_unicode_content(fixtures_path: Path) -> None:
    """parse_note preserves Unicode content (Japanese, accented, emoji)."""
    note = parse_note(fixtures_path / "unicode.md", fixtures_path)

    # Title comes from frontmatter.
    assert note.title == "ユニコード文書"

    full_content = " ".join(c.content for c in note.chunks)
    assert "日本語テスト" in full_content
    assert "Ñoño" in full_content
    assert "🎉" in full_content


# ---------------------------------------------------------------------------
# Chunk start lines
# ---------------------------------------------------------------------------


def test_chunk_start_lines(fixtures_path: Path) -> None:
    """Each chunk's start_line is a non-negative integer.

    When HeadingChunker splits a document the second chunk must start after
    the first heading line (start_line > 0).
    """
    chunker = HeadingChunker(short_doc_lines=0)
    note = parse_note(fixtures_path / "simple.md", fixtures_path, chunker)

    for chunk in note.chunks:
        assert isinstance(chunk.start_line, int)
        assert chunk.start_line >= 0

    # The H1 chunk starts at line 0 (the heading itself).
    assert note.chunks[0].start_line == 0
    # The H2 chunk must start at a later line.
    assert note.chunks[1].start_line > 0


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_chunk_strategy_protocol() -> None:
    """HeadingChunker and WholeDocumentChunker satisfy the ChunkStrategy Protocol."""
    heading_chunker = HeadingChunker()
    whole_chunker = WholeDocumentChunker()

    assert isinstance(heading_chunker, ChunkStrategy)
    assert isinstance(whole_chunker, ChunkStrategy)


def test_chunk_strategy_protocol_custom() -> None:
    """A custom class with a matching chunk() signature satisfies ChunkStrategy."""

    class MyChunker:
        def chunk(self, content: str, _metadata: dict[str, Any]) -> list[Chunk]:
            return [Chunk(heading=None, heading_level=0, content=content, start_line=0)]

    assert isinstance(MyChunker(), ChunkStrategy)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_file(fixtures_path: Path) -> None:
    """parse_note handles a 0-byte file without raising."""
    note = parse_note(fixtures_path / "empty.md", fixtures_path)

    # Title falls back to filename stem.
    assert note.title == "empty"
    assert note.frontmatter == {}
    assert len(note.chunks) == 1


def test_malformed_yaml_raises(fixtures_path: Path) -> None:
    """parse_note propagates ParserError for malformed YAML frontmatter."""
    import yaml

    with pytest.raises(yaml.YAMLError):
        parse_note(fixtures_path / "malformed_yaml.md", fixtures_path)


def test_relative_path_uses_forward_slashes(fixtures_path: Path) -> None:
    """ParsedNote.path always uses forward slashes regardless of OS."""
    note = parse_note(fixtures_path / "subfolder" / "deep" / "doc.md", fixtures_path)

    assert note.path == "subfolder/deep/doc.md"
    assert "\\" not in note.path


def test_parse_note_path_relative_to_source_dir(fixtures_path: Path) -> None:
    """ParsedNote.path is relative to source_dir, not absolute."""
    note = parse_note(fixtures_path / "subfolder/nested.md", fixtures_path)

    assert not Path(note.path).is_absolute()
    assert note.path == "subfolder/nested.md"


# ---------------------------------------------------------------------------
# HeadingChunker preamble (content before first heading)
# ---------------------------------------------------------------------------


class TestHeadingChunkerPreamble:
    def test_preamble_before_first_heading_is_its_own_chunk(
        self, tmp_path: Path
    ) -> None:
        """Text before the first H1/H2 becomes a preamble chunk with heading=None."""
        # Build a document long enough to avoid the short-doc bypass (>30 lines).
        intro_lines = ["Introductory text — no heading yet.\n"] * 5
        padding = [f"More intro line {i}.\n" for i in range(30)]
        heading_section = [
            "# First Section\n",
            "Section body content.\n",
            "More section content.\n",
        ]
        content = "".join(intro_lines + padding + heading_section)

        doc = tmp_path / "with_preamble.md"
        doc.write_text(content, encoding="utf-8")

        chunker = HeadingChunker(short_doc_lines=0)
        note = parse_note(doc, tmp_path, chunker)

        # There must be at least two chunks: a preamble and the first section.
        assert len(note.chunks) >= 2

        preamble = note.chunks[0]
        assert preamble.heading is None
        assert preamble.heading_level == 0
        assert "Introductory text" in preamble.content

        section = note.chunks[1]
        assert section.heading == "First Section"
        assert section.heading_level == 1

    def test_no_preamble_when_heading_is_first_line(self, tmp_path: Path) -> None:
        """When the document starts immediately with a heading, no preamble chunk."""
        # 35+ lines starting directly with a heading.
        lines = ["# Opening Heading\n"]
        lines += [f"body line {i}\n" for i in range(35)]
        content = "".join(lines)

        doc = tmp_path / "heading_first.md"
        doc.write_text(content, encoding="utf-8")

        chunker = HeadingChunker(short_doc_lines=0)
        note = parse_note(doc, tmp_path, chunker)

        # All chunks must have a non-None heading (no preamble).
        assert all(c.heading is not None for c in note.chunks)


# ---------------------------------------------------------------------------
# HeadingChunker empty section body skipped
# ---------------------------------------------------------------------------


class TestHeadingChunkerEmptySection:
    def test_empty_body_section_not_in_chunks(self, tmp_path: Path) -> None:
        """A heading immediately followed by the next heading (no body) is skipped."""
        # Three headings; the first has no body content.
        lines = ["# Ghost Heading\n"]  # line 0 — empty body
        lines += ["## Real Section\n"]  # line 1
        lines += [f"Real content line {i}.\n" for i in range(35)]

        content = "".join(lines)
        doc = tmp_path / "empty_section.md"
        doc.write_text(content, encoding="utf-8")

        chunker = HeadingChunker(short_doc_lines=0)
        note = parse_note(doc, tmp_path, chunker)

        headings = [c.heading for c in note.chunks]
        # "Ghost Heading" had no body — it must not appear as a chunk.
        assert "Ghost Heading" not in headings
        # "Real Section" has body content — it must appear.
        assert "Real Section" in headings

    def test_multiple_consecutive_empty_sections_skipped(self, tmp_path: Path) -> None:
        """Multiple consecutive heading-only sections are all skipped."""
        lines = [
            "# Empty A\n",
            "# Empty B\n",
            "# Empty C\n",
            "## Content Section\n",
        ]
        lines += [f"content {i}\n" for i in range(35)]

        content = "".join(lines)
        doc = tmp_path / "multi_empty.md"
        doc.write_text(content, encoding="utf-8")

        chunker = HeadingChunker(short_doc_lines=0)
        note = parse_note(doc, tmp_path, chunker)

        headings = [c.heading for c in note.chunks]
        assert "Empty A" not in headings
        assert "Empty B" not in headings
        assert "Empty C" not in headings
        assert "Content Section" in headings


# ---------------------------------------------------------------------------
# HeadingChunker char-budget cap (#649)
# ---------------------------------------------------------------------------


def test_char_cap_splits_token_dense_chunk_under_word_cap() -> None:
    """A chunk under the word cap but over the char cap is split."""
    dense = " ".join("x" * 40 for _ in range(300))  # 300 words, ~12k chars
    doc = f"# Title\n\n{dense}\n"
    chunks = HeadingChunker(max_chunk_words=400, max_chunk_chars=8000).chunk(doc, {})
    assert len(chunks) >= 2
    assert all(len(c.content) <= 8000 for c in chunks)


def test_char_cap_none_preserves_word_only_behaviour() -> None:
    """No char cap means a chunk under the word cap stays a single chunk."""
    dense = " ".join("x" * 40 for _ in range(300))
    doc = f"# Title\n\n{dense}\n"
    chunks = HeadingChunker(max_chunk_words=400, max_chunk_chars=None).chunk(doc, {})
    assert len(chunks) == 1


def test_normal_prose_unaffected_by_generous_char_cap() -> None:
    """A generous char cap does not split normal prose under it."""
    body = "\n\n".join("word " * 50 for _ in range(3))
    doc = f"# Title\n\n{body}\n"
    assert (
        len(HeadingChunker(max_chunk_words=400, max_chunk_chars=22000).chunk(doc, {}))
        == 1
    )


def test_char_cap_invariant_and_metadata_preserved_on_mixed_doc() -> None:
    """Every fragment satisfies the char budget and keeps heading/start_line.

    Mixes a heading section, a token-dense paragraph well over the char cap,
    and a giant heading-less table-like block. The invariant
    ``not _over_budget(chunk.content)`` must hold for every emitted chunk and
    each fragment must carry the parent section's ``heading`` plus a
    monotone, in-range ``start_line``.
    """
    dense_para = " ".join("x" * 30 for _ in range(200))  # ~6k chars, 200 words
    table_block = "\n".join(
        "| " + " | ".join("y" * 20 for _ in range(8)) + " |" for _ in range(40)
    )
    doc = (
        "# Heading One\n\n"
        f"{dense_para}\n\n"
        "## Heading Two\n\n"
        f"{table_block}\n\n"
        "Closing prose paragraph with a few words.\n"
    )
    chunker = HeadingChunker(max_chunk_words=400, max_chunk_chars=3000)
    total_lines = len(doc.splitlines(keepends=True))
    chunks = chunker.chunk(doc, {})

    assert len(chunks) >= 3
    for c in chunks:
        # Invariant: no emitted chunk exceeds either budget (the only allowed
        # exception is a single unsplittable token longer than the cap, which
        # this fixture does not contain).
        assert not chunker._over_budget(c.content)
        assert 0 <= c.start_line < total_lines
        # Every fragment keeps a heading attribution (None only for a true
        # preamble; this doc has none before its first heading).
        assert c.heading in {"Heading One", "Heading Two"}


def test_unsplittable_token_emitted_intact_over_budget() -> None:
    """The one allowed invariant exception: a single whitespace-free token
    longer than max_chunk_chars is emitted alone and intact (not corrupted by
    mid-string splitting), while every other chunk stays within budget."""
    giant = "z" * 5000  # one token, no split points, > the 1000-char cap
    doc = f"# Title\n\nsmall intro paragraph.\n\n{giant}\n"
    chunker = HeadingChunker(max_chunk_words=400, max_chunk_chars=1000)
    chunks = chunker.chunk(doc, {})

    # The giant token survives intact as exactly one chunk's content.
    assert any(c.content == giant for c in chunks)
    # Every other chunk respects the budget; only the giant token may exceed.
    for c in chunks:
        assert c.content == giant or not chunker._over_budget(c.content)


# ---------------------------------------------------------------------------
# UTF-8 BOM normalization (#673)
# ---------------------------------------------------------------------------


def test_parse_note_strips_bom_so_frontmatter_parses(tmp_path: Path) -> None:
    """A UTF-8 BOM before the frontmatter fence must not prevent parsing (#673)."""
    p = tmp_path / "bom.md"
    p.write_bytes(b"\xef\xbb\xbf---\ntitle: BOM Note\ntags: [x]\n---\n\n# Body\n")

    note = parse_note(p, tmp_path)

    # BOM must not swallow the frontmatter — title and tags must be parsed.
    assert note.frontmatter.get("title") == "BOM Note"
    assert note.frontmatter.get("tags") == ["x"]
    # The YAML block must not appear in any body chunk.
    all_body = "".join(c.content for c in note.chunks)
    assert "title:" not in all_body


# ---------------------------------------------------------------------------
# Section extraction helpers (#741)
# ---------------------------------------------------------------------------


class TestExtractSection:
    def test_spans_subsections_until_next_same_level(self) -> None:
        """A section's span includes its sub-sections and stops at the next
        heading of the same or higher level."""
        text = (
            "# Title\n"
            "## Parent\n"
            "parent preamble.\n"
            "### Child\n"
            "child body.\n"
            "## Sibling\n"
            "sibling body.\n"
        )
        out = extract_section(text, "Parent")
        assert out is not None
        assert "parent preamble." in out
        assert "child body." in out
        assert "sibling body." not in out
        # The heading line itself is excluded.
        assert "## Parent" not in out

    def test_last_section_spans_to_end(self) -> None:
        text = "# Title\n## First\nfirst.\n## Last\nlast line one.\nlast line two.\n"
        out = extract_section(text, "Last")
        assert out is not None
        # Spans both lines of the final section, excludes the earlier section.
        # (Frontmatter parsing may drop a trailing newline, so compare loosely.)
        assert out.splitlines() == ["last line one.", "last line two."]

    def test_fenced_hash_line_acts_as_boundary(self) -> None:
        """A '#'-prefixed line inside a code fence ends the span, matching the
        (deliberately non-code-fence-aware) chunker. Characterises the current
        contract so a future fence-aware change to one side cannot silently
        desync extraction from indexing."""
        text = (
            "# Title\n"
            "## Section\n"
            "before fence.\n"
            "```sh\n"
            "# not really a heading\n"
            "```\n"
            "after fence.\n"
            "## Next\nnext body.\n"
        )
        out = extract_section(text, "Section")
        assert out is not None
        assert "before fence." in out
        # The fenced '#' line is treated as an H1 boundary, so the span ends
        # there — content after the fence is not part of this section.
        assert "after fence." not in out

    def test_empty_section_returns_empty_string_not_none(self) -> None:
        """A present-but-empty section (heading immediately followed by a
        same-or-higher heading) returns "" — distinct from the None that a
        *missing* heading returns. _read_section keys 'not found' on None, so
        this `"" is not None` boundary must hold."""
        text = "# A\n## Empty\n## Next\nnext body.\n"
        assert extract_section(text, "Empty") == ""
        assert extract_section(text, "Missing") is None

    def test_returns_none_for_missing_heading(self) -> None:
        assert extract_section("# A\nbody.\n", "Nope") is None

    def test_returns_none_for_empty_heading(self) -> None:
        assert extract_section("# A\nbody.\n", "   ") is None

    def test_whitespace_normalized_match(self) -> None:
        text = "# Title\n## 1.3.  Reducing deps\nbody content.\n"
        out = extract_section(text, "1.3. Reducing deps")
        assert out is not None
        assert "body content." in out

    def test_strips_frontmatter_before_scanning(self) -> None:
        text = "---\ntitle: Doc\ntags: [x]\n---\n# Title\n## Section\nsection body.\n"
        out = extract_section(text, "Section")
        assert out is not None
        assert "section body." in out
        assert "tags:" not in out

    def test_first_occurrence_when_duplicate(self) -> None:
        text = (
            "# Title\n"
            "## Repeat\nfirst body.\n"
            "## Middle\nmid.\n"
            "## Repeat\nsecond body.\n"
        )
        out = extract_section(text, "Repeat")
        assert out is not None
        assert "first body." in out
        assert "second body." not in out


class TestListSectionHeadings:
    def test_returns_headings_in_order(self) -> None:
        text = "# A\n## One\nx\n### Deep\ny\n## Two\nz\n"
        assert list_section_headings(text) == ["A", "One", "Deep", "Two"]

    def test_empty_when_no_headings(self) -> None:
        assert list_section_headings("just plain text\nno headings here\n") == []

    def test_strips_frontmatter(self) -> None:
        text = "---\ntitle: Doc\n---\n# Only\nbody\n"
        assert list_section_headings(text) == ["Only"]


def test_normalize_heading_collapses_whitespace() -> None:
    assert normalize_heading("1.3.   Reducing   deps  ") == "1.3. Reducing deps"
    assert normalize_heading("\tA\nB\t") == "A B"


class TestScanDirectoryOnSkip:
    """scan_directory reports surfaced skips through on_skip (#775)."""

    def _collect(self, source_dir: Path) -> tuple[list, list]:
        from markdown_vault_mcp.scanner import scan_directory
        from markdown_vault_mcp.types import SkippedFile

        skips: list[SkippedFile] = []
        notes = list(
            scan_directory(
                source_dir,
                required_frontmatter=["title"],
                exclude_patterns=["excluded/**"],
                on_skip=skips.append,
            )
        )
        return notes, skips

    def test_reports_parse_error(self, tmp_path: Path) -> None:
        (tmp_path / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\nbody", encoding="utf-8"
        )
        _notes, skips = self._collect(tmp_path)
        assert [s.category for s in skips] == ["parse_error"]
        assert skips[0].path == "bad.md"
        assert skips[0].detail

    def test_reports_encoding_error(self, tmp_path: Path) -> None:
        (tmp_path / "latin.md").write_bytes(b"\xff\xfe not utf-8")
        _notes, skips = self._collect(tmp_path)
        assert [s.category for s in skips] == ["encoding_error"]
        assert skips[0].path == "latin.md"

    def test_reports_missing_frontmatter(self, tmp_path: Path) -> None:
        (tmp_path / "nomatter.md").write_text("no frontmatter here", encoding="utf-8")
        _notes, skips = self._collect(tmp_path)
        assert [s.category for s in skips] == ["missing_frontmatter"]
        assert skips[0].path == "nomatter.md"
        assert "title" in skips[0].detail

    def test_not_called_for_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "excluded").mkdir()
        (tmp_path / "excluded" / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\n", encoding="utf-8"
        )
        _notes, skips = self._collect(tmp_path)
        assert skips == []

    def test_omitting_on_skip_preserves_behavior(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.scanner import scan_directory

        (tmp_path / "good.md").write_text("---\ntitle: ok\n---\nbody", encoding="utf-8")
        (tmp_path / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\n", encoding="utf-8"
        )
        notes = list(scan_directory(tmp_path, required_frontmatter=["title"]))
        assert [n.path for n in notes] == ["good.md"]

    def test_every_reported_category_is_legal(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.types import SKIP_CATEGORIES

        (tmp_path / "bad.md").write_text(
            "---\ntitle: [unclosed\n---\n", encoding="utf-8"
        )
        (tmp_path / "latin.md").write_bytes(b"\xff\xfe")
        (tmp_path / "nomatter.md").write_text("plain", encoding="utf-8")
        _notes, skips = self._collect(tmp_path)
        assert {s.category for s in skips} <= SKIP_CATEGORIES

    def test_reports_unexpected_exception_as_parse_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A non-YAML/non-decode/non-OSError failure inside parse_note (e.g. a
        # chunker bug raising RuntimeError) hits the generic ``except
        # Exception`` branch and is surfaced as ``parse_error`` via on_skip.
        import markdown_vault_mcp.scanner as scanner_module
        from markdown_vault_mcp.scanner import scan_directory
        from markdown_vault_mcp.types import SkippedFile

        (tmp_path / "boom.md").write_text("---\ntitle: ok\n---\nbody", encoding="utf-8")

        def exploding_parse(abs_path, source_dir, chunk_strategy):  # noqa: ARG001
            raise RuntimeError("chunker exploded")

        monkeypatch.setattr(scanner_module, "parse_note", exploding_parse)
        skips: list[SkippedFile] = []
        notes = list(
            scan_directory(
                tmp_path, required_frontmatter=["title"], on_skip=skips.append
            )
        )
        assert notes == []
        assert [s.category for s in skips] == ["parse_error"]
        assert skips[0].path == "boom.md"
        assert "chunker exploded" in skips[0].detail
