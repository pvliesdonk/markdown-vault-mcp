"""Exercise the Windows writer-lock API through a fake on POSIX."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from markdown_vault_mcp.exceptions import StoreBusyError
from markdown_vault_mcp.stores import _writer_lock
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from tests.fixtures.vector_store_contract import IDENT
from tests.fixtures.windows_writer_lock import FakeMsvcrt


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> FakeMsvcrt:
    """Install the fake Windows API without POSIX-only positioned I/O.

    Args:
        monkeypatch: Restores platform APIs after the test.

    Returns:
        The configured fake API.
    """
    fake = FakeMsvcrt()
    monkeypatch.delattr(os, "pread", raising=False)
    monkeypatch.delattr(os, "pwrite", raising=False)
    monkeypatch.setattr(_writer_lock, "fcntl", None)
    monkeypatch.setattr(_writer_lock, "msvcrt", fake, raising=False)
    return fake


@pytest.mark.parametrize("kind", ["numpy", "sqlite", "keyword"])
def test_windows_contention_refuses_callers(
    windows: FakeMsvcrt, tmp_path: Path, kind: str
) -> None:
    base = tmp_path / ("index.db" if kind == "keyword" else "emb")
    lock = Path(f"{base}.lock") if kind == "keyword" else base.with_suffix(".lock")
    lock.write_text("123456789\n")
    windows.error = errno.EACCES
    with pytest.raises(StoreBusyError, match="pid 123456789"):
        if kind == "keyword":
            SqliteKeywordBackend().open(base)
        else:
            backend = (
                NumpySidecarBackend() if kind == "numpy" else SqliteVectorBackend()
            )
            backend.open(base, IDENT)
    assert windows.calls == [(windows.LK_NBLCK, 32, 1)]


def test_windows_refcounts_and_unlocks_same_range(
    windows: FakeMsvcrt, tmp_path: Path
) -> None:
    base = tmp_path / "emb"
    a = _writer_lock.hold_writer_lock(base, backend="test")
    b = _writer_lock.hold_writer_lock(base, backend="test")
    assert base.with_suffix(".lock").read_text().strip() == str(os.getpid())
    a.release()
    assert windows.calls == [(windows.LK_NBLCK, 32, 1)]
    b.release()
    assert windows.calls == [(windows.LK_NBLCK, 32, 1), (windows.LK_UNLCK, 32, 1)]
    assert base.with_suffix(".lock").read_text().strip() == ""
    assert base.with_suffix(".lock").exists()


def test_windows_other_errors_skip(
    windows: FakeMsvcrt, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    windows.error = errno.EBADF
    with caplog.at_level("DEBUG"):
        _writer_lock.hold_writer_lock(tmp_path / "emb", backend="test").release()
    assert any(
        r.msg.startswith("writer_lock_skipped") and r.args[1] == "unwritable"
        for r in caplog.records
    )


def test_windows_read_only_file_can_lock(
    windows: FakeMsvcrt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "emb.lock"
    lock.write_text("")
    monkeypatch.setattr(
        _writer_lock, "_open_lock_file", lambda _path: os.open(lock, os.O_RDONLY)
    )
    share = _writer_lock.hold_writer_lock(tmp_path / "emb", backend="test")
    share.release()
    assert windows.calls == [(windows.LK_NBLCK, 32, 1), (windows.LK_UNLCK, 32, 1)]
