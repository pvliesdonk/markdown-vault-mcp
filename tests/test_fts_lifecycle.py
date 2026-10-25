"""FTSIndex as a StoreLifecycle: checkpoint and the schema marker (#1766)."""

from __future__ import annotations

import logging
import sqlite3
import time
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


def _attach_reader(db: Path) -> sqlite3.Connection:
    """Another process's read-only connection, idle after one read."""
    reader = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    reader.execute("SELECT count(*) FROM meta").fetchone()
    return reader


def _meta_keys_of_a_copy(db: Path, folder: Path) -> set[str]:
    """Open a copy of *db* alone, without its WAL, and list its meta keys."""
    folder.mkdir()
    copy = folder / db.name
    copy.write_bytes(db.read_bytes())
    conn = sqlite3.connect(copy)
    try:
        return {row[0] for row in conn.execute("SELECT key FROM meta")}
    except sqlite3.OperationalError:  # the table itself is still in the WAL
        return set()
    finally:
        conn.close()


class TestCloseFoldsTheWal:
    """A stopped server's index.db is complete without its WAL (#1505 child 10)."""

    def test_close_with_a_reader_attached_leaves_index_db_complete(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "index.db"
        index = FTSIndex(db_path=db)
        index.set_build_completed()
        reader = _attach_reader(db)
        try:
            index.close()
            assert "build_completed_at" in _meta_keys_of_a_copy(db, tmp_path / "copy")
        finally:
            reader.close()

    def test_close_does_not_wait_for_a_reader_mid_transaction(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "index.db"
        index = FTSIndex(db_path=db)
        index.set_build_completed()
        reader = _attach_reader(db)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM meta").fetchone()
        try:
            started = time.monotonic()
            index.close()
            assert time.monotonic() - started < 1.0  # not busy_timeout's 5 s
            assert "build_completed_at" in _meta_keys_of_a_copy(db, tmp_path / "copy")
        finally:
            reader.close()

    def test_close_warns_when_frames_stay_in_the_wal(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        db = tmp_path / "index.db"
        index = FTSIndex(db_path=db)
        index.set_build_completed()
        reader = _attach_reader(db)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM meta").fetchone()  # an older snapshot
        index.set_build_completed()  # committed after the reader's snapshot
        try:
            with caplog.at_level(logging.WARNING):
                index.close()
        finally:
            reader.close()
        lines = [
            r
            for r in caplog.records
            if r.msg.startswith("fts_index_checkpoint_incomplete")
        ]
        assert len(lines) == 1
        path, frames, checkpointed = lines[0].args  # type: ignore[misc]
        assert str(path) == str(db)
        assert frames > checkpointed

    def test_a_failed_checkpoint_on_close_is_logged_and_still_closes(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        index = FTSIndex(db_path=tmp_path / "index.db")
        real = index._registry.conn

        class _Refusing:
            def __init__(self, conn: sqlite3.Connection) -> None:
                self._conn = conn

            def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
                if "wal_checkpoint" in sql:
                    raise sqlite3.OperationalError("disk I/O error")
                return self._conn.execute(sql, *args)

        index._registry.conn = lambda: _Refusing(real())  # type: ignore[assignment,return-value]
        with caplog.at_level(logging.WARNING):
            index.close()
        assert any(
            r.msg.startswith("fts_index_checkpoint_failed") for r in caplog.records
        )
        with pytest.raises(sqlite3.ProgrammingError):
            real()

    @pytest.mark.parametrize("kind", ["memory", "read_only"])
    def test_close_runs_no_checkpoint_where_it_cannot(
        self, tmp_path: Path, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        if kind == "memory":
            index = FTSIndex(":memory:")
        else:
            FTSIndex(db_path=tmp_path / "index.db").close()
            index = FTSIndex(db_path=tmp_path / "index.db", read_only=True)
        with caplog.at_level(logging.WARNING):
            index.close()
            index.close()  # idempotent
        assert not [
            r for r in caplog.records if r.msg.startswith("fts_index_checkpoint_")
        ]

    def test_closing_twice_logs_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        index = FTSIndex(db_path=tmp_path / "index.db")
        index.set_build_completed()
        with caplog.at_level(logging.WARNING):
            index.close()
            index.close()
        assert not [
            r for r in caplog.records if r.msg.startswith("fts_index_checkpoint_")
        ]

    def test_a_close_racing_another_close_logs_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        index = FTSIndex(db_path=tmp_path / "index.db")
        real = index._registry.conn

        def closed_meanwhile() -> sqlite3.Connection:
            # Another thread's close() won between the closed check and conn().
            raise sqlite3.ProgrammingError("Cannot operate on a closed FTSIndex")

        index._registry.conn = closed_meanwhile  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING):
            index.close()
        assert not [
            r for r in caplog.records if r.msg.startswith("fts_index_checkpoint_")
        ]
        with pytest.raises(sqlite3.ProgrammingError):
            real()
