"""Searching the PostgreSQL vector store, and the shared vector contracts (#1504)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
from markdown_vault_mcp.types import ANY_TOKEN, EmbeddingIdentity
from tests.fixtures.vector_store_contract import (
    IDENT,
    PersistentVectorStoreContract,
    VectorStoreContract,
    doc,
    paths,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.fixtures.postgres import PostgresSchema


@pytest.fixture
def opened() -> Iterator[list[Any]]:
    """The stores a factory fixture opens, closed when the test ends."""
    stores: list[Any] = []
    yield stores
    for store in stores:
        store.close()


@pytest.fixture
def backend(postgres_schema: PostgresSchema) -> PostgresVectorBackend:
    return PostgresVectorBackend(postgres_schema.dsn)


def _tracked(stores: list[Any], store: Any) -> Any:
    stores.append(store)
    return store


class TestPostgresConformance(VectorStoreContract, PersistentVectorStoreContract):
    @pytest.fixture
    def make_store(
        self, backend: Any, postgres_schema: PostgresSchema, opened: list[Any]
    ) -> Callable[..., Any]:
        return lambda identity: _tracked(
            opened, backend.open(postgres_schema.name, identity)
        )

    @pytest.fixture
    def reopen_store(
        self, backend: Any, postgres_schema: PostgresSchema, opened: list[Any]
    ) -> Callable[..., Any]:
        return lambda identity, owns: _tracked(
            opened, backend.open(postgres_schema.name, identity, owns_files=owns)
        )


@pytest.fixture
def store(backend: Any, postgres_schema: PostgresSchema) -> Iterator[Any]:
    opened_store = backend.open(postgres_schema.name, IDENT)
    yield opened_store
    opened_store.close()


def test_a_selective_predicate_reads_past_the_first_batch(store: Any) -> None:
    near = [doc(f"n{i:03}.md", [[1, 0.001 * i, 0]]) for i in range(150)]
    store.publish_generation(IDENT, [*near, doc("far.md", [[0, 0, 1]], folder="t")])
    seen: list[str] = []

    def wanted(row: dict[str, Any]) -> bool:
        seen.append(row["path"])
        return row["folder"] == "t"

    results = store.search([1, 0, 0], limit=1, predicate=wanted)
    assert paths(results) == ["far.md"]
    assert len(seen) == 151  # every row, in order, once
    assert set(results[0]) == {"path", "folder", "content", "score"}


def test_folder_scopes_are_exact_prefixes(store: Any) -> None:
    for name, folder in [
        ("a", "a"),
        ("ab", "ab"),
        ("a_b", "a_b"),
        ("axb", "axb"),
        ("pct", "a%"),
        ("deep", "a/b"),
        ("root", ""),
    ]:
        store.replace_document(
            doc(f"{name}.md", [[1, 0, 0]], folder=folder), expected_token=ANY_TOKEN
        )

    def scope(folder: str) -> list[str]:
        return sorted(paths(store.search([1, 0, 0], limit=20, folder=folder)))

    assert scope("a") == ["a.md", "deep.md"]
    assert scope("a_b") == ["a_b.md"]  # "_" is no wildcard
    assert scope("a%") == ["pct.md"]  # nor is "%"
    assert scope("") == ["root.md"]  # the root only
    assert scope("A") == []  # case-sensitive


def test_a_zero_score_is_positive_zero(store: Any) -> None:
    store.replace_document(doc("z.md", [[0, 0, 0]]), expected_token=ANY_TOKEN)
    (result,) = store.search([1, 0, 0])
    assert math.copysign(1, result["score"]) == 1


def test_results_are_copies(store: Any) -> None:
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    first = store.search([1, 0, 0])
    first[0]["path"] = "mutated"
    assert paths(store.search([1, 0, 0])) == ["a.md"]


def test_a_non_finite_query_is_refused(store: Any) -> None:
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    for bad in ([math.nan, 0, 0], [math.inf, 0, 0]):
        with pytest.raises(ValueError, match="finite"):
            store.search(bad)


def test_equal_scores_order_by_path_then_ordinal(store: Any) -> None:
    store.replace_document(
        doc("b.md", [[1, 0, 0], [1, 0, 0]]), expected_token=ANY_TOKEN
    )
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    results = store.search([1, 0, 0], limit=3)
    assert [r["content"] for r in results] == ["a.md#0", "b.md#0", "b.md#1"]


def test_a_reader_opened_before_any_dimension_searches_at_the_stored_one(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    unknown = EmbeddingIdentity("fake", "m", "", "v1", 0)
    reader = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, unknown, owns_files=False
    )
    writer = backend.open(postgres_schema.name, IDENT)
    try:
        writer.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
        assert paths(reader.search([1, 0, 0])) == ["a.md"]
        with pytest.raises(ValueError, match="dimension 3"):
            reader.search([1, 0])
    finally:
        writer.close()
        reader.close()


def test_a_fresh_store_searches_nothing_until_its_first_write(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    old = backend.open(postgres_schema.name, IDENT)
    old.replace_document(doc("old.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    old.close()
    fresh = backend.open_fresh(postgres_schema.name, IDENT)
    try:
        assert fresh.search([1, 0, 0]) == []
        assert fresh.similar_to_document("old.md") == []
    finally:
        fresh.close()


def test_search_after_close_raises(
    backend: Any, postgres_schema: PostgresSchema
) -> None:
    closed = backend.open(postgres_schema.name, IDENT)
    closed.close()
    for limit in (5, 0):
        with pytest.raises(RuntimeError, match="closed"):
            closed.search([1, 0, 0], limit=limit)
        with pytest.raises(RuntimeError, match="closed"):
            closed.similar_to_document("a.md", limit=limit)


def test_a_predicate_keeping_fewer_than_the_limit_returns_what_it_keeps(
    store: Any,
) -> None:
    store.replace_document(doc("a.md", [[1, 0, 0]]), expected_token=ANY_TOKEN)
    store.replace_document(
        doc("b.md", [[0, 1, 0]], folder="t"), expected_token=ANY_TOKEN
    )
    results = store.search([1, 0, 0], limit=5, predicate=lambda r: r["folder"] == "t")
    assert paths(results) == ["b.md"]
    assert results[0]["score"] == 0.0
