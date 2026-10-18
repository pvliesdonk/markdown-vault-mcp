"""The PostgreSQL vector store's tables: their DDL, layout check and meta row (#1504).

Three tables in the vault's schema hold a store. Only ordinary tables with
exactly the expected columns count as one; none of the three is an empty
store, and anything in between is corrupt. The check reads ``pg_catalog``,
which shows every object whatever the role may select, so a store the role
cannot read is never mistaken for an empty one. Every name is
schema-qualified; nothing relies on ``search_path``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from markdown_vault_mcp.exceptions import StoreCorruptError, StoreUnsupportedSchemaError
from markdown_vault_mcp.stores._identity import identity_from_mapping, identity_mapping
from markdown_vault_mcp.stores._postgres import (
    BACKEND_NAME,
    FORMAT_VERSION,
    Extension,
    classify_error,
)

if TYPE_CHECKING:
    from markdown_vault_mcp.types import EmbeddingIdentity

TABLES = ("vector_meta", "vector_documents", "vector_chunks")
Layout = Literal["absent", "present"]

# (column, type name) per table; the embedding's type must also live in the
# extension's schema.
_COLUMNS: dict[str, frozenset[tuple[str, str]]] = {
    "vector_meta": frozenset(
        {
            ("id", "bool"),
            ("format_version", "int4"),
            ("identity", "jsonb"),
            ("generation", "int8"),
        }
    ),
    "vector_documents": frozenset({("path", "text"), ("token", "text")}),
    "vector_chunks": frozenset(
        {
            ("path", "text"),
            ("ordinal", "int4"),
            ("folder", "text"),
            ("embedding", "vector"),
            ("metadata", "jsonb"),
        }
    ),
}

_DDL = (
    "CREATE TABLE {s}.vector_meta (id boolean PRIMARY KEY DEFAULT true CHECK (id),"
    " format_version integer NOT NULL, identity jsonb NOT NULL,"
    " generation bigint NOT NULL CHECK (generation >= 0))",
    "CREATE TABLE {s}.vector_documents (path text PRIMARY KEY, token text)",
    "CREATE TABLE {s}.vector_chunks (path text NOT NULL REFERENCES"
    " {s}.vector_documents (path) ON DELETE CASCADE, ordinal integer NOT NULL,"
    " folder text NOT NULL, embedding {e}.vector NOT NULL, metadata jsonb NOT NULL,"
    " PRIMARY KEY (path, ordinal))",
    "CREATE INDEX ON {s}.vector_chunks (folder)",
)

_CATALOGUE = (
    "SELECT c.relname, c.relkind, a.attname, t.typname, tn.nspname"
    " FROM pg_catalog.pg_class c"
    " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
    " LEFT JOIN pg_catalog.pg_attribute a"
    "   ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped"
    " LEFT JOIN pg_catalog.pg_type t ON t.oid = a.atttypid"
    " LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid = t.typnamespace"
    " WHERE n.nspname = %s AND c.relname = ANY(%s)"
)


class Meta(NamedTuple):
    """The store's single meta row.

    Attributes:
        format_version: The table layout's version.
        identity: The embedding space of the rows.
        generation: Bumped by each generation publish.
    """

    format_version: int
    identity: EmbeddingIdentity
    generation: int


def where(schema: str) -> str:
    """How an outcome's message names the store in *schema*."""
    return f"{schema!r} in PostgreSQL"


def _corrupt(message: str, schema: str) -> StoreCorruptError:
    return StoreCorruptError(
        f"Vector store at {where(schema)} {message}",
        backend=BACKEND_NAME,
        location=schema,
    )


