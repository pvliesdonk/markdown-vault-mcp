"""Tests for DocumentManager.read(path, section=...) section retrieval."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.scanner import HeadingChunker, scan_directory


@pytest.fixture()
def doc_mgr(tmp_path):
    a = tmp_path / "a.md"
    # Use multi-line bodies so the doc clears the 30-line short-doc bypass and
    # actually splits into per-section chunks.
    body = (
        "# A\n"
        + "\n".join(["intro"] * 12)
        + "\n## Section One\n"
        + "\n".join(["first body word"] * 12)
        + "\n## Section Two\n"
        + "\n".join(["second body word"] * 12)
        + "\n"
    )
    a.write_text(body, encoding="utf-8")
    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    for note in scan_directory(tmp_path, chunk_strategy=chunker):
        fts.upsert_note(note)
    return DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=chunker,
        read_only=False,
    )


def test_read_no_section_returns_full_file(doc_mgr):
    nc = doc_mgr.read("a.md")
    assert nc is not None
    assert "Section One" in nc.content
    assert "Section Two" in nc.content


def test_read_with_section_returns_only_that_chunk(doc_mgr):
    nc = doc_mgr.read("a.md", section="Section One")
    assert nc is not None
    assert "first body word" in nc.content
    assert "second body word" not in nc.content


def test_read_unknown_section_raises(doc_mgr):
    with pytest.raises(ValueError, match="Section"):
        doc_mgr.read("a.md", section="No Such Heading")


def test_read_empty_section_raises(doc_mgr):
    with pytest.raises(ValueError):
        doc_mgr.read("a.md", section="   ")


def test_read_returns_none_when_path_unknown(doc_mgr):
    assert doc_mgr.read("missing.md") is None
    # With section, missing path also raises (cannot resolve section in
    # nonexistent doc).
    with pytest.raises(ValueError):
        doc_mgr.read("missing.md", section="Anything")


def test_read_section_collapses_internal_whitespace(tmp_path):
    """Lookup tolerates whitespace runs differing from storage."""
    a = tmp_path / "a.md"
    # Stored heading has two spaces after the numbering prefix — the kind of
    # editor artefact LLM callers rarely reproduce from a rendered TOC.
    # Doc must clear the 30-line short-doc bypass to get per-section chunks.
    body = (
        "# A\n"
        + "\n".join(["intro"] * 16)
        + "\n## 1.3.  Reducing excessive dependencies\n"
        + "\n".join(["body content"] * 16)
        + "\n"
    )
    a.write_text(body, encoding="utf-8")
    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    for note in scan_directory(tmp_path, chunk_strategy=chunker):
        fts.upsert_note(note)
    mgr = DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=chunker,
    )

    # Two-spaces stored, one-space lookup — the production failure shape.
    nc = mgr.read("a.md", section="1.3. Reducing excessive dependencies")
    assert nc is not None
    assert "body content" in nc.content
    # Symmetric: two-spaces stored, three-spaces lookup also collapses.
    nc = mgr.read("a.md", section="1.3.   Reducing excessive dependencies")
    assert nc is not None
    assert "body content" in nc.content


def test_read_unknown_section_lists_available_headings(doc_mgr):
    """Miss message includes the actual stored headings so callers can recover."""
    with pytest.raises(ValueError) as excinfo:
        doc_mgr.read("a.md", section="No Such Heading")
    message = str(excinfo.value)
    assert "No Such Heading" in message
    assert "available headings include" in message
    assert "'Section One'" in message
    assert "'Section Two'" in message


def test_read_section_no_headings_message(tmp_path):
    """When the document has no indexed headings, the miss message says so."""
    a = tmp_path / "a.md"
    # Long body with no markdown headings — clears short-doc bypass.
    a.write_text("\n".join(["plain text line"] * 60) + "\n", encoding="utf-8")
    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    for note in scan_directory(tmp_path, chunk_strategy=chunker):
        fts.upsert_note(note)
    mgr = DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=chunker,
    )

    with pytest.raises(ValueError) as excinfo:
        mgr.read("a.md", section="Anything")
    assert "document has no headings" in str(excinfo.value)


def test_read_section_duplicate_heading_returns_first_by_start_line(tmp_path):
    """When a heading repeats, _read_section returns the first occurrence."""
    a = tmp_path / "a.md"
    # Each section body must be long enough that the total doc exceeds the
    # 30-line short-doc bypass (HeadingChunker default), so we get per-section
    # chunks rather than a single whole-document chunk.
    body = (
        "# A\n## Repeat\n"
        + "\n".join(["first occurrence body"] * 16)
        + "\n## Repeat\n"
        + "\n".join(["second occurrence body"] * 16)
        + "\n"
    )
    a.write_text(body, encoding="utf-8")
    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    for note in scan_directory(tmp_path, chunk_strategy=chunker):
        fts.upsert_note(note)
    mgr = DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=chunker,
    )

    nc = mgr.read("a.md", section="Repeat")
    assert nc is not None
    assert "first occurrence body" in nc.content
    assert "second occurrence body" not in nc.content


def _make_mgr(tmp_path, body: str) -> DocumentManager:
    """Write *body* to ``a.md``, index it, and return a DocumentManager.

    Uses a budgeted chunker (mirroring the production vault's
    ``max_chunk_words=400`` default) so long sections are split into multiple
    same-heading rows — the configuration under which #741 reproduces.
    """
    (tmp_path / "a.md").write_text(body, encoding="utf-8")
    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker(max_chunk_words=400, max_chunk_chars=6000)
    for note in scan_directory(tmp_path, chunk_strategy=chunker):
        fts.upsert_note(note)
    return DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=chunker,
        read_only=False,
    )


def test_read_section_returns_full_body_when_budget_split(tmp_path):
    """A section whose body exceeds the chunk budget (no sub-headings) is
    returned in full, not truncated to its first chunk (#741)."""
    intro = "marker_intro opening paragraph."
    bullets = "\n".join(f"- bullet{i} " + "word " * 30 for i in range(20))
    closing = "marker_closing final paragraph."
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Section One\n"
        + intro
        + "\n\n"
        + bullets
        + "\n\n"
        + closing
        + "\n## Section Two\n"
        + "second body word\n"
    )
    mgr = _make_mgr(tmp_path, body)

    nc = mgr.read("a.md", section="Section One")
    assert nc is not None
    # The whole section comes back: intro paragraph, the last bullet, and the
    # closing paragraph — not merely the first chunk.
    assert "marker_intro" in nc.content
    assert "bullet19" in nc.content
    assert "marker_closing" in nc.content
    # The next sibling section is excluded.
    assert "second body word" not in nc.content


