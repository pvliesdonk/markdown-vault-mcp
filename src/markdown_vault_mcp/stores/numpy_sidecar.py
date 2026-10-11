"""The numpy sidecar vector backend (#1767).

Rows live in memory as one float32 matrix with a parallel metadata list,
held together with the tokens, identity and generation in one immutable
snapshot. A writer builds a new snapshot and replaces the old one with a
single reference assignment, so a reader sees the old rows or the new
ones, never a mix.

On disk a store is ``{base}.json``, the commit point, and the matrix it
names, ``{base}.m{seq}.npy``. A persist writes the matrix under a new name
and then atomically replaces the JSON, so an interrupted persist leaves the
previous state whole. A sidecar written before #1767 (``{base}.npy`` beside
a JSON that names no matrix) opens as it is; its rows carry no token.

``replace_document`` and ``remove_document`` publish in memory at once and
become durable at :meth:`NumpySidecarStore.checkpoint` or ``close()``;
``publish_generation`` is durable before it returns.

Reachable open outcomes: *unavailable* (a parent that is not a directory,
a refused read), *corrupt* (a JSON or matrix that does not parse, a missing
matrix, rows and matrix disagreeing), *unsupported schema* (a
``format_version`` above :data:`FORMAT_VERSION`) and *incompatible
identity*. *Not configured* cannot happen: no location means in memory.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import IO, TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
    StoreCorruptError,
    StoreIncompatibleError,
    StoreOpenError,
    StoreUnavailableError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.stores.registry import register_backend
from markdown_vault_mcp.types import ANY_TOKEN, EmbeddingIdentity, VectorRow
from markdown_vault_mcp.utils import folder_matches
from markdown_vault_mcp.utils.fs import is_directory, path_exists

try:
    import numpy as np

    _NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _NUMPY_AVAILABLE = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from markdown_vault_mcp.types import (
        DocumentRows,
        ExpectedToken,
        PublicationOutcome,
        RevisionToken,
        StoreFamily,
    )

logger = logging.getLogger(__name__)

#: The on-disk layout this code writes and the highest it reads.
FORMAT_VERSION = 2

_BACKEND_NAME = "numpy"


@dataclass(frozen=True)
class _Snapshot:
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


def _frozen(matrix: Any) -> Any:
    matrix.setflags(write=False)
    return matrix


def _fresh(identity: EmbeddingIdentity) -> _Snapshot:
    empty = _frozen(np.zeros((0, identity.dimension), dtype=np.float32))
    return _Snapshot(identity, 0, empty, (), MappingProxyType({}))


def _normalised(vectors: Any) -> Any:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return (vectors / norms).astype(np.float32, copy=False)


def _unit(vector: Any) -> Any:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def _row_vectors(rows: Sequence[VectorRow], dimension: int) -> Any:
    """Return *rows*' vectors as a normalised matrix, refusing bad ones."""
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


def _metadata(document: DocumentRows) -> list[dict[str, Any]]:
    return [{**row.metadata, "path": document.path} for row in document.rows]


def _matrix_path(base: Path, seq: int) -> Path:
    return base.with_suffix(f".m{seq}.npy")


