"""Tests for SQLITE_LOCKED retry wrapper in FTSIndex (#560)."""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

import pytest

from markdown_vault_mcp.fts_index import (
    FTSIndex,
    _retry_on_sqlite_locked,
)


class TestRetryOnSqliteLocked:
    """Unit tests for the retry helper."""

    def test_passthrough_on_success(self) -> None:
        """No locked error → operation runs once and returns."""
        calls = [0]

        def op() -> str:
            calls[0] += 1
            return "ok"

        result = _retry_on_sqlite_locked(op, timeout=1.0)
        assert result == "ok"
        assert calls[0] == 1

    def test_retries_on_locked_then_succeeds(self) -> None:
        """Operation fails twice with 'locked', third call succeeds."""
        calls = [0]

        def op() -> str:
            calls[0] += 1
            if calls[0] < 3:
                raise sqlite3.OperationalError("database table is locked: documents")
            return "ok"

        result = _retry_on_sqlite_locked(op, timeout=1.0)
        assert result == "ok"
        assert calls[0] == 3

    def test_passes_through_non_locked_operational_error(self) -> None:
        """Other OperationalErrors propagate immediately without retry."""
        calls = [0]

        def op() -> None:
            calls[0] += 1
            raise sqlite3.OperationalError("syntax error")

        with pytest.raises(sqlite3.OperationalError, match="syntax error"):
            _retry_on_sqlite_locked(op, timeout=1.0)
        assert calls[0] == 1

    def test_raises_after_timeout(self) -> None:
        """Operation that keeps raising 'locked' eventually times out."""
        calls = [0]

        def op() -> None:
            calls[0] += 1
            raise sqlite3.OperationalError("database table is locked: documents")

        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _retry_on_sqlite_locked(op, timeout=0.1)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1
        # Some retries happened (10ms initial sleep doubles to 20, 40, 80...).
        assert calls[0] >= 2

    def test_does_not_overshoot_timeout(self) -> None:
        """The retry loop must not sleep past the declared timeout.

        With max_sleep=500ms, an earlier implementation could check the
        deadline at e.g. 4.999s, then sleep the full 500ms, waking at
        5.5s — busting a 5s timeout by half a second. Cap the sleep to
        the remaining budget so the deadline is honoured.
        """
        calls = [0]

        def op() -> None:
            calls[0] += 1
            raise sqlite3.OperationalError("database table is locked: documents")

        # Use a tiny timeout so the test is fast but the overshoot
        # window matters relative to it. With exponential backoff
        # starting at 10ms, by the 5th retry sleep would be 160ms —
        # well past a 50ms timeout if not capped.
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _retry_on_sqlite_locked(op, timeout=0.05)
        elapsed = time.monotonic() - start
        # Allow a small fudge for scheduler latency, but the overshoot
        # without the cap was ~MAX_SLEEP_S (500ms). With the cap, the
        # last sleep is at most `remaining`, so total elapsed should
        # be within roughly 2x the timeout (the timeout itself plus
        # the post-deadline check overhead).
        assert elapsed < 0.15, (
            f"retry overshot timeout: elapsed={elapsed:.3f}s, "
            f"timeout=0.05s, calls={calls[0]}"
        )


class _FailingCursor:
    """Wrap a real sqlite3.Cursor, raising SQLITE_LOCKED once per matched SQL."""

    def __init__(self, real_cursor, call_state: dict, match: str) -> None:
        self._real = real_cursor
        self._state = call_state
        self._match = match.upper()

    def execute(self, sql: str, *params):  # type: ignore[no-untyped-def]
        if not self._state["failed"] and self._match in sql.upper():
            self._state["failed"] = True
            raise sqlite3.OperationalError("database table is locked: documents")
        return self._real.execute(sql, *params)

    def executemany(self, sql, seq):  # type: ignore[no-untyped-def]
        return self._real.executemany(sql, seq)

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self._real.fetchone()

    def fetchall(self):  # type: ignore[no-untyped-def]
        return self._real.fetchall()

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._real)

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self._real, name)


class _WrappedConnection:
    """Wrap a real sqlite3.Connection so cursor()/execute() can be intercepted."""

    def __init__(self, real_conn, call_state: dict, match: str) -> None:
        self._real = real_conn
        self._state = call_state
        self._match = match.upper()

    def cursor(self):  # type: ignore[no-untyped-def]
        return _FailingCursor(self._real.cursor(), self._state, self._match)

    def execute(self, sql, *params):  # type: ignore[no-untyped-def]
        # Mirror the cursor.execute() interception path for code that uses
        # Connection.execute() directly (e.g. is_build_completed,
        # set_build_completed, get_note, list_notes, search, ...).
        if not self._state["failed"] and self._match in sql.upper():
            self._state["failed"] = True
            raise sqlite3.OperationalError("database table is locked: documents")
        return self._real.execute(sql, *params)

    def executemany(self, sql, seq):  # type: ignore[no-untyped-def]
        return self._real.executemany(sql, seq)

    def __enter__(self):  # type: ignore[no-untyped-def]
        self._real.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        return self._real.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self._real, name)


