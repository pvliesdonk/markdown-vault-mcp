"""SQLite FTS5 index for full-text search and tag filtering."""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from markdown_vault_mcp._fts_connection import (
    SqliteConnectionRegistry,
)
from markdown_vault_mcp._fts_connection import (
    retry_on_locked as _retry_on_locked,
)
from markdown_vault_mcp._fts_connection import (
    retry_on_sqlite_locked as _retry_on_sqlite_locked,
)
from markdown_vault_mcp.embed_text import fields_text as _fields_text
from markdown_vault_mcp.types import FTSResult, ParsedNote, SubtreeNote, TocEntry

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


# Thresholds for running FTS5 'optimize' after a bulk purge.  Deleting rows
# from an FTS5 table only marks their tokens as deleted inside the inverted
# index — the dead entries stay in the on-disk segment b-trees until segments
# are merged, which FTS5 does lazily and may never get around to.  A purge
# pass that removes many documents (e.g. newly-configured exclude patterns
# expelling previously indexed files at boot, issue #255) can therefore leave
# hundreds of megabytes of dead segments that bloat the database file and
# slow keyword queries.  'optimize' merges everything into one clean segment.
# An optimize pass is cheap relative to the bloat it removes, so the
# thresholds err toward running it: a purge of at least
# ``OPTIMIZE_MIN_PURGED_DOCS`` documents, or at least
# ``OPTIMIZE_MIN_PURGED_FRACTION`` of the pre-purge corpus, qualifies.
OPTIMIZE_MIN_PURGED_DOCS = 25
OPTIMIZE_MIN_PURGED_FRACTION = 0.10


def should_optimize(purged: int, total_before: int) -> bool:
    """Decide whether a bulk purge warrants an FTS5 'optimize' pass.

    Args:
        purged: Number of documents removed in a single purge pass.
        total_before: Number of indexed documents before the purge.

    Returns:
        ``True`` when ``purged`` is at least :data:`OPTIMIZE_MIN_PURGED_DOCS`
        or at least :data:`OPTIMIZE_MIN_PURGED_FRACTION` of ``total_before``.
    """
    if purged <= 0 or total_before <= 0:
        return False
    if purged >= OPTIMIZE_MIN_PURGED_DOCS:
        return True
    return purged / total_before >= OPTIMIZE_MIN_PURGED_FRACTION


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
# set here — `SqliteConnectionRegistry.apply_pragmas` sets it on every
# connection (primary and per-thread) before `_init_schema` runs.
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
    path, title, folder, heading, content, summary,
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

# Keys recording the embedding provenance that determines FTS chunk
# boundaries at build time (#649). The chunker is shared by FTS and
# embeddings; its char cap is derived from the embedding model's context
# unless an explicit override is set. We record the two STABLE inputs to that
# derivation — the model name and the explicit ``MAX_CHUNK_CHARS`` override —
# rather than the derived cap itself. Comparing the stable inputs on warm
# restart rejects the short-circuit on a genuine model or override change,
# while NOT flapping when the derived cap merely differs because the model's
# context length was read transiently differently (e.g. an Ollama instance
# briefly unreachable). A ``None`` value is stored as the empty string and
# read back as ``None``.
_META_EMBED_MODEL_KEY = "embed_model_name"
_META_MAX_CHUNK_CHARS_OVERRIDE_KEY = "max_chunk_chars_override"
# Curated-ranking provenance: the title field and searchable frontmatter
# fields in force at build time. Stored as "" when equal to the default so a
# pre-upgrade index (absent keys) reads back as defaults and keeps the
# warm-restart short-circuit intact.
_META_TITLE_FIELD_KEY = "title_field"
_META_SEARCHABLE_FIELDS_KEY = "searchable_fields"
# Structured-filter provenance: the frontmatter fields promoted to
# document_tags at build time. Tracked separately from searchable_fields
# (#927) because indexed_frontmatter_fields feeds document_tags only, never
# FTS summary/embedding text, so a change must reject the warm-restart
# short-circuit on its own trigger rather than piggybacking on
# searchable_fields (which only shares its value when SEARCHABLE_FIELDS is
# left to default to INDEXED_FIELDS).
_META_INDEXED_FIELDS_KEY = "indexed_frontmatter_fields"