def _write_atomic(target: Path, write: Callable[[IO[bytes]], object]) -> None:
    """Write *target* through a synced temp file and one atomic rename."""
    fd, tmp = tempfile.mkstemp(
        dir=target.parent, prefix=f"{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _commit_file(path: Path, data: bytes) -> None:
    """Atomically replace the JSON commit file: the moment a persist lands."""
    _write_atomic(path, lambda fh: fh.write(data))


def _numbered_matrices(base: Path) -> dict[int, Path]:
    """Return the ``{base}.m{seq}.npy`` matrices beside *base*, by ``seq``."""
    prefix, suffix = base.with_suffix(".m").name, ".npy"
    found: dict[int, Path] = {}
    for path in base.parent.glob(f"{prefix}*{suffix}"):
        seq = path.name[len(prefix) : -len(suffix)]
        if seq.isdigit():
            found[int(seq)] = path
    return found


def _sweep(base: Path, keep: Path) -> None:
    """Remove every matrix beside *base* except *keep*: superseded or orphaned."""
    stale = [base.with_suffix(".npy"), *_numbered_matrices(base).values()]
    for path in stale:
        if path == keep:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("numpy_store_stale_matrix_kept path=%s err=%s", path, exc)


def _highest_seq(base: Path) -> int:
    """Return the highest ``m{seq}`` matrix number beside *base*, 0 if none."""
    return max(_numbered_matrices(base), default=0)


def _payload(snap: _Snapshot, matrix_name: str, seq: int) -> bytes:
    ident = snap.identity
    body = {
        "format_version": FORMAT_VERSION,
        "index_metadata": {
            "provider": ident.provider,
            "model": ident.model,
            "provider_variant": ident.variant,
            "embed_text_format": ident.text_format,
            "dimension": ident.dimension,
            "generation": snap.generation,
            "matrix": matrix_name,
            "matrix_seq": seq,
        },
        "rows": list(snap.rows),
        "tokens": dict(snap.tokens),
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


class NumpySidecarStore:
    """A vector store held in memory, optionally persisted beside a base path.

    Construct through :class:`NumpySidecarBackend`.
    """

    def __init__(
        self,
        snapshot: _Snapshot,
        *,
        base: Path | None,
        read_only: bool,
        seq: int,
        matrix_path: Path | None,
    ) -> None:
        self._snap = snapshot
        self._base = base
        self._read_only = read_only
        self._seq = seq
        self._matrix_path = matrix_path
        self._dirty = False
        self._closed = False
        self._lock = threading.Lock()

    # -- reading -------------------------------------------------------------

    def _current(self) -> _Snapshot:
        if self._closed:
            raise RuntimeError("The vector store is closed.")
        return self._snap

    @property
    def identity(self) -> EmbeddingIdentity:
        """The embedding space of the stored rows."""
        return self._current().identity

    @property
    def generation(self) -> int:
        """Bumped by each :meth:`publish_generation`."""
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
        scores = snap.matrix @ _unit(query)
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
    ) -> list[dict[str, Any]]:
        """Return the rows nearest the mean of *path*'s rows, excluding *path*."""
        snap = self._current()
        if not snap.rows or limit <= 0:
            return []
        own = [i for i, row in enumerate(snap.rows) if row.get("path") == path]
        if not own:
            return []
        scores = snap.matrix @ _unit(np.mean(snap.matrix[own], axis=0))
        candidates = [
            (float(score), i)
            for i, score in enumerate(scores)
            if snap.rows[i].get("path") != path
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

    # -- publishing ----------------------------------------------------------

    def _writable(self) -> _Snapshot:
        snap = self._current()
        if self._read_only:
            raise ReadOnlyError(
                "This vector store was opened read-only: another process owns its files."
            )
        return snap

    @staticmethod
    def _conflicts(snap: _Snapshot, path: str, expected: ExpectedToken) -> bool:
        return expected is not ANY_TOKEN and snap.tokens.get(path) != expected

    def _swap(
        self,
        snap: _Snapshot,
        matrix: Any,
        rows: tuple[dict[str, Any], ...],
        tokens: dict[str, RevisionToken | None],
    ) -> None:
        self._snap = _Snapshot(
            snap.identity,
            snap.generation,
            _frozen(matrix),
            rows,
            MappingProxyType(tokens),
        )
        self._dirty = True

    def replace_document(
        self, document: DocumentRows, *, expected_token: ExpectedToken
    ) -> PublicationOutcome:
        """Replace ``document.path``'s rows; see :class:`~markdown_vault_mcp.interfaces.VectorPublisher`.

        Raises:
            ReadOnlyError: If the store was opened read-only.
            ValueError: If a row is of another dimension or not finite.
        """
        with self._lock:
            snap = self._writable()
            if self._conflicts(snap, document.path, expected_token):
                return "conflict"
            vectors = _row_vectors(document.rows, snap.identity.dimension)
            keep = [
                i for i, row in enumerate(snap.rows) if row.get("path") != document.path
            ]
            matrix = np.vstack([snap.matrix[keep], vectors])
            rows = tuple(snap.rows[i] for i in keep) + tuple(_metadata(document))
            tokens = {**snap.tokens, document.path: document.token}
            self._swap(snap, matrix, rows, tokens)
        return "published"

    def remove_document(
        self, path: str, *, expected_token: ExpectedToken
    ) -> PublicationOutcome:
        """Remove *path*'s rows and token, with the same condition.

        Raises:
            ReadOnlyError: If the store was opened read-only.
        """
        with self._lock:
            snap = self._writable()
            if self._conflicts(snap, path, expected_token):
                return "conflict"
            keep = [i for i, row in enumerate(snap.rows) if row.get("path") != path]
            if len(keep) == len(snap.rows) and path not in snap.tokens:
                return "published"
            tokens = {k: v for k, v in snap.tokens.items() if k != path}
            self._swap(
                snap, snap.matrix[keep], tuple(snap.rows[i] for i in keep), tokens
            )
        return "published"

    def publish_generation(
        self, identity: EmbeddingIdentity, documents: Iterable[DocumentRows]
    ) -> None:
        """Replace the store with *documents* under *identity*, durably.

        Raises:
            ReadOnlyError: If the store was opened read-only.
            ValueError: If a path appears twice, or a row is of another
                dimension or not finite.
        """
        docs = list(documents)
        seen: set[str] = set()
        for document in docs:
            if document.path in seen:
                raise ValueError(f"publish_generation got {document.path} twice.")
            seen.add(document.path)
        blocks = [_row_vectors(d.rows, identity.dimension) for d in docs]
        matrix = (
            np.vstack(blocks)
            if blocks
            else np.zeros((0, identity.dimension), dtype=np.float32)
        )
        rows = tuple(meta for d in docs for meta in _metadata(d))
        tokens = MappingProxyType({d.path: d.token for d in docs})
        with self._lock:
            snap = self._writable()
            new = _Snapshot(
                identity, snap.generation + 1, _frozen(matrix), rows, tokens
            )
            self._persist(new)
            self._snap = new
            self._dirty = False
        logger.info(
            "numpy_store_generation_published generation=%d rows=%d",
            new.generation,
            len(rows),
        )

    # -- lifecycle -----------------------------------------------------------

    def _persist(self, snap: _Snapshot) -> None:
        """Write *snap* to disk, committing with the JSON; a no-op in memory.

        The payload is encoded before any file is written, so metadata JSON
        cannot encode leaves the disk untouched.
        """
        if self._base is None:
            return
        seq = self._seq + 1
        matrix_path = _matrix_path(self._base, seq)
        data = _payload(snap, matrix_path.name, seq)
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(
            matrix_path, lambda fh: np.save(fh, np.ascontiguousarray(snap.matrix))
        )
        _commit_file(self._base.with_suffix(".json"), data)
        self._seq, self._matrix_path = seq, matrix_path
        _sweep(self._base, matrix_path)
        logger.debug(
            "numpy_store_checkpointed path=%s rows=%d generation=%d",
            self._base,
            len(snap.rows),
            snap.generation,
        )

    def checkpoint(self) -> None:
        """Persist pending changes; a no-op in memory, read-only or clean.

        Raises:
            RuntimeError: If the store is closed.
        """
        self._current()
        if self._base is None or self._read_only:
            return
        with self._lock:
            if not self._dirty:
                return
            self._persist(self._snap)
            self._dirty = False

    def close(self) -> None:
        """Persist pending changes and release the store. Idempotent.

        The store is closed afterwards even if the final persist raises.
        """
        if self._closed:
            return
        try:
            self.checkpoint()
        finally:
            self._closed = True


@dataclass(frozen=True)
class _Stored:
    """What a commit file says, before the matrix is read."""

    version: int
    meta: dict[str, Any] | None
    rows: list[dict[str, Any]]
    tokens: dict[str, RevisionToken | None]
    generation: int
    seq: int
    dimension: int
    matrix_path: Path


def _outcome(kind: type[StoreOpenError], message: str, where: str) -> StoreOpenError:
    return kind(message, backend=_BACKEND_NAME, location=where)


def _read_json(path: Path, where: str) -> Any:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise _outcome(
            StoreCorruptError, f"Vector store at {where} lost {path.name}.", where
        ) from exc
    except OSError as exc:
        raise _outcome(
            StoreUnavailableError,
            f"Vector store at {where} cannot be read: {exc}",
            where,
        ) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has an unreadable {path.name}: {exc}",
            where,
        ) from exc


def _matrix_from(meta: dict[str, Any], base: Path, version: int, where: str) -> Path:
    if version < 2:
        return base.with_suffix(".npy")
    name = meta.get("matrix")
    if not isinstance(name, str) or Path(name).name != name or not name:
        raise _outcome(
            StoreCorruptError, f"Vector store at {where} names no usable matrix.", where
        )
    return base.with_name(name)


def _count(meta: dict[str, Any], key: str, where: str) -> int:
    """Return a non-negative integer field of the commit file, or refuse it."""
    value = meta.get(key, 0)
    if value is None and key == "dimension":
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has a malformed {key}: {value!r}.",
            where,
        )
    return value