class TestFTSIndexMethodRetry:
    """End-to-end: FTSIndex methods recover from one SQLITE_LOCKED error."""

    def test_upsert_note_retries(self, tmp_path) -> None:
        """upsert_note recovers transparently from a single SQLITE_LOCKED."""
        from markdown_vault_mcp.scanner import HeadingChunker
        from markdown_vault_mcp.scanner import parse_note as _parse

        # Set up a tiny vault and FTSIndex.
        (tmp_path / "a.md").write_text(
            "---\ntitle: A\n---\n# A\n\nbody\n", encoding="utf-8"
        )
        fts = FTSIndex(db_path=tmp_path / "fts.db")
        try:
            note = _parse(tmp_path / "a.md", tmp_path, HeadingChunker())

            # Wrap _conn() so the FIRST DELETE call inside upsert_note's
            # transaction raises SQLITE_LOCKED. The retry decorator
            # rolls back the with-block and re-invokes upsert_note,
            # which then proceeds to completion (call_state["failed"]
            # is now True so subsequent DELETEs pass through).
            call_state = {"failed": False}
            real_conn_method = fts._conn

            def wrapped_conn():  # type: ignore[no-untyped-def]
                return _WrappedConnection(real_conn_method(), call_state, "DELETE")

            with patch.object(fts, "_conn", wrapped_conn):
                fts.upsert_note(note)
            assert call_state["failed"], "patched DELETE should have fired"

            # Verify the row landed.
            row = fts.get_note("a.md")
            assert row is not None
            assert row["title"] == "A"
        finally:
            fts.close()

    def test_get_note_retries(self, tmp_path) -> None:
        """Read path retries on SQLITE_LOCKED too."""
        from markdown_vault_mcp.scanner import HeadingChunker
        from markdown_vault_mcp.scanner import parse_note as _parse

        (tmp_path / "a.md").write_text(
            "---\ntitle: A\n---\n# A\n\nbody\n", encoding="utf-8"
        )
        fts = FTSIndex(db_path=tmp_path / "fts.db")
        try:
            note = _parse(tmp_path / "a.md", tmp_path, HeadingChunker())
            fts.upsert_note(note)

            call_state = {"failed": False}
            real_conn_method = fts._conn

            def wrapped_conn():  # type: ignore[no-untyped-def]
                return _WrappedConnection(real_conn_method(), call_state, "SELECT")

            with patch.object(fts, "_conn", wrapped_conn):
                row = fts.get_note("a.md")
            assert call_state["failed"], "patched SELECT should have fired"
            assert row is not None
            assert row["title"] == "A"
        finally:
            fts.close()

    def test_build_from_notes_retries_with_generator_input(self, tmp_path) -> None:
        """build_from_notes must materialise the iterable so the
        @_retry_on_locked decorator can re-invoke the method body on a
        SQLITE_LOCKED without seeing an exhausted generator (#560)."""
        from markdown_vault_mcp.scanner import HeadingChunker
        from markdown_vault_mcp.scanner import parse_note as _parse

        # Two notes; pass them as a single-use generator.
        for path in ("a.md", "b.md"):
            (tmp_path / path).write_text(
                f"---\ntitle: {path}\n---\n# {path}\n\nbody\n", encoding="utf-8"
            )

        fts = FTSIndex(db_path=tmp_path / "fts.db")
        try:
            chunker = HeadingChunker()

            def _gen():
                yield _parse(tmp_path / "a.md", tmp_path, chunker)
                yield _parse(tmp_path / "b.md", tmp_path, chunker)

            call_state = {"failed": False}
            real_conn_method = fts._conn

            def wrapped_conn():  # type: ignore[no-untyped-def]
                return _WrappedConnection(real_conn_method(), call_state, "DELETE")

            with patch.object(fts, "_conn", wrapped_conn):
                total = fts.build_from_notes(_gen())

            assert call_state["failed"], "patched DELETE should have fired"
            # Both notes must be present after retry; if the generator
            # had been re-consumed empty on retry, build_from_notes
            # would silently return 0 / 1 instead of indexing both.
            assert total >= 2
            assert fts.get_note("a.md") is not None
            assert fts.get_note("b.md") is not None
        finally:
            fts.close()

    def test_get_chunk_counts_retries_with_generator_input(self, tmp_path) -> None:
        """get_chunk_counts must materialise the iterable before the retry
        window so a one-shot generator survives a SQLITE_LOCKED retry (#560).

        Same class of bug as build_from_notes: the @_retry_on_locked
        decorator captures the original argument tuple and re-invokes
        with the now-exhausted generator. The inner-function pattern
        materialises the list once and closes over it.
        """
        from markdown_vault_mcp.scanner import HeadingChunker
        from markdown_vault_mcp.scanner import parse_note as _parse

        for path in ("a.md", "b.md"):
            (tmp_path / path).write_text(
                f"---\ntitle: {path}\n---\n# {path}\n\nbody\n", encoding="utf-8"
            )

        fts = FTSIndex(db_path=tmp_path / "fts.db")
        try:
            chunker = HeadingChunker()
            fts.upsert_note(_parse(tmp_path / "a.md", tmp_path, chunker))
            fts.upsert_note(_parse(tmp_path / "b.md", tmp_path, chunker))

            def _gen():
                yield "a.md"
                yield "b.md"

            call_state = {"failed": False}
            real_conn_method = fts._conn

            def wrapped_conn():  # type: ignore[no-untyped-def]
                return _WrappedConnection(real_conn_method(), call_state, "SELECT")

            with patch.object(fts, "_conn", wrapped_conn):
                result = fts.get_chunk_counts(_gen())

            assert call_state["failed"], "patched SELECT should have fired"
            # If the generator had been exhausted on retry, result would
            # be {} (paths_list==[] in inner method → early return).
            assert set(result.keys()) == {"a.md", "b.md"}
        finally:
            fts.close()
