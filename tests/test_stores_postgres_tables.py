"""The PostgreSQL store's tables: layout check and meta row (#1504)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg import sql

from markdown_vault_mcp.exceptions import (
    StoreCorruptError,
    StoreUnavailableError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.stores import _postgres as pg
from markdown_vault_mcp.stores import _postgres_tables as tables
from markdown_vault_mcp.types import EmbeddingIdentity

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.fixtures.postgres import PostgresSchema

_ID = EmbeddingIdentity("ollama", "nomic", "", "v1", 3)


@pytest.fixture
def conn(postgres_schema: PostgresSchema) -> Iterator[psycopg.Connection[Any]]:
    conninfo = pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name)
    with pg.connect(conninfo, schema=postgres_schema.name) as connection:
        yield connection


def _ext(conn: psycopg.Connection[Any], schema: str) -> pg.Extension:
    return pg.find_extension(conn, schema=schema)


def _run(conn: psycopg.Connection[Any], schema: str, statement: str) -> None:
    """Run *statement* with ``{s}`` replaced by the quoted schema."""
    conn.execute(sql.SQL(statement).format(s=sql.Identifier(schema)))


def test_an_empty_schema_has_no_layout(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    assert (
        tables.check_layout(conn, schema=name, extension=_ext(conn, name)) == "absent"
    )
    assert tables.read_meta(conn, schema=name, action="opened") is None


def test_unrelated_tables_leave_the_layout_absent(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    _run(conn, name, "CREATE TABLE {s}.notes (i int)")
    assert (
        tables.check_layout(conn, schema=name, extension=_ext(conn, name)) == "absent"
    )


def test_created_tables_are_present_with_a_meta_row(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    ext = _ext(conn, name)
    with conn.transaction():
        tables.create_tables(conn, schema=name, extension=ext, identity=_ID)
    assert tables.check_layout(conn, schema=name, extension=ext) == "present"
    assert tables.read_meta(conn, schema=name, action="opened") == tables.Meta(
        pg.FORMAT_VERSION, _ID, 0
    )


def test_an_interrupted_create_leaves_nothing(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    ext = _ext(conn, name)

    def create_then_fail() -> None:
        with conn.transaction():
            tables.create_tables(conn, schema=name, extension=ext, identity=_ID)
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        create_then_fail()
    assert tables.check_layout(conn, schema=name, extension=ext) == "absent"


@pytest.mark.parametrize("drop", ["vector_documents", "vector_chunks"])
def test_partial_tables_are_corrupt(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema, drop: str
) -> None:
    name = postgres_schema.name
    ext = _ext(conn, name)
    with conn.transaction():
        tables.create_tables(conn, schema=name, extension=ext, identity=_ID)
    _run(conn, name, f"DROP TABLE {{s}}.{drop} CASCADE")
    with pytest.raises(StoreCorruptError, match="some of the vector store tables"):
        tables.check_layout(conn, schema=name, extension=ext)


def test_a_wrong_column_type_is_corrupt(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    ext = _ext(conn, name)
    with conn.transaction():
        tables.create_tables(conn, schema=name, extension=ext, identity=_ID)
    _run(conn, name, "ALTER TABLE {s}.vector_chunks ALTER COLUMN embedding TYPE text")
    with pytest.raises(StoreCorruptError, match="vector_chunks"):
        tables.check_layout(conn, schema=name, extension=ext)


def test_a_view_named_like_a_store_table_is_corrupt(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    _run(conn, name, "CREATE VIEW {s}.vector_meta AS SELECT 1 AS id")
    with pytest.raises(StoreCorruptError):
        tables.check_layout(conn, schema=name, extension=_ext(conn, name))


def test_the_embedding_type_must_be_the_extensions_vector(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    ext = _ext(conn, name)
    with conn.transaction():
        tables.create_tables(conn, schema=name, extension=ext, identity=_ID)
    _run(conn, name, "CREATE DOMAIN {s}.vector AS text")
    _run(
        conn,
        name,
        "ALTER TABLE {s}.vector_chunks ALTER COLUMN embedding TYPE {s}.vector USING embedding::text",
    )
    with pytest.raises(StoreCorruptError, match="embedding"):
        tables.check_layout(conn, schema=name, extension=ext)


def _set_meta(conn: psycopg.Connection[Any], schema: str, **values: object) -> None:
    for column, value in values.items():
        conn.execute(
            sql.SQL("UPDATE {}.vector_meta SET {} = %s").format(
                sql.Identifier(schema), sql.Identifier(column)
            ),
            (value,),
        )


@pytest.fixture
def planted(conn: psycopg.Connection[Any], postgres_schema: PostgresSchema) -> str:
    name = postgres_schema.name
    with conn.transaction():
        tables.create_tables(
            conn, schema=name, extension=_ext(conn, name), identity=_ID
        )
    return name


def test_a_newer_format_is_unsupported(
    conn: psycopg.Connection[Any], planted: str
) -> None:
    _set_meta(conn, planted, format_version=pg.FORMAT_VERSION + 1)
    with pytest.raises(StoreUnsupportedSchemaError, match="format_version"):
        tables.read_meta(conn, schema=planted, action="opened")


@pytest.mark.parametrize(
    "identity",
    [
        psycopg.types.json.Jsonb(["not", "an", "object"]),
        psycopg.types.json.Jsonb({"provider": "ollama"}),
        psycopg.types.json.Jsonb(
            {
                **json.loads(
                    '{"provider":"o","model":"m","variant":"","text_format":"v1"}'
                ),
                "dimension": "3",
            }
        ),
    ],
)
def test_a_malformed_identity_is_corrupt(
    conn: psycopg.Connection[Any], planted: str, identity: object
) -> None:
    _set_meta(conn, planted, identity=identity)
    with pytest.raises(StoreCorruptError, match="malformed identity"):
        tables.read_meta(conn, schema=planted, action="opened")


def test_a_missing_meta_row_is_corrupt(
    conn: psycopg.Connection[Any], planted: str
) -> None:
    _run(conn, planted, "DELETE FROM {s}.vector_meta")
    with pytest.raises(StoreCorruptError, match="meta row"):
        tables.read_meta(conn, schema=planted, action="opened")


def test_the_generation_is_read(conn: psycopg.Connection[Any], planted: str) -> None:
    _set_meta(conn, planted, generation=7)
    meta = tables.read_meta(conn, schema=planted, action="read")
    assert meta is not None
    assert meta.generation == 7


def test_an_error_during_the_meta_read_is_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    conninfo = pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name)
    closed = pg.connect(conninfo, schema=postgres_schema.name)
    closed.close()
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        tables.read_meta(closed, schema=postgres_schema.name, action="read")


def test_a_table_of_ours_with_other_columns_is_corrupt(
    conn: psycopg.Connection[Any], postgres_schema: PostgresSchema
) -> None:
    name = postgres_schema.name
    ext = _ext(conn, name)
    with conn.transaction():
        tables.create_tables(conn, schema=name, extension=ext, identity=_ID)
    _run(conn, name, "DROP TABLE {s}.vector_meta")
    _run(conn, name, "CREATE TABLE {s}.vector_meta ()")
    with pytest.raises(StoreCorruptError, match="vector_meta table with other columns"):
        tables.check_layout(conn, schema=name, extension=ext)


def test_an_error_during_the_layout_read_is_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    conninfo = pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name)
    live = pg.connect(conninfo, schema=postgres_schema.name)
    ext = _ext(live, postgres_schema.name)
    live.close()
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        tables.check_layout(live, schema=postgres_schema.name, extension=ext)
