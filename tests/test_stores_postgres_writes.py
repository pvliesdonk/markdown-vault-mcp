"""Writes to the PostgreSQL vector store, and the reads that check them (#1504)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg import sql

from markdown_vault_mcp.stores import _postgres as pg
from markdown_vault_mcp.stores import _postgres_tables as tables
from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
from markdown_vault_mcp.types import (
    DocumentRows,
    EmbeddingIdentity,
    VectorRow,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.fixtures.postgres import PostgresSchema

_ID = EmbeddingIdentity("fake", "m", "", "v1", 3)


def _doc(
    path: str, vectors: list[list[float]], *, token: str | None = "t1", folder: str = ""
) -> DocumentRows:
    rows = [
        VectorRow(vector=v, metadata={"folder": folder, "content": f"{path}#{i}"})
        for i, v in enumerate(vectors)
    ]
    return DocumentRows(path=path, token=token, rows=rows)


@pytest.fixture
def conn(postgres_schema: PostgresSchema) -> Iterator[psycopg.Connection[Any]]:
    conninfo = pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name)
    with pg.connect(conninfo, schema=postgres_schema.name) as connection:
        yield connection


@pytest.mark.parametrize(
    ("configured", "inside"),
    [
        ("off", "on"),
        ("local", "on"),
        ("remote_write", "on"),
        ("on", "on"),
        ("remote_apply", "remote_apply"),
    ],
)
def test_make_durable_raises_only_weaker_settings(
    conn: psycopg.Connection[Any], configured: str, inside: str
) -> None:
    conn.execute(sql.SQL("SET synchronous_commit = {}").format(sql.Literal(configured)))
    with conn.transaction():
        tables.make_durable(conn, lock_timeout_ms=1234)
        assert conn.execute("SHOW synchronous_commit").fetchone() == (inside,)
        assert conn.execute("SHOW lock_timeout").fetchone() == ("1234ms",)
    assert conn.execute("SHOW synchronous_commit").fetchone() == (configured,)


def test_lock_meta_returns_the_row_and_holds_it(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    ext = pg.find_extension(conn, schema=name)
    with conn.transaction():
        tables.create_tables(conn, schema=name, extension=ext, identity=_ID)
    with conn.transaction():
        meta = tables.lock_meta(conn, schema=name)
        assert meta.identity == _ID
        with psycopg.connect(postgres_schema.dsn, autocommit=True) as other:
            other.execute("SET lock_timeout = 200")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute(
                    sql.SQL("SELECT 1 FROM {}.vector_meta FOR UPDATE").format(
                        sql.Identifier(name)
                    )
                )


def test_reads_of_an_empty_store(postgres_schema: PostgresSchema) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        assert store.tokens() == {}
        assert store.rows_for("a.md") == []
        assert store.count == 0
    finally:
        store.close()
