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
    assert "document has no indexed headings" in str(excinfo.value)


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
