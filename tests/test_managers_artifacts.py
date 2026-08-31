"""Tests for :class:`ArtifactStore` (#1235).

Constructed directly, with no ``DocumentManager`` and no FTS index, so these
exercise the store rather than the manager that composes it.

Two are structural rather than behavioural. The wiring test pins that the
store shares the manager's lock and notifier *by identity*: a private lock
would stop artifact writes serialising against note writes and would leave
``Vault.pause_writes`` unable to hold artifacts still during a git rebase. And
``unlink``/``move`` are asserted to fire *no* callback, because the manager
fires exactly one after its note/artifact branch closes — owning it in the
store would double-fire or move it outside the lock.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    ReadOnlyError,
)
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.managers._write_notifier import WriteNotifier
from markdown_vault_mcp.managers.artifacts import ArtifactPolicy, ArtifactStore

if TYPE_CHECKING:
    from markdown_vault_mcp.types import WriteOperation


class _Recorder:
    """Captures the calls a WriteNotifier would forward."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str, str]] = []
        self.renames: list[tuple[Path, str, Path]] = []

    def __call__(
        self, path: Path, content: str, operation: WriteOperation, /, **_kw: Any
    ) -> None:
        if operation == "rename":
            self.renames.append((path, content, Path()))
        else:
            self.calls.append((path, content, operation))


def _store(
    tmp_path: Path,
    *,
    extensions: list[str] | None = None,
    read_only: bool = False,
    write_protect_existing: bool = False,
) -> tuple[ArtifactStore, _Recorder]:
    recorder = _Recorder()
    store = ArtifactStore(
        tmp_path,
        write_lock=threading.RLock(),
        notifier=WriteNotifier(recorder),
        policy=ArtifactPolicy(
            attachment_extensions=extensions,
            read_only=read_only,
            write_protect_existing=write_protect_existing,
        ),
    )
    return store, recorder


