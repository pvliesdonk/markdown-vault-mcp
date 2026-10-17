"""Bounded exact search over a vector table, a batch at a time (#1377).

A scan reads rows of ``(rowid, vector BLOB, ...)`` from a cursor at most
``batch_rows`` at a time, scores each batch against a unit query, and keeps
a running top *limit*. A row outside the top *limit* of the rows seen so
far cannot be in the final one, so the result is exact; rows of equal score
may order differently from a full sort. Working memory is one batch plus
*limit* rows. Nothing here opens a connection or a transaction: the store
runs a scan inside its own read transaction.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

with contextlib.suppress(ImportError):  # numpy is the embeddings extra
    import numpy as np

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Sequence

#: The default search workspace: bytes of vector data per batch (#1377).
DEFAULT_WORKSPACE_BYTES = 1 << 20

_FLOAT32_BYTES = 4


def rows_per_batch(workspace_bytes: int, dimension: int) -> int:
    """Return how many rows of *dimension* fit *workspace_bytes*, at least one.

    Args:
        workspace_bytes: The vector bytes one batch may hold.
        dimension: The stored vectors' dimension; 0 counts as 1.

    Returns:
        The rows per batch.
    """
    return max(1, workspace_bytes // (_FLOAT32_BYTES * max(dimension, 1)))


def folder_params(
    folder: str | None,
) -> tuple[str | None, str | None, int, str | None]:
    """Return the parameters of ``(? IS NULL OR folder = ? OR substr(folder, 1, ?) = ?)``.

    The condition holds for the scope itself and every sub-folder, exactly
    as :func:`~markdown_vault_mcp.utils.folder_matches`: case-sensitive, and
    no character in a folder name is a wildcard.

    Args:
        folder: The scope, already normalised; ``None`` for every row.

    Returns:
        The four parameters, in the condition's order.
    """
    if folder is None:
        return None, None, 0, None
    prefix = f"{folder}/"
    return folder, folder, len(prefix), prefix


def merged(
    best_ids: Any, best_scores: Any, ids: Any, scores: Any, limit: int
) -> tuple[Any, Any]:
    """Return the top *limit* of the running best and one batch, unordered.

    Args:
        best_ids: The running best rowids.
        best_scores: Their scores.
        ids: One batch's rowids.
        scores: Their scores.
        limit: How many to keep.

    Returns:
        The kept rowids and scores.
    """
    all_ids = np.concatenate((best_ids, ids))
    all_scores = np.concatenate((best_scores, scores))
    if len(all_scores) <= limit:
        return all_ids, all_scores
    keep = np.argpartition(all_scores, -limit)[-limit:]
    return all_ids[keep], all_scores[keep]


def scan(
    cursor: sqlite3.Cursor,
    query: Any,
    *,
    limit: int,
    batch_rows: int,
    check_blob: Callable[[object], bytes],
    keep: Callable[[Sequence[Any]], bool] | None = None,
) -> list[tuple[int, float]]:
    """Return the *limit* rows of *cursor* nearest *query*, most similar first.

    Args:
        cursor: Rows whose ``[0]`` is the rowid and ``[1]`` a float32
            little-endian vector of ``len(query)`` values.
        query: The unit query vector, float32.
        limit: How many rows to return; positive.
        batch_rows: The most rows held at once.
        check_blob: Returns a vector BLOB of the right width or raises.
        keep: Keeps only the rows it accepts, before a batch is cut.

    Returns:
        ``(rowid, score)`` pairs.
    """
    dimension = len(query)
    best_ids = np.empty(0, dtype=np.int64)
    best_scores = np.empty(0, dtype=np.float32)
    while batch := cursor.fetchmany(batch_rows):
        if keep is not None:
            batch = [row for row in batch if keep(row)]
            if not batch:
                continue
        ids = np.fromiter((row[0] for row in batch), dtype=np.int64, count=len(batch))
        matrix = np.frombuffer(
            b"".join(check_blob(row[1]) for row in batch), dtype="<f4"
        ).reshape(len(batch), dimension)
        best_ids, best_scores = merged(
            best_ids, best_scores, ids, matrix @ query, limit
        )
    order = np.argsort(-best_scores, kind="stable")
    return [(int(best_ids[i]), float(best_scores[i])) for i in order]
