"""Tests for DocumentManager.move_folder (#511).

Exercised through the public ``vault.writer.move_folder`` facet surface,
mirroring the established writable-Vault test pattern in
``tests/test_vault.py`` (the ``writable`` fixture + ``wait_for_writer_drain``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import (
    DocumentExistsError,
    DocumentNotFoundError,
    ReadOnlyError,
)
from markdown_vault_mcp.types import MoveFolderResult
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    """Empty vault root the tests seed via ``vault.writer.write``."""
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def vault(source_dir: Path) -> Iterator[Vault]:
    """Writable Vault with an allowlist that permits ``.png`` attachments.

    ``png`` is allowlisted (so the attachment-carry test gets a rename
    callback) while ``.DS_Store`` is *not* (so the non-allowlisted-carry
    test exercises the silent-move branch).
    """
    col = Vault(
        source_dir=source_dir,
        read_only=False,
        attachment_extensions=["png"],
    )
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


@pytest.fixture
def read_only_vault(source_dir: Path) -> Iterator[Vault]:
    """Read-only Vault for the rejection test."""
    col = Vault(source_dir=source_dir, read_only=True)
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


def _write(vault: Vault, path: str, body: str) -> None:
    """Seed a note and wait for the async writer to index it."""
    vault.writer.write(path, body)
    wait_for_writer_drain(vault)


def test_move_folder_moves_notes_and_rewrites_inter_subtree_links(
    vault: Vault, source_dir: Path
) -> None:
    _write(vault, "drafts/a.md", "See [b](./b.md) and [[c]].\n")
    _write(vault, "drafts/b.md", "I am b.\n")
    _write(vault, "drafts/c.md", "I am c.\n")

    result = vault.writer.move_folder("drafts", "archive/2026")
    wait_for_writer_drain(vault)

    assert isinstance(result, MoveFolderResult)
    assert result.files_moved == 3
    # Files landed at the new prefix.
    assert (source_dir / "archive/2026/a.md").is_file()
    assert (source_dir / "archive/2026/b.md").is_file()
    assert (source_dir / "archive/2026/c.md").is_file()
    # Old prefix is gone.
    assert not (source_dir / "drafts").exists()
    # The relative link a->b still resolves to b in the same (moved) directory.
    # Both moved by the same prefix shift, so the link target is recomputed by
    # the rewrite machinery to a normalised same-dir relative path ("b.md" /
    # "./b.md") — never the cross-prefix "archive/2026/b.md".
    a_text = (source_dir / "archive/2026/a.md").read_text()
    assert "(b.md)" in a_text or "(./b.md)" in a_text
    assert "archive/2026/b.md" not in a_text


def test_move_folder_preserves_root_relative_link_spelling(
    vault: Vault, source_dir: Path
) -> None:
    """A `/root/relative.md` link stays root-relative across a move (#1105).

    OKF recommends the leading-slash spelling and `okf_convert_links` /
    `okf_generate_index` emit it, so rewriting it as a bare relative
    filename silently undid a vault's link conformance on every move. The
    link resolved either way, which is why nothing showed up as broken.
    """
    _write(vault, "drafts/a.md", "See [B](/drafts/b.md).\n")
    _write(vault, "drafts/b.md", "I am b.\n")
    _write(vault, "index.md", "See [A](/drafts/a.md).\n")

    vault.writer.move_folder("drafts", "archive/2026")
    wait_for_writer_drain(vault)

    a_text = (source_dir / "archive/2026/a.md").read_text()
    assert "[B](/archive/2026/b.md)" in a_text

    index_text = (source_dir / "index.md").read_text()
    assert "[A](/archive/2026/a.md)" in index_text


def test_move_folder_rewrites_external_backlinks(
    vault: Vault, source_dir: Path
) -> None:
    _write(vault, "drafts/a.md", "content\n")
    _write(vault, "index.md", "Link to [A](drafts/a.md).\n")

    result = vault.writer.move_folder("drafts", "archive")
    wait_for_writer_drain(vault)

    assert result.updated_links >= 1
    index_text = (source_dir / "index.md").read_text()
    assert "archive/a.md" in index_text
    assert "drafts/a.md" not in index_text


def test_move_folder_carries_allowlisted_attachment(
    vault: Vault, source_dir: Path
) -> None:
    _write(vault, "drafts/note.md", "![img](./pic.png)\n")
    (source_dir / "drafts" / "pic.png").write_bytes(b"\x89PNG\r\n")

    result = vault.writer.move_folder("drafts", "media")
    wait_for_writer_drain(vault)

    assert (source_dir / "media/pic.png").is_file()
    assert not (source_dir / "drafts").exists()
    assert result.files_moved == 2


def test_move_folder_target_collision_aborts_nothing_moved(
    vault: Vault, source_dir: Path
) -> None:
    _write(vault, "drafts/a.md", "a\n")
    _write(vault, "archive/a.md", "pre-existing\n")  # destination clash

    with pytest.raises(DocumentExistsError):
        vault.writer.move_folder("drafts", "archive")

    # Nothing moved: source intact, pre-existing target untouched.
    assert (source_dir / "drafts/a.md").is_file()
    assert (source_dir / "archive/a.md").read_text() == "pre-existing\n"


def test_move_folder_merges_into_existing_target(
    vault: Vault, source_dir: Path
) -> None:
    _write(vault, "drafts/a.md", "a\n")
    _write(vault, "archive/existing.md", "keep me\n")

    result = vault.writer.move_folder("drafts", "archive")
    wait_for_writer_drain(vault)

    assert (source_dir / "archive/a.md").is_file()
    assert (source_dir / "archive/existing.md").read_text() == "keep me\n"
    assert result.files_moved == 1


def test_move_folder_best_effort_skips_unwritable_source(
    vault: Vault, source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(vault, "drafts/a.md", "content\n")
    _write(vault, "ext1.md", "[A](drafts/a.md)\n")
    _write(vault, "ext2.md", "[A](drafts/a.md)\n")

    # Make exactly one source rewrite fail.
    real = vault.writer._doc_mgr._rewrite_one_source

    def flaky(source_abs, rewrites, source_path):  # type: ignore[no-untyped-def]
        if source_abs.name == "ext1.md":
            raise OSError("boom")
        return real(source_abs, rewrites, source_path)

    monkeypatch.setattr(vault.writer._doc_mgr, "_rewrite_one_source", flaky)

    result = vault.writer.move_folder("drafts", "archive")
    wait_for_writer_drain(vault)

    assert "ext1.md" in result.failed_links
    assert result.updated_links >= 1
    assert "archive/a.md" in (source_dir / "ext2.md").read_text()


def test_move_folder_missing_backlink_source_reported(
    vault: Vault, source_dir: Path
) -> None:
    """A backlink source deleted out-of-band is skipped and reported."""
    _write(vault, "drafts/a.md", "content\n")
    _write(vault, "ext1.md", "[A](drafts/a.md)\n")
    _write(vault, "ext2.md", "[A](drafts/a.md)\n")

    # Remove one source behind the vault's back — the index still holds its
    # backlink row, so the rewrite loop must skip-and-report the source.
    (source_dir / "ext1.md").unlink()

    result = vault.writer.move_folder("drafts", "archive")
    wait_for_writer_drain(vault)

    assert "ext1.md" in result.failed_links
    assert result.updated_links >= 1
    assert "archive/a.md" in (source_dir / "ext2.md").read_text()


def test_move_folder_read_only_rejected(read_only_vault: Vault) -> None:
    with pytest.raises(ReadOnlyError):
        read_only_vault.writer.move_folder("drafts", "archive")


def test_move_folder_missing_source_raises(vault: Vault) -> None:
    with pytest.raises(DocumentNotFoundError):
        vault.writer.move_folder("nope", "archive")


def test_move_folder_index_consistency(vault: Vault) -> None:
    _write(vault, "drafts/findme.md", "unique_token_zzz\n")
    vault.writer.move_folder("drafts", "archive")
    wait_for_writer_drain(vault)

    hits = vault.reader.search("unique_token_zzz")
    paths = [h.path for h in hits]
    assert "archive/findme.md" in paths
    assert "drafts/findme.md" not in paths


def test_move_folder_carries_non_allowlisted_file(
    vault: Vault, source_dir: Path
) -> None:
    _write(vault, "drafts/a.md", "a\n")
    (source_dir / "drafts" / ".DS_Store").write_bytes(b"junk")

    result = vault.writer.move_folder("drafts", "archive")
    wait_for_writer_drain(vault)

    assert (source_dir / "archive/.DS_Store").is_file()
    assert not (source_dir / "drafts").exists()
    assert result.files_moved == 2


def test_move_folder_reports_every_moved_file_to_the_write_callback(
    source_dir: Path,
) -> None:
    """Moving a file and not reporting it loses it (#1238).

    Git staging is scoped to the paths a callback names, so a file moved
    without one leaves an unstaged delete at the old path and an untracked
    add at the new one, and is never committed. The allowlist governs which
    files the *tools* expose, not what the repository tracks — so every
    moved file gets a rename callback, allowlisted or not.

    Asserting the disk result alone is what let this through before: the
    file does arrive at its new path either way.
    """
    calls: list[tuple[str, ...]] = []

    col = Vault(
        source_dir=source_dir,
        read_only=False,
        attachment_extensions=["png"],
        on_write=lambda path, _content, operation: calls.append(
            (operation, Path(path).name)
        ),
    )
    try:
        col.index.build_index()
        _write(col, "drafts/note.md", "n\n")
        (source_dir / "drafts" / "pic.png").write_bytes(b"\x89PNG\r\n")
        (source_dir / "drafts" / ".DS_Store").write_bytes(b"junk")
        calls.clear()

        col.writer.move_folder("drafts", "archive")
        wait_for_writer_drain(col)
    finally:
        col.close()

    renamed = {name for operation, name in calls if operation == "rename"}

    assert "note.md" in renamed, "the note should be reported"
    assert "pic.png" in renamed, "an allowlisted attachment should be reported"
    assert ".DS_Store" in renamed, (
        "a non-allowlisted file is moved on disk, so git must be told about "
        "it too — otherwise it silently leaves the repository"
    )


def test_move_folder_rejects_nested_target(vault: Vault) -> None:
    _write(vault, "drafts/a.md", "a\n")
    with pytest.raises(ValueError):
        vault.writer.move_folder("drafts", "drafts/sub")


def test_move_folder_rejects_same_folder(vault: Vault) -> None:
    _write(vault, "drafts/a.md", "a\n")
    with pytest.raises(ValueError):
        vault.writer.move_folder("drafts", "drafts")


def test_move_folder_rejects_vault_root(vault: Vault) -> None:
    _write(vault, "drafts/a.md", "a\n")
    with pytest.raises(ValueError):
        vault.writer.move_folder(".", "archive")


def test_move_folder_rejects_path_traversal(vault: Vault) -> None:
    _write(vault, "drafts/a.md", "a\n")
    with pytest.raises(ValueError):
        vault.writer.move_folder("../escape", "archive")


def test_move_folder_empty_folder_raises(vault: Vault, source_dir: Path) -> None:
    (source_dir / "empty").mkdir()
    with pytest.raises(DocumentNotFoundError):
        vault.writer.move_folder("empty", "archive")


def test_move_folder_preserves_nested_structure(vault: Vault, source_dir: Path) -> None:
    _write(vault, "drafts/a.md", "top\n")
    _write(vault, "drafts/sub/deep.md", "deep\n")

    result = vault.writer.move_folder("drafts", "archive")
    wait_for_writer_drain(vault)

    assert (source_dir / "archive/a.md").is_file()
    assert (source_dir / "archive/sub/deep.md").is_file()
    assert not (source_dir / "drafts").exists()
    assert result.files_moved == 2


def test_move_folder_mid_move_oserror_leaves_subtree_partial(
    vault: Vault, source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError on the 2nd shutil.move call leaves the subtree partially moved.

    Asserts the documented partial-failure contract:
    - move_folder raises OSError.
    - The first file reached its new path; the second file is still at the old path.
    - The index was NOT updated: searching a unique token from the first (moved)
      file does NOT return the new path, confirming mark_paths_dirty never ran.
    """
    _write(vault, "drafts/alpha.md", "unique_alpha_token_qzx\n")
    _write(vault, "drafts/beta.md", "unique_beta_token_qzx\n")
    _write(vault, "backlinker.md", "Links to [alpha](drafts/alpha.md).\n")

    import shutil as _shutil_real

    call_count = 0
    real_move = _shutil_real.move

    def flaky_move(src: str, dst: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("injected mid-move failure")
        real_move(src, dst)

    import markdown_vault_mcp.managers.document as _doc_mod

    monkeypatch.setattr(_doc_mod.shutil, "move", flaky_move)

    with pytest.raises(OSError, match="injected mid-move failure"):
        vault.writer.move_folder("drafts", "moved")

    wait_for_writer_drain(vault)

    # Subtree is partially moved: the first file succeeded, the second did not.
    # (shutil.move processes moves in sorted order via rglob+sorted; alpha < beta.)
    assert (source_dir / "moved/alpha.md").is_file(), "first file should have moved"
    assert (source_dir / "drafts/beta.md").is_file(), (
        "second file should still be at old path"
    )

    # Index was NOT updated by the failed operation: mark_paths_dirty never ran,
    # so searching the unique token from the moved file returns NO new-path hit.
    hits = vault.reader.search("unique_alpha_token_qzx")
    new_paths = [h.path for h in hits]
    assert "moved/alpha.md" not in new_paths, (
        "index should not have been updated after mid-move OSError"
    )


def test_move_folder_best_effort_generic_exception(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(vault, "drafts/a.md", "content\n")
    _write(vault, "ext.md", "[A](drafts/a.md)\n")

    def boom(_source_abs, _rewrites, _source_path):  # type: ignore[no-untyped-def]
        raise RuntimeError("unexpected")

    monkeypatch.setattr(vault.writer._doc_mgr, "_rewrite_one_source", boom)

    result = vault.writer.move_folder("drafts", "archive")
    wait_for_writer_drain(vault)

    assert "ext.md" in result.failed_links
    assert result.updated_links == 0


def test_move_folder_no_double_callback_for_intra_subtree_source(
    tmp_path: Path,
) -> None:
    """A moved note that is also link-rewritten gets ONE callback, not two.

    Such a note appears in both the moved set (a ``"rename"`` callback) and
    the rewritten-sources set (an ``"edit"`` callback). Firing both would emit
    a redundant git commit for the same file, so the ``"edit"`` is suppressed.
    """
    import threading

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.scanner import HeadingChunker, scan_directory

    vault_dir = tmp_path / "v"
    (vault_dir / "drafts").mkdir(parents=True)
    # a.md links to its sibling b.md — both move together.
    (vault_dir / "drafts" / "a.md").write_text("Link to [b](./b.md).\n")
    (vault_dir / "drafts" / "b.md").write_text("I am b.\n")

    fts = FTSIndex(db_path=":memory:")
    for note in scan_directory(vault_dir):
        fts.upsert_note(note)
    fts.resolve_vault_wikilinks()

    calls: list[tuple[str, str]] = []
    mgr = DocumentManager(
        fts=fts,
        source_dir=vault_dir,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        on_write_callback=lambda p, _c, op: calls.append((p.as_posix(), op)),
        mark_paths_dirty=lambda _paths: None,
    )

    mgr.move_folder("drafts", "archive")

    # archive/a.md was moved AND link-rewritten -> exactly one "rename".
    a_ops = [op for path, op in calls if path.endswith("archive/a.md")]
    assert a_ops == ["rename"], a_ops
    # archive/b.md only moved -> one "rename".
    b_ops = [op for path, op in calls if path.endswith("archive/b.md")]
    assert b_ops == ["rename"], b_ops
