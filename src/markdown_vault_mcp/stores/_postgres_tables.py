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
from markdown_vault_mcp.types import ANY_TOKEN, VectorRow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from markdown_vault_mcp.types import EmbeddingIdentity, ExpectedToken

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
    conn: psycopg.Connection[Any],
    *,
    schema: str,
    extension: Extension,
    action: str = "opened",
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
        failure = classify_error(exc, schema=schema, action=action)
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


def _is_count(value: object) -> bool:
    """Whether *value* is a non-negative integer (a bool is not)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _judge_meta(rows: list[tuple[Any, ...]], *, schema: str) -> Meta:
    """Turn the meta rows into a :class:`Meta`, refusing a malformed one."""
    if len(rows) != 1:
        raise _corrupt(f"has {len(rows)} meta rows, not one.", schema)
    version, raw_identity, generation = rows[0]
    if not _is_count(version) or not _is_count(generation):
        raise _corrupt(
            f"has a meta row with format_version {version!r} and generation "
            f"{generation!r}; both must be non-negative integers.",
            schema,
        )
    if version > FORMAT_VERSION:
        raise StoreUnsupportedSchemaError(
            f"Vector store at {where(schema)} has format_version {version}; this "
            f"version reads up to {FORMAT_VERSION}.",
            backend=BACKEND_NAME,
            location=schema,
        )
    identity = identity_from_mapping(
        raw_identity, backend=BACKEND_NAME, where=where(schema), location=schema
    )
    return Meta(int(version), identity, int(generation))


_WEAKER_COMMITS = frozenset({"off", "local", "remote_write"})


def make_durable(conn: psycopg.Connection[Any], *, lock_timeout_ms: int) -> None:
    """Make this transaction's commit wait for a local flush, and bound its lock waits.

    ``synchronous_commit`` is raised to ``on`` only from a weaker value; a
    stronger one (``remote_apply``) is left as configured.

    Args:
        conn: A connection inside the write transaction.
        lock_timeout_ms: The ``lock_timeout`` for this transaction.
    """
    row = conn.execute("SHOW synchronous_commit").fetchone()
    if row is not None and str(row[0]) in _WEAKER_COMMITS:
        conn.execute("SET LOCAL synchronous_commit = on")
    conn.execute(
        sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(lock_timeout_ms))
    )


def lock_meta(conn: psycopg.Connection[Any], *, schema: str) -> Meta:
    """Lock the meta row for this transaction and return it.

    Raises:
        StoreCorruptError / StoreUnsupportedSchemaError: As :func:`read_meta`.
        psycopg.Error: Anything the lock or read raises; the caller classifies it.
    """
    query = sql.SQL(
        "SELECT format_version, identity, generation FROM {}.vector_meta FOR UPDATE"
    ).format(sql.Identifier(schema))
    return _judge_meta(conn.execute(query).fetchall(), schema=schema)


def token_conflicts(
    conn: psycopg.Connection[Any], *, schema: str, path: str, expected: ExpectedToken
) -> bool:
    """Whether the token stored for *path* is not *expected*.

    Args:
        conn: A connection inside the write transaction.
        schema: The vault schema.
        path: The document.
        expected: The token the writer saw; ``ANY_TOKEN`` matches anything,
            and an absent document holds ``None``.

    Returns:
        ``True`` when the write must not proceed.
    """
    if expected is ANY_TOKEN:
        return False
    row = conn.execute(
        sql.SQL("SELECT token FROM {}.vector_documents WHERE path = %s").format(
            sql.Identifier(schema)
        ),
        (path,),
    ).fetchone()
    return (row[0] if row is not None else None) != expected


def stored_tokens(
    conn: psycopg.Connection[Any], *, schema: str
) -> dict[str, str | None]:
    """Every stored document's token.

    Args:
        conn: A connection inside a read transaction.
        schema: The vault schema.

    Returns:
        Each document path with its token (``None`` when it has none).
    """
    query = sql.SQL("SELECT path, token FROM {}.vector_documents").format(
        sql.Identifier(schema)
    )
    return {str(path): token for path, token in conn.execute(query)}


def stored_rows(
    conn: psycopg.Connection[Any], *, schema: str, path: str
) -> list[VectorRow]:
    """*path*'s rows in ordinal order: normalised vectors and their metadata.

    Args:
        conn: A connection inside a read transaction.
        schema: The vault schema.
        path: The document.

    Returns:
        The rows; empty when the document has none.
    """
    query = sql.SQL(
        "SELECT embedding, metadata FROM {}.vector_chunks WHERE path = %s ORDER BY ordinal"
    ).format(sql.Identifier(schema))
    return [
        VectorRow(
            vector=[float(x) for x in embedding.to_numpy()], metadata=dict(metadata)
        )
        for embedding, metadata in conn.execute(query, (path,))
    ]


def stored_count(conn: psycopg.Connection[Any], *, schema: str) -> int:
    """The number of stored chunks.

    Args:
        conn: A connection inside a read transaction.
        schema: The vault schema.

    Returns:
        The row count.
    """
    row = conn.execute(
        sql.SQL("SELECT count(*) FROM {}.vector_chunks").format(sql.Identifier(schema))
    ).fetchone()
    return int(row[0]) if row is not None else 0


def write_document(
    conn: psycopg.Connection[Any],
    *,
    schema: str,
    path: str,
    token: str | None,
    chunks: Sequence[tuple[str, int, str, Any, Jsonb]],
) -> None:
    """Replace *path*'s rows with *chunks* and record its *token*.

    Args:
        conn: A connection inside the write transaction.
        schema: The vault schema.
        path: The document.
        token: Its revision token.
        chunks: Its rows, as :func:`~markdown_vault_mcp.stores._postgres_rows.chunk_values`
            returns them.
    """
    s = sql.Identifier(schema)
    conn.execute(
        sql.SQL("DELETE FROM {}.vector_chunks WHERE path = %s").format(s), (path,)
    )
    conn.execute(
        sql.SQL(
            "INSERT INTO {}.vector_documents (path, token) VALUES (%s, %s)"
            " ON CONFLICT (path) DO UPDATE SET token = excluded.token"
        ).format(s),
        (path, token),
    )
    if chunks:
        with conn.cursor() as cur:
            cur.executemany(
                sql.SQL(
                    "INSERT INTO {}.vector_chunks (path, ordinal, folder, embedding, metadata)"
                    " VALUES (%s, %s, %s, %s, %s)"
                ).format(s),
                chunks,
            )


def delete_document(conn: psycopg.Connection[Any], *, schema: str, path: str) -> None:
    """Remove *path*'s token and, by cascade, its rows.

    Args:
        conn: A connection inside the write transaction.
        schema: The vault schema.
        path: The document.
    """
    conn.execute(
        sql.SQL("DELETE FROM {}.vector_documents WHERE path = %s").format(
            sql.Identifier(schema)
        ),
        (path,),
    )