def _meta_of(payload: dict[str, Any], where: str) -> dict[str, Any]:
    meta = payload.get("index_metadata")
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has malformed index_metadata.",
            where,
        )
    return meta


def _tokens_of(
    payload: dict[str, Any], version: int, where: str
) -> dict[str, RevisionToken | None]:
    tokens = payload.get("tokens", {}) if version >= 2 else {}
    well_formed = isinstance(tokens, dict) and all(
        isinstance(k, str) and (v is None or isinstance(v, str))
        for k, v in tokens.items()
    )
    if not well_formed:
        raise _outcome(
            StoreCorruptError, f"Vector store at {where} has malformed tokens.", where
        )
    return dict(tokens)


def _parse(payload: Any, base: Path, where: str) -> _Stored:
    if isinstance(payload, list):
        logger.warning("numpy_store_legacy_rows_unverified path=%s", where)
        return _Stored(1, None, payload, {}, 0, 0, 0, base.with_suffix(".npy"))
    if not isinstance(payload, dict):
        raise _outcome(
            StoreCorruptError, f"Vector store at {where} has an unknown layout.", where
        )
    version = payload.get("format_version", 1)
    if not isinstance(version, int) or not 1 <= version <= FORMAT_VERSION:
        raise _outcome(
            StoreUnsupportedSchemaError,
            f"Vector store at {where} has format_version {version!r}; "
            f"this version reads up to {FORMAT_VERSION}.",
            where,
        )
    meta = _meta_of(payload, where)
    rows = payload.get("rows", [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise _outcome(
            StoreCorruptError, f"Vector store at {where} has malformed rows.", where
        )
    return _Stored(
        version,
        meta,
        rows,
        _tokens_of(payload, version, where),
        _count(meta, "generation", where),
        _count(meta, "matrix_seq", where),
        _count(meta, "dimension", where),
        _matrix_from(meta, base, version, where),
    )


def _read_matrix(path: Path, where: str) -> Any | None:
    """Return the matrix at *path*, or ``None`` when it is gone.

    A missing matrix may mean a writer committed a newer one and removed
    this one after the commit file was read, so the caller decides.
    """
    try:
        return np.load(path, allow_pickle=False).astype(np.float32, copy=False)
    except FileNotFoundError:
        return None
    except (ValueError, TypeError, EOFError) as exc:
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has an unreadable {path.name}: {exc}",
            where,
        ) from exc
    except OSError as exc:
        raise _outcome(
            StoreUnavailableError,
            f"Vector store at {where} cannot be read: {exc}",
            where,
        ) from exc


def _check_identity(
    stored: _Stored, matrix: Any, identity: EmbeddingIdentity, where: str
) -> None:
    if stored.meta is not None:
        persisted = (
            stored.meta.get("provider"),
            stored.meta.get("model"),
            stored.meta.get("provider_variant") or "",
            stored.meta.get("embed_text_format") or "v1",
        )
        expected = (
            identity.provider,
            identity.model,
            identity.variant,
            identity.text_format,
        )
        if persisted != expected:
            raise _outcome(
                StoreIncompatibleError,
                f"Vector store at {where} holds {persisted}, not {expected}.",
                where,
            )
    dimension = stored.dimension or (
        matrix.shape[1] if matrix.ndim == 2 and matrix.shape[0] else 0
    )
    # An identity of dimension 0 does not know it yet: the stored one stands.
    if dimension and identity.dimension and dimension != identity.dimension:
        raise _outcome(
            StoreIncompatibleError,
            f"Vector store at {where} holds dimension {dimension}, not {identity.dimension}.",
            where,
        )


def _snapshot_from(
    stored: _Stored, matrix: Any, identity: EmbeddingIdentity, where: str
) -> _Snapshot:
    if not identity.dimension:
        known = stored.dimension or (
            matrix.shape[1] if matrix.ndim == 2 and matrix.shape[0] else 0
        )
        identity = replace(identity, dimension=known)
    if matrix.size == 0:
        matrix = np.zeros((0, identity.dimension), dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != len(stored.rows):
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has {len(stored.rows)} rows but a "
            f"{matrix.shape} matrix.",
            where,
        )
    if matrix.shape[0] and matrix.shape[1] != identity.dimension:
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} records dimension {identity.dimension} but its "
            f"matrix has {matrix.shape[1]} columns.",
            where,
        )
    return _Snapshot(
        identity,
        stored.generation,
        _frozen(np.array(matrix, dtype=np.float32)),
        tuple(stored.rows),
        MappingProxyType(stored.tokens),
    )


