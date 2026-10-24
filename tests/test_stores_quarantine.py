"""A corrupt built-in store's files are moved aside, never deleted (#1505 child 9)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import StoreUnavailableError
from markdown_vault_mcp.stores._quarantine import move_aside
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend

if TYPE_CHECKING:
    from pathlib import Path

_NOON = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
_STAMP = "corrupt-20261024T120000Z"


def _touch(*paths: Path) -> None:
    for path in paths:
        path.write_bytes(path.name.encode())


def _names(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()}


def test_numpy_quarantine_moves_its_own_files_only(tmp_path: Path) -> None:
    base = tmp_path / "emb"
    own = (tmp_path / "emb.v2.json", tmp_path / "emb.v2.m3.npy")
    kept = (tmp_path / "emb.json", tmp_path / "emb.npy", tmp_path / "emb.lock")
    _touch(*own, *kept)
    moved = NumpySidecarBackend().quarantine(base, clock=lambda: _NOON)
    assert sorted(p.name for p in moved) == [
        f"emb.v2.json.{_STAMP}",
        f"emb.v2.m3.npy.{_STAMP}",
    ]
    assert _names(tmp_path) == {p.name for p in kept} | {p.name for p in moved}
    assert (tmp_path / f"emb.v2.json.{_STAMP}").read_bytes() == b"emb.v2.json"


def test_sqlite_quarantine_moves_the_database_and_its_wal(tmp_path: Path) -> None:
    base = tmp_path / "emb"
    own = (
        tmp_path / "emb.sqlite3",
        tmp_path / "emb.sqlite3-wal",
        tmp_path / "emb.sqlite3-shm",
    )
    _touch(*own, tmp_path / "emb.lock")
    moved = SqliteVectorBackend().quarantine(base, clock=lambda: _NOON)
    assert sorted(p.name for p in moved) == sorted(f"{p.name}.{_STAMP}" for p in own)
    assert _names(tmp_path) == {"emb.lock"} | {p.name for p in moved}


def test_sqlite_quarantine_moves_whichever_files_exist(tmp_path: Path) -> None:
    _touch(tmp_path / "emb.sqlite3")
    moved = SqliteVectorBackend().quarantine(tmp_path / "emb", clock=lambda: _NOON)
    assert [p.name for p in moved] == [f"emb.sqlite3.{_STAMP}"]


def test_nothing_to_move_moves_nothing(tmp_path: Path) -> None:
    assert NumpySidecarBackend().quarantine(tmp_path / "emb") == []
    assert SqliteVectorBackend().quarantine(":memory:") == []
    assert NumpySidecarBackend().quarantine(":memory:") == []


def test_two_quarantines_in_one_second_keep_both(tmp_path: Path) -> None:
    backend = NumpySidecarBackend()
    _touch(tmp_path / "emb.v2.json")
    backend.quarantine(tmp_path / "emb", clock=lambda: _NOON)
    (tmp_path / "emb.v2.json").write_bytes(b"second")
    moved = backend.quarantine(tmp_path / "emb", clock=lambda: _NOON)
    assert [p.name for p in moved] == [f"emb.v2.json.{_STAMP}-2"]
    assert (tmp_path / f"emb.v2.json.{_STAMP}").read_bytes() == b"emb.v2.json"
    assert moved[0].read_bytes() == b"second"


def test_a_clash_on_any_file_moves_the_whole_set_to_one_suffix(tmp_path: Path) -> None:
    own = (tmp_path / "emb.v2.json", tmp_path / "emb.v2.m1.npy")
    _touch(*own)
    (tmp_path / f"emb.v2.m1.npy.{_STAMP}").write_bytes(b"earlier")
    moved = move_aside(own, backend="numpy", location="emb", clock=lambda: _NOON)
    assert sorted(p.name for p in moved) == [
        f"emb.v2.json.{_STAMP}-2",
        f"emb.v2.m1.npy.{_STAMP}-2",
    ]


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions"
)
def test_a_refused_rename_is_unavailable(tmp_path: Path) -> None:
    own = (tmp_path / "emb.v2.json", tmp_path / "emb.v2.m1.npy")
    _touch(*own)
    before = _names(tmp_path)
    tmp_path.chmod(0o555)
    try:
        with pytest.raises(StoreUnavailableError) as raised:
            move_aside(own, backend="numpy", location="emb")
    finally:
        tmp_path.chmod(0o755)
    assert raised.value.backend == "numpy"
    assert _names(tmp_path) == before


def test_a_rename_failing_midway_puts_the_first_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path as RealPath

    own = (tmp_path / "emb.v2.json", tmp_path / "emb.v2.m1.npy")
    _touch(*own)
    before = _names(tmp_path)
    real = RealPath.rename

    def flaky(self: RealPath, target: RealPath) -> RealPath:
        if self.name == "emb.v2.m1.npy":
            raise PermissionError("refused")
        return real(self, target)

    monkeypatch.setattr(RealPath, "rename", flaky)
    with pytest.raises(StoreUnavailableError):
        move_aside(own, backend="numpy", location="emb")
    assert _names(tmp_path) == before


def test_the_move_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    own = (tmp_path / "emb.v2.json", tmp_path / "emb.v2.m1.npy")
    _touch(*own)
    with caplog.at_level(logging.WARNING):
        moved = move_aside(own, backend="numpy", location="emb", clock=lambda: _NOON)
    lines = [r for r in caplog.records if r.msg.startswith("vector_store_moved_aside")]
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING
    location, paths = lines[0].args  # type: ignore[misc]
    assert location == "emb"
    assert paths == ",".join(str(p) for p in moved)


def test_an_undo_that_fails_is_logged_and_still_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from pathlib import Path as RealPath

    own = (tmp_path / "emb.v2.json", tmp_path / "emb.v2.m1.npy")
    _touch(*own)
    real = RealPath.rename

    def flaky(self: RealPath, target: RealPath) -> RealPath:
        if self.name == "emb.v2.m1.npy" or ".corrupt-" in self.name:
            raise PermissionError("refused")
        return real(self, target)

    monkeypatch.setattr(RealPath, "rename", flaky)
    with caplog.at_level(logging.WARNING), pytest.raises(StoreUnavailableError):
        move_aside(own, backend="numpy", location="emb")
    assert any(
        r.msg.startswith("vector_store_move_aside_undo_failed") for r in caplog.records
    )


def test_a_refused_stat_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.stores import _quarantine

    def refused(_path: Path) -> bool:
        raise PermissionError("refused")

    monkeypatch.setattr(_quarantine, "path_exists", refused)
    with pytest.raises(StoreUnavailableError, match="cannot be moved aside"):
        move_aside([tmp_path / "emb.v2.json"], backend="numpy", location="emb")
