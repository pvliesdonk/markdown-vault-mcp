"""Tests for DocumentManager in isolation (no Collection dependency)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.scanner import HeadingChunker, scan_directory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def doc_vault(tmp_path: Path) -> Path:
    """Create a small vault with a few notes and an attachment."""
    alpha = tmp_path / "alpha.md"
    alpha.write_text(
        "---\ntitle: Alpha\n---\n# Alpha\n\nHello world.\n",
        encoding="utf-8",
    )
    beta = tmp_path / "beta.md"
    beta.write_text(
        "---\ntitle: Beta\n---\n# Beta\n\nLink to [alpha](alpha.md).\n",
        encoding="utf-8",
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    gamma = sub / "gamma.md"
    gamma.write_text(
        "---\ntitle: Gamma\n---\n# Gamma\n\n## Section One\n\nContent.\n",
        encoding="utf-8",
    )
    # Attachment
    img = tmp_path / "image.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return tmp_path


@pytest.fixture()
def doc_mgr(doc_vault: Path) -> DocumentManager:
    """Build a DocumentManager with an indexed FTS and writable vault."""
    fts = FTSIndex(db_path=":memory:")
    for note in scan_directory(doc_vault):
        fts.upsert_note(note)
    fts.resolve_vault_wikilinks()
    return DocumentManager(
        fts=fts,
        source_dir=doc_vault,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
    )


@pytest.fixture()
def ro_doc_mgr(doc_vault: Path) -> DocumentManager:
    """Build a read-only DocumentManager."""
    fts = FTSIndex(db_path=":memory:")
    for note in scan_directory(doc_vault):
        fts.upsert_note(note)
    return DocumentManager(
        fts=fts,
        source_dir=doc_vault,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=True,
    )


# ---------------------------------------------------------------------------
# Read tests
# ---------------------------------------------------------------------------


class TestRead:
    """Tests for DocumentManager.read()."""

    def test_read_existing(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.read("alpha.md")
        assert result is not None
        assert result.path == "alpha.md"
        assert result.title == "Alpha"
        assert "Hello world" in result.content
        assert result.etag  # non-empty hash

    def test_read_nonexistent(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.read("nonexistent.md")
        assert result is None

    def test_read_path_traversal(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.read("../../etc/passwd.md")
        assert result is None

    def test_read_subfolder(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.read("sub/gamma.md")
        assert result is not None
        assert result.folder == "sub"

    def test_read_root_folder_empty_string(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.read("alpha.md")
        assert result is not None
        assert result.folder == ""


# ---------------------------------------------------------------------------
# Write tests
# ---------------------------------------------------------------------------


class TestWrite:
    """Tests for DocumentManager.write()."""

    def test_write_creates_file(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.write("new.md", "# New\n\nContent.\n")
        assert result.created is True
        assert result.path == "new.md"

        note = doc_mgr.read("new.md")
        assert note is not None
        assert "Content." in note.content

    def test_write_creates_intermediate_dirs(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.write("deep/nested/note.md", "# Deep\n")
        assert result.created is True

        note = doc_mgr.read("deep/nested/note.md")
        assert note is not None

    def test_write_with_frontmatter(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.write(
            "fm.md", "Body.", frontmatter={"title": "FM Note", "tags": ["a"]}
        )
        assert result.created is True
        note = doc_mgr.read("fm.md")
        assert note is not None
        assert note.frontmatter.get("title") == "FM Note"

    def test_write_overwrites_existing(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.write("alpha.md", "# Replaced\n")
        assert result.created is False
        note = doc_mgr.read("alpha.md")
        assert note is not None
        assert "Replaced" in note.content

    def test_write_read_only_raises(self, ro_doc_mgr: DocumentManager) -> None:
        with pytest.raises(ReadOnlyError):
            ro_doc_mgr.write("new.md", "# New\n")

    def test_write_path_traversal_raises(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            doc_mgr.write("../../escape.md", "bad")


# ---------------------------------------------------------------------------
# Edit tests
# ---------------------------------------------------------------------------


class TestEdit:
    """Tests for DocumentManager.edit()."""

    def test_edit_exact_match(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.edit("alpha.md", old_text="Hello world.", new_text="Goodbye.")
        assert result.replacements == 1
        assert result.match_type == "exact"

        note = doc_mgr.read("alpha.md")
        assert note is not None
        assert "Goodbye." in note.content
        assert "Hello world." not in note.content

    def test_edit_returns_edit_result(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.edit("alpha.md", old_text="Hello world.", new_text="X")
        assert result.path == "alpha.md"

    def test_edit_etag_validation(self, doc_mgr: DocumentManager) -> None:
        note = doc_mgr.read("alpha.md")
        assert note is not None
        # Valid etag should succeed.
        doc_mgr.edit(
            "alpha.md",
            old_text="Hello world.",
            new_text="Changed.",
            if_match=note.etag,
        )
        # Now the etag is stale.
        with pytest.raises(ConcurrentModificationError):
            doc_mgr.edit(
                "alpha.md",
                old_text="Changed.",
                new_text="Oops.",
                if_match=note.etag,
            )

    def test_edit_nonexistent_raises(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(DocumentNotFoundError):
            doc_mgr.edit("missing.md", old_text="x", new_text="y")

    def test_edit_not_found_raises_conflict(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(EditConflictError, match="not found"):
            doc_mgr.edit("alpha.md", old_text="NOPE NOPE NOPE", new_text="x")

    def test_edit_read_only_raises(self, ro_doc_mgr: DocumentManager) -> None:
        with pytest.raises(ReadOnlyError):
            ro_doc_mgr.edit("alpha.md", old_text="Hello", new_text="Bye")

    def test_edit_line_range(self, doc_mgr: DocumentManager) -> None:
        # alpha.md has: ---\ntitle: Alpha\n---\n# Alpha\n\nHello world.\n
        # Lines: 1=---, 2=title: Alpha, 3=---, 4=# Alpha, 5=(empty), 6=Hello world.
        result = doc_mgr.edit(
            "alpha.md", new_text="Replaced line.", line_start=6, line_end=6
        )
        assert result.match_type == "exact"
        note = doc_mgr.read("alpha.md")
        assert note is not None
        assert "Replaced line." in note.content

    def test_edit_empty_old_text_raises(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(ValueError, match="old_text must not be empty"):
            doc_mgr.edit("alpha.md", old_text="", new_text="x")

    def test_edit_no_params_raises(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(ValueError, match="Must provide"):
            doc_mgr.edit("alpha.md")


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------


class TestDelete:
    """Tests for DocumentManager.delete()."""

    def test_delete_md(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.delete("alpha.md")
        assert result.path == "alpha.md"
        assert doc_mgr.read("alpha.md") is None

    def test_delete_nonexistent_raises(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(DocumentNotFoundError):
            doc_mgr.delete("missing.md")

    def test_delete_read_only_raises(self, ro_doc_mgr: DocumentManager) -> None:
        with pytest.raises(ReadOnlyError):
            ro_doc_mgr.delete("alpha.md")

    def test_delete_attachment(self, doc_mgr: DocumentManager, doc_vault: Path) -> None:
        result = doc_mgr.delete("image.png")
        assert result.path == "image.png"
        assert not (doc_vault / "image.png").exists()


# ---------------------------------------------------------------------------
# Rename tests
# ---------------------------------------------------------------------------


class TestRename:
    """Tests for DocumentManager.rename()."""

    def test_rename_md(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.rename("alpha.md", "alpha_renamed.md")
        assert result.old_path == "alpha.md"
        assert result.new_path == "alpha_renamed.md"
        assert doc_mgr.read("alpha.md") is None
        assert doc_mgr.read("alpha_renamed.md") is not None

    def test_rename_nonexistent_raises(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(DocumentNotFoundError):
            doc_mgr.rename("missing.md", "other.md")

    def test_rename_target_exists_raises(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(DocumentExistsError):
            doc_mgr.rename("alpha.md", "beta.md")

    def test_rename_read_only_raises(self, ro_doc_mgr: DocumentManager) -> None:
        with pytest.raises(ReadOnlyError):
            ro_doc_mgr.rename("alpha.md", "other.md")

    def test_rename_attachment(self, doc_mgr: DocumentManager, doc_vault: Path) -> None:
        result = doc_mgr.rename("image.png", "photo.png")
        assert result.old_path == "image.png"
        assert result.new_path == "photo.png"
        assert not (doc_vault / "image.png").exists()
        assert (doc_vault / "photo.png").exists()


# ---------------------------------------------------------------------------
# Attachment tests
# ---------------------------------------------------------------------------


class TestAttachments:
    """Tests for read_attachment and write_attachment."""

    def test_read_attachment(self, doc_mgr: DocumentManager) -> None:
        result = doc_mgr.read_attachment("image.png")
        assert result.path == "image.png"
        assert result.size_bytes > 0
        assert result.content_base64  # non-empty
        assert result.etag  # non-empty

    def test_read_attachment_not_found(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            doc_mgr.read_attachment("missing.png")

    def test_write_attachment(self, doc_mgr: DocumentManager, doc_vault: Path) -> None:
        data = b"fake PDF content"
        result = doc_mgr.write_attachment("doc.pdf", data)
        assert result.created is True
        assert (doc_vault / "doc.pdf").read_bytes() == data

    def test_write_attachment_read_only(self, ro_doc_mgr: DocumentManager) -> None:
        with pytest.raises(ReadOnlyError):
            ro_doc_mgr.write_attachment("x.pdf", b"data")


# ---------------------------------------------------------------------------
# TOC tests
# ---------------------------------------------------------------------------


class TestGetToc:
    """Tests for DocumentManager.get_toc()."""

    def test_get_toc(self, doc_mgr: DocumentManager) -> None:
        toc = doc_mgr.get_toc("sub/gamma.md")
        assert len(toc) >= 1
        assert toc[0]["heading"] == "Gamma"
        assert toc[0]["level"] == 1

    def test_get_toc_not_found(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(ValueError, match="Document not found"):
            doc_mgr.get_toc("missing.md")


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidation:
    """Tests for path validation helpers."""

    def test_validate_path_rejects_traversal(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(ValueError, match="Path traversal"):
            doc_mgr._validate_path("../../etc/passwd.md")

    def test_validate_path_rejects_non_md(self, doc_mgr: DocumentManager) -> None:
        with pytest.raises(ValueError, match=r"must end with '\.md'"):
            doc_mgr._validate_path("file.txt")

    def test_validate_path_valid(
        self, doc_mgr: DocumentManager, doc_vault: Path
    ) -> None:
        result = doc_mgr._validate_path("alpha.md")
        assert result == (doc_vault / "alpha.md").resolve()

    def test_check_writable_raises_when_read_only(
        self, ro_doc_mgr: DocumentManager
    ) -> None:
        with pytest.raises(ReadOnlyError):
            ro_doc_mgr._check_writable()

    def test_check_writable_ok_when_writable(self, doc_mgr: DocumentManager) -> None:
        doc_mgr._check_writable()  # should not raise

    def test_is_attachment(self, doc_mgr: DocumentManager) -> None:
        assert doc_mgr._is_attachment("image.png") is True
        assert doc_mgr._is_attachment("note.md") is False

    def test_is_path_excluded(self, doc_vault: Path) -> None:
        fts = FTSIndex(db_path=":memory:")
        mgr = DocumentManager(
            fts=fts,
            source_dir=doc_vault,
            write_lock=threading.RLock(),
            chunk_strategy=HeadingChunker(),
            exclude_patterns=["*.tmp", "drafts/*"],
        )
        assert mgr._is_path_excluded("notes.tmp") is True
        assert mgr._is_path_excluded("drafts/foo.md") is True
        assert mgr._is_path_excluded("alpha.md") is False


# ---------------------------------------------------------------------------
# Callback wiring tests
# ---------------------------------------------------------------------------


class TestCallbacks:
    """Tests that callbacks are invoked during write operations."""

    def test_write_fires_callbacks(self, doc_vault: Path) -> None:
        fts = FTSIndex(db_path=":memory:")
        for note in scan_directory(doc_vault):
            fts.upsert_note(note)

        write_calls: list[tuple] = []
        vector_calls: list[str] = []

        mgr = DocumentManager(
            fts=fts,
            source_dir=doc_vault,
            write_lock=threading.RLock(),
            chunk_strategy=HeadingChunker(),
            read_only=False,
            on_write_callback=lambda p, _c, op: write_calls.append((p, op)),
            on_vector_update=lambda note: vector_calls.append(note.path),
        )
        mgr.write("cb_test.md", "# Callback test\n")
        assert len(write_calls) == 1
        assert write_calls[0][1] == "write"
        assert "cb_test.md" in vector_calls

    def test_delete_fires_dirty_callback(self, doc_vault: Path) -> None:
        fts = FTSIndex(db_path=":memory:")
        for note in scan_directory(doc_vault):
            fts.upsert_note(note)

        dirty_calls: list[str] = []
        write_calls: list[str] = []

        mgr = DocumentManager(
            fts=fts,
            source_dir=doc_vault,
            write_lock=threading.RLock(),
            chunk_strategy=HeadingChunker(),
            read_only=False,
            on_write_callback=lambda _p, _c, op: write_calls.append(op),
            on_vector_dirty=lambda path: dirty_calls.append(path),
        )
        mgr.delete("alpha.md")
        assert "alpha.md" in dirty_calls
        assert "delete" in write_calls