def create_tables(
    conn: psycopg.Connection[Any],
    *,
    schema: str,
    extension: Extension,
    identity: EmbeddingIdentity,
) -> None:
    """Create the store's tables and its meta row, in the caller's transaction.

    Args:
        conn: A connection inside an open transaction.
        schema: The vault schema.
        extension: Where the ``vector`` type lives.
        identity: The identity of the rows the first write publishes.
    """
    names = {"s": sql.Identifier(schema), "e": sql.Identifier(extension.schema)}
    for statement in _DDL:
        conn.execute(sql.SQL(statement).format(**names))
    conn.execute(
        sql.SQL(
            "INSERT INTO {s}.vector_meta (format_version, identity, generation)"
            " VALUES (%s, %s, 0)"
        ).format(**names),
        (FORMAT_VERSION, Jsonb(identity_mapping(identity))),
    )


def check_layout(
    conn: psycopg.Connection[Any], *, schema: str, extension: Extension
) -> Layout:
    """Say whether *schema* holds the store's tables, refusing anything else.

    Returns:
        ``"absent"`` when none of the three tables exists (other tables are
        ignored); ``"present"`` when all three do, with exactly their columns.

    Raises:
        StoreCorruptError: If some exist and some do not, one is not an
            ordinary table, or a column set or type differs.
        StoreUnavailableError: If the catalogue cannot be read.
    """
    try:
        rows = conn.execute(_CATALOGUE, (schema, list(TABLES))).fetchall()
    except psycopg.Error as exc:
        failure = classify_error(exc, schema=schema, action="opened")
    else:
        return _judge_layout(rows, schema=schema, extension=extension)
    raise failure from None


def _judge_layout(
    rows: list[tuple[Any, ...]], *, schema: str, extension: Extension
) -> Layout:
    """Apply the layout rule to the catalogue rows of :func:`check_layout`."""
    found: dict[str, set[tuple[str, str]]] = {}
    for relname, relkind, attname, typname, typschema in rows:
        if relkind != "r":
            raise _corrupt(f"has {relname} as something other than a table.", schema)
        columns = found.setdefault(relname, set())
        if attname is not None:
            if attname == "embedding" and typschema != extension.schema:
                raise _corrupt(
                    f"has a {relname}.embedding column that is not the vector "
                    "extension's type.",
                    schema,
                )
            columns.add((attname, typname))
    if not found:
        return "absent"
    if set(found) != set(TABLES):
        raise _corrupt(
            "has some of the vector store tables but not all of them.", schema
        )
    for name, expected in _COLUMNS.items():
        if frozenset(found[name]) != expected:
            raise _corrupt(f"has a {name} table with other columns.", schema)
    return "present"


def read_meta(
    conn: psycopg.Connection[Any], *, schema: str, action: str
) -> Meta | None:
    """Read the meta row; ``None`` when the store's tables do not exist.

    Args:
        conn: A connection, inside the read's transaction when there is one.
        schema: The vault schema.
        action: For an error's message ("opened", "read").

    Raises:
        StoreCorruptError: If there is not exactly one meta row, or its
            identity or generation is malformed.
        StoreUnsupportedSchemaError: If its format is newer than this version.
        StoreUnavailableError: If it cannot be read.
    """
    query = sql.SQL(
        "SELECT format_version, identity, generation FROM {}.vector_meta"
    ).format(sql.Identifier(schema))
    try:
        rows = conn.execute(query).fetchall()
    except psycopg.errors.UndefinedTable:
        return None
    except psycopg.Error as exc:
        failure = classify_error(exc, schema=schema, action=action)
    else:
        return _judge_meta(rows, schema=schema)
    raise failure from None


def _judge_meta(rows: list[tuple[Any, ...]], *, schema: str) -> Meta:
    """Turn the meta rows into a :class:`Meta`, refusing a malformed one."""
    if len(rows) != 1:
        raise _corrupt(f"has {len(rows)} meta rows, not one.", schema)
    version, raw_identity, generation = rows[0]
    if version > FORMAT_VERSION:
        raise StoreUnsupportedSchemaError(
            f"Vector store at {where(schema)} has format_version {version}; this "
            f"version reads up to {FORMAT_VERSION}.",
            backend=BACKEND_NAME,
            location=schema,
        )
    identity = identity_from_mapping(
        raw_identity, backend=BACKEND_NAME, where=where(schema)
    )
    return Meta(int(version), identity, int(generation))