class TestValidatePath:
    def test_accepts_allowlisted(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        assert store.validate_path("assets/a.png") == tmp_path / "assets/a.png"

    def test_rejects_markdown(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        with pytest.raises(ValueError, match="use the note read/write methods"):
            store.validate_path("note.md")

    def test_rejects_traversal(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        with pytest.raises(ValueError, match="traversal"):
            store.validate_path("../outside.png")

    def test_rejects_disallowed_extension_naming_the_env_var(
        self, tmp_path: Path
    ) -> None:
        """The operator-facing hint is the contract, not an implementation detail."""
        store, _ = _store(tmp_path, extensions=["png"])
        with pytest.raises(ValueError) as exc:
            store.validate_path("a.xyz")
        assert "not in the attachment allowlist" in str(exc.value)
        assert "MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS" in str(exc.value)

    def test_wildcard_accepts_anything_non_markdown(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path, extensions=["*"])
        assert store.validate_path("a.whatever")
        with pytest.raises(ValueError, match="use the note read/write methods"):
            store.validate_path("a.md")


class TestSize:
    def test_returns_byte_size(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"12345")
        assert store.size("a.png") == 5

    def test_missing_raises(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        with pytest.raises(ValueError, match="Attachment not found"):
            store.size("gone.png")

    def test_stat_race_surfaces_as_value_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file vanishing between is_file() and stat() is not an OSError leak."""
        store, _ = _store(tmp_path)

        class _Racy:
            def is_file(self) -> bool:
                return True

            def stat(self) -> object:
                raise OSError("vanished mid-stat")

        monkeypatch.setattr(store, "validate_path", lambda _p: _Racy())
        with pytest.raises(ValueError, match="Attachment not found"):
            store.size("gone.png")


class TestRead:
    def test_returns_content_and_metadata(self, tmp_path: Path) -> None:
        import base64

        store, _ = _store(tmp_path)
        raw = b"\x89PNG fake"
        (tmp_path / "a.png").write_bytes(raw)

        got = store.read("a.png")

        assert got.path == "a.png"
        assert got.mime_type == "image/png"
        assert got.size_bytes == len(raw)
        assert base64.b64decode(got.content_base64) == raw
        assert got.etag == compute_etag(raw)

    def test_missing_raises(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        with pytest.raises(ValueError, match="Attachment not found"):
            store.read("gone.png")


class TestWrite:
    def test_creates_and_reports_created(self, tmp_path: Path) -> None:
        store, rec = _store(tmp_path)
        result = store.write("assets/a.png", b"bytes")

        assert result.created is True
        assert (tmp_path / "assets/a.png").read_bytes() == b"bytes"
        assert rec.calls == [(tmp_path / "assets/a.png", "", "write")]

    def test_overwrite_reports_not_created(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"old")
        assert store.write("a.png", b"new").created is False
        assert (tmp_path / "a.png").read_bytes() == b"new"

    def test_read_only_refuses(self, tmp_path: Path) -> None:
        """Message pinned, not just the type.

        The store and DocumentManager must refuse a read-only vault with the
        same words: an operator hitting this sees one wording whichever path
        raised, and pinning it is what stops the two drifting apart.
        """
        store, rec = _store(tmp_path, read_only=True)
        with pytest.raises(
            ReadOnlyError,
            match="Vault is read-only; write operations are not permitted",
        ):
            store.write("a.png", b"x")
        assert rec.calls == []

    def test_read_only_message_matches_the_manager(self, tmp_path: Path) -> None:
        """The two guards state the same rule, so they must say the same thing."""
        from markdown_vault_mcp.fts_index import FTSIndex
        from markdown_vault_mcp.managers.document import DocumentManager
        from markdown_vault_mcp.scanner import HeadingChunker

        store, _ = _store(tmp_path, read_only=True)
        fts = FTSIndex(db_path=":memory:")
        try:
            mgr = DocumentManager(
                fts,
                tmp_path,
                write_lock=threading.RLock(),
                chunk_strategy=HeadingChunker(),
                read_only=True,
            )
            with pytest.raises(ReadOnlyError) as from_store:
                store.write("a.png", b"x")
            with pytest.raises(ReadOnlyError) as from_manager:
                mgr.write("a.md", "x")
            assert str(from_store.value) == str(from_manager.value)
        finally:
            fts.close()

    def test_write_protect_refuses_blind_overwrite(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path, write_protect_existing=True)
        (tmp_path / "a.png").write_bytes(b"old")
        with pytest.raises(DocumentExistsError, match="proof of read"):
            store.write("a.png", b"new")

    def test_write_protect_allows_with_if_match(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path, write_protect_existing=True)
        (tmp_path / "a.png").write_bytes(b"old")
        from markdown_vault_mcp.hashing import compute_file_hash

        etag = compute_file_hash(tmp_path / "a.png")
        assert store.write("a.png", b"new", etag).created is False

    def test_if_match_mismatch_raises(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"old")
        with pytest.raises(ConcurrentModificationError):
            store.write("a.png", b"new", "not-the-etag")

    def test_if_match_on_missing_file_raises(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        with pytest.raises(ConcurrentModificationError):
            store.write("a.png", b"x", "some-etag")


class TestUnlinkAndMove:
    def test_unlink_removes_and_fires_nothing(self, tmp_path: Path) -> None:
        """The manager owns the delete callback, so the store must stay silent."""
        store, rec = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"x")

        removed = store.unlink("a.png", None)

        assert removed == tmp_path / "a.png"
        assert not (tmp_path / "a.png").exists()
        assert rec.calls == []
        assert rec.renames == []

    def test_unlink_missing_raises(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        with pytest.raises(DocumentNotFoundError, match="Attachment not found"):
            store.unlink("gone.png", None)

    def test_unlink_honours_if_match(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"x")
        with pytest.raises(ConcurrentModificationError):
            store.unlink("a.png", "wrong")

    def test_move_relocates_and_fires_nothing(self, tmp_path: Path) -> None:
        store, rec = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"x")

        old_abs, new_abs = store.move("a.png", "sub/b.png", None)

        assert old_abs == tmp_path / "a.png"
        assert new_abs == tmp_path / "sub/b.png"
        assert new_abs.read_bytes() == b"x"
        assert rec.calls == []
        assert rec.renames == []

    def test_move_missing_source_raises(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        with pytest.raises(DocumentNotFoundError, match="Attachment not found"):
            store.move("gone.png", "b.png", None)

    def test_move_onto_existing_target_raises(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"x")
        (tmp_path / "b.png").write_bytes(b"y")
        with pytest.raises(DocumentExistsError, match="Target already exists"):
            store.move("a.png", "b.png", None)

    def test_move_honours_if_match(self, tmp_path: Path) -> None:
        store, _ = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"x")
        with pytest.raises(ConcurrentModificationError):
            store.move("a.png", "b.png", "wrong")

    def test_unlink_refuses_on_a_read_only_store(self, tmp_path: Path) -> None:
        """The store enforces its own policy, not just the manager's.

        DocumentManager.delete happens to check first, so this guard is
        unreachable through that path -- which is exactly why it needs a test:
        a store used directly would otherwise delete from a read-only vault.
        """
        store, _ = _store(tmp_path, read_only=True)
        (tmp_path / "a.png").write_bytes(b"x")

        with pytest.raises(ReadOnlyError):
            store.unlink("a.png", None)

        assert (tmp_path / "a.png").exists()

    def test_move_refuses_on_a_read_only_store(self, tmp_path: Path) -> None:
        """Same for move: policy holds however the store is reached."""
        store, _ = _store(tmp_path, read_only=True)
        (tmp_path / "a.png").write_bytes(b"x")

        with pytest.raises(ReadOnlyError):
            store.move("a.png", "b.png", None)

        assert (tmp_path / "a.png").exists()
        assert not (tmp_path / "b.png").exists()

    def test_unlink_and_move_are_reentrant_under_the_managers_lock(
        self, tmp_path: Path
    ) -> None:
        """Taking the lock inside must not deadlock the manager that holds it.

        DocumentManager.delete and .rename call these while already holding
        the shared lock. It is an RLock, so re-acquiring on the same thread is
        free -- this pins that, since a plain Lock here would hang the vault.
        """
        store, _ = _store(tmp_path)
        (tmp_path / "a.png").write_bytes(b"x")
        (tmp_path / "c.png").write_bytes(b"y")

        with store._file_write_lock:
            assert store.unlink("a.png", None) == tmp_path / "a.png"
            old_abs, new_abs = store.move("c.png", "d.png", None)

        assert not old_abs.exists()
        assert new_abs.read_bytes() == b"y"


class TestSharedWiring:
    """The store must share the manager's lock and notifier, not copy them."""

    def test_manager_passes_its_own_lock_and_notifier(self, tmp_path: Path) -> None:
        """Identity, not equality.

        A private lock in the store would stop artifact writes serialising
        against note writes, and would leave Vault.pause_writes unable to hold
        artifacts still during a git rebase.
        """
        from markdown_vault_mcp.fts_index import FTSIndex
        from markdown_vault_mcp.managers.document import DocumentManager
        from markdown_vault_mcp.scanner import HeadingChunker

        fts = FTSIndex(db_path=":memory:")
        try:
            lock = threading.RLock()
            mgr = DocumentManager(
                fts,
                tmp_path,
                write_lock=lock,
                chunk_strategy=HeadingChunker(),
                read_only=False,
            )
            assert mgr._artifacts._file_write_lock is lock
            assert mgr._artifacts._notifier is mgr._notifier
        finally:
            fts.close()
