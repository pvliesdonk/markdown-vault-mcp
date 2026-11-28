"""A streamed SQLite store publishes a generation as it reads it (#1938)."""

from __future__ import annotations

import math
import sqlite3
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from markdown_vault_mcp.types import DocumentRows, EmbeddingIdentity, VectorRow
from tests.fixtures.vector_store_contract import IDENT, doc

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path


def _base(tmp_path: Path, name: str = "idx-embeddings") -> Path:
    return tmp_path / "vectors" / name


def _streamed() -> SqliteVectorBackend:
    return SqliteVectorBackend(search="streamed")


def _state(store: Any) -> tuple[int, int, dict[str, Any], list[list[float]]]:
    return (
        store.generation,
        store.count,
        store.tokens(),
        [list(row.vector) for row in store.rows_for("a.md")],
    )


def _seeded(tmp_path: Path) -> Any:
    store = _streamed().open(_base(tmp_path), IDENT)
    store.publish_generation(
        IDENT, [doc("a.md", [[1, 0, 0]], token="ta"), doc("b.md", [[0, 1, 0]])]
    )
    return store


def _then_failing(documents: Iterable[DocumentRows]) -> Iterator[DocumentRows]:
    yield from documents
    raise OSError("the staged generation could not be read")


_FAILURES: dict[str, Any] = {
    "a later document is not finite": lambda: [
        doc("c.md", [[1, 1, 0]]),
        doc("d.md", [[math.nan, 0, 0]]),
    ],
    "a later document repeats a path": lambda: [
        doc("c.md", [[1, 1, 0]]),
        doc("c.md", [[0, 1, 1]]),
    ],
    "a later document is of another dimension": lambda: [
        doc("c.md", [[1, 1, 0]]),
        doc("d.md", [[1, 0]]),
    ],
    "reading the documents fails": lambda: _then_failing([doc("c.md", [[1, 1, 0]])]),
}
_ERRORS = {
    "a later document is not finite": ValueError,
    "a later document repeats a path": ValueError,
    "a later document is of another dimension": ValueError,
    "reading the documents fails": OSError,
}


class TestAFailureMidStream:
    @pytest.mark.parametrize("case", sorted(_FAILURES))
    def test_leaves_the_previous_generation_and_rows(
        self, tmp_path: Path, case: str
    ) -> None:
        store = _seeded(tmp_path)
        before = _state(store)
        with pytest.raises(_ERRORS[case]):
            store.publish_generation(IDENT, _FAILURES[case]())
        assert _state(store) == before
        store.close()
        again = _streamed().open(_base(tmp_path), IDENT)
        assert _state(again) == before
        again.close()

    def test_on_a_first_write_leaves_a_database_that_opens_empty(
        self, tmp_path: Path
    ) -> None:
        store = _streamed().open(_base(tmp_path), IDENT)
        with pytest.raises(ValueError, match="twice"):
            store.publish_generation(
                IDENT, _FAILURES["a later document repeats a path"]()
            )
        assert (store.generation, store.count, store.tokens()) == (0, 0, {})
        store.close()
        assert _base(tmp_path).with_suffix(".sqlite3").is_file()
        again = _streamed().open(_base(tmp_path), IDENT)
        assert (again.generation, again.count, again.tokens()) == (0, 0, {})
        again.publish_generation(IDENT, [doc("a.md", [[1, 0, 0]])])
        assert (again.generation, again.count) == (1, 1)
        again.close()

    def test_into_a_fresh_store_keeps_the_rows_it_would_replace(
        self, tmp_path: Path
    ) -> None:
        _seeded(tmp_path).close()
        other = EmbeddingIdentity("fake", "m2", "", "v1", 2)
        fresh = _streamed().open_fresh(_base(tmp_path), other)
        with pytest.raises(OSError, match="staged"):
            fresh.publish_generation(other, _then_failing([doc("n.md", [[1, 0]])]))
        assert fresh.count == 0
        fresh.close()
        again = _streamed().open(_base(tmp_path), IDENT)
        assert again.tokens() == {"a.md": "ta", "b.md": "t1"}
        again.close()


class TestStreaming:
    def test_each_document_is_written_before_the_next_is_read(
        self, tmp_path: Path
    ) -> None:
        store = _streamed().open(_base(tmp_path), IDENT)
        store.replace_document(doc("old.md", [[1, 0, 0]]), expected_token=None)
        written: list[int] = []

        def documents() -> Iterator[DocumentRows]:
            for i in range(3):
                # The writer's own connection sees its uncommitted rows.
                conn = store._registry.conn()
                written.append(
                    conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
                )
                yield doc(f"n{i}.md", [[1, 0, 0], [0, 1, 0]])

        store.publish_generation(IDENT, documents())
        assert written == [0, 2, 4]
        assert store.count == 6
        store.close()


def _documents(seed: int) -> list[DocumentRows]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(40):
        n = int(rng.integers(0, 5))
        rows = [
            VectorRow(
                vector=rng.normal(size=8).astype(np.float32).tolist(),
                metadata={
                    "path": f"f{i % 4}/n{i}.md",
                    "folder": f"f{i % 4}",
                    "content": f"n{i}#{j} ünï",
                    "ordinal": j,
                },
            )
            for j in range(n)
        ]
        out.append(DocumentRows(f"f{i % 4}/n{i}.md", f"t{i}", rows))
    return out


def _dump(path: Path) -> tuple[list[tuple[Any, ...]], ...]:
    conn = sqlite3.connect(path)
    try:
        return (
            conn.execute(
                "SELECT id, path, ordinal, folder, vector, metadata FROM chunks"
                " ORDER BY id"
            ).fetchall(),
            conn.execute("SELECT path, token FROM documents ORDER BY path").fetchall(),
            conn.execute("SELECT key, value FROM meta ORDER BY key").fetchall(),
        )
    finally:
        conn.close()


class TestTheSameGenerationAsBefore:
    def test_rows_and_search_match_the_resident_publish(self, tmp_path: Path) -> None:
        ident = EmbeddingIdentity("fake", "m", "", "v1", 8)
        documents = _documents(1938)
        streamed = _streamed().open(_base(tmp_path, "s"), ident)
        resident = SqliteVectorBackend().open(_base(tmp_path, "r"), ident)
        for store in (streamed, resident):
            store.publish_generation(ident, documents)
        rng = np.random.default_rng(7)
        queries = [rng.normal(size=8).tolist() for _ in range(12)]
        for query in queries:
            for folder in (None, "f1"):
                got = streamed.search(query, limit=7, folder=folder)
                want = resident.search(query, limit=7, folder=folder)
                assert [(r["path"], r["ordinal"]) for r in got] == [
                    (r["path"], r["ordinal"]) for r in want
                ]
                assert [r["score"] for r in got] == pytest.approx(
                    [r["score"] for r in want], abs=1e-6
                )
        streamed.close()
        resident.close()
        assert _dump(_base(tmp_path, "s").with_suffix(".sqlite3")) == _dump(
            _base(tmp_path, "r").with_suffix(".sqlite3")
        )
