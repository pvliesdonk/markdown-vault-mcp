"""SQLite FTS5 index for full-text search and tag filtering."""

from __future__ import annotations

import contextlib
import datetime
import functools
import json
import logging
import sqlite3
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from markdown_vault_mcp.types import FTSResult, ParsedNote

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)


_T = TypeVar("_T")

# Match the busy_timeout setting; SQLITE_LOCKED needs application-level retry
# because sqlite3.OperationalError("...locked...") is error code 6 (LOCKED)
# rather than 5 (BUSY), and Python's sqlite3 / SQLite C-level busy_handler
# only handles BUSY. See https://www.sqlite.org/rescode.html#locked.
_SQLITE_LOCKED_RETRY_TIMEOUT_S = 5.0
_SQLITE_LOCKED_INITIAL_SLEEP_S = 0.01
_SQLITE_LOCKED_MAX_SLEEP_S = 0.5


def _retry_on_sqlite_locked(
    operation: Callable[[], _T],
    *,
    timeout: float = _SQLITE_LOCKED_RETRY_TIMEOUT_S,
) -> _T:
    """Retry *operation* on transient SQLite "locked" errors.

    Python's ``sqlite3.Connection``'s ``busy_timeout`` only retries on
    ``SQLITE_BUSY`` (error code 5). FTS5 virtual-table internal locking
    raises ``SQLITE_LOCKED`` (error code 6) which is never retried by the
    SQLite C-level busy handler — see #560. This helper provides
    application-level retry with exponential backoff so transient FTS5
    locks (writer mid-upsert blocking a concurrent reader, or vice
    versa) don't surface as user-visible failures.

    Non-"locked" ``OperationalError``s propagate immediately.

    Args:
        operation: Callable to invoke. Re-invoked on each retry, so the
            caller is responsible for any state reset (e.g. running
            inside a fresh ``with conn:`` transaction that auto-rolls
            back on exception).
        timeout: Maximum total wall-clock retry budget in seconds.

    Returns:
        Whatever *operation* returns on success.

    Raises:
        sqlite3.OperationalError: If the operation continues to raise a
            "locked" error past *timeout*, or if it raises an
            ``OperationalError`` that does not mention "locked".
    """
    deadline = time.monotonic() + timeout
    sleep = _SQLITE_LOCKED_INITIAL_SLEEP_S
    while True:
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            # Cap the sleep to the remaining budget so the contract
            # "retry for at most *timeout* seconds" is honoured even
            # near the deadline — without this, the final sleep could
            # push us up to _SQLITE_LOCKED_MAX_SLEEP_S past *timeout*.
            time.sleep(min(sleep, _SQLITE_LOCKED_MAX_SLEEP_S, remaining))
            sleep *= 2


def _retry_on_locked(method: Callable[..., _T]) -> Callable[..., _T]:
    """Method decorator: retry the method body on SQLITE_LOCKED.

    Wraps the method so the entire body (including any ``with conn:``
    transaction) is re-invoked on a locked error. This is safe because
    Python's sqlite3 ``with conn:`` block rolls back the transaction on
    exception before the wrapper sees it — the retry starts from a
    clean state.
    """

    @functools.wraps(method)
    def wrapper(self: object, *args: object, **kwargs: object) -> _T:
        return _retry_on_sqlite_locked(lambda: method(self, *args, **kwargs))

    return wrapper


def _json_default(obj: Any) -> str:
    """Handle non-JSON-native types from YAML frontmatter.

    YAML parsers auto-detect date-like strings (e.g. ``2024-01-15``) as
    ``datetime.date`` or ``datetime.datetime`` objects, and bare time
    strings (e.g. ``15:30:00``) as ``datetime.time``.  This handler
    converts them to ISO-format strings so ``json.dumps()`` can
    serialise frontmatter without crashing.
    """
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# DDL executed once on connection open. `foreign_keys` is intentionally NOT
# set here — `_apply_pragmas` sets it on every connection (primary and
# per-thread) before `_init_schema` runs.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
    frontmatter_json TEXT,
    content_hash TEXT NOT NULL,
    modified_at REAL NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    heading TEXT,
    heading_level INTEGER NOT NULL,
    content TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sections_docid ON sections(document_id);

CREATE TABLE IF NOT EXISTS document_tags (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    tag_key TEXT NOT NULL,
    tag_value TEXT NOT NULL,
    UNIQUE(document_id, tag_key, tag_value),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tags_kv ON document_tags(tag_key, tag_value);
CREATE INDEX IF NOT EXISTS idx_tags_docid ON document_tags(document_id);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    target_path TEXT NOT NULL,
    link_text TEXT NOT NULL DEFAULT '',
    link_type TEXT NOT NULL,
    fragment TEXT,
    raw_target TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (source_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_path);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id);

