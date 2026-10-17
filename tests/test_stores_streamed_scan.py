"""The bounded exact scan the SQLite vector store streams with (#1377)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import numpy as np
import pytest

from markdown_vault_mcp.stores import _streamed
from markdown_vault_mcp.stores._streamed import (
    DEFAULT_WORKSPACE_BYTES,
    folder_params,
    rows_per_batch,
    scan,
)
from markdown_vault_mcp.utils import folder_matches

if TYPE_CHECKING:
    from collections.abc import Iterator


def _blob(v: list[float]) -> bytes:
    return np.asarray(v, dtype="<f4").tobytes()


@pytest.fixture
def table() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, vector BLOB, folder TEXT)")
    yield conn
    conn.close()


def _fill(conn: sqlite3.Connection, vectors: np.ndarray) -> None:
    conn.executemany(
        "INSERT INTO t(id, vector, folder) VALUES (?, ?, '')",
        [(i + 1, v.astype("<f4").tobytes()) for i, v in enumerate(vectors)],
    )


class TestRowsPerBatch:
    def test_the_default_is_256_rows_at_1024_dimensions(self) -> None:
        assert rows_per_batch(DEFAULT_WORKSPACE_BYTES, 1024) == 256

    def test_a_workspace_below_one_row_still_reads_one(self) -> None:
        assert rows_per_batch(1, 3072) == 1

    def test_an_unknown_dimension_does_not_divide_by_zero(self) -> None:
        assert rows_per_batch(4096, 0) == 1024


class TestScan:
    @pytest.mark.parametrize(
        ("batch_rows", "limit"), [(1, 1), (3, 5), (7, 50), (64, 10)]
    )
    def test_matches_a_full_sort(
        self, table: sqlite3.Connection, batch_rows: int, limit: int
    ) -> None:
        rng = np.random.default_rng(1377)
        vectors = rng.normal(size=(40, 8)).astype(np.float32)
        _fill(table, vectors)
        query = rng.normal(size=8).astype(np.float32)
        got = scan(
            table.execute("SELECT id, vector FROM t"),
            query,
            limit=limit,
            batch_rows=batch_rows,
            check_blob=lambda b: b,  # type: ignore[arg-type,return-value]
        )
        scores = vectors @ query
        want = [int(i) + 1 for i in np.argsort(-scores)[:limit]]
        assert [rowid for rowid, _ in got] == want
        # float32 dot products differ in the last bits between matrix shapes.
        assert [s for _, s in got] == pytest.approx(
            [float(scores[i - 1]) for i in want], abs=1e-5
        )

    def test_keep_runs_before_each_batch_is_cut(
        self, table: sqlite3.Connection
    ) -> None:
        # Every kept row scores below every dropped one.
        vectors = np.array(
            [[1.0, 0]] * 20 + [[0.1 * i, 1] for i in range(5)], np.float32
        )
        _fill(table, vectors)
        got = scan(
            table.execute("SELECT id, vector FROM t"),
            np.array([1, 0], np.float32),
            limit=5,
            batch_rows=4,
            check_blob=lambda b: b,  # type: ignore[arg-type,return-value]
            keep=lambda row: row[0] > 20,
        )
        assert [rowid for rowid, _ in got] == [25, 24, 23, 22, 21]

    def test_holds_one_batch_and_limit_rows_at_most(
        self, table: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rng = np.random.default_rng(7)
        _fill(table, rng.normal(size=(100, 4)).astype(np.float32))
        seen: list[tuple[int, int]] = []
        real = _streamed.merged

        def recording(best_ids, best_scores, ids, scores, limit):  # type: ignore[no-untyped-def]
            out = real(best_ids, best_scores, ids, scores, limit)
            seen.append((len(ids), len(out[0])))
            return out

        monkeypatch.setattr(_streamed, "merged", recording)
        scan(
            table.execute("SELECT id, vector FROM t"),
            np.ones(4, np.float32),
            limit=6,
            batch_rows=9,
            check_blob=lambda b: b,  # type: ignore[arg-type,return-value]
        )
        assert len(seen) == 12  # ceil(100 / 9)
        assert max(batch for batch, _ in seen) <= 9
        assert max(kept for _, kept in seen) <= 6

    def test_an_empty_cursor_returns_nothing(self, table: sqlite3.Connection) -> None:
        got = scan(
            table.execute("SELECT id, vector FROM t"),
            np.ones(2, np.float32),
            limit=3,
            batch_rows=2,
            check_blob=lambda b: b,  # type: ignore[arg-type,return-value]
        )
        assert got == []

    def test_check_blob_sees_every_vector(self, table: sqlite3.Connection) -> None:
        _fill(table, np.ones((3, 2), np.float32))

        def refuse(_blob: object) -> bytes:
            raise ValueError("bad width")

        cursor = table.execute("SELECT id, vector FROM t")
        query = np.ones(2, np.float32)
        with pytest.raises(ValueError, match="bad width"):
            scan(cursor, query, limit=1, batch_rows=2, check_blob=refuse)


class TestFolderParams:
    @pytest.mark.parametrize(
        "folder",
        ["x", "", "a_b", "50%", "x/y", "X", "a\\b"],
    )
    def test_sql_agrees_with_folder_matches(
        self, table: sqlite3.Connection, folder: str
    ) -> None:
        stored = [
            "x",
            "x/y",
            "xy",
            "X",
            "X/y",
            "",
            "a_b",
            "axb",
            "a_b/c",
            "50%",
            "50x/y",
            "50%/z",
            "a\\b",
            "a\\bc",
            "y/x",
        ]
        table.executemany(
            "INSERT INTO t(vector, folder) VALUES (x'', ?)", [(f,) for f in stored]
        )
        rows = table.execute(
            "SELECT folder FROM t WHERE (? IS NULL OR folder = ? OR substr(folder, 1, ?) = ?)",
            folder_params(folder),
        ).fetchall()
        assert sorted(r[0] for r in rows) == sorted(
            f for f in stored if folder_matches(f, folder)
        )

    def test_none_selects_everything(self, table: sqlite3.Connection) -> None:
        table.executemany(
            "INSERT INTO t(vector, folder) VALUES (x'', ?)", [("a",), ("",)]
        )
        rows = table.execute(
            "SELECT folder FROM t WHERE (? IS NULL OR folder = ? OR substr(folder, 1, ?) = ?)",
            folder_params(None),
        ).fetchall()
        assert len(rows) == 2