def _load_committed(json_path: Path, base: Path, where: str) -> tuple[_Stored, Any]:
    """Read the commit file and the matrix it names.

    A writer in another process may commit a newer matrix and remove the
    named one between the two reads; the commit file is read again once
    before the store is called corrupt.
    """
    for _ in range(2):
        stored = _parse(_read_json(json_path, where), base, where)
        matrix = _read_matrix(stored.matrix_path, where)
        if matrix is not None:
            return stored, matrix
    raise _outcome(
        StoreCorruptError,
        f"Vector store at {where} lost {stored.matrix_path.name}.",
        where,
    )


def _open_file(
    base: Path, identity: EmbeddingIdentity, *, read_only: bool
) -> NumpySidecarStore:
    where = str(base)
    json_path, legacy = base.with_suffix(".json"), base.with_suffix(".npy")
    try:
        if path_exists(base.parent) and not is_directory(base.parent):
            raise _outcome(
                StoreUnavailableError,
                f"Vector store at {where} cannot be opened: {base.parent} is not a directory.",
                where,
            )
        has_json, has_legacy = path_exists(json_path), path_exists(legacy)
    except OSError as exc:
        raise _outcome(
            StoreUnavailableError,
            f"Vector store at {where} cannot be checked: {exc}",
            where,
        ) from exc
    if not has_json:
        if has_legacy:
            raise _outcome(
                StoreCorruptError,
                f"Vector store at {where} has {legacy.name} but no {json_path.name}.",
                where,
            )
        return NumpySidecarStore(
            _fresh(identity), base=base, read_only=read_only, seq=0, matrix_path=None
        )
    stored, matrix = _load_committed(json_path, base, where)
    _check_identity(stored, matrix, identity, where)
    snap = _snapshot_from(stored, matrix, identity, where)
    logger.info(
        "numpy_store_opened path=%s rows=%d generation=%d format_version=%d",
        where,
        len(snap.rows),
        snap.generation,
        stored.version,
    )
    return NumpySidecarStore(
        snap,
        base=base,
        read_only=read_only,
        seq=stored.seq,
        matrix_path=stored.matrix_path,
    )


