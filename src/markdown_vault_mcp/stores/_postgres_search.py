"""Searching the PostgreSQL vector store: PostgreSQL scores and sorts (#1504).

Rows are normalised when written, so the negated inner product ``<#>`` is
the cosine similarity, and a zero vector scores ``0.0`` (cosine distance,
``<=>``, gives ``NaN`` there). The distance is computed once, in SQL, and
negated here. Only metadata and the distance cross the wire. Without a
predicate the limit is in the query; with one, a server-side cursor in the
read's transaction yields rows in order until *limit* pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from psycopg import sql

from markdown_vault_mcp.stores._resident import unit_vector

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import psycopg

_MIN_BATCH_ROWS = 100  # a server-side cursor's batch; psycopg's default itersize


@dataclass(frozen=True)
class SearchRequest:
    """One search: what to keep, and how many.

    Attributes:
        limit: The most rows to return; positive.
        folder: Keep rows in this folder or below it; ``None`` keeps all.
        exclude: Leave out this document's rows (``similar_to_document``).
        predicate: Keep only the rows whose metadata it accepts.
    """

    limit: int
    folder: str | None = None
    exclude: str | None = None
    predicate: Callable[[dict[str, Any]], bool] | None = None


def query_unit(vector: Sequence[float], dimension: int) -> Any:
    """Return *vector* as a unit float32 query; a zero query stays zero.

    Raises:
        ValueError: If it is not of *dimension*, or not finite.
    """
    query = np.asarray(vector, dtype=np.float32)
    if query.shape != (dimension,):
        raise ValueError(
            f"Query dimension mismatch: the store holds dimension {dimension}, "
            f"the query has {query.size}."
        )
    if not np.isfinite(query).all():
        raise ValueError("The query vector is not finite.")
    return unit_vector(query)


def document_mean(conn: psycopg.Connection[Any], *, schema: str, path: str) -> Any:
    """The unit mean of *path*'s rows; ``None`` when it has none.

    A cancelling mean stays zero, so every row then scores ``0.0``.
    """
    row = conn.execute(
        sql.SQL("SELECT avg(embedding) FROM {}.vector_chunks WHERE path = %s").format(
            sql.Identifier(schema)
        ),
        (path,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return unit_vector(row[0].to_numpy().astype(np.float32))


def _scan(schema: str, request: SearchRequest) -> sql.Composed:
    conditions: list[sql.Composable] = []
    if request.folder is not None:
        conditions.append(
            sql.SQL("(folder = %(folder)s OR starts_with(folder, %(prefix)s))")
        )
    if request.exclude is not None:
        conditions.append(sql.SQL("path <> %(exclude)s"))
    where = (
        sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
        if conditions
        else sql.SQL("")
    )
    limit = sql.SQL(" LIMIT %(limit)s") if request.predicate is None else sql.SQL("")
    return sql.SQL(
        "SELECT metadata, embedding <#> %(q)s AS distance FROM {}.vector_chunks{}"
        " ORDER BY distance, path, ordinal{}"
    ).format(sql.Identifier(schema), where, limit)


def scored_rows(
    conn: psycopg.Connection[Any],
    *,
    schema: str,
    query: Any,
    request: SearchRequest,
) -> list[dict[str, Any]]:
    """The rows nearest the unit *query* that *request* keeps, most similar first.

    Args:
        conn: A connection inside the read's transaction.
        schema: The vault schema.
        query: A unit (or zero) float32 vector of the store's dimension.
        request: What to keep, and how many.

    Returns:
        Each row's metadata with its ``score``.
    """
    params = {
        "q": query,
        "folder": request.folder,
        "prefix": None if request.folder is None else f"{request.folder}/",
        "exclude": request.exclude,
        "limit": request.limit,
    }
    statement = _scan(schema, request)
    wanted = request.predicate
    if wanted is None:
        rows = conn.execute(statement, params).fetchall()
        return [{**meta, "score": 0.0 - float(distance)} for meta, distance in rows]
    kept: list[dict[str, Any]] = []
    with conn.cursor(name="mvm_vector_search") as cursor:
        cursor.itersize = max(request.limit, _MIN_BATCH_ROWS)
        cursor.execute(statement, params)
        for meta, distance in cursor:
            if wanted(meta):
                kept.append({**meta, "score": 0.0 - float(distance)})
                if len(kept) >= request.limit:
                    break
    return kept