def test_read_section_includes_subsections(tmp_path):
    """Reading a parent heading whose body was split at sub-headings returns
    the sub-sections too, stopping at the next same-or-higher heading (#741)."""
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Parent\n"
        + "marker_pre parent preamble.\n"
        + "### Child One\n"
        + "marker_c1 "
        + "word " * 250
        + "\n"
        + "### Child Two\n"
        + "marker_c2 "
        + "word " * 250
        + "\n"
        + "## Sibling\n"
        + "marker_sib sibling body.\n"
    )
    mgr = _make_mgr(tmp_path, body)

    nc = mgr.read("a.md", section="Parent")
    assert nc is not None
    assert "marker_pre" in nc.content
    assert "marker_c1" in nc.content
    assert "marker_c2" in nc.content
    # Stops at the next H2.
    assert "marker_sib" not in nc.content


def test_read_subsection_stops_at_next_same_level(tmp_path):
    """A sub-heading read returns only its own span, stopping at the next
    heading of the same or higher level (#741)."""
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Parent\n"
        + "### X\n"
        + "marker_x body.\n"
        + "### Y\n"
        + "marker_y body.\n"
        + "## Z\n"
        + "marker_z body.\n"
    )
    mgr = _make_mgr(tmp_path, body)

    nc = mgr.read("a.md", section="X")
    assert nc is not None
    assert "marker_x" in nc.content
    assert "marker_y" not in nc.content
    assert "marker_z" not in nc.content


def test_read_section_spans_to_end_of_document(tmp_path):
    """The last section in a document spans to EOF (#741)."""
    last_body = "marker_last " + "word " * 250
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## First\n"
        + "marker_first body.\n"
        + "## Last\n"
        + last_body
        + "\n"
    )
    mgr = _make_mgr(tmp_path, body)

    nc = mgr.read("a.md", section="Last")
    assert nc is not None
    assert "marker_last" in nc.content
    assert "marker_first" not in nc.content