CREATE TABLE IF NOT EXISTS document_aliases (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    UNIQUE(document_id, alias),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_aliases_alias ON document_aliases(alias);
CREATE INDEX IF NOT EXISTS idx_aliases_docid ON document_aliases(document_id);

CREATE INDEX IF NOT EXISTS idx_documents_modified_at
    ON documents(modified_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path, title, folder, heading, content,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Key written into ``meta`` after :meth:`IndexFacet.build_index` completes
# a full scan successfully. Warm-restart short-circuits keyed solely on
# ``documents`` row presence would otherwise treat a partial index (left
# by a crash mid-build, since per-document upserts each commit in their
# own transaction) as ready — see issue #525.
_META_BUILD_COMPLETED_KEY = "build_completed_at"


def _escape_like(value: str) -> str:
    """Escape SQLite LIKE special characters in ``value``.

    SQLite LIKE treats ``%`` and ``_`` as wildcards and ``\\`` as the escape
    character (when ``ESCAPE '\\'`` is declared).  This function escapes all
    three so that a user-supplied folder name is matched literally.

    Args:
        value: Raw string that will be embedded in a LIKE pattern.

    Returns:
        String with ``\\``, ``%``, and ``_`` replaced by their escaped forms.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _derive_folder(path: str) -> str:
    """Derive the folder from a document's relative path.

    Args:
        path: Document relative path including ``.md`` extension,
            e.g. ``"Journal/2024-01-15.md"`` or ``"README.md"``.

    Returns:
        The parent directory as a forward-slash string, or ``""`` for
        documents at the vault root. Examples::

            "Journal/note.md"             -> "Journal"
            "Journal/2024/January/a.md"   -> "Journal/2024/January"
            "README.md"                   -> ""
    """
    parent = Path(path).parent
    # PurePosixPath('.') means no parent directory.
    folder = parent.as_posix()
    return "" if folder == "." else folder


def _normalize_heading(heading: str) -> str:
    """Collapse all whitespace runs in *heading* to single spaces and strip.

    Used by :meth:`FTSIndex.get_section` to compare stored vs queried
    heading strings tolerantly: rendered TOCs and markdown editors
    normalise whitespace inconsistently (single vs double space after
    a numbered prefix, tabs vs spaces, trailing whitespace), but the
    semantic identity of the heading is unchanged.
    """
    return " ".join(heading.split())


def _resolve_connect_uri(db_path: Path | str) -> tuple[str, bool, bool]:
    """Resolve a db_path into (connect_string, uses_uri, is_memory).

    For ``":memory:"`` returns a shared-cache URI unique to this call so that
    every per-thread ``sqlite3.connect()`` joins the same in-memory database
    (required for the per-thread connection model — see #519). For file paths
    returns the path string directly.

    The shared-cache URI is unique per ``FTSIndex`` instance (uuid4 token) so
    distinct in-process vaults do not collide.
    """
    if str(db_path) == ":memory:":
        token = uuid.uuid4().hex
        return f"file:fts_{token}?mode=memory&cache=shared", True, True
    return str(db_path), False, False


class FTSIndex:
    """SQLite FTS5 index providing BM25 search and tag filtering.

    Wraps a SQLite database file (or in-memory database) and exposes CRUD
    operations and full-text search over a vault of markdown documents.

    **Thread safety (issue #519):** every public method is safe to call from
    any thread. Each thread that touches the index opens its own
    ``sqlite3.Connection`` on first use via :meth:`_conn`; a side registry
    (``_all_conns``, guarded by ``_reg_lock``) holds strong refs so
    :meth:`close` can close every connection — including those opened by
    threads that have since exited. Concurrent index mutations are
    serialised by the single-owner :class:`IndexWriter` thread (#559),
    not by this class. After :meth:`close`,
    every public method raises ``sqlite3.ProgrammingError``. See
    ``docs/design.md`` "Vault thread-safety contract" for the full
    contract.

    Tag indexing behaviour is controlled by ``indexed_frontmatter_fields``:
    only the listed frontmatter keys are promoted into the ``document_tags``
    table for structured filtering.  Scalar values produce one row each; list
    values produce one row per item (deduplicated per document).  Complex
    types (nested dicts, objects) are stored in the raw JSON blob only and
    are not indexed.

    Args:
        db_path: Path to the SQLite database file.  Pass ``":memory:"`` (the
            default) for a transient in-memory database.
        indexed_frontmatter_fields: Frontmatter keys whose values are
            promoted to the ``document_tags`` table.  ``None`` means no tag
            indexing.
    """

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        indexed_frontmatter_fields: list[str] | None = None,
    ) -> None:
        self._db_path = db_path
        self._indexed_fields: list[str] = indexed_frontmatter_fields or []
        # Resolve URI (translates ``:memory:`` to a shared-cache URI so that
        # per-thread opens see the same in-memory DB).
        self._connect_uri, self._uses_uri, self._is_memory = _resolve_connect_uri(
            db_path
        )
        # Thread-safety state — see #519 and docs/design.md.
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._reg_lock = threading.Lock()
        self._closed = False
        self._primary_conn: sqlite3.Connection | None = None

        # Open primary connection on the constructing thread. The whole
        # init-schema + probe sequence runs under one BaseException cleanup
        # block so any failure (pragma, ALTER TABLE, or the shared-cache
        # probe) closes the primary connection — symmetric with the
        # slow-path cleanup in _conn().
        primary = self._connect()
        try:
            # PRAGMAS FIRST — busy_timeout must be active for ALTER TABLE migrations
            # in _init_schema (per #519 carryover).
            self._apply_pragmas(primary)
            self._init_schema(primary)
            self._local.conn = primary
            self._primary_conn = primary
            self._all_conns.append(primary)
            # Fail-fast probe: if shared-cache ``:memory:`` translation is in
            # use but SQLITE_ENABLE_SHARED_CACHE was disabled at build time, a
            # second connection to the URI will see an empty DB. Surface that
            # immediately instead of letting per-thread reads fail mysteriously
            # downstream.
            if self._is_memory:
                self._probe_shared_cache()
        except BaseException:
            # Close the primary regardless of how far init progressed (the
            # probe call may fail after append, leaving primary in
            # _all_conns; reset both paths to a clean state).
            try:
                primary.close()
            except Exception:
                logger.debug(
                    "fts_index.__init__ cleanup: error closing primary",
                    exc_info=True,
                )
            # Mirror the _conn() slow-path TLS clear: if a partially-built
            # FTSIndex's _local.conn still pointed at the now-closed primary,
            # a caller holding the instance after __init__ raised would
            # fast-path the closed conn instead of getting ProgrammingError.
            self._local.conn = None
            self._all_conns.clear()
            self._primary_conn = None
            raise

    def _connect(self) -> sqlite3.Connection:
        """Open a raw sqlite3 connection to this index's URI."""
        conn = sqlite3.connect(
            self._connect_uri,
            check_same_thread=False,
            uri=self._uses_uri,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """Apply per-connection pragmas (foreign_keys, busy_timeout, synchronous).

        Called on every ``sqlite3.connect()`` — the primary connection and
        every per-thread open. These are per-connection settings that do NOT
        persist across opens.
        """
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        """Run DDL, migrations, and WAL on the primary connection.

        Called exactly once per ``FTSIndex`` instance, on the constructing
        thread. Per-thread opens do NOT call this method — they only apply
        pragmas, since DDL and WAL are persisted in the DB header.
        """
        conn.executescript(_SCHEMA_SQL)
        try:
            conn.execute(
                "ALTER TABLE links ADD COLUMN raw_target TEXT NOT NULL DEFAULT ''"
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS document_aliases (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                UNIQUE(document_id, alias),
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_aliases_alias ON document_aliases(alias);
            CREATE INDEX IF NOT EXISTS idx_aliases_docid ON document_aliases(document_id);
            """
        )
        cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "chunk_count" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN chunk_count INTEGER NOT NULL DEFAULT 1"
                )
                conn.commit()
                logger.info(
                    "fts_index: migrated documents table — added chunk_count column"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
                logger.debug(
                    "fts_index: chunk_count column already added by concurrent process"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sections_docid ON sections(document_id)"
        )
        conn.commit()
        # WAL is a DB-header pragma — persists across opens. Skip for in-memory
        # databases (SQLite silently falls back to 'memory' journal mode there).
        if not self._is_memory:
            result = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            if result is None or str(result[0]).lower() != "wal":
                logger.warning(
                    "Could not enable WAL journal mode (got %s); "
                    "concurrent reads during writes may block",
                    result[0] if result else "no result",
                )
        conn.commit()

    def _probe_shared_cache(self) -> None:
        """Verify that a second connection to the same in-memory URI sees the schema.

        If ``SQLITE_ENABLE_SHARED_CACHE`` is unavailable in this SQLite build,
        the second connection will see an empty database — per-thread reads
        would then fail with confusing OperationalErrors. Surface that
        condition immediately with an operator-actionable error.
        """
        # The probe connection intentionally bypasses _all_conns: it is opened
        # and closed entirely within this method before the FTSIndex instance
        # is exposed to any caller, so the registry-based close() machinery is
        # not needed for it.
        probe = self._connect()
        try:
            row = probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "FTSIndex: in-memory shared-cache probe failed — this SQLite "
                    "build appears to lack SQLITE_ENABLE_SHARED_CACHE. Use a file "
                    "path for db_path, or rebuild SQLite with shared-cache support."
                )
        finally:
            try:
                probe.close()
            except sqlite3.Error:
                logger.debug(
                    "fts_index._probe_shared_cache: probe.close failed",
                    exc_info=True,
                )

    def _conn(self) -> sqlite3.Connection:
        """Return this thread's sqlite3 connection, opening one on first touch.

        Uses double-checked locking: the fast path is lock-free; the slow path
        re-checks ``_closed`` under ``_reg_lock`` so a concurrent ``close()``
        cannot race with a new-thread open.

        Raises:
            sqlite3.ProgrammingError: If ``close()`` has been called.

        Note on connection accumulation: dead-thread connections remain in
        ``_all_conns`` (strong refs) until ``close()``. This is bounded for
        the MCP server's workload (long-lived lifespan thread + bounded
        ``asyncio.to_thread`` pool) and is preferred over weakrefs (see
        ``feedback_519_weakref_whackamole.md``).
        """
        if self._closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed FTSIndex")
        existing: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        new_conn = self._connect()
        try:
            self._apply_pragmas(new_conn)
            with self._reg_lock:
                if self._closed:
                    raise sqlite3.ProgrammingError(
                        "Cannot operate on a closed FTSIndex"
                    )
                self._local.conn = new_conn
                self._all_conns.append(new_conn)
        except BaseException:
            # Cover KeyboardInterrupt / SystemExit / asyncio.CancelledError —
            # sqlite3.Error alone would leak the open connection on teardown.
            # Clear the TLS slot first: CPython delivers signals at bytecode
            # boundaries, so an interrupt between the _local.conn assignment
            # and the registry append would otherwise leave a closed conn in
            # TLS for the next fast-path call to silently return.
            self._local.conn = None
            # Re-acquire _reg_lock for the registry mutation: a concurrent
            # close() iterates _all_conns under the lock, so an unguarded
            # remove() here could trigger "list changed size during iteration"
            # in close().
            with self._reg_lock, contextlib.suppress(ValueError):
                self._all_conns.remove(new_conn)
            new_conn.close()
            raise
        return new_conn

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _insert_document(
        self,
        cur: sqlite3.Cursor,
        note: ParsedNote,
        folder: str,
    ) -> int:
        """Insert a row into ``documents`` and return its new ``id``.

        Args:
            cur: Active cursor inside the current transaction.
            note: Parsed document to insert.
            folder: Pre-derived folder string.

        Returns:
            The ``ROWID`` / ``id`` of the newly inserted document row.

        Raises:
            RuntimeError: If the INSERT did not return a row ID.
        """
        cur.execute(
            """
            INSERT INTO documents (path, title, folder, frontmatter_json,
                                   content_hash, modified_at, chunk_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note.path,
                note.title,
                folder,
                json.dumps(note.frontmatter, default=_json_default),
                note.content_hash,
                note.modified_at,
                len(note.chunks),
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("INSERT did not return a row ID")
        return cur.lastrowid

    def _insert_sections(
        self,
        cur: sqlite3.Cursor,
        document_id: int,
        note: ParsedNote,
    ) -> None:
        """Insert all chunks for a document into ``sections``.

        Also inserts one row per chunk into the ``notes_fts`` virtual table.

        Args:
            cur: Active cursor inside the current transaction.
            document_id: The ``id`` of the parent document row.
            note: Parsed document whose chunks are to be inserted.
        """
        folder = _derive_folder(note.path)
        for chunk in note.chunks:
            cur.execute(
                """
                INSERT INTO sections (document_id, heading, heading_level,
                                      content, start_line)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    chunk.heading,
                    chunk.heading_level,
                    chunk.content,
                    chunk.start_line,
                ),
            )
            cur.execute(
                """
                INSERT INTO notes_fts (path, title, folder, heading, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    note.path,
                    note.title,
                    folder,
                    chunk.heading or "",
                    chunk.content,
                ),
            )

    def _insert_tags(
        self,
        cur: sqlite3.Cursor,
        document_id: int,
        note: ParsedNote,
    ) -> None:
        """Index frontmatter values into ``document_tags``.

        Only keys listed in ``indexed_frontmatter_fields`` are processed.
        Scalar values produce one row; list values produce one row per item
        (deduplicated per document).  Complex types (dicts, etc.) are skipped.

        Args:
            cur: Active cursor inside the current transaction.
            document_id: The ``id`` of the parent document row.
            note: Parsed document whose frontmatter is to be indexed.
        """
        if not self._indexed_fields:
            return

        for key in self._indexed_fields:
            value = note.frontmatter.get(key)
            if value is None:
                continue

            if isinstance(value, list):
                # One row per item; skip non-scalar elements.
                seen: set[str] = set()
                for item in value:
                    if (
                        isinstance(item, (str, int, float, bool))
                        and not isinstance(item, bool)
                    ) or isinstance(item, bool):
                        # Accept all scalar types.
                        tag_val = str(item)
                        if tag_val not in seen:
                            seen.add(tag_val)
                            cur.execute(
                                """
                                INSERT OR IGNORE INTO document_tags
                                    (document_id, tag_key, tag_value)
                                VALUES (?, ?, ?)
                                """,
                                (document_id, key, tag_val),
                            )
            elif isinstance(value, dict):
                # Complex type — skip.
                logger.debug(
                    "Skipping complex frontmatter value for key %r in %s",
                    key,
                    note.path,
                )
            else:
                # Scalar value.
                cur.execute(
                    """
                    INSERT OR IGNORE INTO document_tags
                        (document_id, tag_key, tag_value)
                    VALUES (?, ?, ?)
                    """,
                    (document_id, key, str(value)),
                )

    def _insert_aliases(
        self,
        cur: sqlite3.Cursor,
        document_id: int,
        note: ParsedNote,
    ) -> None:
        """Index frontmatter ``aliases`` into ``document_aliases``.

        Obsidian allows documents to declare alternative names via a YAML
        ``aliases`` field (list of strings).  These are stored so that
        :meth:`resolve_vault_wikilinks` can resolve ``[[Alias]]`` to the
        document that declares it.

        Both ``aliases`` (list) and ``alias`` (single string) frontmatter
        keys are supported, matching Obsidian's behaviour.

        Args:
            cur: Active cursor inside the current transaction.
            document_id: The ``id`` of the parent document row.
            note: Parsed document whose aliases are to be indexed.
        """
        raw = note.frontmatter.get("aliases") or note.frontmatter.get("alias")
        if raw is None:
            return

        values: list[str] = []
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, list):
            values = [str(item) for item in raw if isinstance(item, (str, int, float))]
        else:
            return

        seen: set[str] = set()
        for alias in values:
            alias = alias.strip()
            if alias and alias not in seen:
                seen.add(alias)
                cur.execute(
                    """
                    INSERT OR IGNORE INTO document_aliases
                        (document_id, alias)
                    VALUES (?, ?)
                    """,
                    (document_id, alias),
                )

    def _insert_links(
        self,
        cur: sqlite3.Cursor,
        document_id: int,
        note: ParsedNote,
    ) -> None:
        """Insert all extracted links for a document into ``links``.

        Follows the same delete-then-insert pattern as :meth:`_insert_tags`.
        Any existing links for ``document_id`` are removed first (via ON DELETE
        CASCADE when the document row is deleted), so this method simply inserts.

        Args:
            cur: Active cursor inside the current transaction.
            document_id: The ``id`` of the parent document row.
            note: Parsed document whose links are to be inserted.
        """
        for link in note.links:
            cur.execute(
                """
                INSERT INTO links (source_id, target_path, link_text,
                                   link_type, fragment, raw_target)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    link.target_path,
                    link.link_text,
                    link.link_type,
                    link.fragment,
                    link.raw_target,
                ),
            )

    def _delete_document(self, cur: sqlite3.Cursor, path: str) -> int:
        """Delete a document row (cascade deletes sections and tags).

        Also removes all FTS rows for the document's path.

        Args:
            cur: Active cursor inside the current transaction.
            path: Relative document path.

        Returns:
            Number of document rows deleted (0 or 1).
        """
        cur.execute("DELETE FROM notes_fts WHERE path = ?", (path,))
        cur.execute("DELETE FROM documents WHERE path = ?", (path,))
        return cur.rowcount

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_from_notes(self, notes: Iterable[ParsedNote]) -> int:
        """Bulk-insert all notes, replacing any existing data.

        Existing documents are upserted (delete + re-insert) so
        ``build_from_notes`` is idempotent when called on an already-populated
        index.  All inserts are wrapped in a single transaction for
        performance and atomicity.

        Materialises *notes* into a list BEFORE the retry window opens.
        The :func:`_retry_on_sqlite_locked` helper re-invokes its
        operation on a transient ``SQLITE_LOCKED``; a one-shot generator
        would otherwise be empty on retry. Cannot use ``@_retry_on_locked``
        directly here because the decorator captures the original
        argument tuple — the generator would be exhausted by the first
        attempt before the inner ``list()`` call runs.

        Args:
            notes: Iterable of parsed documents to index.

        Returns:
            Total number of chunks (sections) indexed.
        """
        notes_list = list(notes)

        def _do() -> int:
            return self._build_from_notes_inner(notes_list)

        return _retry_on_sqlite_locked(_do)

    def _build_from_notes_inner(self, notes: list[ParsedNote]) -> int:
        """Inner body of :meth:`build_from_notes`, safe to re-invoke on retry."""
        total_chunks = 0
        conn = self._conn()
        with conn:
            cur = conn.cursor()
            for note in notes:
                folder = _derive_folder(note.path)
                self._delete_document(cur, note.path)
                doc_id = self._insert_document(cur, note, folder)
                self._insert_sections(cur, doc_id, note)
                self._insert_tags(cur, doc_id, note)
                self._insert_aliases(cur, doc_id, note)
                self._insert_links(cur, doc_id, note)
                total_chunks += len(note.chunks)
                logger.debug(
                    "build_from_notes: indexed %d chunks for %s",
                    len(note.chunks),
                    note.path,
                )
        logger.info("build_from_notes: indexed %d chunks total", total_chunks)
        return total_chunks

    @_retry_on_locked
    def upsert_note(self, note: ParsedNote) -> int:
        """Insert or replace a single document in the index.

        Deletes any existing rows for ``note.path``, then inserts the
        document, its sections, and its tags in a single transaction.

        Args:
            note: Parsed document to insert or replace.

        Returns:
            Number of chunks (sections) indexed for this document.
        """
        folder = _derive_folder(note.path)
        conn = self._conn()
        with conn:
            cur = conn.cursor()
            self._delete_document(cur, note.path)
            doc_id = self._insert_document(cur, note, folder)
            self._insert_sections(cur, doc_id, note)
            self._insert_tags(cur, doc_id, note)
            self._insert_aliases(cur, doc_id, note)
            self._insert_links(cur, doc_id, note)
        logger.debug(
            "upsert_note: indexed %d chunks for %s", len(note.chunks), note.path
        )
        return len(note.chunks)

    @_retry_on_locked
    def delete_by_path(self, path: str) -> int:
        """Remove a document and all its data from the index.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).

        Returns:
            Number of document rows deleted (0 if path was not indexed).
        """
        conn = self._conn()
        with conn:
            cur = conn.cursor()
            deleted = self._delete_document(cur, path)
        if deleted:
            logger.debug("delete_by_path: removed %s", path)
        return deleted

    # ------------------------------------------------------------------
    # Build completeness sentinel (issue #525)
    # ------------------------------------------------------------------

    @_retry_on_locked
    def is_build_completed(self) -> bool:
        """Return ``True`` iff a prior ``build_index`` run committed the
        completeness sentinel into the ``meta`` table.

        Absence of the sentinel — paired with non-empty ``documents`` —
        signals a partial index left by a crashed prior build, and the
        caller (``IndexFacet.build_index``) treats it as cold.
        """
        conn = self._conn()
        with conn:
            row = conn.execute(
                "SELECT 1 FROM meta WHERE key = ?",
                (_META_BUILD_COMPLETED_KEY,),
            ).fetchone()
        return row is not None

    @_retry_on_locked
    def set_build_completed(self) -> None:
        """Mark the FTS index as the result of a clean full build."""
        conn = self._conn()
        ts = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                (_META_BUILD_COMPLETED_KEY, ts),
            )

    @_retry_on_locked
    def clear_build_completed(self) -> None:
        """Erase the completeness sentinel (before a destructive rebuild)."""
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM meta WHERE key = ?", (_META_BUILD_COMPLETED_KEY,))

    @_retry_on_locked
    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        folder: str | None = None,
        filters: dict[str, str] | None = None,
        snippet_words: int | None = None,
    ) -> list[FTSResult]:
        """Full-text search using BM25 ranking.

        Args:
            query: FTS5 query string.
            limit: Maximum number of results to return.
            folder: If provided, only return documents whose ``folder``
                starts with this string.
            filters: Dict of ``{tag_key: tag_value}`` pairs (AND semantics).
            snippet_words: When set to a positive integer, returned
                ``content`` is replaced with FTS5's ``snippet()`` of the
                matched content column, sized to approximately this many
                tokens. ``None`` or ``0`` returns the full chunk.

        Returns:
            List of :class:`~markdown_vault_mcp.types.FTSResult` ordered by
            descending BM25 score.
        """
        # Build tag subquery filters (one per entry, ANDed).
        tag_clauses: list[str] = []
        tag_params: list[str] = []
        if filters:
            for key, value in filters.items():
                tag_clauses.append(
                    "d.id IN ("
                    "  SELECT document_id FROM document_tags"
                    "  WHERE tag_key = ? AND tag_value = ?"
                    ")"
                )
                tag_params.extend([key, value])

        folder_clause = ""
        folder_params: list[str] = []
        if folder is not None:
            escaped = _escape_like(folder)
            folder_clause = "AND (d.folder = ? OR d.folder LIKE ? ESCAPE '\\')"
            folder_params = [folder, escaped + "/%"]

        tag_filter_sql = ""
        if tag_clauses:
            tag_filter_sql = "AND " + " AND ".join(tag_clauses)

        # column index 4 is the 'content' column in
        #   notes_fts USING fts5(path, title, folder, heading, content, ...)
        if snippet_words and snippet_words > 0:
            content_expr = "snippet(notes_fts, 4, '', '', '…', ?) AS content"
            snippet_params: list[object] = [snippet_words]
        else:
            content_expr = "f.content AS content"
            snippet_params = []

        # Correlated subqueries pick the matching sections row to source
        # start_line and section_id for each FTS hit, so the keyword/hybrid
        # channels can honour the documented (score DESC, start_line ASC,
        # section_id ASC) within-group tie-break.  Matching is on
        # (document_id, content, heading) — sections.content stores the
        # chunk text unmodified, the same value that notes_fts.content
        # stores; the snippet() projection only rewrites the SELECT list,
        # not the underlying f.content column referenced here.  COALESCE on
        # heading treats NULL/empty as equivalent so identical-content
        # chunks under different headings don't cross-match.  MIN()+fallback
        # 0 handles legacy on-disk indices that pre-date this query
        # (preserves prior behaviour).  Sections are inserted in document
        # order, so within one document MIN(s.id) and MIN(s.start_line)
        # resolve to the same row.  The trailing f.rowid tie-break makes the
        # candidate set itself deterministic at the LIMIT boundary when
        # several hits share a bm25 rank.
        sql = f"""
            SELECT
                f.path,
                d.title,
                d.folder,
                f.heading,
                {content_expr},
                ABS(f.rank) AS score,
                d.chunk_count AS chunk_count,
                COALESCE((
                    SELECT MIN(s.start_line)
                    FROM sections s
                    WHERE s.document_id = d.id
                      AND s.content = f.content
                      AND COALESCE(s.heading, '') = COALESCE(f.heading, '')
                ), 0) AS start_line,
                COALESCE((
                    SELECT MIN(s.id)
                    FROM sections s
                    WHERE s.document_id = d.id
                      AND s.content = f.content
                      AND COALESCE(s.heading, '') = COALESCE(f.heading, '')
                ), 0) AS section_id
            FROM notes_fts f
            JOIN documents d ON d.path = f.path
            WHERE notes_fts MATCH ?
              {folder_clause}
              {tag_filter_sql}
            ORDER BY score DESC, f.rowid ASC
            LIMIT ?
        """

        if not query:
            return []

        params: list[object] = [
            *snippet_params,
            query,
            *folder_params,
            *tag_params,
            limit,
        ]
        logger.debug(
            "FTS search: query=%r folder=%r filters=%r limit=%d snippet_words=%r",
            query,
            folder,
            filters,
            limit,
            snippet_words,
        )
        try:
            cur = self._conn().execute(sql, params)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if (
                "fts5" in msg
                or "syntax error" in msg
                or "no such column" in msg
                or "unterminated" in msg
            ):
                logger.debug("FTS search: malformed query %r — %s", query, exc)
                return []
            raise
        rows = cur.fetchall()
        logger.debug("FTS search: %d results for query=%r", len(rows), query)

        results: list[FTSResult] = []
        for row in rows:
            results.append(
                FTSResult(
                    path=row["path"],
                    title=row["title"],
                    folder=row["folder"],
                    heading=row["heading"] or None,
                    content=row["content"],
                    score=row["score"],
                    chunk_count=row["chunk_count"],
                    start_line=row["start_line"],
                    section_id=row["section_id"],
                )
            )
        return results

    @_retry_on_locked
    def get_note(self, path: str) -> dict[str, Any] | None:
        """Return document metadata for a single note.

        Args:
            path: Relative document path.

        Returns:
            A dict with keys ``path``, ``title``, ``folder``,
            ``frontmatter_json``, ``content_hash``, ``modified_at``, or
            ``None`` if the document is not indexed.
        """
        cur = self._conn().execute(
            """
            SELECT path, title, folder, frontmatter_json,
                   content_hash, modified_at
            FROM documents
            WHERE path = ?
            """,
            (path,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    @_retry_on_locked
    def list_notes(self, *, folder: str | None = None) -> list[dict[str, Any]]:
        """List all indexed documents, optionally filtered by folder.

        Args:
            folder: If provided, only return documents whose ``folder``
                matches exactly or is a sub-folder of this value.

        Returns:
            List of dicts with the same shape as :meth:`get_note`.
        """
        if folder is not None:
            # Escape LIKE wildcards in the user-supplied folder value so that
            # literal '%' and '_' characters are matched as-is.
            escaped = _escape_like(folder)
            cur = self._conn().execute(
                """
                SELECT path, title, folder, frontmatter_json,
                       content_hash, modified_at
                FROM documents
                WHERE folder = ? OR folder LIKE ? ESCAPE '\\'
                ORDER BY path
                """,
                (folder, escaped + "/%"),
            )
        else:
            cur = self._conn().execute(
                """
                SELECT path, title, folder, frontmatter_json,
                       content_hash, modified_at
                FROM documents
                ORDER BY path
                """
            )
        return [dict(row) for row in cur.fetchall()]

    @_retry_on_locked
    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the index.

        Returns:
            Sorted list of folder strings (including ``""`` for the root).
        """
        cur = self._conn().execute(
            "SELECT DISTINCT folder FROM documents ORDER BY folder"
        )
        return [row[0] for row in cur.fetchall()]

    @_retry_on_locked
    def list_field_values(self, field: str) -> list[str]:
        """Return all distinct tag values for a given frontmatter field.

        If ``field`` was not in ``indexed_frontmatter_fields``, returns an
        empty list.

        Args:
            field: Frontmatter key (e.g. ``"cluster"``).

        Returns:
            Sorted list of distinct value strings.
        """
        cur = self._conn().execute(
            """
            SELECT DISTINCT tag_value
            FROM document_tags
            WHERE tag_key = ?
            ORDER BY tag_value
            """,
            (field,),
        )
        return [row[0] for row in cur.fetchall()]

    @_retry_on_locked
    def count_chunks(self) -> int:
        """Return the total number of chunk rows in the ``sections`` table.

        Returns:
            Integer count of all indexed chunks across all documents.
        """
        row = self._conn().execute("SELECT COUNT(*) FROM sections").fetchone()
        return int(row[0])

    @_retry_on_locked
    def get_toc(self, path: str) -> list[dict[str, str | int]]:
        """Return headings for a document, ordered by position.

        Queries the sections table for distinct non-NULL headings, ordered by
        the first row that introduces each heading.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).

        Returns:
            List of ``{"heading": str, "level": int}`` dicts ordered by
            first appearance.  Empty list if the document is not found or
            has no headings.
        """
        cur = self._conn().execute(
            """
            SELECT heading, heading_level
            FROM sections
            WHERE document_id = (SELECT id FROM documents WHERE path = ?)
              AND heading IS NOT NULL
            GROUP BY heading, heading_level
            ORDER BY MIN(rowid)
            """,
            (path,),
        )
        return [
            {"heading": row["heading"], "level": row["heading_level"]}
            for row in cur.fetchall()
        ]

    @_retry_on_locked
    def get_backlinks(
        self, path: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return all documents that link TO the given path.

        Args:
            path: Relative document path that is the link target
                (e.g. ``"notes/topic.md"``).
            limit: If provided, return at most this many results.

        Returns:
            List of dicts with keys ``source_path``, ``source_title``,
            ``link_text``, ``link_type``, ``fragment``, ``raw_target``.
        """
        limit_clause = "" if limit is None else "LIMIT ?"
        params: tuple[str | int, ...] = (path,) if limit is None else (path, limit)
        cur = self._conn().execute(
            f"""
            SELECT d.path AS source_path,
                   d.title AS source_title,
                   l.link_text,
                   l.link_type,
                   l.fragment,
                   l.raw_target
            FROM links l
            JOIN documents d ON d.id = l.source_id
            WHERE l.target_path = ?
            ORDER BY d.path, l.rowid
            {limit_clause}
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    @_retry_on_locked
    def get_outlinks(
        self, path: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return all links FROM the given document.

        Uses a LEFT JOIN to check target existence in a single query,
        avoiding N+1 round-trips.

        Args:
            path: Relative document path that is the link source
                (e.g. ``"notes/topic.md"``).
            limit: If provided, return at most this many results.

        Returns:
            List of dicts with keys ``target_path``, ``link_text``,
            ``link_type``, ``fragment``, ``raw_target``, ``exists`` (bool).
        """
        limit_clause = "" if limit is None else "LIMIT ?"
        params: tuple[str | int, ...] = (path,) if limit is None else (path, limit)
        cur = self._conn().execute(
            f"""
            SELECT l.target_path,
                   l.link_text,
                   l.link_type,
                   l.fragment,
                   l.raw_target,
                   (t.id IS NOT NULL) AS target_exists
            FROM links l
            JOIN documents d ON d.id = l.source_id
            LEFT JOIN documents t ON t.path = l.target_path
            WHERE d.path = ?
            ORDER BY l.rowid
            {limit_clause}
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    @_retry_on_locked
    def resolve_vault_wikilinks(self) -> int:
        """Resolve vault-wide wikilink ``target_path`` values against the document set.

        Obsidian resolves bare wikilinks (e.g. ``[[Note]]``) by searching the
        entire vault for a document whose filename matches, picking the shortest
        path (fewest path components) when multiple candidates exist.  Wikilinks
        with an explicit path (e.g. ``[[folder/Note]]``) are resolved to any
        document whose path ends with ``folder/Note.md``, again preferring the
        shortest match.

        Resolution anchors on ``raw_target`` (the original wikilink text as
        written) rather than the current ``target_path``.  This ensures
        re-resolution works correctly after a target document is moved or
        renamed — the original stem is always available regardless of how many
        times the index has been rebuilt.

        When no path match is found, the method also checks the
        ``document_aliases`` table — if a document declares an ``aliases``
        frontmatter field containing the wikilink stem, the link resolves to
        that document.  This mirrors Obsidian's alias resolution behaviour
        (e.g. ``[[AI]]`` resolves to a document with ``aliases: [AI]``).

        Wikilinks with an explicit relative prefix (``./`` or ``../``) are
        skipped; they are resolved relative to the source document at scan time
        and do not participate in vault-wide resolution.

        Call this method after all documents have been indexed
        (``IndexFacet.build_index()`` and ``IndexFacet.reindex()`` do this
        automatically).  Callers that add documents via :meth:`upsert_note`
        directly are responsible for calling this method once all upserts are
        complete.

        Uses ``substr``/``length`` comparisons instead of ``LIKE`` to avoid
        wildcard-escaping issues with filenames that contain ``%`` or ``_``.

        Returns:
            Number of link rows whose ``target_path`` was updated.
        """
        conn = self._conn()
        # Load all document paths once into a Python set/list for in-memory
        # matching — avoids O(N) SQL round-trips (one SELECT per wikilink row).
        doc_paths: list[str] = [
            r["path"] for r in conn.execute("SELECT path FROM documents").fetchall()
        ]

        # Build alias → document path mapping for fallback resolution.
        # Case-insensitive: Obsidian alias matching is case-insensitive.
        alias_rows = conn.execute(
            """
            SELECT da.alias, d.path
            FROM document_aliases da
            JOIN documents d ON d.id = da.document_id
            """
        ).fetchall()
        # Map lowercased alias to list of document paths (multiple docs could
        # share an alias; pick shortest path like the path-based resolution).
        alias_map: dict[str, list[str]] = {}
        for ar in alias_rows:
            alias_map.setdefault(ar["alias"].lower(), []).append(ar["path"])

        # Fetch all wikilinks eligible for vault-wide resolution.
        # Explicit relative prefixes (./  ../) are excluded — those were
        # resolved at scan time and must not be overwritten.
        rows = conn.execute(
            """
            SELECT id, raw_target, target_path
            FROM links
            WHERE link_type = 'wikilink'
              AND raw_target NOT LIKE './%'
              AND raw_target NOT LIKE '../%'
            """
        ).fetchall()

        # Resolve each wikilink in Python, then batch-UPDATE.
        updates: list[tuple[str, int]] = []
        for row in rows:
            # Derive the search filename from raw_target:
            # 1. Strip any trailing fragment (#heading).
            stem = row["raw_target"]
            if "#" in stem:
                stem = stem[: stem.index("#")]
            if not stem:
                continue
            # 2. Append .md if not already present.
            search_target = stem if stem.lower().endswith(".md") else stem + ".md"

            # Find the best match: exact path or suffix match, shortest wins.
            candidates = [
                p
                for p in doc_paths
                if p == search_target or p.endswith("/" + search_target)
            ]
            if not candidates:
                # Fallback: check if the stem matches a document alias.
                # Use the raw stem (without .md) for alias matching.
                alias_candidates = alias_map.get(stem.lower(), [])
                if alias_candidates:
                    candidates = alias_candidates
            if not candidates:
                continue  # Genuinely broken — no document matches.
            new_path = min(candidates, key=len)
            if new_path != row["target_path"]:
                updates.append((new_path, row["id"]))

        with conn:
            conn.executemany("UPDATE links SET target_path = ? WHERE id = ?", updates)
        updated = len(updates)

        if updated:
            logger.debug("resolve_vault_wikilinks: resolved %d wikilink(s)", updated)
        return updated

    @_retry_on_locked
    def get_broken_links(self, *, folder: str | None = None) -> list[dict[str, Any]]:
        """Return all links whose target does not exist as an indexed document.

        Args:
            folder: If provided, restrict to source documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of dicts with keys ``source_path``, ``source_title``,
            ``target_path``, ``link_text``, ``link_type``, ``fragment``,
            ``raw_target``.
        """
        folder_clause = ""
        params: list[str] = []
        if folder is not None:
            escaped = _escape_like(folder)
            folder_clause = "AND (d.folder = ? OR d.folder LIKE ? ESCAPE '\\')"
            params = [folder, escaped + "/%"]

        sql = f"""
            SELECT d.path AS source_path,
                   d.title AS source_title,
                   l.target_path,
                   l.link_text,
                   l.link_type,
                   l.fragment,
                   l.raw_target
            FROM links l
            JOIN documents d ON d.id = l.source_id
            WHERE NOT EXISTS (
                SELECT 1 FROM documents d2 WHERE d2.path = l.target_path
            )
              {folder_clause}
            ORDER BY d.path, l.rowid
        """
        cur = self._conn().execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    @_retry_on_locked
    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the most recently modified documents.

        Args:
            limit: Maximum number of documents to return.
            folder: If provided, restrict to documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of dicts with the same shape as :meth:`get_note`, ordered
            by ``modified_at`` descending (most recent first).
        """
        if folder is not None:
            escaped = _escape_like(folder)
            cur = self._conn().execute(
                """
                SELECT path, title, folder, frontmatter_json,
                       modified_at
                FROM documents
                WHERE folder = ? OR folder LIKE ? ESCAPE '\\'
                ORDER BY modified_at DESC
                LIMIT ?
                """,
                (folder, escaped + "/%", limit),
            )
        else:
            cur = self._conn().execute(
                """
                SELECT path, title, folder, frontmatter_json,
                       modified_at
                FROM documents
                ORDER BY modified_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in cur.fetchall()]

    @_retry_on_locked
    def get_orphan_notes(self) -> list[dict[str, Any]]:
        """Return all documents with no inbound or outbound links.

        A document is an orphan if it has zero rows in ``links`` as either
        source (no outlinks) AND does not appear as a target in any link row
        (no backlinks).

        Returns:
            List of dicts with keys ``path``, ``title``, ``folder``,
            ``frontmatter_json``, and ``modified_at``, ordered by path.
        """
        cur = self._conn().execute(
            """
            SELECT path, title, folder, frontmatter_json, modified_at
            FROM documents d
            WHERE NOT EXISTS (SELECT 1 FROM links WHERE source_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM links WHERE target_path = d.path)
            ORDER BY path
            """
        )
        return [dict(row) for row in cur.fetchall()]

    @_retry_on_locked
    def get_most_linked(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the documents with the most distinct source documents linking to them.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of dicts with keys ``path``, ``title``, ``folder``,
            ``backlink_count``, ordered by backlink_count descending.
        """
        cur = self._conn().execute(
            """
            SELECT d.path,
                   d.title,
                   d.folder,
                   COUNT(DISTINCT l.source_id) AS backlink_count
            FROM links l
            JOIN documents d ON d.path = l.target_path
            GROUP BY d.path, d.title, d.folder
            ORDER BY backlink_count DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    @_retry_on_locked
    def get_connection_path(
        self, source_path: str, target_path: str, max_depth: int = 10
    ) -> list[str] | None:
        """Return the shortest undirected path between two notes via BFS.

        Treats the link graph as undirected — a link in either direction
        counts as a connection.  Raises ``ValueError`` if either path is
        not in the documents table.

        Args:
            source_path: Vault-relative path of the starting note.
            target_path: Vault-relative path of the destination note.
            max_depth: Maximum path length (number of edges).  Clamped to
                ``[1, 10]``.  Defaults to ``10``.

        Returns:
            Ordered list of vault-relative paths from *source_path* to
            *target_path* (inclusive), or ``None`` if no path exists within
            *max_depth* hops.

        Raises:
            ValueError: If *source_path* or *target_path* is not found in
                the documents table.
        """
        max_depth = max(1, min(10, max_depth))

        # Validate both endpoints exist.
        for path in (source_path, target_path):
            row = (
                self._conn()
                .execute("SELECT 1 FROM documents WHERE path = ?", (path,))
                .fetchone()
            )
            if row is None:
                raise ValueError(f"Path not found in index: {path!r}")

        # Trivial case.
        if source_path == target_path:
            return [source_path]

        # Load all edges into an undirected adjacency dict.
        adj: dict[str, set[str]] = {}
        cur = self._conn().execute(
            "SELECT d1.path, d2.path FROM links l"
            " JOIN documents d1 ON d1.id = l.source_id"
            " JOIN documents d2 ON d2.path = l.target_path"
        )
        for src, tgt in cur.fetchall():
            adj.setdefault(src, set()).add(tgt)
            adj.setdefault(tgt, set()).add(src)

        # BFS with depth tracking.
        queue: deque[tuple[str, list[str]]] = deque()
        queue.append((source_path, [source_path]))
        visited: set[str] = {source_path}

        while queue:
            current, current_path = queue.popleft()
            if len(current_path) - 1 >= max_depth:
                continue
            for neighbour in adj.get(current, set()):
                if neighbour in visited:
                    continue
                new_path = [*current_path, neighbour]
                if neighbour == target_path:
                    logger.debug(
                        "get_connection_path: found path %s → %s in %d hops",
                        source_path,
                        target_path,
                        len(new_path) - 1,
                    )
                    return new_path
                visited.add(neighbour)
                queue.append((neighbour, new_path))

        logger.debug(
            "get_connection_path: no path found between %r and %r within depth %d",
            source_path,
            target_path,
            max_depth,
        )
        return None

    def _count_links_query(self, sql: str) -> int:
        """Execute a COUNT query, returning 0 if the links table does not exist.

        Args:
            sql: A SQL statement returning a single COUNT(*) row.

        Returns:
            The integer count, or 0 when the links table is absent (backward
            compatibility with index files predating link tracking).

        Raises:
            sqlite3.OperationalError: For any database error other than a
                missing links table.
        """
        try:
            row = self._conn().execute(sql).fetchone()
            return int(row[0])
        except sqlite3.OperationalError as e:
            if "no such table: links" in str(e).lower():
                return 0
            raise

    @_retry_on_locked
    def count_links(self) -> int:
        """Return the total number of link rows in the links table.

        Returns:
            Total link count, or 0 if the links table does not exist.
        """
        return self._count_links_query("SELECT COUNT(*) FROM links")

    @_retry_on_locked
    def count_broken_links(self) -> int:
        """Return the number of links whose target is not in the documents table.

        Returns:
            Broken link count, or 0 if the links table does not exist.
        """
        return self._count_links_query(
            """
            SELECT COUNT(*)
            FROM links
            WHERE NOT EXISTS (
                SELECT 1 FROM documents d WHERE d.path = links.target_path
            )
            """
        )

    @_retry_on_locked
    def count_orphans(self) -> int:
        """Return the number of documents with no inbound or outbound links.

        Returns:
            Orphan count, or 0 if the links table does not exist.
        """
        return self._count_links_query(
            """
            SELECT COUNT(*)
            FROM documents d
            WHERE NOT EXISTS (SELECT 1 FROM links WHERE source_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM links WHERE target_path = d.path)
            """
        )

    @_retry_on_locked
    def get_chunk_count(self, path: str) -> int:
        """Return the chunk_count for a single document, defaulting to 1.

        Args:
            path: Relative document path.

        Returns:
            The ``chunk_count`` stored in the documents table, or ``1`` if
            the document is not found.
        """
        row = (
            self._conn()
            .execute("SELECT chunk_count FROM documents WHERE path = ?", (path,))
            .fetchone()
        )
        return int(row["chunk_count"]) if row else 1

    def get_chunk_counts(self, paths: Iterable[str]) -> dict[str, int]:
        """Return a ``{path: chunk_count}`` map for the given paths.

        Missing paths are omitted; callers should default to ``1``.

        Materialises *paths* into a list BEFORE the retry window opens.
        ``@_retry_on_locked`` cannot be used directly here because the
        decorator captures the original argument tuple — a one-shot
        generator would be exhausted by the first attempt before the
        inner ``list()`` call runs, causing the retry to silently return
        ``{}`` instead of the correct map. Mirrors :meth:`build_from_notes`.

        Args:
            paths: Iterable of relative document paths to look up.

        Returns:
            Dict mapping each found path to its ``chunk_count`` value.
        """
        paths_list = list(paths)

        def _do() -> dict[str, int]:
            return self._get_chunk_counts_inner(paths_list)

        return _retry_on_sqlite_locked(_do)

    def _get_chunk_counts_inner(self, paths: list[str]) -> dict[str, int]:
        """Inner body of :meth:`get_chunk_counts`, safe to re-invoke on retry."""
        if not paths:
            return {}
        placeholders = ",".join("?" * len(paths))
        rows = (
            self._conn()
            .execute(
                f"SELECT path, chunk_count FROM documents WHERE path IN ({placeholders})",
                paths,
            )
            .fetchall()
        )
        return {r["path"]: int(r["chunk_count"]) for r in rows}

    @_retry_on_locked
    def get_section(self, path: str, heading: str) -> dict[str, Any] | None:
        """Return the first section row for (path, heading), or ``None``.

        Returns a dict with keys ``'content'``, ``'heading'``,
        ``'heading_level'`` on hit; ``None`` when the heading is not found
        in the document.  Tie-breaks by ``start_line ASC``.

        Matching collapses internal whitespace on both sides — the lookup
        ``"1.3.  Reducing..."`` (two spaces) hits a stored heading
        ``"1.3. Reducing..."`` (one space) and vice versa.  Markdown
        editors and rendered TOC widgets normalise whitespace
        unpredictably, so LLM callers rarely reproduce the on-disk byte
        sequence; this collapse closes that gap without changing storage.

        Args:
            path: Relative document path.
            heading: Heading string to match (internal whitespace
                collapsed before comparison).

        Performance: comparison runs in Python after fetching all section
        rows for ``path``, since SQLite has no portable regex-collapse
        operator that would let us push the normalisation into the WHERE
        clause.  ``idx_sections_docid`` keeps the per-doc fetch cheap (a
        few tens to low hundreds of rows for a typical note); documents
        with thousands of sections would prefer a stored ``heading_norm``
        column.  Not optimising preemptively — the chunker's
        ``chunks_per_file`` ceiling and adaptive splitting make very
        deeply-sectioned documents rare.

        Returns:
            A dict with the following fields, or ``None`` when not found:

            * ``content``: The section's text content.
            * ``heading``: The matched heading string (as stored).
            * ``heading_level``: The heading level (1-6).
        """
        norm_query = _normalize_heading(heading)
        if not norm_query:
            return None
        rows = (
            self._conn()
            .execute(
                """
            SELECT s.content, s.heading, s.heading_level
            FROM sections s
            JOIN documents d ON d.id = s.document_id
            WHERE d.path = ? AND s.heading IS NOT NULL
            ORDER BY s.start_line ASC
            """,
                (path,),
            )
            .fetchall()
        )
        for row in rows:
            if _normalize_heading(row["heading"]) == norm_query:
                return {
                    "content": row["content"],
                    "heading": row["heading"],
                    "heading_level": row["heading_level"],
                }
        return None

    @_retry_on_locked
    def list_section_headings(self, path: str, *, limit: int = 50) -> list[str]:
        """Return up to *limit* section headings for *path*, in document order.

        Used to build "did you mean" suggestions when
        :meth:`get_section` misses.  Sections without a heading
        (preamble) are skipped — only string headings worth suggesting
        to a caller are returned.

        Args:
            path: Relative document path.
            limit: Maximum number of headings to return.

        Returns:
            List of heading strings in document order, deduplicated while
            preserving the first-occurrence order.
        """
        rows = (
            self._conn()
            .execute(
                """
            SELECT s.heading
            FROM sections s
            JOIN documents d ON d.id = s.document_id
            WHERE d.path = ? AND s.heading IS NOT NULL
            GROUP BY s.heading
            ORDER BY MIN(s.start_line) ASC
            LIMIT ?
            """,
                (path, limit),
            )
            .fetchall()
        )
        return [row["heading"] for row in rows]

    def close(self) -> None:
        """Close every per-thread connection and mark this index closed.

        Idempotent: a second call finds an empty registry and performs no
        connection closes. After ``close()``, any thread calling a public
        method raises ``sqlite3.ProgrammingError`` from ``_conn()``.
        """
        with self._reg_lock:
            self._closed = True
            try:
                for conn in self._all_conns:
                    try:
                        conn.close()
                    except sqlite3.ProgrammingError:
                        logger.debug("fts_index.close: connection already closed")
                    except Exception:
                        # Catch non-sqlite3.Error subclasses too (e.g. OSError
                        # from underlying file handle, RuntimeError from C
                        # wrappers) so the loop never exits mid-iteration and
                        # leaves connections un-closed. KeyboardInterrupt /
                        # SystemExit still propagate.
                        logger.error(
                            "fts_index.close: error closing connection",
                            exc_info=True,
                        )
            finally:
                # Ensure the registry is cleared even if a BaseException
                # (KeyboardInterrupt / SystemExit) interrupts the loop, so a
                # subsequent close() retry sees a clean state.
                self._all_conns.clear()
                self._primary_conn = None
        logger.debug("FTSIndex closed (db_path=%s)", self._db_path)