# Derived-content provenance: the version of this server's own parse-to-row
# pipeline (#1124). Unlike every key above it, this records no operator input
# at all — it is a source constant, bumped whenever a code change alters the
# rows derived from unchanged file bytes. Indexing is hash-based, so an
# unchanged file is never re-parsed after an upgrade and its stale rows would
# be served indefinitely; recording the version lets the warm-restart
# short-circuit reject an index built by an older pipeline and cold-rebuild
# it once. An index written before this key existed reads back as 0, so the
# first start after the upgrade rebuilds — which is the repair #1124 found
# missing after the link-extraction fixes in #1104 / #1107.
_META_INDEX_SEMANTICS_KEY = "index_semantics_version"

#: Current version of the parse-to-row pipeline whose output is stored in the
#: index (link extraction, chunk boundaries, tag/alias/heading derivation).
#:
#: **Bump this in the same commit** as any change that makes an unchanged file
#: yield different stored rows. The bump is what converts such a fix into a
#: fix operators actually receive: on the next start, the warm-restart
#: short-circuit is rejected and every file is re-parsed once. Changes that
#: only affect how stored rows are *queried* or *rendered* (ranking, snippets,
#: response shaping) need no bump — nothing on disk went stale.
INDEX_SEMANTICS_VERSION = 1


class ChunkingMeta(NamedTuple):
    """Embedding/indexing provenance recorded with an FTS build (#649).

    Attributes:
        model: Embedding model name, or ``None`` when no provider is
            configured.
        max_chunk_chars_override: The explicit operator ``MAX_CHUNK_CHARS``
            override in force, or ``None`` when the cap was derived from the
            model context.
        title_field: Frontmatter key used for title resolution at build time
            (default ``"title"``).
        searchable_fields: Comma-joined canonical form of the searchable
            frontmatter fields feeding the FTS ``summary`` column (default
            ``""`` — none configured).
        indexed_frontmatter_fields: Comma-joined canonical form of the
            frontmatter fields promoted to ``document_tags`` for structured
            filtering (default ``""`` — none configured).
        semantics_version: :data:`INDEX_SEMANTICS_VERSION` in force when the
            build ran (``0`` for an index written before the key existed).
    """

    model: str | None
    max_chunk_chars_override: int | None
    title_field: str = "title"
    searchable_fields: str = ""
    indexed_frontmatter_fields: str = ""
    semantics_version: int = 0


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


def _is_fts5_query_error(exc: sqlite3.OperationalError) -> bool:
    """True when an ``OperationalError`` is an FTS5 *query-syntax* error.

    Distinguishes a malformed MATCH expression (user input problem, safe to
    recover from) from an environmental error such as a database lock (must
    propagate). Matches the error strings SQLite emits for FTS5 query
    problems: ``fts5: syntax error``, ``no such column`` (a bareword parsed as
    a column filter), and ``unterminated string``.
    """
    msg = str(exc).lower()
    return (
        "fts5" in msg
        or "syntax error" in msg
        or "no such column" in msg
        or "unterminated" in msg
    )


def _quote_fts_query(query: str) -> str:
    """Quote each whitespace-separated token as an FTS5 string literal.

    FTS5 query syntax treats characters like ``-`` and ``:`` between barewords
    as operators/column filters, so a natural-language query such as
    ``File-back Ingest Lint`` or a slug like ``vault-mcp`` is rejected with an
    ``OperationalError`` (#866). Wrapping each token in a string literal
    (``"File-back" "Ingest" "Lint"`` — case is preserved; FTS5's tokenizer
    handles case-folding) turns the query into an implicit-AND of phrase
    literals: the special characters are matched literally, a compound
    like ``File-back`` becomes an adjacency phrase over its tokens, and no
    token can be mistaken for an operator or column reference. Internal quotes
    are escaped by doubling, per FTS5's literal grammar.

    Args:
        query: The raw user query that FTS5 rejected.

    Returns:
        A space-joined string of quoted literals, or ``""`` when *query* has
        no tokens (the caller then returns no results).
    """
    tokens = query.split()
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


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


