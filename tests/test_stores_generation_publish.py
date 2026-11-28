"""A resident store builds a published generation's matrix in one copy (#1938)."""

from __future__ import annotations

import sqlite3
import tracemalloc
from array import array
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from markdown_vault_mcp.stores._resident import (
    ResidentStore,
    Snapshot,
    generation_snapshot,
    row_metadata,
    row_vectors,
)
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend, _chunk_rows
from markdown_vault_mcp.types import DocumentRows, EmbeddingIdentity, VectorRow
from tests.fixtures.vector_store_contract import doc

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

IDENT8 = EmbeddingIdentity("fake", "m", "", "v1", 8)


def _documents(seed: int, count: int = 40) -> list[DocumentRows]:
    """Documents of 0 to 4 rows in four folders, vectors as float32 arrays."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(count):
        path = f"f{i % 4}/n{i}.md"
        rows = [
            VectorRow(
                vector=array("f", rng.normal(size=8).astype(np.float32).tobytes()),
                metadata={"folder": f"f{i % 4}", "content": f"n{i}#{j} ünï", "j": j},
            )
            for j in range(int(rng.integers(0, 5)))
        ]
        out.append(DocumentRows(path, f"t{i}", rows))
    return out


def _main_matrix(documents: list[DocumentRows], dimension: int) -> Any:
    """The matrix main built: each document's block, stacked."""
    return np.vstack([row_vectors(d.rows, dimension) for d in documents])


def _main_chunks(documents: list[DocumentRows], dimension: int) -> list[Any]:
    """The ``chunks`` rows main wrote, without their ids."""
    return [
        chunk
        for d in documents
        for chunk in _chunk_rows(
            d.path, row_metadata(d), row_vectors(d.rows, dimension)
        )
    ]


def _queries() -> list[list[float]]:
    rng = np.random.default_rng(7)
    return [rng.normal(size=8).tolist() for _ in range(12)]


def _top_k(store: Any) -> list[list[tuple[str, int, float]]]:
    return [
        [(r["path"], r["j"], r["score"]) for r in store.search(q, limit=7, folder=f)]
        for q in _queries()
        for f in (None, "f1")
    ]


class TestTheMatrix:
    def test_is_byte_identical_to_the_stacked_blocks(self) -> None:
        documents = _documents(1938)
        snap = generation_snapshot(IDENT8, documents)
        assert snap.matrix.dtype == np.float32
        assert snap.matrix.tobytes() == _main_matrix(documents, 8).tobytes()
        assert snap.rows == tuple(m for d in documents for m in row_metadata(d))
        assert dict(snap.tokens) == {d.path: d.token for d in documents}
        assert snap.generation == 0
        assert not snap.matrix.flags.writeable

    def test_of_no_documents_is_empty(self) -> None:
        snap = generation_snapshot(IDENT8, [])
        assert snap.matrix.shape == (0, 8)
        assert (snap.rows, dict(snap.tokens)) == ((), {})

    def test_is_held_once(self) -> None:
        dimension = 256
        rng = np.random.default_rng(3)
        documents = [
            DocumentRows(
                f"n{i}.md",
                None,
                [
                    VectorRow(array("f", rng.normal(size=dimension).astype("f4")), {})
                    for _ in range(8)
                ],
            )
            for i in range(1000)
        ]
        matrix_bytes = 1000 * 8 * dimension * 4
        tracemalloc.start()
        try:
            snap = generation_snapshot(
                EmbeddingIdentity("f", "m", "", "v1", dimension), documents
            )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert snap.matrix.nbytes == matrix_bytes
        # Main held every block and their stacked copy: twice the matrix.
        assert peak < 1.5 * matrix_bytes, peak


class _Replay:
    """An iterable whose second pass yields *second* instead of *first*."""

    def __init__(self, first: list[DocumentRows], second: list[DocumentRows]) -> None:
        self._passes = [first, second]
        self.reads = 0

    def __iter__(self) -> Iterator[DocumentRows]:
        self.reads += 1
        return iter(self._passes[min(self.reads, 2) - 1])


_A, _B = doc("a.md", [[1, 0, 0]]), doc("b.md", [[0, 1, 0], [0, 0, 1]])
_SECOND_PASSES = {
    "fewer documents": [_A],
    "more documents": [_A, _B, doc("c.md", [[1, 1, 0]])],
    "another path": [_A, doc("c.md", [[0, 1, 0], [0, 0, 1]])],
    "more rows": [_A, doc("b.md", [[0, 1, 0], [0, 0, 1], [1, 0, 0]])],
    "fewer rows": [_A, doc("b.md", [[0, 1, 0]])],
    "another order": [_B, _A],
}