def test_read_section_works_in_short_document(tmp_path):
    """Section read works even for short docs the chunker keeps as a single
    whole-document chunk (#741)."""
    body = "# Title\n## Alpha\nmarker_a body.\n## Beta\nmarker_b body.\n"
    mgr = _make_mgr(tmp_path, body)

    nc = mgr.read("a.md", section="Alpha")
    assert nc is not None
    assert "marker_a" in nc.content
    assert "marker_b" not in nc.content


def test_read_section_excludes_heading_line(tmp_path):
    """The section's own heading line is not part of the returned content."""
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Section One\n"
        + "first body word\n"
        + "## Section Two\n"
        + "second body word\n"
    )
    mgr = _make_mgr(tmp_path, body)

    nc = mgr.read("a.md", section="Section One")
    assert nc is not None
    assert "## Section One" not in nc.content
    assert "first body word" in nc.content


def test_read_section_ignores_frontmatter(tmp_path):
    """Frontmatter is stripped before heading scanning; section content is the
    body only."""
    body = (
        "---\n"
        "title: A doc\n"
        "tags: [x]\n"
        "---\n"
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Section One\n"
        + "first body word\n"
    )
    mgr = _make_mgr(tmp_path, body)

    nc = mgr.read("a.md", section="Section One")
    assert nc is not None
    assert "first body word" in nc.content
    assert "tags:" not in nc.content


def test_read_section_raises_when_indexed_file_missing_on_disk(tmp_path):
    """If the index still has the doc but its file is gone, section read raises
    a ValueError rather than leaking the underlying OSError (#741)."""
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Section One\n"
        + "first body word\n"
    )
    mgr = _make_mgr(tmp_path, body)
    # Remove the file from disk without reindexing — get_note still hits.
    (tmp_path / "a.md").unlink()

    with pytest.raises(ValueError, match="not readable"):
        mgr.read("a.md", section="Section One")


def test_read_empty_but_present_section_returns_empty_content(tmp_path):
    """A heading with no body (immediately followed by a same-level heading) is
    a present-but-empty section: it returns NoteContent with empty content, NOT
    a 'section not found' ValueError (#741)."""
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Empty\n## Next\nmarker_next body.\n"
    )
    mgr = _make_mgr(tmp_path, body)

    nc = mgr.read("a.md", section="Empty")
    assert nc is not None
    assert nc.content.strip() == ""
    assert "marker_next" not in nc.content


def test_read_unknown_section_suggestion_dedupes_headings(tmp_path):
    """The 'did you mean' suggestion lists each heading once even when the
    document repeats a heading."""
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Repeat\nfirst.\n"
        + "## Other\nmid.\n"
        + "## Repeat\nsecond.\n"
    )
    mgr = _make_mgr(tmp_path, body)

    with pytest.raises(ValueError) as excinfo:
        mgr.read("a.md", section="No Such Heading")
    message = str(excinfo.value)
    assert message.count("'Repeat'") == 1


def test_read_section_raises_on_non_utf8_file(tmp_path):
    """A file that decodes cleanly at index time but is later overwritten with
    invalid UTF-8 yields a ValueError, not a leaked UnicodeDecodeError (#741).

    ``UnicodeDecodeError`` subclasses ``ValueError`` but does not subclass
    ``OSError``; matching on the message pins the friendly error rather than the
    raw decode failure.
    """
    body = (
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Section One\n"
        + "first body word\n"
    )
    mgr = _make_mgr(tmp_path, body)
    # Corrupt the file with invalid UTF-8 bytes after indexing.
    (tmp_path / "a.md").write_bytes(b"\xff\xfe invalid \x80\x81 bytes")

    with pytest.raises(ValueError, match="not readable"):
        mgr.read("a.md", section="Section One")


def test_read_section_raises_on_malformed_frontmatter(tmp_path):
    """A file with valid frontmatter at index time, later overwritten with a
    malformed YAML block, yields a ValueError rather than a leaked
    yaml.YAMLError (#741)."""
    body = (
        "---\n"
        "title: A\n"
        "---\n"
        "# A\n"
        + "\n".join(["preamble"] * 12)
        + "\n## Section One\n"
        + "first body word\n"
    )
    mgr = _make_mgr(tmp_path, body)
    # Overwrite with an unclosed YAML flow sequence after indexing.
    (tmp_path / "a.md").write_text(
        "---\ntitle: [unclosed\n---\n## Section One\nbody\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="not parseable"):
        mgr.read("a.md", section="Section One")


def test_write_fires_callback_while_holding_file_write_lock(tmp_path: Path) -> None:
    """The write callback must fire INSIDE _file_write_lock (#571): a concurrent
    thread must not be able to acquire the lock while the callback runs."""
    import threading

    write_lock = threading.RLock()
    lock_free_during_callback = threading.Event()

    def on_write(_abs_path, _content, _operation) -> None:
        # Probe from another thread: if the lock is held (fix in place), the
        # probe fails to acquire it; if fire ran outside the lock, it succeeds.
        def probe() -> None:
            if write_lock.acquire(blocking=False):
                lock_free_during_callback.set()
                write_lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join()

    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    mgr = DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=write_lock,
        chunk_strategy=chunker,
        read_only=False,
        on_write_callback=on_write,
    )
    mgr.write("note.md", "# hello\n")
    assert not lock_free_during_callback.is_set(), (
        "callback fired outside _file_write_lock"
    )