class FTSIndex:
    """SQLite FTS5 index providing BM25 search and tag filtering.

    Wraps a SQLite database file (or in-memory database) and exposes CRUD
    operations and full-text search over a vault of markdown documents.

    **Thread safety (issue #519):** every public method is safe to call from
    any thread. Each thread that touches the index opens its own
    ``sqlite3.Connection`` on first use via :meth:`_conn`; a side registry
    (``SqliteConnectionRegistry.all_conns`` in ``_fts_connection.py``,
    guarded by ``reg_lock``) holds strong refs so
    :meth:`close` can close every connection — including those opened by
    threads that have since exited. Concurrent index mutations are
    serialised by the single-owner :class:`IndexWriter` thread (#559),
    not by this class. After :meth:`close`,
    every public method raises ``sqlite3.ProgrammingError``. See
    ``docs/design/design.md`` "Vault thread-safety contract" for the full
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
        searchable_frontmatter_fields: Frontmatter keys whose scalar values
            are newline-joined into the ``notes_fts.summary`` column on each
            document's chunk-0 row, making them keyword-searchable.  ``None``
            (default) stores an empty summary.
        fts_weights: Per-column BM25 weights (keys are FTS column names in
            ``path``, ``title``, ``folder``, ``heading``, ``content``,
            ``summary``; missing keys default to ``1.0``).  Persisted into
            the FTS5 rank configuration at init; ``None`` or all-``1.0``
            resets to the default ``bm25()``.
    """

    # notes_fts column order — bm25() weight argument order must match.
    _FTS_COLUMNS = ("path", "title", "folder", "heading", "content", "summary")

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        indexed_frontmatter_fields: list[str] | None = None,
        *,
        searchable_frontmatter_fields: list[str] | None = None,
        fts_weights: dict[str, float] | None = None,
    ) -> None:
        self._db_path = db_path
        self._indexed_fields: list[str] = indexed_frontmatter_fields or []
        self._searchable_fields: tuple[str, ...] = tuple(
            searchable_frontmatter_fields or ()
        )
        self._fts_weights: dict[str, float] = dict(fts_weights or {})
        # SearchConfig validates fts_weights keys, but direct FTSIndex/Vault
        # construction bypasses it; an unknown column is silently ignored by
        # _persist_rank_config (get(col, 1.0)). Warn so a typo'd column
        # ("titel") is visible rather than degrading to default ranking.
        unknown_weight_cols = set(self._fts_weights) - set(self._FTS_COLUMNS)
        if unknown_weight_cols:
            logger.warning(
                "fts_index: ignoring unknown fts_weights columns=%s (valid: %s)",
                sorted(unknown_weight_cols),
                ", ".join(self._FTS_COLUMNS),
            )
        # Thread-safety core (#519): per-thread connections, the strong-ref
        # close registry, and the BaseException-hardened bootstrap live in
        # the extracted SqliteConnectionRegistry (#760). Schema DDL and the
        # shared-cache probe stay FTSIndex-owned and run via callbacks.
        self._registry = SqliteConnectionRegistry(db_path)
        self._registry.open_primary(self._init_schema, self._probe_shared_cache)

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
        self._migrate_notes_fts_summary(conn)
        self._persist_rank_config(conn)
        # WAL is a DB-header pragma — persists across opens. Skip for in-memory
        # databases (SQLite silently falls back to 'memory' journal mode there).
        if not self._registry.is_memory:
            result = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            if result is None or str(result[0]).lower() != "wal":
                logger.warning(
                    "Could not enable WAL journal mode (got %s); "
                    "concurrent reads during writes may block",
                    result[0] if result else "no result",
                )
        conn.commit()

    def _migrate_notes_fts_summary(self, conn: sqlite3.Connection) -> None:
        """Migrate a legacy 5-column ``notes_fts`` to the 6-column schema.

        FTS5 virtual tables do not support ``ALTER TABLE ADD COLUMN``, so a
        pre-``summary`` table is dropped, recreated with the current schema,
        and repopulated in pure SQL from the ``sections``/``documents``
        source of truth — no filesystem rescan.  Migrated rows carry an
        empty ``summary`` (correct for the default configuration; a
        non-default ``searchable_frontmatter_fields`` change flips the
        chunking provenance and triggers a cold rebuild anyway).  The
        ``meta`` table is untouched, so the warm-restart completeness
        sentinel survives the migration.

        The DROP/CREATE/INSERT run inside one explicit transaction rather
        than :meth:`sqlite3.Connection.executescript`, which commits before
        it runs and leaves each statement to autocommit individually. A
        crash (SIGKILL/OOM/power loss) between the CREATE and the INSERT
        would otherwise leave a present-but-empty ``notes_fts``; the next
        boot would see the ``summary`` column, early-return here, and the
        warm-restart short-circuit (meta sentinel + ``documents`` both
        intact) would skip the rebuild — keyword search silently returning
        nothing forever. On any failure the transaction rolls back, so the
        legacy table is preserved and the migration retries cleanly on the
        next boot.

        Args:
            conn: The primary connection (inside :meth:`_init_schema`).
        """
        cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(notes_fts)").fetchall()
        }
        if "summary" in cols:
            return
        try:
            conn.execute("BEGIN")
            conn.execute("DROP TABLE notes_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE notes_fts USING fts5("
                "path, title, folder, heading, content, summary, "
                "tokenize='porter unicode61')"
            )
            conn.execute(
                "INSERT INTO notes_fts"
                "(path, title, folder, heading, content, summary) "
                "SELECT d.path, d.title, d.folder, COALESCE(s.heading, ''), "
                "s.content, '' "
                "FROM sections s "
                "JOIN documents d ON d.id = s.document_id"
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            logger.error(
                "fts_index: notes_fts summary migration failed; "
                "legacy table preserved for retry",
                exc_info=True,
            )
            raise
        logger.info(
            "fts_index: migrated notes_fts — added summary column and "
            "repopulated from sections"
        )

    def _persist_rank_config(self, conn: sqlite3.Connection) -> None:
        """Persist the BM25 per-column weights into the FTS5 rank config.

        When any configured weight differs from ``1.0``, writes a weighted
        ``bm25(...)`` rank function (arguments in ``notes_fts`` column
        order; unspecified columns weigh ``1.0``).  Otherwise writes the
        default ``bm25()`` — an idempotent reset so removing the
        ``FTS_WEIGHTS`` configuration restores default ranking.  ``f.rank``
        reads pick the persisted function up transparently.

        Args:
            conn: The primary connection (inside :meth:`_init_schema`).
        """
        weights = [self._fts_weights.get(col, 1.0) for col in self._FTS_COLUMNS]
        if any(w != 1.0 for w in weights):
            rank = "bm25({})".format(", ".join(f"{w:g}" for w in weights))
        else:
            rank = "bm25()"
        conn.execute(
            "INSERT INTO notes_fts(notes_fts, rank) VALUES('rank', ?)",
            (rank,),
        )
        conn.commit()
        logger.debug("fts_index: persisted rank config %s", rank)

    def _probe_shared_cache(self) -> None:
        """Verify that a second connection to the same in-memory URI sees the schema.

        If ``SQLITE_ENABLE_SHARED_CACHE`` is unavailable in this SQLite build,
        the second connection will see an empty database — per-thread reads
        would then fail with confusing OperationalErrors. Surface that
        condition immediately with an operator-actionable error.
        """
        # The probe connection intentionally bypasses the close registry: it
        # is opened and closed entirely within this method before the FTSIndex
        # instance is exposed to any caller, so the registry-based close()
        # machinery is not needed for it.
        probe = self._registry.connect()
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

        Delegates to :meth:`SqliteConnectionRegistry.conn` — see there for
        the double-checked-locking contract and the note on connection
        accumulation.

        Raises:
            sqlite3.ProgrammingError: If ``close()`` has been called.
        """
        return self._registry.conn()

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
        The ``summary`` column carries the newline-joined scalar values of
        the configured ``searchable_frontmatter_fields`` on the chunk-0 row
        only (``""`` for every other row and when no fields are configured),
        so frontmatter text is keyword-searchable without perturbing the
        per-chunk ``content`` column.

        Args:
            cur: Active cursor inside the current transaction.
            document_id: The ``id`` of the parent document row.
            note: Parsed document whose chunks are to be inserted.
        """
        folder = _derive_folder(note.path)
        summary = _fields_text(note.frontmatter, self._searchable_fields)
        for i, chunk in enumerate(note.chunks):
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
                INSERT INTO notes_fts (path, title, folder, heading, content,
                                       summary)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    note.path,
                    note.title,
                    folder,
                    chunk.heading or "",
                    chunk.content,
                    summary if i == 0 else "",
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

    def optimize(self) -> bool:
        """Merge FTS5 inverted-index segments, dropping deleted-document tokens.

        Runs ``INSERT INTO notes_fts(notes_fts) VALUES('optimize')``, which
        merges all FTS5 segments into one and discards entries for deleted
        rows.  Call this after a bulk purge (see :func:`should_optimize`) so
        dead segments do not accumulate in the database file.

        The merge frees pages *inside* the database file; the file itself
        only shrinks after a ``VACUUM``.  ``VACUUM`` is never run
        automatically because it takes an exclusive lock and multiple server
        processes may share one index file — instead the reclaimable size
        (freelist pages times page size) is logged at INFO so operators can
        decide whether a manual vacuum is worthwhile.

        Lock contention beyond the retry budget is tolerated: the optimize
        is skipped with a warning and the next bulk purge retries.

        Returns:
            ``True`` when the optimize ran, ``False`` when it was skipped
            because the database stayed busy or locked.
        """
        conn = self._conn()

        def _do() -> None:
            with conn:
                conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('optimize')")

        try:
            _retry_on_sqlite_locked(_do)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "busy" in msg or "locked" in msg:
                logger.warning(
                    "optimize: skipped — database contended (%s); "
                    "next bulk purge will retry",
                    exc,
                )
                return False
            raise
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        reclaimable = int(freelist) * int(page_size)
        logger.info(
            "optimize: merged FTS5 segments — %d bytes reclaimable "
            "(run VACUUM on the index file to reclaim disk space)",
            reclaimable,
        )
        return True

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

    # ------------------------------------------------------------------
    # Chunking provenance: embedding model + char cap (issue #649)
    # ------------------------------------------------------------------

    @_retry_on_locked
    def set_chunking_meta(
        self,
        *,
        model: str | None,
        max_chunk_chars_override: int | None,
        title_field: str = "title",
        searchable_fields: str = "",
        indexed_frontmatter_fields: str = "",
    ) -> None:
        """Record the embedding/indexing provenance used for this build.

        Persists the stable inputs that determine the FTS build's contents —
        the embedding model name, the explicit char-cap override, the title
        field, the searchable frontmatter fields, and the indexed
        (structured-filter) frontmatter fields — so a later warm-restart can
        detect a genuine option change and reject the short-circuit,
        triggering a cold rebuild (#649, #927).

        :data:`INDEX_SEMANTICS_VERSION` is written alongside them and is
        deliberately not a parameter: it records this build's *code*
        provenance rather than an operator input, so a pipeline change that
        alters derived rows invalidates the index the same way an option
        change does (#1124).

        Args:
            model: Embedding model name, or ``None`` when no provider is
                configured. ``None`` is stored as the empty string.
            max_chunk_chars_override: The explicit operator char-cap override
                in force, or ``None`` when the cap was derived from the model
                context. ``None`` is stored as the empty string; an int is
                stored as its decimal string form.
            title_field: Frontmatter key used for title resolution. The
                default ``"title"`` is stored as the empty string so a
                pre-upgrade index (absent key) reads back as the default.
            searchable_fields: Comma-joined canonical searchable-field list.
                The default (no fields) is already the empty string.
            indexed_frontmatter_fields: Comma-joined canonical
                structured-filter field list. The default (no fields) is
                already the empty string.
        """
        conn = self._conn()
        model_value = "" if model is None else model
        override_value = (
            ""
            if max_chunk_chars_override is None
            else str(int(max_chunk_chars_override))
        )
        title_value = "" if title_field == "title" else title_field
        with conn:
            for key, value in (
                (_META_EMBED_MODEL_KEY, model_value),
                (_META_MAX_CHUNK_CHARS_OVERRIDE_KEY, override_value),
                (_META_TITLE_FIELD_KEY, title_value),
                (_META_SEARCHABLE_FIELDS_KEY, searchable_fields),
                (_META_INDEXED_FIELDS_KEY, indexed_frontmatter_fields),
                (_META_INDEX_SEMANTICS_KEY, str(INDEX_SEMANTICS_VERSION)),
            ):
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                    (key, value),
                )

    @_retry_on_locked
    def get_chunking_meta(self) -> ChunkingMeta:
        """Return the recorded provenance, with defaults for absent keys.

        Returns:
            A :class:`ChunkingMeta`. An absent key or a stored empty string
            reads back as the default (``None`` for ``model`` and
            ``max_chunk_chars_override``, ``"title"`` for ``title_field``,
            ``""`` for ``searchable_fields`` and
            ``indexed_frontmatter_fields``);
            ``max_chunk_chars_override`` reads back as ``int``, and
            ``semantics_version`` as ``int`` (``0`` when absent or
            unparseable — either way the index predates the pipeline
            version it claims and must be rebuilt).
        """
        conn = self._conn()
        with conn:
            rows = conn.execute(
                "SELECT key, value FROM meta WHERE key IN (?, ?, ?, ?, ?, ?)",
                (
                    _META_EMBED_MODEL_KEY,
                    _META_MAX_CHUNK_CHARS_OVERRIDE_KEY,
                    _META_TITLE_FIELD_KEY,
                    _META_SEARCHABLE_FIELDS_KEY,
                    _META_INDEXED_FIELDS_KEY,
                    _META_INDEX_SEMANTICS_KEY,
                ),
            ).fetchall()
        stored = {row[0]: row[1] for row in rows}
        model_raw = stored.get(_META_EMBED_MODEL_KEY)
        override_raw = stored.get(_META_MAX_CHUNK_CHARS_OVERRIDE_KEY)
        model: str | None = model_raw if model_raw else None
        # An empty string (a stored ``None``) is falsy → reads back as None;
        # config rejects a 0 override so truthiness is safe here.
        override: int | None = int(override_raw) if override_raw else None
        try:
            semantics_version = int(stored.get(_META_INDEX_SEMANTICS_KEY) or 0)
        except ValueError:
            # A hand-edited or corrupted row is not a reason to serve stale
            # derived rows: read it as "older than anything" and rebuild.
            semantics_version = 0
        return ChunkingMeta(
            model=model,
            max_chunk_chars_override=override,
            title_field=stored.get(_META_TITLE_FIELD_KEY) or "title",
            searchable_fields=stored.get(_META_SEARCHABLE_FIELDS_KEY) or "",
            indexed_frontmatter_fields=stored.get(_META_INDEXED_FIELDS_KEY) or "",
            semantics_version=semantics_version,
        )

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
            query: FTS5 query string. Well-formed operator syntax (``AND`` /
                ``OR`` / ``NEAR`` / phrases / prefixes) is honoured as-is. A
                query FTS5 rejects as malformed — most commonly a
                natural-language query with a hyphenated term or slug (e.g.
                ``vault-mcp``), which parses as a column filter — is retried
                once with every token quoted as a phrase literal, so it
                degrades to an implicit-AND term search instead of returning
                ``[]`` (#866).
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

        def _execute(match: str) -> sqlite3.Cursor:
            return self._conn().execute(
                sql,
                [*snippet_params, match, *folder_params, *tag_params, limit],
            )

        logger.debug(
            "FTS search: query=%r folder=%r filters=%r limit=%d snippet_words=%r",
            query,
            folder,
            filters,
            limit,
            snippet_words,
        )
        try:
            cur = _execute(query)
        except sqlite3.OperationalError as exc:
            if not _is_fts5_query_error(exc):
                raise
            # #866: FTS5 rejected the raw query — e.g. a hyphenated slug like
            # `vault-mcp` parses as a column filter ("no such column: mcp").
            # Retry once with every token quoted so a natural-language query
            # degrades to an implicit-AND of phrase literals instead of
            # silently returning [] (which, in hybrid mode, masks the failure
            # as a semantic-only result).
            fallback = _quote_fts_query(query)
            if not fallback:
                logger.debug("FTS search: unrecoverable query %r — %s", query, exc)
                return []
            logger.debug(
                "FTS search: query %r rejected by FTS5 (%s); retrying quoted %r",
                query,
                exc,
                fallback,
            )
            try:
                cur = _execute(fallback)
            except sqlite3.OperationalError as retry_exc:
                if not _is_fts5_query_error(retry_exc):
                    raise
                # Degraded but continuing: the query could not execute even
                # after quoting every token, so the caller gets []. Log at
                # WARNING (per the logging standard) so this is visible at the
                # default level rather than masking as an empty result set.
                logger.warning(
                    "fts_search_unexecutable query=%r fallback=%r reason=%s",
                    query,
                    fallback,
                    retry_exc,
                )
                return []
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
    def count_documents(self) -> int:
        """Return the total number of indexed documents."""
        row = self._conn().execute("SELECT COUNT(*) FROM documents").fetchone()
        return int(row[0])

    @_retry_on_locked
    def count_chunks(self) -> int:
        """Return the total number of chunk rows in the ``sections`` table.

        Returns:
            Integer count of all indexed chunks across all documents.
        """
        row = self._conn().execute("SELECT COUNT(*) FROM sections").fetchone()
        return int(row[0])

    @_retry_on_locked
    def list_chunks(self) -> list[dict[str, Any]]:
        """Return every indexed chunk joined with its document metadata.

        Used to converge the vector index against the FTS chunk set
        (#665): the returned rows carry exactly the shape stored as
        vector-row metadata, so callers can diff and embed without
        re-parsing source files.

        Returns:
            List of dicts with keys ``path``, ``title``, ``folder``,
            ``heading`` (may be ``None``), ``content``, ``start_line``,
            ``frontmatter_json`` (the document's stored frontmatter), and
            ``is_first_chunk`` (``True`` on each document's first row),
            ordered by document path then chunk position.
        """
        cur = self._conn().execute(
            """
            SELECT d.path, d.title, d.folder, s.heading, s.content,
                   s.start_line, d.frontmatter_json
            FROM sections AS s
            JOIN documents AS d ON d.id = s.document_id
            ORDER BY d.path, s.id
            """
        )
        rows: list[dict[str, Any]] = []
        prev_path: str | None = None
        for raw in cur.fetchall():
            row = dict(raw)
            row["is_first_chunk"] = row["path"] != prev_path
            prev_path = row["path"]
            rows.append(row)
        return rows

    @_retry_on_locked
    def get_toc(self, path: str, *, max_level: int | None = None) -> list[TocEntry]:
        """Return headings for a document, ordered by position.

        Queries the sections table for distinct non-NULL headings, ordered by
        the first row that introduces each heading.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).
            max_level: If set, drop headings with ``level`` greater than this.

        Returns:
            List of :class:`~markdown_vault_mcp.types.TocEntry` ordered by first appearance.
            Empty list if the document is not found or has no headings.
        """
        level_clause = "" if max_level is None else "AND heading_level <= ?"
        params: list[object] = [path]
        if max_level is not None:
            params.append(max_level)
        cur = self._conn().execute(
            f"""
            SELECT heading, heading_level
            FROM sections
            WHERE document_id = (SELECT id FROM documents WHERE path = ?)
              AND heading IS NOT NULL
              {level_clause}
            GROUP BY heading, heading_level
            ORDER BY MIN(rowid)
            """,
            params,
        )
        return [
            TocEntry(heading=row["heading"], level=row["heading_level"])
            for row in cur.fetchall()
        ]

    @_retry_on_locked
    def get_subtree_toc(
        self,
        prefix: str,
        *,
        max_level: int | None = None,
        max_notes: int = 200,
    ) -> tuple[list[SubtreeNote], bool]:
        """Return per-note headings for every document under a folder prefix.

        Matches documents whose ``folder`` equals *prefix* or is a descendant
        of it (boundary match, so ``"Project"`` never matches ``"Projects"``).

        Args:
            prefix: Folder path with no trailing slash (e.g. ``"Projects"``).
            max_level: If set, drop headings with ``level`` greater than this.
            max_notes: Cap on distinct notes returned (default 200).

        Returns:
            ``(notes, truncated)`` where *notes* is a list of
            :class:`~markdown_vault_mcp.types.SubtreeNote` ordered by path,
            each with raw :class:`~markdown_vault_mcp.types.TocEntry` headings
            (no synthetic H1 — the manager layer prepends the synthetic H1
            before exposing them to consumers), and *truncated* is True when
            more than ``max_notes`` notes matched.
        """
        escaped = _escape_like(prefix)
        doc_rows = (
            self._conn()
            .execute(
                """
            SELECT id, path, title
            FROM documents
            WHERE (folder = ? OR folder LIKE ? ESCAPE '\\')
            ORDER BY path ASC
            LIMIT ?
            """,
                (prefix, escaped + "/%", max_notes + 1),
            )
            .fetchall()
        )
        truncated = len(doc_rows) > max_notes
        doc_rows = doc_rows[:max_notes]
        if not doc_rows:
            return [], truncated

        ids = [row["id"] for row in doc_rows]
        placeholders = ",".join("?" * len(ids))
        level_clause = "" if max_level is None else "AND heading_level <= ?"
        params: list[object] = [*ids]
        if max_level is not None:
            params.append(max_level)
        section_rows = (
            self._conn()
            .execute(
                f"""
            SELECT document_id, heading, heading_level
            FROM sections
            WHERE document_id IN ({placeholders})
              AND heading IS NOT NULL
              {level_clause}
            GROUP BY document_id, heading, heading_level
            ORDER BY document_id, MIN(rowid)
            """,
                params,
            )
            .fetchall()
        )

        by_doc: dict[int, list[TocEntry]] = {}
        for row in section_rows:
            by_doc.setdefault(row["document_id"], []).append(
                TocEntry(heading=row["heading"], level=row["heading_level"])
            )

        notes = [
            SubtreeNote(
                path=row["path"],
                title=row["title"],
                headings=by_doc.get(row["id"], []),
            )
            for row in doc_rows
        ]
        return notes, truncated

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

    def close(self) -> None:
        """Close every per-thread connection and mark this index closed.

        Idempotent — delegates to :meth:`SqliteConnectionRegistry.close`.
        After ``close()``, any thread calling a public method raises
        ``sqlite3.ProgrammingError`` from ``_conn()``.
        """
        self._registry.close()
        logger.debug("FTSIndex closed (db_path=%s)", self._db_path)
