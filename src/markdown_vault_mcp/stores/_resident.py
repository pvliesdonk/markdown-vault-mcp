"""The resident vector snapshot the built-in stores read from (#1497).

The numpy store always reads from it, the SQLite store in its resident
mode; a streamed SQLite store keeps only a rowless one (#1377).

Rows live in memory as one float32 matrix with a parallel metadata list,
held together with the tokens, identity and generation in one immutable
:class:`Snapshot`. A store builds the next snapshot with the functions here,
persists it its own way, and only then installs it with a single reference
assignment, so a reader sees the old rows or the new ones, never a mix.
Nothing here touches a file.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import ReadOnlyError
from markdown_vault_mcp.types import ANY_TOKEN, VectorRow
from markdown_vault_mcp.utils import folder_matches

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    NUMPY_AVAILABLE = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from markdown_vault_mcp.types import (
        DocumentRows,
        EmbeddingIdentity,
        ExpectedToken,
        RevisionToken,
    )


@dataclass(frozen=True)
class Snapshot:
    """One committed state: what every read observes, whole."""

    identity: EmbeddingIdentity
    generation: int
    matrix: Any  # np.ndarray (n, dimension), float32, read-only
    rows: tuple[dict[str, Any], ...]
    tokens: Mapping[str, RevisionToken | None]

    @cached_property
    def by_path(self) -> Mapping[str, tuple[int, ...]]:
        """Each path's row positions, built once per snapshot on first use."""
        index: dict[str, list[int]] = {}
        for i, row in enumerate(self.rows):
            path = row.get("path")
            if path is not None:
                index.setdefault(path, []).append(i)
        return MappingProxyType({path: tuple(at) for path, at in index.items()})


def frozen(matrix: Any) -> Any:
    """Mark *matrix* read-only and return it."""
    matrix.setflags(write=False)
    return matrix


def fresh_snapshot(identity: EmbeddingIdentity) -> Snapshot:
    """Return an empty snapshot at *identity*."""
    empty = frozen(np.zeros((0, identity.dimension), dtype=np.float32))
    return Snapshot(identity, 0, empty, (), MappingProxyType({}))


def hollow_snapshot(identity: EmbeddingIdentity, generation: int) -> Snapshot:
    """Return a rowless snapshot carrying only *identity* and *generation*.

    What a streamed store keeps resident (#1377): its rows stay in the
    database.
    """
    empty = fresh_snapshot(identity)
    return Snapshot(identity, generation, empty.matrix, (), empty.tokens)


def _normalised(vectors: Any) -> Any:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return (vectors / norms).astype(np.float32, copy=False)


def unit_vector(vector: Any) -> Any:
    """Return *vector* scaled to unit length; a zero vector unchanged."""
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def row_vectors(rows: Sequence[VectorRow], dimension: int) -> Any:
    """Return *rows*' vectors as a normalised matrix, refusing bad ones.

    Raises:
        ValueError: If a row is of another dimension or not finite.
    """
    if not rows:
        return np.zeros((0, dimension), dtype=np.float32)
    try:
        vectors = np.asarray([row.vector for row in rows], dtype=np.float32)
    except ValueError as exc:
        raise ValueError(f"Rows of unequal dimension: {exc}") from exc
    if vectors.ndim != 2 or vectors.shape[1] != dimension:
        raise ValueError(
            f"Embedding dimension mismatch: the store holds dimension {dimension}, "
            f"the rows have {vectors.shape[-1]}."
        )
    if not np.isfinite(vectors).all():
        raise ValueError(
            "Embedding values must be finite; a row holds NaN or infinity."
        )
    return _normalised(vectors)


def row_metadata(document: DocumentRows) -> list[dict[str, Any]]:
    """Return *document*'s row metadata, each naming the document's path."""
    return [{**row.metadata, "path": document.path} for row in document.rows]


def conflicts(snap: Snapshot, path: str, expected: ExpectedToken) -> bool:
    """Whether *path*'s token in *snap* is not the one the writer expects."""
    return expected is not ANY_TOKEN and snap.tokens.get(path) != expected


def with_document(snap: Snapshot, document: DocumentRows, vectors: Any) -> Snapshot:
    """Return *snap* with ``document.path``'s rows replaced by *vectors*."""
    keep = [i for i, row in enumerate(snap.rows) if row.get("path") != document.path]
    matrix = np.vstack([snap.matrix[keep], vectors])
    rows = tuple(snap.rows[i] for i in keep) + tuple(row_metadata(document))
    tokens = {**snap.tokens, document.path: document.token}
    return Snapshot(
        snap.identity, snap.generation, frozen(matrix), rows, MappingProxyType(tokens)
    )


def without_document(snap: Snapshot, path: str) -> Snapshot | None:
    """Return *snap* without *path*'s rows and token; ``None`` if it holds neither."""
    keep = [i for i, row in enumerate(snap.rows) if row.get("path") != path]
    if len(keep) == len(snap.rows) and path not in snap.tokens:
        return None
    tokens = {k: v for k, v in snap.tokens.items() if k != path}
    return Snapshot(
        snap.identity,
        snap.generation,
        frozen(snap.matrix[keep]),
        tuple(snap.rows[i] for i in keep),
        MappingProxyType(tokens),
    )