class NumpySidecarBackend:
    """Open a :class:`NumpySidecarStore` at a base path, or in memory."""

    family: StoreFamily = "vector"
    name = _BACKEND_NAME

    def open(
        self,
        location: Path | str | None,
        identity: object | None = None,
        *,
        owns_files: bool = True,
    ) -> NumpySidecarStore:
        """Open the store.

        Args:
            location: The base path of the sidecar files, or ``None`` /
                ``":memory:"`` for a store held in memory.
            identity: The :class:`~markdown_vault_mcp.types.EmbeddingIdentity`
                the rows must belong to.
            owns_files: ``False`` opens files read-only; ignored in memory.

        Returns:
            The open store.

        Raises:
            ImportError: If numpy is not installed.
            ValueError: If *identity* is not an ``EmbeddingIdentity``.
            StoreUnavailableError: If the files cannot be reached.
            StoreCorruptError: If they do not form a store.
            StoreUnsupportedSchemaError: If their format is newer.
            StoreIncompatibleError: If they hold another identity.
        """
        if not _NUMPY_AVAILABLE:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "NumpySidecarBackend requires 'numpy'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            )
        if not isinstance(identity, EmbeddingIdentity):
            raise ValueError("A vector store opens against an EmbeddingIdentity.")
        if location is None or str(location) == ":memory:":
            return NumpySidecarStore(
                _fresh(identity), base=None, read_only=False, seq=0, matrix_path=None
            )
        return _open_file(Path(location), identity, read_only=not owns_files)

    def open_fresh(
        self, location: Path | str | None, identity: EmbeddingIdentity
    ) -> NumpySidecarStore:
        """Open *location* as an empty store at *identity*, reading nothing there.

        The next persist commits over whatever the location held, under a
        matrix name above every one already there.

        Raises:
            ImportError: If numpy is not installed.
        """
        if not _NUMPY_AVAILABLE:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "NumpySidecarBackend requires 'numpy'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            )
        if location is None or str(location) == ":memory:":
            return NumpySidecarStore(
                _fresh(identity), base=None, read_only=False, seq=0, matrix_path=None
            )
        base = Path(location)
        seq = _highest_seq(base) if is_directory(base.parent) else 0
        # Not dirty: a rebuild that writes nothing (every provider call
        # failed) must not commit an empty store over the previous one.
        return NumpySidecarStore(
            _fresh(identity), base=base, read_only=False, seq=seq, matrix_path=None
        )


register_backend("vector", _BACKEND_NAME, NumpySidecarBackend)