def test_write_attachment_fires_callback_while_holding_file_write_lock(
    tmp_path: Path,
) -> None:
    """write_attachment must also fire its callback INSIDE _file_write_lock (#571)."""
    import threading

    write_lock = threading.RLock()
    lock_free_during_callback = threading.Event()

    def on_write(_abs_path, _content, _operation) -> None:
        def probe() -> None:
            if write_lock.acquire(blocking=False):
                lock_free_during_callback.set()
                write_lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join()

    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    mgr = DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=write_lock,
        chunk_strategy=chunker,
        read_only=False,
        on_write_callback=on_write,
    )
    mgr.write_attachment("assets/pic.png", b"\x89PNG\r\n\x1a\n")
    assert not lock_free_during_callback.is_set(), (
        "write_attachment callback fired outside _file_write_lock"
    )


def test_edit_rename_delete_fire_callbacks_while_holding_file_write_lock(
    tmp_path: Path,
) -> None:
    """edit/rename/delete must also fire their callbacks INSIDE _file_write_lock
    (#571) — the probe thread must never acquire the lock during the callback."""
    import threading

    write_lock = threading.RLock()
    lock_free_during_callback = threading.Event()

    def on_write(_abs_path, _content, _operation) -> None:
        def probe() -> None:
            if write_lock.acquire(blocking=False):
                lock_free_during_callback.set()
                write_lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join()

    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    mgr = DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=write_lock,
        chunk_strategy=chunker,
        read_only=False,
        on_write_callback=on_write,
    )

    # Seed a file (the write itself fires under the lock); then probe each of
    # edit/rename/delete in turn, clearing the flag immediately before each so
    # the assertion isolates that op's callback.
    mgr.write("note.md", "# hello\nold body\n")

    lock_free_during_callback.clear()
    mgr.edit("note.md", "old body", "new body")
    assert not lock_free_during_callback.is_set(), (
        "edit callback fired outside _file_write_lock"
    )

    lock_free_during_callback.clear()
    mgr.rename("note.md", "renamed.md")
    assert not lock_free_during_callback.is_set(), (
        "rename callback fired outside _file_write_lock"
    )

    lock_free_during_callback.clear()
    mgr.delete("renamed.md")
    assert not lock_free_during_callback.is_set(), (
        "delete callback fired outside _file_write_lock"
    )


# ---------------------------------------------------------------------------
# UTF-8 BOM normalization (#673)
# ---------------------------------------------------------------------------


def test_read_and_rewrite_normalizes_bom(tmp_path: Path) -> None:
    """read() returns BOM-free content; a rewrite drops the BOM on disk (#673)."""
    src = tmp_path / "vault"
    src.mkdir()
    (src / "note.md").write_bytes(b"\xef\xbb\xbf# Title\n\noriginal body\n")

    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    mgr = DocumentManager(
        fts=fts,
        source_dir=src,
        write_lock=threading.RLock(),
        chunk_strategy=chunker,
        read_only=False,
    )

    # Seed FTS so read() can locate the document.
    for note in scan_directory(src, chunk_strategy=chunker):
        fts.upsert_note(note)

    nc = mgr.read("note.md")
    assert nc is not None
    # BOM must be stripped on read — content must not start with the BOM char.
    assert not nc.content.startswith("\ufeff"), "read() returned BOM-prefixed content"
    assert nc.content.startswith("# Title")

    # Rewrite via edit(); the on-disk file must also lose the BOM.
    mgr.edit("note.md", old_text="original body", new_text="new body")
    raw = (src / "note.md").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "rewritten file still has BOM"
    assert b"new body" in raw
