"""Closing the vault never closes the keyword index under a running writer (#1896)."""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

import pytest

import markdown_vault_mcp.indexing.coordinator as coordinator_mod
import markdown_vault_mcp.vault as vault_mod
from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.indexing import ProcessDirtyPaths
from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from pathlib import Path


def _vault(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    return Vault(
        source_dir=root,
        settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
    )


def _skipped(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.msg.startswith("index_close_skipped reason=writer_running")
    ]


def test_close_leaves_the_index_open_while_its_writer_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    real = coordinator_mod.run_process_dirty_paths

    def blocked(job: ProcessDirtyPaths, ctx: Any) -> None:
        entered.set()
        release.wait(timeout=30)
        real(job, ctx)

    monkeypatch.setattr(coordinator_mod, "run_process_dirty_paths", blocked)
    vault = _vault(tmp_path)
    fts = vault._fts
    slot_closes: list[None] = []
    real_slot_close = vault._vector_slot.close

    def counted_slot_close() -> None:
        slot_closes.append(None)
        real_slot_close()

    monkeypatch.setattr(vault._vector_slot, "close", counted_slot_close)
    writer = vault._coordinator.writer
    (tmp_path / "vault" / "late.md").write_text("# Late\n\nWritten during close.\n")
    writer.mark_dirty({"late.md"})
    job = writer.submit(ProcessDirtyPaths())
    assert entered.wait(timeout=10)
    monkeypatch.setattr(vault_mod, "_WRITER_DRAIN_TIMEOUT_S", 0.2)

    try:
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.vault"):
            vault.close()
        assert [r.levelno for r in _skipped(caplog)] == [logging.WARNING]
        assert slot_closes == []
        assert writer.close(timeout=0) is False
    finally:
        release.set()
    job.result(timeout=10)
    assert fts.get_note("late.md") is not None

    assert writer.close(timeout=10) is True
    caplog.clear()
    vault.close()
    assert _skipped(caplog) == []
    assert slot_closes == [None]
    with pytest.raises(sqlite3.ProgrammingError):
        fts.get_note("late.md")

    index = SqliteKeywordBackend().open(tmp_path / "index.db")
    try:
        assert index.get_note("late.md") is not None
    finally:
        index.close()


def test_close_closes_the_index_once_the_writer_stopped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    vault = _vault(tmp_path)
    fts = vault._fts
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.vault"):
        vault.close()
    assert _skipped(caplog) == []
    with pytest.raises(sqlite3.ProgrammingError):
        fts.get_note("alpha.md")
