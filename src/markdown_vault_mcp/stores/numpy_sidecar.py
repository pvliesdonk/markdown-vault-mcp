"""The numpy sidecar vector backend (#1767).

Reads are served from a resident snapshot (:mod:`._resident`): one float32
matrix with a parallel metadata list, replaced whole by each write.

On disk a store is ``{base}.v2.json``, the commit point, and the matrix it
names, ``{base}.v2.m{seq}.npy``. A persist writes the matrix under a new name
and then atomically replaces the JSON, so an interrupted persist leaves the
previous state whole. Until the first persist, a store with no
``{base}.v2.json`` opens the sidecar a release wrote (``{base}.json`` beside
``{base}.npy``, its rows without tokens) or an edge build wrote
(``{base}.json`` naming ``{base}.m{seq}.npy``) in place; no code writes,
renames or deletes those files, so a downgrade still finds them (#1505).
Once ``{base}.v2.json`` exists, a writer's open logs them as unused.

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

import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import IO, TYPE_CHECKING, Any

from markdown_vault_mcp.exceptions import (
    StoreCorruptError,
    StoreIncompatibleError,
    StoreOpenError,
    StoreUnavailableError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.stores._resident import (
    NUMPY_AVAILABLE,
    ResidentStore,
    Snapshot,
    conflicts,
    fresh_snapshot,
    frozen,
    generation_snapshot,
    row_vectors,
    validated_generation,
    with_document,
    without_document,
)
from markdown_vault_mcp.stores.registry import register_backend
from markdown_vault_mcp.types import EmbeddingIdentity, StoreResources
from markdown_vault_mcp.utils.fs import file_bytes, is_directory, path_exists

with contextlib.suppress(ImportError):  # numpy is the embeddings extra
    import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

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
_COMMIT_SUFFIX = ".v2.json"
_LEGACY_JSON_SUFFIX = ".json"


def commit_path(base: Path) -> Path:
    """Return the store's commit file for *base*: ``{base}.v2.json``.

    Names derive with ``Path.with_suffix``, so a base that carries an
    extension (``emb.npy``) names ``emb.v2.json`` (#819).
    """
    return base.with_suffix(_COMMIT_SUFFIX)


def legacy_paths(base: Path) -> tuple[Path, ...]:
    """Return the released and edge-build sidecar files beside *base*.

    ``{base}.json`` and ``{base}.npy``, whether or not they exist, and every
    ``{base}.m{seq}.npy`` that exists; the store never writes, renames or
    deletes them.
    """
    return (
        base.with_suffix(_LEGACY_JSON_SUFFIX),
        base.with_suffix(".npy"),
        *_numbered_matrices(base, prefix=".m").values(),
    )


def committed_json(base: Path) -> Path | None:
    """Return the JSON a reader of *base* opens, or ``None`` if neither exists.

    The store's own ``{base}.v2.json`` once committed, otherwise a released
    or edge-build ``{base}.json``.

    Raises:
        OSError: If a stat is refused.
    """
    for path in (commit_path(base), base.with_suffix(_LEGACY_JSON_SUFFIX)):
        if path_exists(path):
            return path
    return None


def _matrix_path(base: Path, seq: int) -> Path:
    return base.with_suffix(f".v2.m{seq}.npy")


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


def _numbered_matrices(base: Path, *, prefix: str = ".v2.m") -> dict[int, Path]:
    """Return the ``{base}{prefix}{seq}.npy`` matrices beside *base*, by ``seq``.

    The default prefix names this store's own matrices; ``.m`` names an
    edge build's.
    """
    prefix, suffix = base.with_suffix(prefix).name, ".npy"
    found: dict[int, Path] = {}
    for path in base.parent.glob(f"{prefix}*{suffix}"):
        seq = path.name[len(prefix) : -len(suffix)]
        if seq.isdigit():
            found[int(seq)] = path
    return found


def _sweep(base: Path, keep: Path) -> None:
    """Remove this store's matrices beside *base* except *keep*: superseded or orphaned.

    Only ``{base}.v2.m{seq}.npy``: a released or edge-build sidecar is never
    removed (#1505).
    """
    for path in _numbered_matrices(base).values():
        if path == keep:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("numpy_store_stale_matrix_kept path=%s err=%s", path, exc)


def _highest_seq(base: Path) -> int:
    """Return the highest ``m{seq}`` matrix number beside *base*, 0 if none."""
    return max(_numbered_matrices(base), default=0)


def _payload(snap: Snapshot, matrix_name: str, seq: int) -> bytes:
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


_OWN_IDENTITY = object()


class NumpySidecarStore(ResidentStore):
    """A vector store held in memory, optionally persisted beside a base path.

    Construct through :class:`NumpySidecarBackend`.
    """

    def __init__(
        self,
        snapshot: Snapshot,
        *,
        base: Path | None,
        read_only: bool,
        seq: int,
        matrix_path: Path | None,
        json_path: Path | None = None,
        recorded_identity: EmbeddingIdentity | object | None = _OWN_IDENTITY,
    ) -> None:
        super().__init__(snapshot, read_only=read_only)
        # What the opened files recorded; _OWN_IDENTITY once this store wrote
        # them, or when nothing was read.
        self._recorded = recorded_identity
        self._base = base
        self._seq = seq
        self._matrix_path = matrix_path
        # The commit file the snapshot came from: a sidecar opened in place
        # until this store's first persist commits ``{base}.v2.json``.
        self._json_path = json_path
        self._dirty = False

    # -- publishing ----------------------------------------------------------

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
            if conflicts(snap, document.path, expected_token):
                return "conflict"
            vectors = row_vectors(document.rows, snap.identity.dimension)
            self._snap = with_document(snap, document, vectors)
            self._dirty = True
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
            if conflicts(snap, path, expected_token):
                return "conflict"
            new = without_document(snap, path)
            if new is not None:
                self._snap = new
                self._dirty = True
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
        docs, blocks = validated_generation(identity, documents)
        with self._lock:
            snap = self._writable()
            new = generation_snapshot(identity, snap.generation + 1, docs, blocks)
            self._persist(new)
            self._snap = new
            self._dirty = False
        logger.info(
            "numpy_store_generation_published generation=%d rows=%d",
            new.generation,
            len(new.rows),
        )

    @property
    def recorded_identity(self) -> EmbeddingIdentity | None:
        """The identity the opened files recorded, or ``None`` when they recorded none.

        ``None`` for a bare-list sidecar, which opens under whatever identity
        it is asked for, and for one whose ``index_metadata`` names no
        positive dimension; a released sidecar's missing variant and text
        format read as ``""`` and ``v1``, as :func:`_check_identity` reads
        them. A store that wrote its own files, or read none, records its
        :attr:`identity`. A donor never matches a ``None`` (#1505).
        """
        if self._recorded is _OWN_IDENTITY:
            return self.identity
        return self._recorded  # type: ignore[return-value]

    def resources(self) -> StoreResources:
        """Return the search mode and measured sizes (#1377): always resident.

        Raises:
            RuntimeError: If the store is closed.
            OSError: If a stat of the store's files is refused.
        """
        snap = self._current()
        stored = None
        if self._base is not None:
            # A checkpoint may swap and sweep the matrix between reading its
            # name and stating it; then the files are measured once more.
            for _attempt in range(2):
                matrix = self._matrix_path
                stored = self._stored_bytes(
                    self._json_path or commit_path(self._base), matrix
                )
                if self._matrix_path == matrix:
                    break
        return StoreResources(
            search_mode="resident",
            search_workspace_bytes=None,
            stored_bytes=stored,
            resident_vector_bytes=int(snap.matrix.nbytes),
        )

    @staticmethod
    def _stored_bytes(json_path: Path, matrix: Path | None) -> int:
        """Return the size of *json_path* and *matrix*, a missing one 0.

        *json_path* is the commit file this store's snapshot came from, so a
        reader that opened a sidecar in place keeps measuring that sidecar
        after a writer commits ``{base}.v2.json``.

        Raises:
            OSError: If a stat is refused (#1625).
        """
        files = [
            json_path,
            *([matrix] if matrix is not None else []),
        ]
        return sum(file_bytes(p) for p in files)

    # -- lifecycle -----------------------------------------------------------

    def _persist(self, snap: Snapshot) -> None:
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
        _commit_file(commit_path(self._base), data)
        self._seq, self._matrix_path = seq, matrix_path
        self._json_path = commit_path(self._base)
        self._recorded = _OWN_IDENTITY
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
) -> Snapshot:
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
    return Snapshot(
        identity,
        stored.generation,
        frozen(np.array(matrix, dtype=np.float32)),
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
    v2_json, legacy = commit_path(base), base.with_suffix(".npy")
    try:
        if path_exists(base.parent) and not is_directory(base.parent):
            raise _outcome(
                StoreUnavailableError,
                f"Vector store at {where} cannot be opened: {base.parent} is not a directory.",
                where,
            )
        json_path = committed_json(base) or v2_json
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
                f"Vector store at {where} has {legacy.name} but no "
                f"{base.with_suffix(_LEGACY_JSON_SUFFIX).name}.",
                where,
            )
        return NumpySidecarStore(
            fresh_snapshot(identity),
            base=base,
            read_only=read_only,
            seq=0,
            matrix_path=None,
        )
    stored, matrix = _load_committed(json_path, base, where)
    _check_identity(stored, matrix, identity, where)
    if json_path == v2_json and not read_only:
        _report_unused_sidecar(base)
    snap = _snapshot_from(stored, matrix, identity, where)
    logger.info(
        "numpy_store_opened path=%s rows=%d generation=%d format_version=%d",
        where,
        len(snap.rows),
        snap.generation,
        stored.version,
    )
    # A sidecar opened in place keeps its own numbering; this store's next
    # matrix numbers above its own ``.v2.m*`` files only.
    in_place = json_path != v2_json
    return NumpySidecarStore(
        snap,
        base=base,
        read_only=read_only,
        seq=_highest_seq(base) if in_place else stored.seq,
        matrix_path=stored.matrix_path,
        json_path=json_path,
        recorded_identity=_recorded_identity(stored, identity),
    )


def _recorded_identity(
    stored: _Stored, identity: EmbeddingIdentity
) -> EmbeddingIdentity | None:
    """Return the identity *stored*'s metadata recorded, or ``None`` if it recorded none.

    Called after :func:`_check_identity`, which refuses metadata naming
    another provider, model, variant or text format (missing ones read as
    the released defaults), so only the dimension can still differ: a
    request for dimension 0 does not know it yet.
    """
    if stored.meta is None or stored.dimension <= 0:
        return None
    return replace(identity, dimension=stored.dimension)


def _report_unused_sidecar(base: Path) -> None:
    """Log the released or edge-build files a committed v2 store left behind.

    Informational: a refused stat skips the file rather than failing the open.
    """
    present = 0
    found = False
    for path in legacy_paths(base):
        with contextlib.suppress(OSError):
            if path_exists(path):
                present += file_bytes(path)
                found = True
    if found:
        logger.info(
            "embeddings_legacy_sidecar_unused path=%s bytes=%d", str(base), present
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
        if not NUMPY_AVAILABLE:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "NumpySidecarBackend requires 'numpy'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            )
        if not isinstance(identity, EmbeddingIdentity):
            raise ValueError("A vector store opens against an EmbeddingIdentity.")
        if location is None or str(location) == ":memory:":
            return NumpySidecarStore(
                fresh_snapshot(identity),
                base=None,
                read_only=False,
                seq=0,
                matrix_path=None,
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
        if not NUMPY_AVAILABLE:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "NumpySidecarBackend requires 'numpy'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            )
        if location is None or str(location) == ":memory:":
            return NumpySidecarStore(
                fresh_snapshot(identity),
                base=None,
                read_only=False,
                seq=0,
                matrix_path=None,
            )
        base = Path(location)
        seq = _highest_seq(base) if is_directory(base.parent) else 0
        # Not dirty: a rebuild that writes nothing (every provider call
        # failed) must not commit an empty store over the previous one.
        return NumpySidecarStore(
            fresh_snapshot(identity),
            base=base,
            read_only=False,
            seq=seq,
            matrix_path=None,
        )


register_backend("vector", _BACKEND_NAME, NumpySidecarBackend)
