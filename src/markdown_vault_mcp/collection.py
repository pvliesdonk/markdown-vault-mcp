"""Thin facade tying all markdown-vault-mcp modules together.

:class:`Collection` is the primary public API for the library.  MCP tools,
LangChain wrappers, and CLI commands all go through this class.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import logging
import mimetypes
import os.path as osp
import queue
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
)
from markdown_vault_mcp.fts_index import FTSIndex, _derive_folder
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.managers.search import SearchManager
from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
    parse_note,
    scan_directory,
)
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.types import (
    AttachmentContent,
    AttachmentInfo,
    BacklinkInfo,
    BrokenLinkInfo,
    CollectionStats,
    CommitDiff,
    DeleteResult,
    EditResult,
    HistoryEntry,
    IndexStats,
    MostLinkedNote,
    NoteContent,
    NoteContext,
    NoteInfo,
    OutlinkInfo,
    ParsedNote,
    ReindexResult,
    RenameResult,
    SearchResult,
    WriteCallback,
    WriteResult,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

_DEFAULT_STATE_SUBDIR = ".markdown_vault_mcp"
_DEFAULT_STATE_FILENAME = "state.json"

# ---------------------------------------------------------------------------
# Link-update helpers (used by Collection.rename update_links logic)
# ---------------------------------------------------------------------------


def _compute_new_raw_target(
    link_type: str,
    raw_target: str,
    fragment: str | None,
    new_path: str,
    source_path: str = "",
    old_path: str = "",
) -> str:
    """Compute the replacement raw_target string when a file is renamed.

    Args:
        link_type: One of ``"markdown"``, ``"reference"``, ``"wikilink"``.
        raw_target: The literal link string stored in the source file.
        fragment: The heading fragment (``#heading``) of the link, if any.
        new_path: The vault-relative path of the renamed file (e.g.
            ``"notes/new-name.md"``).
        source_path: Vault-relative path of the file that contains the link.
            Required for correct relative-path handling in markdown and
            reference links (cross-directory links would otherwise be silently
            broken).
        old_path: Vault-relative path of the file being renamed.  Used to
            detect whether *raw_target* was written as a vault-root-relative
            or source-directory-relative path.

    Returns:
        The replacement raw_target string to write into the source file.
    """
    if link_type == "wikilink":
        # Determine whether the original wikilink included the .md extension.
        old_path_part = raw_target.split("#")[0]
        if old_path_part.lower().endswith(".md"):
            new_path_part = new_path
        else:
            new_path_part = new_path[:-3]
        return new_path_part + ("#" + fragment if fragment else "")
    else:
        # markdown and reference links.
        # Detect whether the link was written as vault-root-relative (raw_target
        # matches old_path) or as a path relative to the source file's directory
        # (raw_target != old_path, e.g. "../archive/target.md" from docs/).
        raw_path_part = raw_target.split("#")[0]
        if source_path and old_path and raw_path_part != old_path:
            # Relative-to-source link: compute the correct new relative path so
            # cross-directory links continue to resolve after the rename.
            source_dir = str(Path(source_path).parent)
            new_rel = osp.relpath(new_path, source_dir)
            # os.path.relpath uses OS separators on Windows; normalise to /.
            new_path_part = new_rel.replace("\\", "/")
        else:
            new_path_part = new_path
        return new_path_part + ("#" + fragment if fragment else "")


def _apply_link_replacement(
    content: str, link_type: str, old_raw: str, new_raw: str
) -> str:
    """Replace a single link target occurrence in file content.

    Args:
        content: Full file content to modify.
        link_type: One of ``"markdown"``, ``"reference"``, ``"wikilink"``.
        old_raw: The original raw_target string to find.
        new_raw: The replacement raw_target string.

    Returns:
        Updated content with all occurrences of *old_raw* replaced.
    """
    if link_type == "markdown":
        # Negative lookbehind (?<!!) excludes image links ![](url) — the `!`
        # immediately before `[` is the discriminator. Anchored to [text]( so
        # bare (old_raw) occurrences in plain text are also excluded.
        # Captures and preserves optional link title (e.g. "title" or 'title').
        # NOTE: operates on raw file content; occurrences inside backtick code
        # spans would also be rewritten. Risk is low in practice.
        return re.sub(
            r"(?<!!)(\[[^\]]*?\])\(" + re.escape(old_raw) + r"((?:\s[^)]*)?)\)",
            lambda m: m.group(1) + "(" + new_raw + m.group(2) + ")",
            content,
        )
    elif link_type == "reference":
        # Match reference definition lines: [id]: url optional-title
        # Anchored to line start with MULTILINE so we don't match inline text.
        return re.sub(
            r"^(\[.*?\]:\s+)" + re.escape(old_raw) + r"([ \t].*|$)",
            lambda m: m.group(1) + new_raw + m.group(2),
            content,
            flags=re.MULTILINE,
        )
    elif link_type == "wikilink":
        return re.sub(
            r"\[\[" + re.escape(old_raw) + r"(\|[^\]]*)?\]\]",
            lambda m: "[[" + new_raw + (m.group(1) or "") + "]]",
            content,
        )
    return content


# Maximum chunks per embedding provider call.  Keeps memory bounded during
# build_embeddings() — FastEmbed/ONNX can allocate pathologically large buffers
# when the entire corpus is sent in one batch (see issue #159).
_EMBEDDING_BATCH_SIZE = 4

# Seconds between automatic background flushes of dirty embeddings to disk.
# Write operations mark documents as dirty; the flush re-embeds them in bulk.
_EMBEDDING_FLUSH_INTERVAL = 30

# Default set of allowed attachment extensions (without leading dot, lower-case).
# .md is always excluded — it is always handled as a markdown note.
_DEFAULT_ATTACHMENT_EXTENSIONS: frozenset[str] = frozenset(
    [
        # Documents
        "pdf",
        "docx",
        "xlsx",
        "pptx",
        "odt",
        "ods",
        "odp",
        # Images
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "svg",
        "bmp",
        "tiff",
        # Archives
        "zip",
        "tar",
        "gz",
        # Audio / Video
        "mp3",
        "mp4",
        "wav",
        "ogg",
        # Text and data
        "txt",
        "csv",
        "tsv",
        "json",
        "yaml",
        "toml",
        "xml",
        "html",
        "css",
        "js",
        "ts",
    ]
)


def _resolve_chunk_strategy(strategy: str | ChunkStrategy) -> ChunkStrategy:
    """Return a concrete ChunkStrategy from a string name or pass-through.

    Args:
        strategy: Either ``"heading"``, ``"whole"``, or a :class:`ChunkStrategy`
            instance.

    Returns:
        A concrete :class:`ChunkStrategy` instance.

    Raises:
        ValueError: If *strategy* is an unrecognised string name.
    """
    if isinstance(strategy, str):
        if strategy == "heading":
            return HeadingChunker()
        if strategy == "whole":
            return WholeDocumentChunker()
        raise ValueError(
            f"Unknown chunk_strategy {strategy!r}. "
            "Valid string values: 'heading', 'whole'."
        )
    return strategy


def _fts_row_to_note_info(row: dict) -> NoteInfo:
    """Convert an FTSIndex list_notes() row dict to a :class:`NoteInfo`.

    Args:
        row: Dict returned by :meth:`FTSIndex.list_notes` or
            :meth:`FTSIndex.get_note`.

    Returns:
        A populated :class:`NoteInfo` instance.
    """
    frontmatter: dict = {}
    raw_json = row.get("frontmatter_json")
    if raw_json:
        try:
            frontmatter = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Could not parse frontmatter_json for path %s", row.get("path")
            )
    return NoteInfo(
        path=row["path"],
        title=row["title"],
        folder=row["folder"],
        frontmatter=frontmatter,
        modified_at=row["modified_at"],
    )


class Collection:
    """Facade over FTS5 index, vector index, and change tracker.

    Instantiate once per collection root.  Call :meth:`build_index` (or let
    lazy initialisation handle it) before querying.

    Args:
        source_dir: Root directory of the markdown collection.
        index_path: Path to the SQLite index file.  ``None`` (default) uses
            an in-memory database that is discarded when the object is
            collected.
        embeddings_path: Base path for the ``{path}.npy`` and
            ``{path}.json`` sidecar files.  ``None`` (default) means
            semantic search is disabled.
        embedding_provider: Provider used to generate embeddings.  Required
            when *embeddings_path* is set.
        read_only: When ``True`` (default), write operations raise
            :exc:`~markdown_vault_mcp.exceptions.ReadOnlyError`.
        state_path: Path to the hash-state JSON file used by
            :class:`~markdown_vault_mcp.tracker.ChangeTracker`.  Defaults to
            ``{source_dir}/.markdown_vault_mcp/state.json``.
        indexed_frontmatter_fields: Frontmatter keys whose values are
            promoted to the ``document_tags`` table for structured filtering.
        required_frontmatter: If provided, documents missing any listed field
            are excluded from the index entirely.
        chunk_strategy: ``"heading"`` (default), ``"whole"``, or a custom
            :class:`~markdown_vault_mcp.scanner.ChunkStrategy` instance.
        on_write: Optional callback invoked after every successful write
            operation.  Signature:
            ``Callable[[Path, str, Literal["write","edit","delete","rename"]], None]``.
        git_strategy: Optional git strategy used for background git tasks (e.g.
            periodic fetch + ff-only updates). Started via :meth:`start`.
        git_pull_interval_s: Interval in seconds for periodic pulls. ``0``
            disables the pull loop.
    """

    def __init__(
        self,
        *,
        source_dir: Path,
        index_path: Path | None = None,
        embeddings_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        read_only: bool = True,
        state_path: Path | None = None,
        indexed_frontmatter_fields: list[str] | None = None,
        required_frontmatter: list[str] | None = None,
        chunk_strategy: str | ChunkStrategy = "heading",
        on_write: WriteCallback | None = None,
        git_strategy: GitWriteStrategy | None = None,
        git_pull_interval_s: int = 0,
        exclude_patterns: list[str] | None = None,
        attachment_extensions: list[str] | None = None,
        max_attachment_size_mb: float = 10.0,
    ) -> None:
        self._source_dir = source_dir
        self._index_path = index_path
        self._embeddings_path = embeddings_path
        self._embedding_provider = embedding_provider
        self._read_only = read_only
        self._indexed_frontmatter_fields: list[str] = indexed_frontmatter_fields or []
        self._required_frontmatter = required_frontmatter
        self._chunk_strategy = _resolve_chunk_strategy(chunk_strategy)
        self._on_write = on_write
        self._git_strategy = git_strategy
        self._git_pull_interval_s = git_pull_interval_s
        self._exclude_patterns = exclude_patterns
        self._attachment_extensions = attachment_extensions
        self._max_attachment_size_mb = max_attachment_size_mb

        # Default state path: {source_dir}/.markdown_vault_mcp/state.json
        if state_path is None:
            self._state_path = (
                source_dir / _DEFAULT_STATE_SUBDIR / _DEFAULT_STATE_FILENAME
            )
        else:
            self._state_path = state_path

        # Sub-module construction.
        db_path: Path | str = index_path if index_path is not None else ":memory:"
        self._fts = FTSIndex(
            db_path=db_path,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields or None,
        )
        self._tracker = ChangeTracker(self._state_path)
        self._docs = DocumentManager(self)
        self._search = SearchManager(self)

        # Vector index is loaded lazily (only if embeddings_path is set).
        self._vectors: VectorIndex | None = None

        # Lazy initialisation flag.
        self._initialized = False

        # Serialise concurrent write operations on this instance.
        # Re-entrant: periodic pull tick blocks writes, then reindex() acquires
        # this lock again for its mutation phase.
        self._write_lock = threading.RLock()

        # Deferred embedding updates (issue #175).  Write operations add
        # document paths here instead of re-embedding inline.  A background
        # timer flushes the set periodically; semantic_search() and close()
        # flush synchronously.
        self._dirty_embeddings: set[str] = set()
        self._embedding_flush_timer: threading.Timer | None = None
        self._embedding_flush_lock = threading.Lock()

        # Deferred write callback queue (issue #175).  Git commit (on_write
        # callback) runs in a background worker thread so write methods
        # return immediately after the FTS update.
        self._callback_queue: queue.Queue[tuple[Path, str, str] | None] = queue.Queue()
        self._callback_worker: threading.Thread | None = None
        self._callback_worker_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def pause_writes(self) -> Iterator[None]:
        """Block all write operations until the context exits.

        Write operations are queued (blocked on the lock) rather than being
        rejected. Reads and search remain unblocked at the Python level.
        """
        with self._write_lock:
            yield

    def sync_from_remote_before_index(self) -> None:
        """One-time git fetch + ff-only update before build_index().

        Intended to run during server startup before the initial index build.
        No reindex is triggered here because build_index() will scan the updated
        working tree.
        """
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.sync_once(self._source_dir)

    def start(self) -> None:
        """Start background tasks for this Collection (e.g. git pull loop)."""
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.start(
            repo_path=self._source_dir,
            pull_interval_s=self._git_pull_interval_s,
            pause_writes=self.pause_writes,
            on_pull=self.reindex,
        )

    def stop(self) -> None:
        """Stop background tasks (e.g. git pull loop) without closing the collection.

        Safe to call multiple times.  A no-op if no pull loop was started.
        The SQLite connection and write callback remain open; only the pull
        loop thread is signalled to stop.
        """
        if self._git_strategy is not None:
            self._git_strategy.stop()

    def close(self) -> None:
        """Release resources held by the collection.

        Flushes deferred embeddings and pending write callbacks, then
        closes the SQLite connection and git strategy.
        """
        # 1. Flush any deferred embedding updates.
        self._flush_dirty_embeddings()

        # 2. Drain the write-callback queue (git commits).
        if self._callback_worker is not None and self._callback_worker.is_alive():
            self._callback_queue.put(None)  # sentinel
            self._callback_worker.join(timeout=30)
            if self._callback_worker.is_alive():
                logger.warning(
                    "Write-callback worker did not finish within 30 s; "
                    "pending git commits may be lost."
                )

        # 3. Close git strategy (flush push, etc.).
        if self._git_strategy is not None:
            self._git_strategy.close()
        if (
            self._on_write is not None
            and self._on_write is not self._git_strategy
            and hasattr(self._on_write, "close")
        ):
            self._on_write.close()  # type: ignore[union-attr]

        # 4. Close SQLite.
        self._fts.close()

    # ------------------------------------------------------------------
    # Lazy initialisation
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Build the FTS index on first access if it has not been built yet."""
        if not self._initialized:
            self.build_index()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
        filters: dict[str, str] | None = None,
        folder: str | None = None,
    ) -> list[SearchResult]:
        """Search the collection.

        Args:
            query: Search string.
            limit: Maximum number of results to return.
            mode: ``"keyword"`` for BM25 FTS5, ``"semantic"`` for cosine
                similarity, or ``"hybrid"`` for Reciprocal Rank Fusion of both.
            filters: Dict of ``{frontmatter_key: value}`` pairs (AND semantics).
                Only works for fields in ``indexed_frontmatter_fields``.
            folder: If provided, restrict results to documents in this folder
                (and its sub-folders).

        Returns:
            List of :class:`~markdown_vault_mcp.types.SearchResult` ordered by
            relevance.

        Raises:
            ValueError: If *mode* is ``"semantic"`` or ``"hybrid"`` but no
                embedding provider or embeddings path is configured.
        """
        return self._search.search(
            query, limit=limit, mode=mode, filters=filters, folder=folder
        )

    def _require_vectors(self) -> None:
        """Raise ValueError if semantic search is not configured."""
        if self._embedding_provider is None or self._embeddings_path is None:
            raise ValueError(
                "Semantic search requires both 'embedding_provider' and "
                "'embeddings_path' to be configured."
            )

    def _load_vectors(self) -> VectorIndex:
        """Load or return the cached VectorIndex.

        Returns:
            A :class:`~markdown_vault_mcp.vector_index.VectorIndex` instance.
        """
        if self._vectors is not None:
            return self._vectors

        from markdown_vault_mcp.vector_index import (
            VectorIndex,
            VectorIndexCompatibilityError,
        )

        assert self._embeddings_path is not None
        assert self._embedding_provider is not None

        npy_path = Path(str(self._embeddings_path) + ".npy")
        if npy_path.exists():
            try:
                self._vectors = VectorIndex.load(
                    self._embeddings_path, self._embedding_provider
                )
                logger.info("Loaded vector index from %s", self._embeddings_path)
            except VectorIndexCompatibilityError as exc:
                logger.warning("%s Rebuilding embeddings.", exc)
                self.build_embeddings(force=True)
                assert self._vectors is not None
        else:
            self._vectors = VectorIndex(self._embedding_provider)
            logger.info("No vector index on disk; created empty VectorIndex")

        return self._vectors

    def _get_frontmatter(self, path: str) -> dict[str, Any]:
        """Return the frontmatter dict for a document from the FTS index."""
        return self._search._get_frontmatter(path)

    # ------------------------------------------------------------------
    # Read / list
    # ------------------------------------------------------------------

    def read(self, path: str) -> NoteContent | None:
        """Read the full content of a document from disk.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent` instance, or ``None``
            if the file does not exist.
        """
        return self._docs.read(path)

    def list(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
    ) -> list[NoteInfo | AttachmentInfo]:
        """List documents (and optionally attachments) in the collection.

        Args:
            folder: If provided, only return documents in this folder (and
                sub-folders).
            pattern: Unix glob matched against the relative path using
                :func:`fnmatch.fnmatch`.  Example: ``"Journal/*.md"``.
            include_attachments: When ``True``, also return non-.md files
                that match the attachment allowlist.  Each
                :class:`~markdown_vault_mcp.types.AttachmentInfo` entry
                includes ``kind="attachment"`` and ``mime_type``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` (and
            optionally :class:`~markdown_vault_mcp.types.AttachmentInfo`)
            objects.
        """
        self._ensure_initialized()

        rows = self._fts.list_notes(folder=folder)
        notes: list[NoteInfo | AttachmentInfo] = [
            _fts_row_to_note_info(row) for row in rows
        ]

        if pattern:
            notes = [n for n in notes if fnmatch.fnmatch(n.path, pattern)]

        if not include_attachments:
            return notes

        exts = self._effective_attachment_extensions()
        source_resolved = self._source_dir.resolve()
        attachments: list[AttachmentInfo] = []

        # Attachment scan runs outside _write_lock — result is a best-effort
        # snapshot and is not atomic with the FTS note listing above.
        for abs_path in self._source_dir.rglob("*"):
            if not abs_path.is_file():
                continue
            if abs_path.suffix.lower() == ".md":
                continue
            suffix = abs_path.suffix.lstrip(".").lower()
            if "*" not in exts and suffix not in exts:
                continue
            try:
                rel = abs_path.relative_to(source_resolved)
            except ValueError as exc:
                logger.warning(
                    "_list_attachments: skipping %s — outside source_dir (%s)",
                    abs_path,
                    exc,
                )
                continue
            rel_path = str(rel)
            # Skip files where any path component (including the filename itself) starts with ".".
            if any(part.startswith(".") for part in rel.parts):
                continue
            # Apply exclude_patterns — mirrors scan_directory behaviour.
            if self._is_path_excluded(rel.as_posix()):
                continue
            if pattern and not fnmatch.fnmatch(rel_path, pattern):
                continue
            rel_folder = str(Path(rel_path).parent)
            if rel_folder == ".":
                rel_folder = ""
            if (
                folder is not None
                and rel_folder != folder
                and not rel_folder.startswith(folder + "/")
            ):
                continue
            try:
                stat = abs_path.stat()
            except OSError as exc:
                logger.warning(
                    "_list_attachments: skipping %s — stat error (%s)", abs_path, exc
                )
                continue
            mime_type, _ = mimetypes.guess_type(rel_path)
            attachments.append(
                AttachmentInfo(
                    path=rel_path,
                    folder=rel_folder,
                    mime_type=mime_type,
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                )
            )

        return notes + attachments

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index.

        If the index already contains documents and *force* is ``False``,
        this is a no-op.  ``force=True`` drops all existing data and rebuilds
        from scratch.

        Args:
            force: When ``True``, drop and rebuild the index unconditionally.

        Returns:
            :class:`~markdown_vault_mcp.types.IndexStats` describing what was indexed.
        """
        # Check if index already has data and we are not forcing.
        if not force and self._initialized:
            existing = self._fts.list_notes()
            if existing:
                logger.debug(
                    "build_index: index already populated (%d docs), skipping",
                    len(existing),
                )
                return IndexStats(
                    documents_indexed=len(existing),
                    chunks_indexed=0,
                    skipped=0,
                )

        if force:
            # Drop all data by rebuilding from an empty scan then re-populate.
            logger.info("build_index(force=True): dropping and rebuilding index")
            # Delete all existing documents.
            for row in self._fts.list_notes():
                self._fts.delete_by_path(row["path"])

        logger.info("build_index: scanning %s", self._source_dir)

        notes = list(
            scan_directory(
                self._source_dir,
                required_frontmatter=self._required_frontmatter,
                chunk_strategy=self._chunk_strategy,
                exclude_patterns=self._exclude_patterns,
            )
        )

        total_chunks = 0
        errored = 0
        for note in notes:
            try:
                total_chunks += self._fts.upsert_note(note)
            except Exception:
                errored += 1
                logger.warning(
                    "build_index: failed to index %s", note.path, exc_info=True
                )

        # Purge stale excluded docs from a persistent index that was built
        # before exclude_patterns were configured (upgrade scenario, #255).
        indexed_paths = {note.path for note in notes}
        if self._exclude_patterns:
            # Load persisted vectors so stale entries are purged from the
            # .npy sidecar too (build_embeddings skips if count > 0).
            if (
                self._vectors is None
                and self._embedding_provider is not None
                and self._embeddings_path is not None
            ):
                self._load_vectors()

            purged = 0
            for row in self._fts.list_notes():
                if row["path"] not in indexed_paths and self._is_path_excluded(
                    row["path"]
                ):
                    self._fts.delete_by_path(row["path"])
                    if self._vectors is not None:
                        self._vectors.delete_by_path(row["path"])
                    purged += 1

            if (
                purged
                and self._vectors is not None
                and self._embeddings_path is not None
            ):
                self._vectors.save(self._embeddings_path)

        # Count how many files were skipped due to required_frontmatter.
        # scan_directory logs skipped counts itself; we compute it by comparing
        # indexed count to total files on disk.
        all_files = list(self._source_dir.glob("**/*.md"))
        skipped = len(all_files) - len(notes)

        # Resolve vault-wide wikilinks now that all documents are indexed.
        self._fts.resolve_vault_wikilinks()

        # Update tracker state so reindex() knows the baseline.
        self._tracker.update_state(notes)

        self._initialized = True
        if errored:
            logger.warning(
                "build_index: indexed %d documents, %d chunks (%d skipped, %d errors)",
                len(notes) - errored,
                total_chunks,
                skipped,
                errored,
            )
        else:
            logger.info(
                "build_index: indexed %d documents, %d chunks (%d skipped)",
                len(notes),
                total_chunks,
                skipped,
            )
        return IndexStats(
            documents_indexed=len(notes) - errored,
            chunks_indexed=total_chunks,
            skipped=max(skipped, 0),
        )

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Uses :class:`~markdown_vault_mcp.tracker.ChangeTracker` to detect which
        files have been added, modified, or deleted since the last scan.
        Only changed files are re-parsed and re-indexed.  Files matching
        ``exclude_patterns`` are skipped, and any previously indexed documents
        that now match the patterns are purged from the FTS and vector indexes.

        Thread-safety: the filesystem scan runs without holding ``_write_lock``
        (read-only), then the mutation phase acquires the lock to prevent races
        with concurrent write/edit/delete/rename operations.

        Returns:
            :class:`~markdown_vault_mcp.types.ReindexResult` with counts of changes
            applied.
        """
        self._ensure_initialized()

        # Phase 1: scan (outside lock — read-only filesystem walk + hashing).
        changes = self._tracker.detect_changes(self._source_dir)
        logger.info(
            "reindex: %d added, %d modified, %d deleted, %d unchanged",
            len(changes.added),
            len(changes.modified),
            len(changes.deleted),
            changes.unchanged,
        )

        # Pre-parse notes outside the lock to minimise lock hold time.
        # NOTE: there is an inherent TOCTOU window between detecting a change
        # in Phase 1 (hash comparison) and re-reading the file for indexing
        # here.  If the file is modified again in that window, the newly
        # written content is indexed rather than the version that triggered
        # the change.  This is acceptable — the next reindex() call will
        # reconcile the difference.
        parsed: list[tuple[str, ParsedNote]] = []
        for path in changes.added + changes.modified:
            # Apply exclude_patterns — mirrors scan_directory behaviour.
            if self._is_path_excluded(path):
                logger.debug("reindex: excluding %s (matched exclude pattern)", path)
                continue

            abs_path = self._source_dir / path
            try:
                note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
            except (UnicodeDecodeError, OSError) as exc:
                logger.warning("reindex: skipping %s — %s", path, exc)
                continue
            except Exception as exc:
                logger.warning(
                    "reindex: skipping %s — parse error (%s)",
                    path,
                    exc,
                    exc_info=True,
                )
                continue

            # Apply required_frontmatter filter.
            if self._required_frontmatter:
                missing = [
                    f for f in self._required_frontmatter if f not in note.frontmatter
                ]
                if missing:
                    logger.info(
                        "reindex: skipping %s — missing frontmatter: %s", path, missing
                    )
                    continue

            parsed.append((path, note))

        # Phase 2: apply mutations (inside lock — prevents races with writes).
        with self._write_lock:
            # Delete removed documents.
            for path in changes.deleted:
                self._fts.delete_by_path(path)
                if self._vectors is not None:
                    self._vectors.delete_by_path(path)

            # Purge stale excluded docs that were indexed before
            # exclude_patterns were enforced in reindex() (issue #255).
            stale_excluded = 0
            if self._exclude_patterns:
                # Load persisted vectors so stale entries are purged from
                # the .npy sidecar too (not just FTS).
                if (
                    self._vectors is None
                    and self._embedding_provider is not None
                    and self._embeddings_path is not None
                ):
                    self._load_vectors()

                for row in self._fts.list_notes():
                    if self._is_path_excluded(row["path"]):
                        self._fts.delete_by_path(row["path"])
                        if self._vectors is not None:
                            self._vectors.delete_by_path(row["path"])
                        stale_excluded += 1
                if stale_excluded:
                    logger.info(
                        "reindex: purged %d stale excluded document(s)",
                        stale_excluded,
                    )

            # Upsert parsed notes.
            indexed_added = 0
            indexed_modified = 0
            added_set = set(changes.added)

            for path, note in parsed:
                try:
                    self._fts.upsert_note(note)
                except Exception:
                    logger.warning("reindex: failed to index %s", path, exc_info=True)
                    continue
                if path in added_set:
                    indexed_added += 1
                else:
                    indexed_modified += 1

                # Update vector index for changed notes if loaded.
                if self._vectors is not None and self._embeddings_path is not None:
                    self._vectors.delete_by_path(note.path)
                    texts = [c.content for c in note.chunks]
                    meta = [
                        {
                            "path": note.path,
                            "title": note.title,
                            "folder": _derive_folder(note.path),
                            "heading": c.heading,
                            "content": c.content,
                        }
                        for c in note.chunks
                    ]
                    if texts:
                        self._vectors.add(texts, meta)

            # Persist updated vector index.
            if self._vectors is not None and self._embeddings_path is not None:
                self._vectors.save(self._embeddings_path)

            # Re-resolve vault-wide wikilinks: adding/removing documents may
            # fix previously broken links or expose new ones.
            self._fts.resolve_vault_wikilinks()

            # Update tracker state: rebuild from current FTS index contents.
            state_notes: list[ParsedNote] = [
                ParsedNote(
                    path=r["path"],
                    frontmatter={},
                    title=r["title"],
                    chunks=[],
                    content_hash=r["content_hash"],
                    modified_at=r["modified_at"],
                )
                for r in self._fts.list_notes()
            ]
            self._tracker.update_state(state_notes)

        return ReindexResult(
            added=indexed_added,
            modified=indexed_modified,
            deleted=len(changes.deleted),
            unchanged=changes.unchanged,
        )

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk.

        Returns:
            Total number of chunks embedded.

        Raises:
            ValueError: If ``embedding_provider`` or ``embeddings_path`` is
                not configured.
        """
        self._ensure_initialized()
        self._require_vectors()

        assert self._embeddings_path is not None
        assert self._embedding_provider is not None

        from markdown_vault_mcp.vector_index import VectorIndex

        if force:
            self._vectors = VectorIndex(self._embedding_provider)
        else:
            # Load persisted vectors (or create empty) so we can check count.
            self._load_vectors()
            if self._vectors.count > 0:
                logger.info(
                    "build_embeddings: index already exists (%d chunks), skipping",
                    self._vectors.count,
                )
                return self._vectors.count
            # Empty index — fall through to build from scratch.

        rows = self._fts.list_notes()
        num_notes = len(rows)
        logger.info("build_embeddings: parsing %d notes into chunks", num_notes)
        texts: list[str] = []
        meta: list[dict] = []

        for i, row in enumerate(rows, 1):
            path = row["path"]
            title = row["title"]
            folder = row["folder"]
            # Re-parse to get chunks with content.
            abs_path = self._source_dir / path
            try:
                note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
            except (UnicodeDecodeError, OSError) as exc:
                logger.warning("build_embeddings: skipping %s — %s", path, exc)
                continue
            for chunk in note.chunks:
                texts.append(chunk.content)
                meta.append(
                    {
                        "path": path,
                        "title": title,
                        "folder": folder,
                        "heading": chunk.heading,
                        "content": chunk.content,
                    }
                )
            if i % 100 == 0 or i == num_notes:
                logger.info(
                    "build_embeddings: parsed %d/%d notes (%d chunks so far)",
                    i,
                    num_notes,
                    len(texts),
                )

        # Embed in bounded batches to avoid pathological memory allocation
        # (see issue #159 -- FastEmbed/ONNX can request >200 GB for a single
        # oversized batch).  Save once at the end so a mid-run crash does not
        # leave a partial index that the skip-if-exists check treats as complete.
        total = len(texts)
        for start in range(0, total, _EMBEDDING_BATCH_SIZE):
            end = min(start + _EMBEDDING_BATCH_SIZE, total)
            self._vectors.add(texts[start:end], meta[start:end])
            logger.info(
                "build_embeddings: embedded chunks %d-%d of %d",
                start + 1,
                end,
                total,
            )

        if total > 0:
            self._vectors.save(self._embeddings_path)
            logger.info("build_embeddings: embedded and saved %d chunks", total)
        else:
            logger.info("build_embeddings: nothing to embed")
        return total

    def embeddings_status(self) -> dict:
        """Return status information about the vector index.

        Returns:
            Dict with keys ``provider``, ``chunk_count``, ``path``,
            ``available``.
        """
        if self._embedding_provider is None or self._embeddings_path is None:
            return {
                "available": False,
                "provider": None,
                "chunk_count": 0,
                "path": None,
            }

        count = 0
        if self._vectors is not None:
            count = self._vectors.count
        else:
            npy_path = Path(str(self._embeddings_path) + ".npy")
            if npy_path.exists():
                # Peek at metadata file for count without loading full matrix.
                json_path = Path(str(self._embeddings_path) + ".json")
                if json_path.exists():
                    try:
                        with json_path.open(encoding="utf-8") as fh:
                            loaded_meta = json.load(fh)
                        if isinstance(loaded_meta, list):
                            count = len(loaded_meta)
                        else:
                            count = len(loaded_meta.get("rows", []))
                    except (OSError, json.JSONDecodeError) as exc:
                        logger.warning(
                            "embeddings_status: could not read metadata from %s — %s",
                            json_path,
                            exc,
                        )

        return {
            "available": True,
            "provider": type(self._embedding_provider).__name__,
            "chunk_count": count,
            "path": str(self._embeddings_path),
        }

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed collection.

        Returns:
            Sorted list of folder strings (``""`` for the collection root).
        """
        self._ensure_initialized()
        return self._fts.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field.

        If *field* was not in ``indexed_frontmatter_fields``, returns ``[]``.

        Args:
            field: Frontmatter key to query (default: ``"tags"``).

        Returns:
            Sorted list of distinct value strings.
        """
        self._ensure_initialized()
        return self._fts.list_field_values(field)

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document.

        Queries the FTS sections table for headings and prepends the document
        title as a synthetic H1 entry.

        Args:
            path: Relative path to the document (e.g. ``"notes/intro.md"``).

        Returns:
            List of ``{"heading": str, "level": int}`` dicts ordered by
            position, with the document title prepended as level 1.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._ensure_initialized()
        self._validate_path(path)

        row = self._fts.get_note(path)
        if row is None:
            raise ValueError(f"Document not found: {path}")

        title: str = row["title"]
        headings = self._fts.get_toc(path)

        # Prepend a synthetic H1 for the document title, filtering out any
        # real H1 that duplicates it (common when docs start with ``# Title``).
        toc: list[dict[str, Any]] = [{"heading": title, "level": 1}]
        toc.extend(
            h for h in headings if not (h["level"] == 1 and h["heading"] == title)
        )
        return toc

    def get_backlinks(self, path: str) -> list[BacklinkInfo]:
        """Return all documents that link to the given document.

        Args:
            path: Relative path of the target document
                (e.g. ``"notes/topic.md"``).

        Returns:
            List of :class:`~markdown_vault_mcp.types.BacklinkInfo` objects
            for each document that contains a link pointing to ``path``.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._ensure_initialized()
        self._validate_path(path)
        if self._fts.get_note(path) is None:
            raise ValueError(f"Document not found: {path}")
        rows = self._fts.get_backlinks(path)
        return [
            BacklinkInfo(
                source_path=row["source_path"],
                source_title=row["source_title"],
                link_text=row["link_text"],
                link_type=row["link_type"],
                fragment=row["fragment"],
                raw_target=row["raw_target"],
            )
            for row in rows
        ]

    def get_outlinks(self, path: str) -> list[OutlinkInfo]:
        """Return all links from the given document to other documents.

        The ``exists`` field on each :class:`~markdown_vault_mcp.types.OutlinkInfo`
        indicates whether the target document is currently indexed.

        Args:
            path: Relative path of the source document
                (e.g. ``"notes/topic.md"``).

        Returns:
            List of :class:`~markdown_vault_mcp.types.OutlinkInfo` objects for
            each link originating from ``path``.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._ensure_initialized()
        self._validate_path(path)
        if self._fts.get_note(path) is None:
            raise ValueError(f"Document not found: {path}")
        rows = self._fts.get_outlinks(path)
        return [
            OutlinkInfo(
                target_path=row["target_path"],
                link_text=row["link_text"],
                link_type=row["link_type"],
                fragment=row["fragment"],
                exists=bool(row["target_exists"]),
                raw_target=row["raw_target"],
            )
            for row in rows
        ]

    def get_broken_links(self, *, folder: str | None = None) -> list[BrokenLinkInfo]:
        """Return all links whose target does not exist in the collection.

        Args:
            folder: If provided, restrict to source documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.BrokenLinkInfo` objects.
        """
        self._ensure_initialized()
        rows = self._fts.get_broken_links(folder=folder)
        return [
            BrokenLinkInfo(
                source_path=row["source_path"],
                source_title=row["source_title"],
                target_path=row["target_path"],
                link_text=row["link_text"],
                link_type=row["link_type"],
                fragment=row["fragment"],
                raw_target=row["raw_target"],
            )
            for row in rows
        ]

    def get_similar(self, path: str, *, limit: int = 10) -> list[SearchResult]:
        """Return the most semantically similar chunks from other documents.

        Args:
            path: Relative path of the reference document.
            limit: Maximum number of results to return.

        Returns:
            List of SearchResult objects.
        """
        return self._search.get_similar(path, limit=limit)

    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> list[NoteInfo]:
        """Return the most recently modified documents.

        Args:
            limit: Maximum number of documents to return.
            folder: If provided, restrict to documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` objects
            ordered by modification time (most recent first).
        """
        self._ensure_initialized()
        rows = self._fts.get_recent(limit=limit, folder=folder)
        return [_fts_row_to_note_info(row) for row in rows]

    def get_context(
        self,
        path: str,
        *,
        similar_limit: int = 5,
        link_limit: int = 10,
    ) -> NoteContext:
        """Return a consolidated context dossier for a document.

        Args:
            path: Relative path of the document.
            similar_limit: Maximum number of similar notes to include.
            link_limit: Maximum number of backlinks and outlinks to include.

        Returns:
            A NoteContext object.
        """
        return self._search.get_context(
            path, similar_limit=similar_limit, link_limit=link_limit
        )

    def get_orphan_notes(self) -> list[NoteInfo]:
        """Return all documents with no inbound or outbound links.

        A document is an orphan if it has zero outlinks and is not referenced
        by any other document's links.

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` objects,
            ordered by path.
        """
        self._ensure_initialized()
        rows = self._fts.get_orphan_notes()
        return [_fts_row_to_note_info(r) for r in rows]

    def get_most_linked(self, *, limit: int = 10) -> list[MostLinkedNote]:
        """Return the documents with the most inbound links.

        Args:
            limit: Maximum number of results to return. Default 10.

        Returns:
            List of :class:`~markdown_vault_mcp.types.MostLinkedNote` ordered
            by backlink_count descending.
        """
        self._ensure_initialized()
        return [MostLinkedNote(**row) for row in self._fts.get_most_linked(limit=limit)]

    def get_connection_path(
        self, source: str, target: str, max_depth: int = 10
    ) -> list[str] | None:
        """Return the shortest undirected path between two notes.

        Treats the link graph as undirected — a link in either direction
        counts as a connection.  Uses BFS with a configurable depth cap.

        Args:
            source: Vault-relative path of the starting note.
            target: Vault-relative path of the destination note.
            max_depth: Maximum path length in edges.  Clamped to ``[1, 10]``.
                Defaults to ``10``.

        Returns:
            Ordered list of vault-relative paths from *source* to *target*
            (inclusive), or ``None`` if unreachable within *max_depth* hops.

        Raises:
            ValueError: If *source* or *target* is not found in the index.
        """
        self._ensure_initialized()
        self._validate_path(source)
        self._validate_path(target)
        return self._fts.get_connection_path(source, target, max_depth=max_depth)

    # ------------------------------------------------------------------
    # Git history query methods
    # ------------------------------------------------------------------

    def get_history(
        self,
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
    ) -> list[HistoryEntry]:
        """Return commits that touched a note or the whole vault.

        When *path* is ``None``, queries the full vault history.  Returns an
        empty list for vaults whose source directory is not inside a git
        repository.

        Args:
            path: Vault-relative path of the note to filter on (e.g.
                ``"notes/alpha.md"``).  Must end with ``.md``.  ``None``
                returns vault-wide history.
            since: ISO 8601 datetime string or git date expression (e.g.
                ``"1 week ago"``).  Passed as ``--since`` to ``git log``.
                ``None`` disables the filter.
            until: ISO 8601 datetime string or git date expression, passed as
                ``--until`` to ``git log``.  ``None`` disables the filter.
                Both ``since`` and ``until`` boundaries are **inclusive**: a
                commit whose author date equals either endpoint is included
                in the result.
            limit: Maximum number of commits to return.  Clamped to
                ``[1, 100]``.  Defaults to ``20``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.HistoryEntry` ordered
            newest-first.  Empty list when the vault has no git history or
            the note has no commits in the given range.

        Raises:
            ValueError: If *path* is provided but fails path validation.
        """
        if self._git_strategy is None:
            return []
        abs_path: Path | None = None
        if path is not None:
            abs_path = self._validate_path(path)
        return self._git_strategy.get_file_history(
            self._source_dir, abs_path, since, limit, until=until
        )

    def get_diff(
        self,
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
    ) -> str | list[CommitDiff]:
        """Return the diff of a note between a reference point and HEAD.

        Exactly one of *since_sha* or *since_timestamp* must be supplied.

        Args:
            path: Vault-relative path of the note to diff.  Must end with
                ``.md``.
            since_sha: A commit SHA (full or abbreviated, at least 4 hex
                digits) to diff from.  Mutually exclusive with
                *since_timestamp*.
            since_timestamp: ISO 8601 datetime string resolved to the most
                recent commit at or before that point via ``git rev-list``
                (boundary inclusive).  Mutually exclusive with *since_sha*.
            per_commit: When ``False`` (default), return a single unified diff
                string from the reference point to HEAD.  When ``True``,
                return one :class:`~markdown_vault_mcp.types.CommitDiff` per
                intervening commit.
            limit: When *per_commit* is ``True``, cap the number of
                intervening commits returned to the *limit* most recent ones.
                Clamped to ``[1, 100]``.  ``None`` (the default) means
                unbounded (still bounded by the underlying ``since..HEAD``
                range).  Silently ignored when *per_commit* is ``False``.

        Returns:
            A unified diff string when *per_commit* is ``False``, or a list of
            :class:`~markdown_vault_mcp.types.CommitDiff` when *per_commit* is
            ``True``.  Returns an empty string / empty list when the note has
            no changes in the given range.

        Raises:
            ValueError: If exactly one of *since_sha* / *since_timestamp* is
                not supplied, *since_sha* contains invalid characters, or the
                resolved ref is not found in history.
        """
        if self._git_strategy is None:
            return [] if per_commit else ""

        if (since_sha is None) == (since_timestamp is None):
            raise ValueError(
                "Exactly one of 'since_sha' or 'since_timestamp' must be provided"
            )

        abs_path = self._validate_path(path)

        if since_sha is not None and not re.fullmatch(r"[0-9a-f]{4,40}", since_sha):
            raise ValueError(
                f"Invalid SHA {since_sha!r}: must be 4-40 lowercase hex digits"
            )

        return self._git_strategy.get_file_diff(
            self._source_dir,
            abs_path,
            ref=since_sha,
            per_commit=per_commit,
            since_timestamp=since_timestamp,
            limit=limit if per_commit else None,
        )

    def stats(self) -> CollectionStats:
        """Return collection-wide statistics.

        Returns:
            :class:`~markdown_vault_mcp.types.CollectionStats` snapshot.
        """
        self._ensure_initialized()

        rows = self._fts.list_notes()
        doc_count = len(rows)

        # Chunk count via the public FTSIndex method.
        chunk_count = self._fts.count_chunks()

        folders = self._fts.list_folders()
        folder_count = len(folders)

        semantic_available = (
            self._embedding_provider is not None and self._embeddings_path is not None
        )

        exts = self._effective_attachment_extensions()
        attachment_extensions = ["*"] if "*" in exts else sorted(exts)

        return CollectionStats(
            document_count=doc_count,
            chunk_count=chunk_count,
            folder_count=folder_count,
            semantic_search_available=semantic_available,
            indexed_frontmatter_fields=list(self._indexed_frontmatter_fields),
            attachment_extensions=attachment_extensions,
            link_count=self._fts.count_links(),
            broken_link_count=self._fts.count_broken_links(),
            orphan_count=self._fts.count_orphans(),
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def _check_writable(self) -> None:
        """Raise ReadOnlyError if the collection is configured as read-only.

        Raises:
            ReadOnlyError: If ``read_only=True``.
        """
        if self._read_only:
            raise ReadOnlyError(
                "Collection is read-only; write operations are not permitted."
            )

    def _effective_attachment_extensions(self) -> frozenset[str]:
        """Return the effective set of allowed attachment extensions.

        Returns:
            Frozenset of lower-case extension strings (without leading dot).
            The special value ``frozenset(["*"])`` means all non-.md files.
        """
        if self._attachment_extensions is None:
            return _DEFAULT_ATTACHMENT_EXTENSIONS
        return frozenset(self._attachment_extensions)

    def _is_attachment(self, path: str) -> bool:
        """Return True if *path* is an allowed non-.md attachment.

        Args:
            path: Relative path to check.

        Returns:
            ``True`` when the extension is in the allowlist and is not ``.md``.
        """
        if path.endswith(".md"):
            return False
        suffix = Path(path).suffix.lstrip(".").lower()
        exts = self._effective_attachment_extensions()
        return "*" in exts or suffix in exts

    def _is_path_excluded(self, path: str) -> bool:
        """Check whether *path* matches any configured exclude pattern.

        Args:
            path: Relative POSIX path string.

        Returns:
            ``True`` if the path matches any pattern in
            ``self._exclude_patterns``.
        """
        if not self._exclude_patterns:
            return False
        return any(fnmatch.fnmatch(path, pat) for pat in self._exclude_patterns)

    def _validate_path(self, path: str) -> Path:
        """Resolve a relative path and validate it is inside source_dir.

        Args:
            path: Relative document path.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path escapes the source directory or does
                not end with ``.md``.
        """
        if not path.endswith(".md"):
            raise ValueError(f"Path must end with '.md': {path}")
        abs_path = (self._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")
        return abs_path

    def _validate_attachment_path(self, path: str) -> Path:
        """Resolve and validate a non-.md attachment path.

        Args:
            path: Relative attachment path.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path escapes the source directory, ends with
                ``.md``, or has an extension not in the attachment allowlist.
        """
        if path.endswith(".md"):
            raise ValueError(
                f"Path ends with '.md' — use the note read/write methods instead: {path}"
            )
        exts = self._effective_attachment_extensions()
        suffix = Path(path).suffix.lstrip(".").lower()
        if "*" not in exts and suffix not in exts:
            allowed_str = ", ".join(f".{e}" for e in sorted(exts))
            raise ValueError(
                f"Extension '.{suffix}' is not in the attachment allowlist. "
                f"Allowed: {allowed_str}. "
                "Set MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS=* to allow all non-.md files."
            )
        abs_path = (self._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")
        return abs_path

    def _update_vector_index(self, note: ParsedNote) -> None:
        """Mark a document for deferred embedding update.

        The actual re-embedding and save happen during the next flush
        (periodic timer, semantic search, or close).

        Args:
            note: Parsed document to index.
        """
        if self._embeddings_path is None or self._embedding_provider is None:
            return
        with self._embedding_flush_lock:
            self._dirty_embeddings.add(note.path)
        self._schedule_embedding_flush()

    def _schedule_embedding_flush(self) -> None:
        """Schedule a deferred flush of dirty embeddings."""
        with self._embedding_flush_lock:
            if self._embedding_flush_timer is not None:
                self._embedding_flush_timer.cancel()
            self._embedding_flush_timer = threading.Timer(
                _EMBEDDING_FLUSH_INTERVAL,
                self._flush_dirty_embeddings,
            )
            self._embedding_flush_timer.daemon = True
            self._embedding_flush_timer.start()

    def _flush_dirty_embeddings(self) -> None:
        """Re-embed all dirty documents and save the vector index once.

        Called by the periodic timer, before semantic search, and on close.
        Thread-safe: the dirty-set swap is atomic under ``_embedding_flush_lock``;
        Phase 2 vector mutations are serialised by ``_write_lock``.

        Two-phase design to minimise lock hold time:

        1. **Outside** ``_write_lock``: parse each dirty document and call
           the (potentially slow) embedding provider.
        2. **Inside** ``_write_lock``: apply the fast numpy mutations
           (delete old rows, append pre-computed vectors, save).

        This prevents the embedding provider from blocking foreground
        write operations for the duration of CPU/network embedding work.
        """
        if self._embeddings_path is None or self._embedding_provider is None:
            return

        with self._embedding_flush_lock:
            # Cancel pending timer (we're flushing now).
            if self._embedding_flush_timer is not None:
                self._embedding_flush_timer.cancel()
                self._embedding_flush_timer = None

            # Atomically swap the dirty set.
            if not self._dirty_embeddings:
                return
            paths = self._dirty_embeddings.copy()
            self._dirty_embeddings.clear()

        # Phase 1: parse and embed OUTSIDE _write_lock.
        # provider.embed() can be slow (seconds on CPU) — don't hold the
        # write lock during it.  Collect (path, raw_vectors, meta) tuples
        # for paths that still exist; paths that have been deleted will
        # have raw_vectors=None so only a delete_by_path is applied.
        pre_embedded: list[tuple[str, list[list[float]] | None, list[dict] | None]] = []
        for path in paths:
            abs_path = self._source_dir / path
            if abs_path.is_file() and path.endswith(".md"):
                try:
                    note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
                    texts = [c.content for c in note.chunks]
                    meta = [
                        {
                            "path": note.path,
                            "title": note.title,
                            "folder": _derive_folder(note.path),
                            "heading": c.heading,
                            "content": c.content,
                        }
                        for c in note.chunks
                    ]
                    if texts:
                        raw_vecs = self._embedding_provider.embed(texts)
                        pre_embedded.append((path, raw_vecs, meta))
                    else:
                        pre_embedded.append((path, None, None))
                except (UnicodeDecodeError, OSError) as exc:
                    logger.warning("Deferred embedding failed for %s: %s", path, exc)
                    # Preserve original semantics: still delete stale vectors.
                    pre_embedded.append((path, None, None))
            else:
                # File deleted or non-.md — remove stale vectors only.
                pre_embedded.append((path, None, None))

        # Phase 2: mutate vector index under _write_lock.
        # All operations here are fast numpy mutations — no I/O or embedding.
        with self._write_lock:
            vectors = self._load_vectors()

            for path, raw_vecs, meta in pre_embedded:
                vectors.delete_by_path(path)
                if raw_vecs is not None and meta:
                    vectors.add_vectors(raw_vecs, meta)

            vectors.save(self._embeddings_path)
            logger.debug("Flushed deferred embeddings for %d paths", len(paths))

    def _ensure_callback_worker(self) -> None:
        """Start the background write-callback worker if not running."""
        with self._callback_worker_lock:
            if self._callback_worker is not None and self._callback_worker.is_alive():
                return

            def _worker() -> None:
                while True:
                    item = self._callback_queue.get()
                    if item is None:
                        break
                    abs_path, content, operation = item
                    try:
                        if self._on_write is None:
                            logger.error(
                                "Write callback is None in worker; dropping %s (%s)",
                                abs_path,
                                operation,
                            )
                            continue
                        self._on_write(abs_path, content, operation)
                    except Exception:
                        logger.error(
                            "Write callback failed for %s (%s)",
                            abs_path,
                            operation,
                            exc_info=True,
                        )

            self._callback_worker = threading.Thread(
                target=_worker, daemon=True, name="write-callback"
            )
            self._callback_worker.start()

    def _fire_write_callback(
        self, abs_path: Path, content: str, operation: str
    ) -> None:
        """Submit a write callback to the background worker thread."""
        if self._on_write is None:
            return
        self._ensure_callback_worker()
        self._callback_queue.put((abs_path, content, operation))

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read the binary content of a non-.md attachment.

        Args:
            path: Relative attachment path (e.g. ``"assets/diagram.pdf"``).

        Returns:
            :class:`~markdown_vault_mcp.types.AttachmentContent` with
            base64-encoded content and MIME type.

        Raises:
            ValueError: If the path escapes the source directory, has an
                extension not in the allowlist, or the file does not exist.
            ValueError: If the file exceeds the configured size limit.
        """
        return self._docs.read_attachment(path)

    def write_attachment(
        self, path: str, content: bytes, if_match: str | None = None
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment.

        Args:
            path: Relative attachment path (e.g. ``"assets/diagram.pdf"``).
            content: Raw bytes to write.
            if_match: Optional etag from a previous :meth:`read_attachment`
                call. When provided, the write is only performed if the
                current file hash matches this value, preventing overwrites
                of concurrent modifications. Pass ``None`` (default) to skip
                the check.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            ValueError: If the path escapes the source directory, has an
                extension not in the allowlist, or the content exceeds the
                size limit.
        """
        return self._docs.write_attachment(path, content, if_match=if_match)

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict | None = None,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a document.

        Creates intermediate directories as needed.  If *frontmatter* is
        provided, it is serialised as a YAML header at the top of the file.

        Args:
            path: Relative document path.
            content: Markdown body (excluding frontmatter).
            frontmatter: Optional frontmatter dict serialised as YAML header.
            if_match: Optional etag from a previous :meth:`read` call.
                When provided, the write is only performed if the current
                file hash matches this value, preventing overwrites of
                concurrent modifications. Supplying *if_match* for a file
                that does not yet exist raises
                :exc:`~markdown_vault_mcp.exceptions.ConcurrentModificationError`.
                Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash (or the file does not exist).
            ValueError: If *path* escapes the source directory.
        """
        return self._docs.write(
            path, content, frontmatter=frontmatter, if_match=if_match
        )

    def edit(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str = "",
        if_match: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> EditResult:
        """Patch a section of a document.

        Supports three modes:

        - **Exact match** (``old_text`` only): verifies *old_text* exists
          exactly once in the full file content (including frontmatter),
          replaces it with *new_text*.
        - **Line-range** (``line_start``/``line_end`` only): replaces the
          specified line range with *new_text*.
        - **Scoped match** (both): searches for *old_text* only within the
          specified line range, allowing disambiguation of repeated text.

        When exact match fails, a normalized comparison is attempted
        (Unicode NFC, dash/quote normalization, whitespace collapsing).
        If a unique normalized match is found, it is used and
        ``match_type="normalized"`` is returned.

        Args:
            path: Relative document path.
            old_text: Text to replace. Required for exact-match and
                scoped-match modes.  Must appear exactly once (in the
                file or in the line range).
            new_text: Replacement text. When using line-range mode with an
                empty string (``""``), the selected lines are replaced with a
                single blank line. To delete lines entirely, pass the literal
                content of those lines as *old_text* (scoped-match mode) and
                supply an empty *new_text*, which removes that text span
                without inserting a blank line.
            if_match: Optional etag from a previous :meth:`read` call.
                When provided, the edit is only performed if the current
                file hash matches this value, preventing edits based on
                stale content. Pass ``None`` (default) to skip the check.
            line_start: First line to replace (1-based, inclusive).
                Must be provided together with *line_end*.
            line_end: Last line to replace (1-based, inclusive).
                Must be provided together with *line_start*.

        Returns:
            :class:`~markdown_vault_mcp.types.EditResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If the file does not exist.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            EditConflictError: If *old_text* is not found or appears
                more than once.
            ValueError: If parameter combination is invalid, or line
                numbers are out of range.
        """
        return self._docs.edit(
            path,
            old_text=old_text,
            new_text=new_text,
            if_match=if_match,
            line_start=line_start,
            line_end=line_end,
        )

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment.

        Removes the file from disk.  For ``.md`` documents, also removes all
        FTS and embedding index entries.  For attachments, only the file is
        deleted (no index update).

        Args:
            path: Relative document or attachment path.
            if_match: Optional etag from a previous :meth:`read` or
                :meth:`read_attachment` call. When provided, the deletion is
                only performed if the current file hash matches this value.
                Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.DeleteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If the file does not exist.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            ValueError: If the path escapes the source directory, or (for
                non-.md paths) has an extension not in the attachment allowlist.
        """
        return self._docs.delete(path, if_match=if_match)

    def rename(
        self,
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        *,
        update_links: bool = False,
    ) -> RenameResult:
        """Rename or move a document or attachment.

        Renames the file on disk.  For ``.md`` documents, also updates FTS
        and embedding index entries.  For attachments, only the file is moved
        (no index update).  Creates intermediate directories for *new_path*
        as needed.

        When *update_links* is ``True`` and *old_path* is a ``.md`` document,
        every document that links to *old_path* is also updated so its links
        point to *new_path*.  Replacement is best-effort: failures are logged
        at ``WARNING`` but do not prevent the rename from succeeding.

        Args:
            old_path: Current relative document or attachment path.
            new_path: Target relative document or attachment path.
            if_match: Optional etag from a previous :meth:`read` or
                :meth:`read_attachment` call for *old_path*. When provided,
                the rename is only performed if the current file hash matches
                this value. Pass ``None`` (default) to skip the check.
            update_links: When ``True``, find all documents that link to
                *old_path* and rewrite their link targets to point to
                *new_path*. Only applies to ``.md`` documents.  Default
                ``False``.

        Returns:
            :class:`~markdown_vault_mcp.types.RenameResult` with
            *updated_links* counting source documents successfully updated.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If *old_path* does not exist.
            DocumentExistsError: If *new_path* already exists.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current hash of *old_path*.
            ValueError: If either path escapes the source directory, or (for
                non-.md paths) has an extension not in the attachment allowlist.
        """
        return self._docs.rename(
            old_path, new_path, if_match=if_match, update_links=update_links
        )

    def _is_attachment(self, path: str) -> bool:
        """Return True if *path* is an allowed non-.md attachment."""
        return self._docs.is_attachment(path)

    def _is_path_excluded(self, path: str) -> bool:
        """Check whether *path* matches any configured exclude pattern."""
        return self._docs.is_path_excluded(path)

    def _validate_path(self, path: str) -> Path:
        """Resolve a relative path and validate it is inside source_dir."""
        return self._docs.validate_path(path)

    def _validate_attachment_path(self, path: str) -> Path:
        """Resolve and validate a non-.md attachment path."""
        return self._docs.validate_attachment_path(path)

    def _effective_attachment_extensions(self) -> frozenset[str]:
        """Return the effective set of allowed attachment extensions."""
        return self._docs.effective_attachment_extensions()

    def _check_writable(self) -> None:
        """Raise ReadOnlyError if the collection is configured as read-only."""
        if self._read_only:
            raise ReadOnlyError(
                "Collection is read-only; write operations are not permitted."
            )
