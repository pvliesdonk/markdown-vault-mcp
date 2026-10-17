"""Conformance every vector store runs (#1767).

Subclass :class:`VectorStoreContract` in a backend's test module and
provide ``make_store``: a callable taking an :class:`EmbeddingIdentity`
and returning a fresh, empty, writable store. File-backed backends also
subclass :class:`PersistentVectorStoreContract` and provide
``reopen_store``: a callable taking an identity and ``owns_files`` and
opening the same location again.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from markdown_vault_mcp.interfaces import EmbeddingStore
from markdown_vault_mcp.types import (
    ANY_TOKEN,
    DocumentRows,
    EmbeddingIdentity,
    VectorRow,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

IDENT = EmbeddingIdentity(
    provider="fake", model="m", variant="", text_format="v1", dimension=3
)


def doc(
    path: str,
    vectors: Sequence[Sequence[float]],
    *,
    token: str | None = "t1",
    folder: str = "",
    **extra: Any,
) -> DocumentRows:
    """Build a document whose row metadata names its path, folder and slot."""
    rows = [
        VectorRow(
            vector=list(v),
            metadata={
                "path": path,
                "folder": folder,
                "content": f"{path}#{i}",
                **extra,
            },
        )
        for i, v in enumerate(vectors)
    ]
    return DocumentRows(path=path, token=token, rows=rows)


def paths(results: list[dict[str, Any]]) -> list[str]:
    """Return the ``path`` of each result, in order."""
    return [r["path"] for r in results]


class VectorStoreContract:
    """Retrieval, publication and reader safety; no reopening needed."""

    # -- retrieval ---------------------------------------------------------

    def test_satisfies_the_store_protocol(self, make_store: Callable[..., Any]) -> None:
        store = make_store(IDENT)
        assert isinstance(store, EmbeddingStore)
        assert (store.identity, store.generation, store.count) == (IDENT, 0, 0)

    def test_scores_are_normalised_dot_products(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[3, 4, 0]]), expected_token=ANY_TOKEN)
        store.replace_document(doc("b.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        results = store.search([2, 0, 0], limit=2)
        assert paths(results) == ["b.md", "a.md"]
        assert results[0]["score"] == pytest.approx(1.0)
        assert results[1]["score"] == pytest.approx(0.6)

    def test_a_zero_vector_is_kept_and_scores_zero(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("z.md", [[0, 0, 0]]), expected_token=ANY_TOKEN)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        results = store.search([1, 0, 0], limit=5)
        assert paths(results) == ["a.md", "z.md"]
        assert results[1]["score"] == 0.0

    def test_a_zero_query_scores_every_row_zero(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.replace_document(doc("b.md", [[0, 1, 0]]), expected_token=ANY_TOKEN)
        results = store.search([0, 0, 0], limit=5)
        assert sorted(paths(results)) == ["a.md", "b.md"]
        assert {r["score"] for r in results} == {0.0}

    def test_the_predicate_runs_before_the_limit(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        for i in range(5):
            store.replace_document(
                doc(f"a{i}.md", [[1, 0.01 * i, 0]], folder="a"),
                expected_token=ANY_TOKEN,
            )
        store.replace_document(
            doc("b0.md", [[0, 1, 0]], folder="b"), expected_token=ANY_TOKEN
        )
        store.replace_document(
            doc("b1.md", [[0, 0, 1]], folder="b"), expected_token=ANY_TOKEN
        )
        results = store.search(
            [1, 0, 0], limit=2, predicate=lambda r: r["folder"] == "b"
        )
        assert sorted(paths(results)) == ["b0.md", "b1.md"]

    def test_the_folder_filter_takes_the_folder_and_below(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        for name, folder in [("x", "x"), ("xy", "x/y"), ("other", "xy"), ("root", "")]:
            store.replace_document(
                doc(f"{name}.md", [[1, 0, 0]], folder=folder), expected_token=ANY_TOKEN
            )
        results = store.search([1, 0, 0], limit=10, folder="x")
        assert sorted(paths(results)) == ["x.md", "xy.md"]

    def test_a_nonpositive_limit_returns_nothing(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.replace_document(doc("b.md", [[0, 1, 0]]), expected_token=ANY_TOKEN)
        for limit in (0, -1):
            assert store.search([1, 0, 0], limit=limit) == []
            assert store.similar_to_document("a.md", limit=limit) == []

    def test_an_empty_store_returns_nothing(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        assert store.search([1, 0, 0]) == []
        assert store.similar_to_document("a.md") == []

    def test_a_query_of_another_dimension_is_refused(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        with pytest.raises(ValueError, match="dimension"):
            store.search([1, 0])

    def test_similar_averages_excludes_itself_and_filters_first(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(
            doc("s.md", [[1, 0, 0], [0, 1, 0]]), expected_token=ANY_TOKEN
        )
        store.replace_document(doc("p.md", [[1, 1, 0]]), expected_token=ANY_TOKEN)
        store.replace_document(doc("q.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.replace_document(doc("r.md", [[0, 0, 1]]), expected_token=ANY_TOKEN)
        results = store.similar_to_document("s.md", limit=2)
        assert paths(results) == ["p.md", "q.md"]
        assert results[0]["score"] == pytest.approx(1.0)
        assert results[1]["score"] == pytest.approx(math.sqrt(0.5))
        filtered = store.similar_to_document(
            "s.md", limit=2, predicate=lambda r: r["path"] != "p.md"
        )
        assert paths(filtered) == ["q.md", "r.md"]

    def test_similar_takes_the_folder_and_below_before_the_limit(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("s.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        for i in range(4):
            store.replace_document(
                doc(f"near{i}.md", [[1, 0.01 * i, 0]], folder="other"),
                expected_token=ANY_TOKEN,
            )
        store.replace_document(
            doc("in.md", [[0, 1, 0]], folder="x"), expected_token=ANY_TOKEN
        )
        store.replace_document(
            doc("deep.md", [[0, 0, 1]], folder="x/y"), expected_token=ANY_TOKEN
        )
        store.replace_document(
            doc("sib.md", [[1, 0, 0]], folder="xy"), expected_token=ANY_TOKEN
        )
        results = store.similar_to_document("s.md", limit=2, folder="x")
        assert sorted(paths(results)) == ["deep.md", "in.md"]

    def test_a_cancelling_mean_scores_zero_and_excludes_itself(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(
            doc("c.md", [[1, 0, 0], [-1, 0, 0]]), expected_token=ANY_TOKEN
        )
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.replace_document(doc("b.md", [[0, 1, 0]]), expected_token=ANY_TOKEN)
        results = store.similar_to_document("c.md", limit=5)
        assert sorted(paths(results)) == ["a.md", "b.md"]
        assert {r["score"] for r in results} == {0.0}

    def test_similar_to_an_unknown_document_is_empty(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        assert store.similar_to_document("nope.md") == []

    def test_rows_round_trip_with_every_key_and_duplicates(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        meta = {
            "path": "d.md",
            "title": "T",
            "heading": None,
            "start_line": 3,
            "extra": [1, 2],
        }
        rows = [VectorRow(vector=[3, 4, 0], metadata=dict(meta)) for _ in range(2)]
        store.replace_document(
            DocumentRows("d.md", "t", rows), expected_token=ANY_TOKEN
        )
        stored = store.rows_for("d.md")
        assert [r.metadata for r in stored] == [meta, meta]
        assert [list(r.vector) for r in stored] == [pytest.approx([0.6, 0.8, 0.0])] * 2
        result = store.search([3, 4, 0], limit=1)[0]
        assert result == {**meta, "score": pytest.approx(1.0)}

    # -- publication --------------------------------------------------------

    def test_tokens_report_each_document(self, make_store: Callable[..., Any]) -> None:
        store = make_store(IDENT)
        store.replace_document(
            doc("a.md", [[1, 0, 0]], token="ta"), expected_token=ANY_TOKEN
        )
        store.replace_document(
            doc("b.md", [[0, 1, 0]], token=None), expected_token=ANY_TOKEN
        )
        assert store.tokens() == {"a.md": "ta", "b.md": None}

    def test_the_stored_token_publishes_and_a_stale_one_conflicts(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(
            doc("a.md", [[1, 0, 0]], token="t1"), expected_token=ANY_TOKEN
        )
        stale = doc("a.md", [[0, 1, 0]], token="t3")
        assert store.replace_document(stale, expected_token="t0") == "conflict"
        assert store.rows_for("a.md")[0].metadata["content"] == "a.md#0"
        assert list(store.rows_for("a.md")[0].vector) == pytest.approx([1, 0, 0])
        assert store.tokens() == {"a.md": "t1"}
        fresh = doc("a.md", [[0, 1, 0]], token="t2")
        assert store.replace_document(fresh, expected_token="t1") == "published"
        assert store.tokens() == {"a.md": "t2"}

    def test_none_expects_no_token(self, make_store: Callable[..., Any]) -> None:
        store = make_store(IDENT)
        first = doc("a.md", [[1, 0, 0]], token="t1")
        assert store.replace_document(first, expected_token=None) == "published"
        again = doc("a.md", [[1, 0, 0]], token="t2")
        assert store.replace_document(again, expected_token=None) == "conflict"

    def test_any_token_always_publishes(self, make_store: Callable[..., Any]) -> None:
        store = make_store(IDENT)
        for token in ("t1", "t2", None):
            outcome = store.replace_document(
                doc("a.md", [[1, 0, 0]], token=token), expected_token=ANY_TOKEN
            )
            assert outcome == "published"

    def test_replacing_twice_is_idempotent(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        document = doc("a.md", [[1, 0, 0], [0, 1, 0]])
        store.replace_document(document, expected_token=ANY_TOKEN)
        store.replace_document(document, expected_token=ANY_TOKEN)
        assert store.count == 2

    def test_remove_document(self, make_store: Callable[..., Any]) -> None:
        store = make_store(IDENT)
        store.replace_document(
            doc("a.md", [[1, 0, 0]], token="ta"), expected_token=ANY_TOKEN
        )
        store.replace_document(
            doc("b.md", [[0, 1, 0]], token="tb"), expected_token=ANY_TOKEN
        )
        assert store.remove_document("a.md", expected_token="stale") == "conflict"
        assert store.count == 2
        assert store.remove_document("a.md", expected_token="ta") == "published"
        assert (store.count, store.tokens()) == (1, {"b.md": "tb"})
        assert (
            store.remove_document("absent.md", expected_token=ANY_TOKEN) == "published"
        )

    def test_a_document_with_no_rows_keeps_its_token(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("e.md", [], token="te"), expected_token=ANY_TOKEN)
        assert (store.count, store.tokens()) == (0, {"e.md": "te"})

    def test_rows_of_another_dimension_are_refused_and_change_nothing(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        wrong = doc("a.md", [[1, 0]])
        with pytest.raises(ValueError, match="dimension"):
            store.replace_document(wrong, expected_token=ANY_TOKEN)
        assert list(store.rows_for("a.md")[0].vector) == pytest.approx([1, 0, 0])

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_values_are_refused_and_change_nothing(
        self, make_store: Callable[..., Any], bad: float
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        broken = doc("a.md", [[bad, 0, 0]])
        with pytest.raises(ValueError, match="finite"):
            store.replace_document(broken, expected_token=ANY_TOKEN)
        assert list(store.rows_for("a.md")[0].vector) == pytest.approx([1, 0, 0])

    def test_publish_generation_replaces_everything(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.replace_document(doc("b.md", [[0, 1, 0]]), expected_token=ANY_TOKEN)
        other = EmbeddingIdentity("fake", "m2", "", "v1", 2)
        store.publish_generation(other, [doc("c.md", [[1, 0]], token="tc")])
        assert (store.identity, store.generation, store.count) == (other, 1, 1)
        assert store.tokens() == {"c.md": "tc"}

    def test_publish_generation_refuses_a_path_twice(
        self, make_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        twice = [doc("a.md", [[1, 0, 0]]), doc("a.md", [[0, 1, 0]])]
        with pytest.raises(ValueError, match=r"a\.md"):
            store.publish_generation(IDENT, twice)
        assert store.generation == 0

    # -- reader safety -------------------------------------------------------

    def test_a_reader_during_replace_sees_whole_rows(
        self, make_store: Callable[..., Any]
    ) -> None:
        ident = EmbeddingIdentity("fake", "m", "", "v1", 4)
        store = make_store(ident)
        rng = np.random.default_rng(1767)
        errors: list[str] = []
        stop = threading.Event()

        def rows(n: int) -> DocumentRows:
            vectors = rng.normal(size=(n, 4)).tolist()
            return DocumentRows(
                "x.md",
                None,
                [
                    VectorRow(vector=v, metadata={"path": "x.md", "v": v})
                    for v in vectors
                ],
            )

        store.replace_document(rows(3), expected_token=ANY_TOKEN)

        def read() -> None:
            local = np.random.default_rng(7)
            try:
                while not stop.is_set():
                    q = local.normal(size=4)
                    qn = q / np.linalg.norm(q)
                    for r in store.search(q.tolist(), limit=10):
                        v = np.asarray(r["v"])
                        expected = float(qn @ (v / np.linalg.norm(v)))
                        if abs(r["score"] - expected) > 1e-4:
                            errors.append(
                                f"score {r['score']} for a row giving {expected}"
                            )
            except BaseException as exc:  # a misaligned pair can also crash a read
                errors.append(f"reader raised {exc!r}")

        # Switch threads as often as possible, so a writer that publishes in
        # two steps is caught between them rather than by luck.
        interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            readers = [threading.Thread(target=read) for _ in range(2)]
            for t in readers:
                t.start()
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                store.replace_document(
                    rows(int(rng.integers(1, 6))), expected_token=ANY_TOKEN
                )
            stop.set()
            for t in readers:
                t.join()
        finally:
            sys.setswitchinterval(interval)
        assert errors == []


class PersistentVectorStoreContract:
    """What a file- or service-backed store keeps across reopening."""

    def test_checkpointed_rows_survive_reopen(
        self, make_store: Callable[..., Any], reopen_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(
            doc("a.md", [[3, 4, 0]], token="ta"), expected_token=ANY_TOKEN
        )
        store.checkpoint()
        store.close()
        again = reopen_store(IDENT, True)
        assert (again.count, again.tokens(), again.generation) == (1, {"a.md": "ta"}, 0)
        assert list(again.rows_for("a.md")[0].vector) == pytest.approx([0.6, 0.8, 0.0])

    def test_close_persists_pending_rows(
        self, make_store: Callable[..., Any], reopen_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.close()
        assert reopen_store(IDENT, True).count == 1

    def test_a_published_generation_is_durable_at_once(
        self, make_store: Callable[..., Any], reopen_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.publish_generation(IDENT, [doc("a.md", [[1, 0, 0]])])
        again = reopen_store(IDENT, False)  # no checkpoint, no close
        assert (again.generation, again.count) == (1, 1)

    @pytest.mark.parametrize(
        "other",
        [
            EmbeddingIdentity("fake", "other", "", "v1", 3),
            EmbeddingIdentity("other", "m", "", "v1", 3),
            EmbeddingIdentity("fake", "m", "typed", "v1", 3),
            EmbeddingIdentity("fake", "m", "", "v2;fields=summary", 3),
            EmbeddingIdentity("fake", "m", "", "v1", 4),
        ],
        ids=["model", "provider", "variant", "text_format", "dimension"],
    )
    def test_another_identity_is_incompatible(
        self,
        make_store: Callable[..., Any],
        reopen_store: Callable[..., Any],
        other: EmbeddingIdentity,
    ) -> None:
        from markdown_vault_mcp.exceptions import StoreIncompatibleError

        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.close()
        with pytest.raises(StoreIncompatibleError):
            reopen_store(other, True)

    def test_a_read_only_reopen_reads(
        self, make_store: Callable[..., Any], reopen_store: Callable[..., Any]
    ) -> None:
        store = make_store(IDENT)
        store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        store.close()
        assert paths(reopen_store(IDENT, False).search([1, 0, 0])) == ["a.md"]
