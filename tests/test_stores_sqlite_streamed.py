"""Streamed reads of the SQLite vector store against resident ones (#1377)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from markdown_vault_mcp.stores import _streamed
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from markdown_vault_mcp.types import ANY_TOKEN, EmbeddingIdentity
from tests.fixtures.vector_store_contract import doc

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

DIM = 16
IDENT = EmbeddingIdentity("fake", "m", "", "v1", DIM)
FOLDERS = ["a", "a/b", "ab", "A", "", "a_b", "50%"]


def _key(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(r["path"], r["content"]) for r in rows]


@pytest.fixture
def pair(tmp_path: Path) -> Iterator[tuple[Any, Any]]:
    """The same 120 documents in a resident store and a streamed one."""
    rng = np.random.default_rng(1377)
    resident = SqliteVectorBackend().open(tmp_path / "r", IDENT)
    streamed = SqliteVectorBackend(search="streamed", workspace_bytes=DIM * 4 * 7).open(
        tmp_path / "s", IDENT
    )
    for i in range(120):
        n = int(rng.integers(1, 4))
        vectors = rng.normal(size=(n, DIM)).tolist()
        if i == 5:
            vectors[0] = [0.0] * DIM  # a zero vector rides along
        document = doc(f"d{i}.md", vectors, folder=FOLDERS[i % len(FOLDERS)])
        resident.replace_document(document, expected_token=ANY_TOKEN)
        streamed.replace_document(document, expected_token=ANY_TOKEN)
    yield resident, streamed
    resident.close()
    streamed.close()


class TestEquivalence:
    @pytest.mark.parametrize("limit", [1, 10, 500])
    @pytest.mark.parametrize("folder", [None, "a", "A", "", "a_b", "50%"])
    def test_search(
        self, pair: tuple[Any, Any], limit: int, folder: str | None
    ) -> None:
        resident, streamed = pair
        rng = np.random.default_rng(limit)
        for _ in range(5):
            q = rng.normal(size=DIM).tolist()
            want = resident.search(q, limit=limit, folder=folder)
            got = streamed.search(q, limit=limit, folder=folder)
            assert _key(got) == _key(want)
            assert [r["score"] for r in got] == pytest.approx(
                [r["score"] for r in want], abs=1e-5
            )

    def test_search_with_a_predicate(self, pair: tuple[Any, Any]) -> None:
        resident, streamed = pair
        keep = lambda r: r["path"].endswith(("1.md", "7.md"))  # noqa: E731
        q = np.random.default_rng(3).normal(size=DIM).tolist()
        assert _key(streamed.search(q, limit=8, predicate=keep)) == _key(
            resident.search(q, limit=8, predicate=keep)
        )

    @pytest.mark.parametrize("folder", [None, "a", ""])
    def test_similar(self, pair: tuple[Any, Any], folder: str | None) -> None:
        resident, streamed = pair
        # Not d5.md: a one-row d5 averages to the zero vector, every score
        # ties at 0, and tie order is not part of the contract (the
        # contract's cancelling-mean test covers that case).
        for path in ["d0.md", "d17.md", "d42.md", "absent.md"]:
            want = resident.similar_to_document(path, limit=12, folder=folder)
            got = streamed.similar_to_document(path, limit=12, folder=folder)
            assert _key(got) == _key(want)

    def test_reads(self, pair: tuple[Any, Any]) -> None:
        resident, streamed = pair
        assert streamed.count == resident.count
        assert streamed.tokens() == resident.tokens()
        for path in ["d3.md", "d5.md", "absent.md"]:
            want, got = resident.rows_for(path), streamed.rows_for(path)
            assert [r.metadata for r in got] == [r.metadata for r in want]
            # The same float32 bits: the resident matrix row and the BLOB.
            assert [list(r.vector) for r in got] == [list(r.vector) for r in want]


class TestBound:
    def test_a_query_holds_one_batch_and_limit_rows(
        self, pair: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _resident, streamed = pair
        seen: list[tuple[int, int]] = []
        real = _streamed.merged

        def recording(best_ids, best_scores, ids, scores, limit):  # type: ignore[no-untyped-def]
            out = real(best_ids, best_scores, ids, scores, limit)
            seen.append((len(ids), len(out[0])))
            return out

        monkeypatch.setattr(_streamed, "merged", recording)
        streamed.search(np.ones(DIM).tolist(), limit=4)
        rows = streamed.count
        assert len(seen) == -(-rows // 7)
        assert max(b for b, _ in seen) <= 7
        assert max(k for _, k in seen) <= 4


class TestConsistency:
    def test_a_write_committed_mid_scan_is_not_seen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = SqliteVectorBackend(search="streamed", workspace_bytes=DIM * 4).open(
            tmp_path / "s", IDENT
        )
        first = [1.0] + [0.0] * (DIM - 1)
        for i in range(5):
            store.replace_document(doc(f"d{i}.md", [first]), expected_token=ANY_TOKEN)
        real = _streamed.merged
        fired = threading.Event()

        def writing(*args: Any) -> Any:
            if not fired.is_set():
                fired.set()
                # The store's writes run on another thread's connection.
                writer = threading.Thread(
                    target=store.replace_document,
                    args=(doc("d4.md", [[-v for v in first]]),),
                    kwargs={"expected_token": ANY_TOKEN},
                )
                writer.start()
                writer.join()
            return real(*args)

        monkeypatch.setattr(_streamed, "merged", writing)
        during = store.search(first, limit=10)
        monkeypatch.setattr(_streamed, "merged", real)
        after = store.search(first, limit=10)
        assert {r["path"]: r["score"] for r in during}["d4.md"] == pytest.approx(1.0)
        assert {r["path"]: r["score"] for r in after}["d4.md"] == pytest.approx(-1.0)
        store.close()
