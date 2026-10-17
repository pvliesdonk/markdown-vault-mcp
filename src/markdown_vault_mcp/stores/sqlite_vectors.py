"""The SQLite vector backend (#1497).

Rows live in their own SQLite database: one ``chunks`` row per vector (a
float32 BLOB and its metadata as a JSON object), one ``documents`` row per
document with its token, and a ``meta`` table holding the format version,
the identity and the generation. Reads are served from a resident snapshot
loaded at open (:mod:`._resident`); #1377 replaces that with a bounded scan
over the same tables.

Each write is one ``BEGIN IMMEDIATE`` transaction that checks the token the
database holds, writes, and commits with ``synchronous=FULL``; only then is
the next snapshot installed. A published write is durable when it returns,
and a failed one leaves the database's rows and the reads as they were.
``checkpoint()`` only folds the WAL into the main file. What WAL with
``synchronous=FULL`` promises, and why the WAL-reset bug does not reach this
single-writer use, is recorded in
``docs/design/reference/sqlite-wal-durability.md``.

A store from :meth:`SqliteVectorBackend.open_fresh` reads nothing at its
location. Before its first write it removes a file that is not a vector
store or whose pages are damaged, whatever that write's outcome; the write
then empties a usable database inside its own transaction, so a conflict
or an interrupt keeps the old rows. Until then a reader still opens them.

Reachable open outcomes: *unavailable* (a parent that is not a directory,
a refused open), *corrupt* (not a database, not a vector store, a vector
of the wrong length, metadata that is not an object), *unsupported schema*
(a ``format_version`` above :data:`FORMAT_VERSION`) and *incompatible
identity*. *Not configured* cannot happen: no location means in memory.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp._fts_connection import SqliteConnectionRegistry
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
    row_metadata,
    row_vectors,
    validated_generation,
    with_document,
    without_document,
)
from markdown_vault_mcp.stores.registry import register_backend
from markdown_vault_mcp.types import ANY_TOKEN, EmbeddingIdentity
from markdown_vault_mcp.utils.fs import is_directory, path_exists

with contextlib.suppress(ImportError):  # numpy is the embeddings extra
    import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from markdown_vault_mcp.types import (
        DocumentRows,
        ExpectedToken,
        PublicationOutcome,
        RevisionToken,
        StoreFamily,
    )

logger = logging.getLogger(__name__)

#: The on-disk layout this code writes and the highest it reads.
FORMAT_VERSION = 1

_BACKEND_NAME = "sqlite"
_SUFFIX = ".sqlite3"
_MEMORY = ":memory:"
_TABLES = frozenset({"meta", "documents", "chunks"})
_CORRUPT_ERRORNAMES = frozenset({"SQLITE_NOTADB", "SQLITE_CORRUPT"})

_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE documents (path TEXT PRIMARY KEY, token TEXT)",
    "CREATE TABLE chunks ("
    " id INTEGER PRIMARY KEY,"
    " path TEXT NOT NULL REFERENCES documents(path) ON DELETE CASCADE,"
    " ordinal INTEGER NOT NULL,"
    " folder TEXT NOT NULL,"
    " vector BLOB NOT NULL,"
    " metadata TEXT NOT NULL,"
    " UNIQUE (path, ordinal))",
)
_UPSERT_DOCUMENT = (
    "INSERT INTO documents(path, token) VALUES (?, ?) "
    "ON CONFLICT(path) DO UPDATE SET token = excluded.token"
)
_INSERT_CHUNK = (
    "INSERT INTO chunks(path, ordinal, folder, vector, metadata) VALUES (?, ?, ?, ?, ?)"
)
_UPSERT_META = (
    "INSERT INTO meta(key, value) VALUES (?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)


def _require_numpy() -> None:
    if not NUMPY_AVAILABLE:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "SqliteVectorBackend requires 'numpy'. "
            "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
        )


def _db_file(location: Path | str) -> Path:
    return Path(location).with_suffix(_SUFFIX)


def _outcome(kind: type[StoreOpenError], message: str, where: str) -> StoreOpenError:
    return kind(message, backend=_BACKEND_NAME, location=where)


def _classified(exc: sqlite3.Error, where: str) -> StoreOpenError:
    """Return the open outcome a SQLite error stands for."""
    name = getattr(exc, "sqlite_errorname", "")
    if name in _CORRUPT_ERRORNAMES or "not a database" in str(exc):
        return _outcome(
            StoreCorruptError,
            f"Vector store at {where} is not a SQLite database: {exc}",
            where,
        )
    return _outcome(
        StoreUnavailableError, f"Vector store at {where} cannot be opened: {exc}", where
    )


def _registry(path: Path | None, *, read_only: bool) -> SqliteConnectionRegistry:
    return SqliteConnectionRegistry(
        path if path is not None else _MEMORY,
        owner_name="SqliteVectorStore",
        read_only=read_only,
        synchronous="FULL",
        autocommit=True,
        memory_prefix="vectors",
    )


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {str(row[0]) for row in rows}


def _prepare_writable(conn: sqlite3.Connection) -> None:
    """Put a new or vector-store database in WAL mode; give a new one the schema.

    A database holding other tables is left as it is: the load refuses it,
    and a writable open must not change someone else's journal mode first.
    """
    tables = _tables(conn)
    if tables and not _TABLES.issubset(tables):
        return
    conn.execute("PRAGMA journal_mode = WAL")
    if tables:
        return  # already a vector store: nothing to create, no write lock taken
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.execute(_UPSERT_META, ("format_version", str(FORMAT_VERSION)))
        conn.execute("COMMIT")
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise


def _prepare_nothing(_conn: sqlite3.Connection) -> None:
    """A read-only connection changes nothing."""


def _memory_probe(registry: SqliteConnectionRegistry) -> Callable[[], None]:
    def probe() -> None:
        other = registry.connect()
        try:
            other.execute("SELECT 1 FROM meta LIMIT 1")
        finally:
            other.close()

    return probe


def _count(raw: str, key: str, where: str) -> int:
    if not (raw.isascii() and raw.isdigit()):
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has a malformed {key}: {raw!r}.",
            where,
        )
    return int(raw)


def _format_meta(conn: sqlite3.Connection, where: str) -> dict[str, str] | None:
    """Return the ``meta`` table, or ``None`` for a database with no tables.

    Raises:
        StoreCorruptError: If the database holds other tables, or a malformed
            format version.
        StoreUnsupportedSchemaError: If its format is newer.
    """
    tables = _tables(conn)
    if not tables:
        return None
    if not _TABLES.issubset(tables):
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} is a SQLite database without the vector "
            f"store tables.",
            where,
        )
    meta = {str(k): str(v) for k, v in conn.execute("SELECT key, value FROM meta")}
    version = _count(meta.get("format_version", ""), "format_version", where)
    if version > FORMAT_VERSION:
        raise _outcome(
            StoreUnsupportedSchemaError,
            f"Vector store at {where} has format_version {version}; "
            f"this version reads up to {FORMAT_VERSION}.",
            where,
        )
    return meta


def _identity_json(identity: EmbeddingIdentity) -> str:
    return json.dumps(
        {
            "provider": identity.provider,
            "model": identity.model,
            "variant": identity.variant,
            "text_format": identity.text_format,
            "dimension": identity.dimension,
        }
    )


def _stored_identity(raw: str | None, where: str) -> EmbeddingIdentity | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return EmbeddingIdentity(
            provider=str(data["provider"]),
            model=str(data["model"]),
            variant=str(data["variant"]),
            text_format=str(data["text_format"]),
            dimension=int(data["dimension"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has a malformed identity: {exc}",
            where,
        ) from exc


def _served_identity(
    stored: EmbeddingIdentity, wanted: EmbeddingIdentity, where: str
) -> EmbeddingIdentity:
    """Return the identity to serve, refusing another embedding space.

    A *wanted* dimension of 0 does not know it yet: the stored one stands.
    """
    held = (stored.provider, stored.model, stored.variant, stored.text_format)
    asked = (wanted.provider, wanted.model, wanted.variant, wanted.text_format)
    if held != asked:
        raise _outcome(
            StoreIncompatibleError,
            f"Vector store at {where} holds {held}, not {asked}.",
            where,
        )
    if stored.dimension and wanted.dimension and stored.dimension != wanted.dimension:
        raise _outcome(
            StoreIncompatibleError,
            f"Vector store at {where} holds dimension {stored.dimension}, "
            f"not {wanted.dimension}.",
            where,
        )
    return EmbeddingIdentity(
        provider=wanted.provider,
        model=wanted.model,
        variant=wanted.variant,
        text_format=wanted.text_format,
        dimension=stored.dimension or wanted.dimension,
    )


def _metadata_object(raw: object, where: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has unreadable row metadata: {exc}",
            where,
        ) from exc
    if not isinstance(value, dict):
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has row metadata that is not an object.",
            where,
        )
    return value


def _loaded_rows(
    conn: sqlite3.Connection, dimension: int, where: str
) -> tuple[Any, tuple[dict[str, Any], ...]]:
    """Read every chunk into one float32 matrix and its metadata, in storage order."""
    count = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    matrix = np.empty((count, dimension), dtype=np.float32)
    rows: list[dict[str, Any]] = []
    width = dimension * 4
    cursor = conn.execute("SELECT vector, metadata FROM chunks ORDER BY path, ordinal")
    for i, (blob, raw) in enumerate(cursor):
        if not isinstance(blob, bytes) or len(blob) != width:
            size = len(blob) if isinstance(blob, bytes) else 0
            raise _outcome(
                StoreCorruptError,
                f"Vector store at {where} holds a vector of {size} bytes, not {width}.",
                where,
            )
        matrix[i] = np.frombuffer(blob, dtype="<f4")
        rows.append(_metadata_object(raw, where))
    return matrix, tuple(rows)


def _load(
    conn: sqlite3.Connection, identity: EmbeddingIdentity, where: str
) -> Snapshot:
    """Read the stored state in one read transaction."""
    conn.execute("BEGIN")
    try:
        meta = _format_meta(conn, where)
        stored = _stored_identity(meta.get("identity"), where) if meta else None
        if stored is None:
            if (
                meta is not None
                and conn.execute("SELECT 1 FROM documents LIMIT 1").fetchone()
            ):
                raise _outcome(
                    StoreCorruptError,
                    f"Vector store at {where} holds documents but no identity.",
                    where,
                )
            return fresh_snapshot(identity)
        served = _served_identity(stored, identity, where)
        generation = _count(meta.get("generation", "0"), "generation", where)  # type: ignore[union-attr]
        matrix, rows = _loaded_rows(conn, served.dimension, where)
        tokens: dict[str, RevisionToken | None] = {
            str(path): token
            for path, token in conn.execute("SELECT path, token FROM documents")
        }
    finally:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
    return Snapshot(served, generation, frozen(matrix), rows, MappingProxyType(tokens))


def _chunk_rows(
    path: str, metadata: Sequence[dict[str, Any]], vectors: Any
) -> list[tuple[Any, ...]]:
    """Return the ``chunks`` rows for one document.

    Raises:
        ValueError: If row metadata cannot be stored as JSON.
    """
    try:
        return [
            (
                path,
                ordinal,
                str(meta.get("folder") or ""),
                np.ascontiguousarray(vector, dtype="<f4").tobytes(),
                json.dumps(meta, ensure_ascii=False),
            )
            for ordinal, (meta, vector) in enumerate(
                zip(metadata, vectors, strict=True)
            )
        ]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Row metadata of {path} cannot be stored: {exc}") from exc


def _generation_chunks(
    documents: Sequence[DocumentRows], blocks: Sequence[Any]
) -> Iterator[tuple[Any, ...]]:
    for document, block in zip(documents, blocks, strict=True):
        yield from _chunk_rows(document.path, row_metadata(document), block)


def _token_conflicts(
    conn: sqlite3.Connection, path: str, expected: ExpectedToken
) -> bool:
    """Whether the token the database holds for *path* is not *expected*."""
    if expected is ANY_TOKEN:
        return False
    row = conn.execute("SELECT token FROM documents WHERE path = ?", (path,)).fetchone()
    return (row[0] if row is not None else None) != expected


def _empty(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM documents")
    conn.execute("DELETE FROM meta WHERE key IN ('identity', 'generation')")


def _write_meta(
    conn: sqlite3.Connection, identity: EmbeddingIdentity, generation: int
) -> None:
    """Record the identity and generation: the last statement before a commit."""
    conn.executemany(
        _UPSERT_META,
        [("identity", _identity_json(identity)), ("generation", str(generation))],
    )


def _check_pages(conn: sqlite3.Connection, where: str) -> None:
    """Refuse a database whose pages SQLite's ``quick_check`` finds damaged.

    Raises:
        StoreCorruptError: If the check reports anything but ``ok``.
    """
    verdict = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    if verdict != "ok":
        raise _outcome(
            StoreCorruptError,
            f"Vector store at {where} has damaged pages: {verdict}",
            where,
        )


def _replace_if_unusable(path: Path) -> None:
    """Remove *path* (and its WAL files) unless it is a vector store to empty.

    A store whose own tables read but whose pages are damaged is unusable
    too: emptying it in place would fail on those pages.

    Raises:
        StoreUnsupportedSchemaError: If it is a vector store of a newer format.
        StoreUnavailableError: If it cannot be read.
    """
    where = str(path)
    probe = _registry(path, read_only=True)
    try:
        conn = probe.conn()
        _format_meta(conn, where)
        _check_pages(conn, where)
        return
    except StoreCorruptError:
        pass
    except sqlite3.DatabaseError as exc:
        outcome = _classified(exc, where)
        if not isinstance(outcome, StoreCorruptError):
            raise outcome from exc
    finally:
        probe.close()
    logger.warning("sqlite_vector_store_corrupt_replaced path=%s", where)
    for stale in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        stale.unlink(missing_ok=True)


def _create(path: Path, *, replace_unusable: bool) -> SqliteConnectionRegistry:
    """Open *path* for writing, creating its directory, file and schema.

    Raises:
        StoreUnavailableError: If the directory cannot be created, or an
            unusable file there cannot be checked or removed.
        StoreUnsupportedSchemaError: If the file there is a vector store of
            a newer format.
        sqlite3.Error: If the database cannot be opened.
    """
    where = str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if replace_unusable and path_exists(path):
            _replace_if_unusable(path)
    except OSError as exc:
        raise _outcome(
            StoreUnavailableError,
            f"Vector store at {where} cannot be created: {exc}",
            where,
        ) from exc
    registry = _registry(path, read_only=False)
    registry.open_primary(_prepare_writable, _memory_probe(registry))
    return registry


def stored_chunk_count(location: Path | str) -> int:
    """Count the chunks the database at *location* holds, opening no store.

    For ``embeddings_status`` before the vault has opened its store.

    Args:
        location: The store's base path.

    Returns:
        The number of chunk rows; 0 when there is no database or no tables.

    Raises:
        sqlite3.Error: If the database cannot be read.
    """
    path = _db_file(location)
    if not path_exists(path):
        return 0
    registry = _registry(path, read_only=True)
    try:
        conn = registry.conn()
        if "chunks" not in _tables(conn):
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    finally:
        registry.close()


class SqliteVectorStore(ResidentStore):
    """A vector store kept in its own SQLite database, read from memory.

    Construct through :class:`SqliteVectorBackend`.
    """

    def __init__(
        self,
        snapshot: Snapshot,
        *,
        path: Path | None,
        read_only: bool,
        registry: SqliteConnectionRegistry | None,
        reset_pending: bool = False,
    ) -> None:
        super().__init__(snapshot, read_only=read_only)
        self._path = path
        self._registry = registry
        self._reset_pending = reset_pending

    @property
    def _where(self) -> str:
        return str(self._path) if self._path is not None else _MEMORY

    def _connection(self) -> sqlite3.Connection:
        if self._registry is None:
            if self._path is None:  # pragma: no cover - memory opens at once
                raise RuntimeError("An in-memory vector store has no database.")
            self._registry = _create(self._path, replace_unusable=self._reset_pending)
        return self._registry.conn()

    def _commit(
        self, op: str, work: Callable[[sqlite3.Connection], PublicationOutcome]
    ) -> PublicationOutcome:
        """Run *work* in one write transaction; commit unless it reports a conflict.

        Raises:
            StoreUnavailableError: If the first write cannot create the
                database; nothing was written.
            sqlite3.Error: If the transaction fails; nothing was written.
        """
        try:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self._reset_pending:
                    _empty(conn)
                outcome = work(conn)
                conn.execute("ROLLBACK" if outcome == "conflict" else "COMMIT")
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise
        except (sqlite3.Error, StoreUnavailableError) as exc:
            logger.warning(
                "sqlite_vector_store_write_failed path=%s op=%s err=%s",
                self._where,
                op,
                exc,
            )
            raise
        if outcome != "conflict":
            self._reset_pending = False
        return outcome

    def replace_document(
        self, document: DocumentRows, *, expected_token: ExpectedToken
    ) -> PublicationOutcome:
        """Replace ``document.path``'s rows, durably; see :class:`~markdown_vault_mcp.interfaces.VectorPublisher`.

        Raises:
            ReadOnlyError: If the store was opened read-only.
            ValueError: If a row is of another dimension or not finite, or its
                metadata cannot be stored as JSON.
            StoreUnavailableError: If the first write cannot create the
                database; nothing changed.
            sqlite3.Error: If the write fails; nothing changed.
        """
        with self._lock:
            snap = self._writable()
            vectors = row_vectors(document.rows, snap.identity.dimension)
            chunks = _chunk_rows(document.path, row_metadata(document), vectors)
            new = with_document(snap, document, vectors)

            def work(conn: sqlite3.Connection) -> PublicationOutcome:
                if _token_conflicts(conn, document.path, expected_token):
                    return "conflict"
                conn.execute("DELETE FROM chunks WHERE path = ?", (document.path,))
                conn.execute(_UPSERT_DOCUMENT, (document.path, document.token))
                conn.executemany(_INSERT_CHUNK, chunks)
                _write_meta(conn, snap.identity, snap.generation)
                return "published"

            outcome = self._commit("replace", work)
            if outcome == "published":
                self._snap = new
        return outcome

    def remove_document(
        self, path: str, *, expected_token: ExpectedToken
    ) -> PublicationOutcome:
        """Remove *path*'s rows and token, durably, with the same condition.

        Raises:
            ReadOnlyError: If the store was opened read-only.
            StoreUnavailableError: If the first write cannot create the
                database; nothing changed.
            sqlite3.Error: If the write fails; nothing changed.
        """
        with self._lock:
            snap = self._writable()
            new = without_document(snap, path)
            if new is None:
                return (
                    "conflict" if conflicts(snap, path, expected_token) else "published"
                )

            def work(conn: sqlite3.Connection) -> PublicationOutcome:
                if _token_conflicts(conn, path, expected_token):
                    return "conflict"
                conn.execute("DELETE FROM documents WHERE path = ?", (path,))
                _write_meta(conn, snap.identity, snap.generation)
                return "published"

            outcome = self._commit("remove", work)
            if outcome == "published":
                self._snap = new
        return outcome

    def publish_generation(
        self, identity: EmbeddingIdentity, documents: Iterable[DocumentRows]
    ) -> None:
        """Replace the store with *documents* under *identity* in one commit.

        Raises:
            ReadOnlyError: If the store was opened read-only.
            ValueError: If a path appears twice, or a row is of another
                dimension, not finite, or its metadata cannot be stored.
            StoreUnavailableError: If the first write cannot create the
                database; the previous generation stands.
            sqlite3.Error: If the write fails; the previous generation stands.
        """
        docs, blocks = validated_generation(identity, documents)
        with self._lock:
            snap = self._writable()
            new = generation_snapshot(identity, snap.generation + 1, docs, blocks)

            def work(conn: sqlite3.Connection) -> PublicationOutcome:
                conn.execute("DELETE FROM chunks")
                conn.execute("DELETE FROM documents")
                conn.executemany(_UPSERT_DOCUMENT, ((d.path, d.token) for d in docs))
                conn.executemany(_INSERT_CHUNK, _generation_chunks(docs, blocks))
                _write_meta(conn, identity, new.generation)
                return "published"

            self._commit("publish_generation", work)
            self._snap = new
            self._truncate_wal()
        logger.info(
            "sqlite_vector_store_generation_published path=%s generation=%d rows=%d",
            self._where,
            new.generation,
            len(new.rows),
        )

    def checkpoint(self) -> None:
        """Fold the WAL into the database file; a no-op in memory or read-only.

        Every published write is already durable, so this makes nothing more
        so, and a checkpoint that fails loses nothing: it is logged at
        WARNING, not raised.

        Raises:
            RuntimeError: If the store is closed.
        """
        with self._lock:
            self._current()
            if self._path is None or self._read_only or self._registry is None:
                return
            try:
                self._registry.conn().execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error as exc:
                logger.warning(
                    "sqlite_vector_store_checkpoint_failed path=%s err=%s",
                    self._where,
                    exc,
                )

    def _truncate_wal(self) -> None:
        """Fold the WAL into the database and truncate it, best effort.

        After a generation publish the WAL holds the whole store; a passive
        checkpoint folds it but leaves the file that size until close.
        """
        if self._registry is None or self._path is None or self._read_only:
            return
        try:
            self._registry.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            logger.warning(
                "sqlite_vector_store_truncate_failed path=%s err=%s", self._where, exc
            )

    def close(self) -> None:
        """Fold the WAL away and release the database. Idempotent."""
        if self._closed:
            return
        with self._lock:
            if self._closed:  # another close() got here first
                return
            try:
                self._truncate_wal()
            finally:
                self._closed = True
                if self._registry is not None:
                    self._registry.close()


def _open_memory(identity: EmbeddingIdentity) -> SqliteVectorStore:
    registry = _registry(None, read_only=False)
    registry.open_primary(_prepare_writable, _memory_probe(registry))
    return SqliteVectorStore(
        fresh_snapshot(identity), path=None, read_only=False, registry=registry
    )


def _open_file(
    path: Path, identity: EmbeddingIdentity, *, read_only: bool
) -> SqliteVectorStore:
    where = str(path)
    try:
        if path_exists(path.parent) and not is_directory(path.parent):
            raise _outcome(
                StoreUnavailableError,
                f"Vector store at {where} cannot be opened: {path.parent} is not a "
                f"directory.",
                where,
            )
        exists = path_exists(path)
    except OSError as exc:
        raise _outcome(
            StoreUnavailableError,
            f"Vector store at {where} cannot be checked: {exc}",
            where,
        ) from exc
    if not exists:
        return SqliteVectorStore(
            fresh_snapshot(identity), path=path, read_only=read_only, registry=None
        )
    registry = _registry(path, read_only=read_only)
    try:
        registry.open_primary(
            _prepare_nothing if read_only else _prepare_writable,
            _memory_probe(registry),
        )
        snap = _load(registry.conn(), identity, where)
    except sqlite3.Error as exc:
        registry.close()
        raise _classified(exc, where) from exc
    except BaseException:
        registry.close()
        raise
    logger.info(
        "sqlite_vector_store_opened path=%s rows=%d generation=%d",
        where,
        len(snap.rows),
        snap.generation,
    )
    return SqliteVectorStore(snap, path=path, read_only=read_only, registry=registry)


class SqliteVectorBackend:
    """Open a :class:`SqliteVectorStore` at a base path, or in memory."""

    family: StoreFamily = "vector"
    name = _BACKEND_NAME

    def open(
        self,
        location: Path | str | None,
        identity: object | None = None,
        *,
        owns_files: bool = True,
    ) -> SqliteVectorStore:
        """Open the store.

        Args:
            location: The base path; the database is ``{base}.sqlite3``.
                ``None`` or ``":memory:"`` keeps the store in memory.
            identity: The :class:`~markdown_vault_mcp.types.EmbeddingIdentity`
                the rows must belong to.
            owns_files: ``False`` opens the database read-only; ignored in
                memory.

        Returns:
            The open store.

        Raises:
            ImportError: If numpy is not installed.
            ValueError: If *identity* is not an ``EmbeddingIdentity``.
            StoreUnavailableError: If the database cannot be reached.
            StoreCorruptError: If it is not a vector store.
            StoreUnsupportedSchemaError: If its format is newer.
            StoreIncompatibleError: If it holds another identity.
        """
        _require_numpy()
        if not isinstance(identity, EmbeddingIdentity):
            raise ValueError("A vector store opens against an EmbeddingIdentity.")
        if location is None or str(location) == _MEMORY:
            return _open_memory(identity)
        return _open_file(_db_file(location), identity, read_only=not owns_files)

    def open_fresh(
        self, location: Path | str | None, identity: EmbeddingIdentity
    ) -> SqliteVectorStore:
        """Open *location* as an empty store at *identity*, reading nothing there.

        Before the first write, a file that is not a vector store or whose
        pages are damaged is removed, whatever that write's outcome; the
        write empties a usable database in its own transaction. Until then
        a reader opens the old rows. Unlike :meth:`open`, this checks
        nothing at the location: the first write raises
        :class:`~markdown_vault_mcp.exceptions.StoreUnavailableError` when
        the database cannot be created there.

        Raises:
            ImportError: If numpy is not installed.
        """
        _require_numpy()
        if location is None or str(location) == _MEMORY:
            return _open_memory(identity)
        return SqliteVectorStore(
            fresh_snapshot(identity),
            path=_db_file(location),
            read_only=False,
            registry=None,
            reset_pending=True,
        )


register_backend("vector", _BACKEND_NAME, SqliteVectorBackend)
