"""Exercise the Windows writer-lock API through a fake on POSIX."""

from __future__ import annotations

import errno
import ntpath
import os
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import StoreBusyError
from markdown_vault_mcp.stores import _writer_lock
from tests.fixtures.windows_writer_lock import FakeMsvcrt

if TYPE_CHECKING:
    from pathlib import Path


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


def test_windows_lock_error_mapping(
    windows: FakeMsvcrt, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base = tmp_path / "emb"
    base.with_suffix(".lock").write_bytes(b"123456789\n".ljust(32, b" "))
    windows.error = errno.EACCES
    with pytest.raises(StoreBusyError, match="pid 123456789"):
        _writer_lock.hold_writer_lock(base, backend="test")
    assert windows.calls == [(windows.LK_NBLCK, 32, 1)]

    windows.calls.clear()
    windows.error = errno.EBADF
    with caplog.at_level("DEBUG"):
        _writer_lock.hold_writer_lock(base, backend="test").release()
    assert windows.calls == [(windows.LK_NBLCK, 32, 1)]
    assert any(
        r.msg.startswith("writer_lock_skipped") and r.args[1] == "unwritable"
        for r in caplog.records
    )


@pytest.mark.parametrize("same_file", [False, True])
def test_windows_refcounts_and_unlocks_same_range(
    windows: FakeMsvcrt,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_file: bool,
) -> None:
    base = tmp_path / "EMB"
    other = tmp_path / "emb"
    # Model Windows canonical names independently of the host filesystem.
    monkeypatch.setattr(_writer_lock.os.path, "normcase", ntpath.normcase)
    monkeypatch.setattr(
        _writer_lock.os.path,
        "realpath",
        lambda path: str(base.with_suffix(".lock") if same_file else path),
    )
    lock = base.with_suffix(".lock")
    first = _writer_lock.hold_writer_lock(base, backend="test")
    second = _writer_lock.hold_writer_lock(other, backend="test")
    count = 1 if same_file else 2
    try:
        pid = lock.read_bytes()
        assert len(pid) == 32
        assert pid.strip() == str(os.getpid()).encode()
        assert windows.calls == [(windows.LK_NBLCK, 32, 1)] * count
        first.release()
        assert windows.calls == [(windows.LK_NBLCK, 32, 1)] * count + (
            [] if same_file else [(windows.LK_UNLCK, 32, 1)]
        )
    finally:
        first.release()
        second.release()
    assert (
        windows.calls
        == [(windows.LK_NBLCK, 32, 1)] * count + [(windows.LK_UNLCK, 32, 1)] * count
    )
    assert lock.read_bytes() == b" " * 32