def _numpy(tmp_path: Path) -> Any:
    return NumpySidecarBackend().open(tmp_path / "np" / "emb", _A_IDENT)


def _resident_sqlite(tmp_path: Path) -> Any:
    return SqliteVectorBackend().open(tmp_path / "sq" / "emb", _A_IDENT)


def _streamed_sqlite(tmp_path: Path) -> Any:
    return SqliteVectorBackend(search="streamed").open(
        tmp_path / "ss" / "emb", _A_IDENT
    )


_A_IDENT = EmbeddingIdentity("fake", "m", "", "v1", 3)
# The stores that read *documents* twice; a streamed one reads it once.
_STORES: dict[str, Callable[[Path], Any]] = {
    "numpy": _numpy,
    "sqlite resident": _resident_sqlite,
}


class TestASinglePassIterator:
    @pytest.mark.parametrize("store_name", sorted(_STORES))
    def test_is_refused_before_anything_is_written(
        self, tmp_path: Path, store_name: str
    ) -> None:
        store = _STORES[store_name](tmp_path)
        store.publish_generation(_A_IDENT, [doc("old.md", [[1, 0, 0]])])
        with pytest.raises(TypeError, match="re-iterable"):
            store.publish_generation(_A_IDENT, iter([_A, _B]))
        assert (store.generation, store.count, store.tokens()) == (
            1,
            1,
            {"old.md": "t1"},
        )
        store.close()

    def test_is_published_by_a_streamed_store(self, tmp_path: Path) -> None:
        store = _streamed_sqlite(tmp_path)
        store.publish_generation(_A_IDENT, (d for d in [_A, _B]))
        assert (store.generation, store.count) == (1, 3)
        store.close()


class TestASecondPassThatDiffers:
    @pytest.mark.parametrize("store_name", sorted(_STORES))
    @pytest.mark.parametrize("case", sorted(_SECOND_PASSES))
    def test_is_refused_and_changes_nothing(
        self, tmp_path: Path, store_name: str, case: str
    ) -> None:
        store = _STORES[store_name](tmp_path)
        store.publish_generation(_A_IDENT, [doc("old.md", [[1, 0, 0]])])
        replay = _Replay([_A, _B], _SECOND_PASSES[case])
        with pytest.raises(ValueError, match="second pass"):
            store.publish_generation(_A_IDENT, replay)
        assert replay.reads == 2
        assert (store.generation, store.count, store.tokens()) == (
            1,
            1,
            {"old.md": "t1"},
        )
        store.close()


def _sqlite_chunks(path: Path) -> list[Any]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT path, ordinal, folder, vector, metadata FROM chunks ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


class TestTheSameGenerationAsBefore:
    def test_numpy_saves_main_s_matrix_and_searches_alike(self, tmp_path: Path) -> None:
        documents = _documents(1938)
        store = NumpySidecarBackend().open(tmp_path / "emb", IDENT8)
        store.publish_generation(IDENT8, documents)
        saved = np.load(store._matrix_path)
        assert saved.tobytes() == _main_matrix(documents, 8).tobytes()
        assert _top_k(store) == _top_k(_main_store(documents))
        store.close()

    @pytest.mark.parametrize("search", ["resident", "streamed"])
    def test_sqlite_writes_main_s_rows_and_searches_alike(
        self, tmp_path: Path, search: Any
    ) -> None:
        documents = _documents(1938)
        store = SqliteVectorBackend(search=search).open(tmp_path / "emb", IDENT8)
        store.publish_generation(IDENT8, documents)
        main = _main_store(documents)
        got, want = _top_k(store), _top_k(main)
        assert [[hit[:2] for hit in q] for q in got] == [
            [hit[:2] for hit in q] for q in want
        ]
        assert [hit[2] for q in got for hit in q] == pytest.approx(
            [hit[2] for q in want for hit in q], abs=1e-6
        )
        store.close()
        assert _sqlite_chunks(tmp_path / "emb.sqlite3") == [
            tuple(c) for c in _main_chunks(documents, 8)
        ]


def _main_store(documents: list[DocumentRows]) -> ResidentStore:
    """A store serving the snapshot main built, for the reference top-k."""
    rows = tuple(m for d in documents for m in row_metadata(d))
    matrix = _main_matrix(documents, 8)
    tokens = {d.path: d.token for d in documents}
    return ResidentStore(Snapshot(IDENT8, 1, matrix, rows, tokens), read_only=True)
