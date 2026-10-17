"""The SQLite vector store under kills, failed commits and sustained edits (#1497)."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING

import numpy as np
import pytest

from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from markdown_vault_mcp.types import DocumentRows, EmbeddingIdentity, VectorRow
from tests.fixtures.vector_store_contract import IDENT, doc

if TYPE_CHECKING:
    from pathlib import Path

_CHILD = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from markdown_vault_mcp.stores import sqlite_vectors as sv
    from markdown_vault_mcp.types import DocumentRows, EmbeddingIdentity, VectorRow

    base, op, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    ident = EmbeddingIdentity("fake", "m", "", "v1", 3)
    other = EmbeddingIdentity("fake", "m2", "", "v1", 2)
    real = sv._write_meta

    def die_before_commit(conn, identity, generation):
        real(conn, identity, generation)
        os._exit(3)

    if point == "before":
        sv._write_meta = die_before_commit
    backend = sv.SqliteVectorBackend()
    if op == "model-change":
        store = backend.open_fresh(base, other)
        row = VectorRow(vector=[0.0, 1.0], metadata={"path": "new.md"})
        store.publish_generation(other, [DocumentRows("new.md", "tn", [row])])
    else:
        store = backend.open(base, ident)
        row = VectorRow(vector=[0.0, 1.0, 0.0], metadata={"path": "new.md"})
        if op == "generation":
            store.publish_generation(ident, [DocumentRows("new.md", "tn", [row])])
        else:
            mine = VectorRow(vector=[0.0, 1.0, 0.0], metadata={"path": "old.md"})
            store.replace_document(
                DocumentRows("old.md", "t2", [mine]), expected_token="t1"
            )
    os._exit(3)
    """
)


def _base(tmp_path: Path) -> Path:
    return tmp_path / "vectors" / "emb"


def _seeded(tmp_path: Path) -> Path:
    base = _base(tmp_path)
    store = SqliteVectorBackend().open(base, IDENT)
    store.replace_document(doc("old.md", [[1, 0, 0]]), expected_token=None)
    store.close()
    return base


def _kill(base: Path, op: str, point: str) -> None:
    done = subprocess.run(
        [sys.executable, "-c", _CHILD, str(base), op, point], check=False
    )
    assert done.returncode == 3


def _intact(base: Path) -> bool:
    conn = sqlite3.connect(base.with_suffix(".sqlite3"))
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


class TestKills:
    def test_a_kill_before_a_generation_commits_keeps_the_previous_one(
        self, tmp_path: Path
    ) -> None:
        base = _seeded(tmp_path)
        _kill(base, "generation", "before")
        assert _intact(base)
        store = SqliteVectorBackend().open(base, IDENT)
        assert (store.generation, store.tokens()) == (0, {"old.md": "t1"})
        store.close()

    def test_a_generation_committed_then_killed_without_close_is_kept(
        self, tmp_path: Path
    ) -> None:
        base = _seeded(tmp_path)
        _kill(base, "generation", "after")
        assert _intact(base)
        store = SqliteVectorBackend().open(base, IDENT)
        assert (store.generation, store.tokens()) == (1, {"new.md": "tn"})
        store.close()

    def test_a_kill_before_a_replace_commits_keeps_the_old_rows(
        self, tmp_path: Path
    ) -> None:
        base = _seeded(tmp_path)
        _kill(base, "replace", "before")
        store = SqliteVectorBackend().open(base, IDENT)
        assert store.tokens() == {"old.md": "t1"}
        assert store.rows_for("old.md")[0].vector == pytest.approx([1, 0, 0])
        store.close()

    def test_a_replace_committed_then_killed_without_close_is_kept(
        self, tmp_path: Path
    ) -> None:
        base = _seeded(tmp_path)
        _kill(base, "replace", "after")
        store = SqliteVectorBackend().open(base, IDENT)
        assert store.tokens() == {"old.md": "t2"}
        store.close()

    def test_a_kill_during_a_model_change_keeps_the_old_model(
        self, tmp_path: Path
    ) -> None:
        base = _seeded(tmp_path)
        _kill(base, "model-change", "before")
        assert _intact(base)
        store = SqliteVectorBackend().open(base, IDENT)
        assert (store.identity, store.tokens()) == (IDENT, {"old.md": "t1"})
        store.close()


class TestFailedCommits:
    def _refusing(self, tmp_path: Path) -> Path:
        base = _seeded(tmp_path)
        conn = sqlite3.connect(base.with_suffix(".sqlite3"))
        conn.execute(
            "CREATE TRIGGER refuse BEFORE INSERT ON chunks "
            "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
        )
        conn.commit()
        conn.close()
        return base

    def test_a_failed_replace_changes_neither_the_file_nor_the_reads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        base = self._refusing(tmp_path)
        store = SqliteVectorBackend().open(base, IDENT)
        document = doc("old.md", [[0, 1, 0]], token="t2")
        with (
            caplog.at_level(logging.WARNING),
            pytest.raises(sqlite3.IntegrityError, match="disk full"),
        ):
            store.replace_document(document, expected_token="t1")
        assert store.tokens() == {"old.md": "t1"}
        assert store.rows_for("old.md")[0].vector == pytest.approx([1, 0, 0])
        assert any(
            r.msg.startswith("sqlite_vector_store_write_failed") for r in caplog.records
        )
        store.close()
        again = SqliteVectorBackend().open(base, IDENT)
        assert again.tokens() == {"old.md": "t1"}
        again.close()

    def test_a_failed_generation_keeps_the_previous_one(self, tmp_path: Path) -> None:
        base = self._refusing(tmp_path)
        store = SqliteVectorBackend().open(base, IDENT)
        documents = [doc("new.md", [[0, 1, 0]])]
        with pytest.raises(sqlite3.IntegrityError):
            store.publish_generation(IDENT, documents)
        assert (store.generation, store.tokens()) == (0, {"old.md": "t1"})
        store.close()


_WIDE = EmbeddingIdentity("fake", "m", "", "v1", 1024)


def _one_update_wal_bytes(base: Path, documents: int) -> tuple[int, int]:
    """Return (WAL bytes one replace adds, database file bytes) for a store of *documents*."""
    rng = np.random.default_rng(7)

    def row() -> VectorRow:
        return VectorRow(rng.standard_normal(1024).tolist(), {"content": "x" * 200})

    store = SqliteVectorBackend().open(base, _WIDE)
    store.publish_generation(
        _WIDE,
        [DocumentRows(f"n{i}.md", "t0", [row()]) for i in range(documents)],
    )
    store.close()
    db = base.with_suffix(".sqlite3")
    wal = db.with_name(db.name + "-wal")
    store = SqliteVectorBackend().open(base, _WIDE)
    before = wal.stat().st_size if wal.exists() else 0
    store.replace_document(DocumentRows("n0.md", "t1", [row()]), expected_token="t0")
    grown = wal.stat().st_size - before
    store.close()
    return grown, db.stat().st_size


def test_one_update_writes_about_the_same_on_a_small_and_a_large_store(
    tmp_path: Path,
) -> None:
    small, _ = _one_update_wal_bytes(tmp_path / "small" / "emb", 10)
    large, large_file = _one_update_wal_bytes(tmp_path / "large" / "emb", 3000)
    assert small > 0
    # A deeper B-tree on the large store touches a few more interior pages.
    assert large <= small + 12 * (4096 + 24)
    assert large < large_file / 100