def validated_generation(
    identity: EmbeddingIdentity, documents: Iterable[DocumentRows]
) -> tuple[list[DocumentRows], list[Any]]:
    """Return *documents* and each one's normalised vectors, refusing bad input.

    Raises:
        ValueError: If a path appears twice, or a row is of another
            dimension or not finite.
    """
    docs = list(documents)
    seen: set[str] = set()
    for document in docs:
        if document.path in seen:
            raise ValueError(f"publish_generation got {document.path} twice.")
        seen.add(document.path)
    return docs, [row_vectors(d.rows, identity.dimension) for d in docs]


def generation_snapshot(
    identity: EmbeddingIdentity,
    generation: int,
    documents: Sequence[DocumentRows],
    blocks: Sequence[Any],
) -> Snapshot:
    """Return the snapshot holding exactly *documents*, with *blocks* as their vectors."""
    matrix = (
        np.vstack(blocks)
        if blocks
        else np.zeros((0, identity.dimension), dtype=np.float32)
    )
    rows = tuple(meta for d in documents for meta in row_metadata(d))
    tokens = MappingProxyType({d.path: d.token for d in documents})
    return Snapshot(identity, generation, frozen(matrix), rows, tokens)


class ResidentStore:
    """The reads of a store served from an installed :class:`Snapshot`.

    A streamed SQLite store overrides these reads with queries (#1377).
    A subclass publishes: it builds the next snapshot, persists it, and
    assigns it to ``_snap`` under ``_lock``.

    Args:
        snapshot: The state to serve first.
        read_only: Whether writes are refused.
    """

    def __init__(self, snapshot: Snapshot, *, read_only: bool) -> None:
        self._snap = snapshot
        self._read_only = read_only
        self._closed = False
        self._lock = threading.Lock()

    def _current(self) -> Snapshot:
        if self._closed:
            raise RuntimeError("The vector store is closed.")
        return self._snap

    def _writable(self) -> Snapshot:
        snap = self._current()
        if self._read_only:
            raise ReadOnlyError(
                "This vector store was opened read-only: another process owns its files."
            )
        return snap

    @property
    def identity(self) -> EmbeddingIdentity:
        """The embedding space of the stored rows."""
        return self._current().identity

    @property
    def generation(self) -> int:
        """Bumped by each ``publish_generation``."""
        return self._current().generation

    @property
    def count(self) -> int:
        """The number of stored rows."""
        return len(self._current().rows)

    def search(
        self,
        vector: Sequence[float],
        *,
        limit: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        folder: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the rows nearest *vector*; see :class:`~markdown_vault_mcp.interfaces.VectorReader`.

        Raises:
            ValueError: If *vector* is not of the store's dimension.
        """
        snap = self._current()
        if not snap.rows or limit <= 0:
            return []
        query = np.asarray(vector, dtype=np.float32)
        if query.shape != (snap.matrix.shape[1],):
            raise ValueError(
                f"Query dimension mismatch: the store holds dimension "
                f"{snap.matrix.shape[1]}, the query has {query.shape[-1]}."
            )
        scores = snap.matrix @ unit_vector(query)
        results: list[dict[str, Any]] = []
        for index in np.argsort(scores)[::-1]:
            i = int(index)
            row = snap.rows[i]
            if folder is not None and not folder_matches(row.get("folder", ""), folder):
                continue
            if predicate is not None and not predicate(row):
                continue
            results.append({**row, "score": float(scores[i])})
            if len(results) >= limit:
                break
        return results

    def similar_to_document(
        self,
        path: str,
        *,
        limit: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        folder: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the rows nearest the mean of *path*'s rows, excluding *path*."""
        snap = self._current()
        if not snap.rows or limit <= 0:
            return []
        own = [i for i, row in enumerate(snap.rows) if row.get("path") == path]
        if not own:
            return []
        scores = snap.matrix @ unit_vector(np.mean(snap.matrix[own], axis=0))
        candidates = [
            (float(score), i)
            for i, score in enumerate(scores)
            if snap.rows[i].get("path") != path
            and (
                folder is None or folder_matches(snap.rows[i].get("folder", ""), folder)
            )
            and (predicate is None or predicate(snap.rows[i]))
        ]
        candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        return [{**snap.rows[i], "score": score} for score, i in candidates[:limit]]

    def rows_for(self, path: str) -> list[VectorRow]:
        """Return *path*'s rows, vectors normalised, in storage order."""
        snap = self._current()
        return [
            VectorRow(vector=snap.matrix[i].tolist(), metadata=dict(snap.rows[i]))
            for i in snap.by_path.get(path, ())
        ]

    def tokens(self) -> dict[str, RevisionToken | None]:
        """Return each stored document's token; ``None`` when unknown."""
        snap = self._current()
        found: dict[str, RevisionToken | None] = {
            row["path"]: None for row in snap.rows if "path" in row
        }
        found.update(snap.tokens)
        return found
