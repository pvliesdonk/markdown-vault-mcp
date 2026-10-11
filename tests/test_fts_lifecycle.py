"""FTSIndex as a StoreLifecycle: checkpoint and the schema marker (#1766)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.interfaces import StoreLifecycle

if TYPE_CHECKING:
    from pathlib import Path


def _set_schema_version(db_path: Path, value: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (value,),
    )
    conn.commit()
    conn.close()


def _read_schema_version(db_path: Path) -> str | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    conn.close()
    return None if row is None else row[0]


class TestSchemaVersion:
    def test_a_fresh_index_records_the_current_version(self, tmp_path: Path) -> None:
        db = tmp_path / "index.db"
        FTSIndex(db_path=db).close()
        assert _read_schema_version(db) == str(FTSIndex.SCHEMA_VERSION)

    def test_an_index_without_the_marker_reads_as_current(self, tmp_path: Path) -> None:
        db = tmp_path / "index.db"
        FTSIndex(db_path=db).close()
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM meta WHERE key='schema_version'")
        conn.commit()
        conn.close()
        FTSIndex(db_path=db).close()  # a pre-#1766 index opens
        assert _read_schema_version(db) == str(FTSIndex.SCHEMA_VERSION)

    @pytest.mark.parametrize("read_only", [False, True], ids=["writable", "read_only"])
    def test_a_newer_index_is_refused(self, tmp_path: Path, read_only: bool) -> None:
        db = tmp_path / "index.db"
        FTSIndex(db_path=db).close()
        _set_schema_version(db, str(FTSIndex.SCHEMA_VERSION + 1))
        with pytest.raises(StoreUnsupportedSchemaError) as info:
            FTSIndex(db_path=db, read_only=read_only)
        assert info.value.backend == "sqlite"
        assert info.value.location == str(db)
        # Refusing must not touch the stored marker.
        assert _read_schema_version(db) == str(FTSIndex.SCHEMA_VERSION + 1)

    def test_an_older_marker_is_brought_to_the_current_version(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "index.db"
        FTSIndex(db_path=db).close()
        _set_schema_version(db, "0")
        FTSIndex(db_path=db).close()  # a writable open migrates and re-stamps
        assert _read_schema_version(db) == str(FTSIndex.SCHEMA_VERSION)

    def test_a_read_only_open_of_a_file_that_is_not_an_index_is_refused(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "empty.db"
        db.write_bytes(b"")  # SQLite opens an empty file as an empty database
        with pytest.raises(StoreUnsupportedSchemaError, match="not a built index"):
            FTSIndex(db_path=db, read_only=True)

    def test_a_garbled_marker_is_refused(self, tmp_path: Path) -> None:
        db = tmp_path / "index.db"
        FTSIndex(db_path=db).close()
        _set_schema_version(db, "not-a-number")
        with pytest.raises(StoreUnsupportedSchemaError):
            FTSIndex(db_path=db)


class TestCheckpoint:
    def test_fts_index_is_a_store_lifecycle(self, tmp_path: Path) -> None:
        index = FTSIndex(db_path=tmp_path / "index.db")
        try:
            assert isinstance(index, StoreLifecycle)
        finally:
            index.close()

    def test_checkpoint_on_a_file_index_truncates_the_wal(self, tmp_path: Path) -> None:
        db = tmp_path / "index.db"
        index = FTSIndex(db_path=db)
        try:
            index.set_build_completed()  # one committed write, so the WAL has frames
            index.checkpoint()
            wal = db.with_name("index.db-wal")
            assert not wal.exists() or wal.stat().st_size == 0
        finally:
            index.close()

    def test_checkpoint_is_a_no_op_in_memory(self) -> None:
        index = FTSIndex(":memory:")
        try:
            index.checkpoint()
        finally:
            index.close()

    def test_checkpoint_is_a_no_op_when_read_only(self, tmp_path: Path) -> None:
        db = tmp_path / "index.db"
        FTSIndex(db_path=db).close()
        index = FTSIndex(db_path=db, read_only=True)
        try:
            index.checkpoint()
        finally:
            index.close()
