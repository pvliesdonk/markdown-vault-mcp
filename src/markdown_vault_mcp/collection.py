"""Thin facade tying all markdown-vault-mcp modules together.

:class:`Collection` is the primary public API for the library.  MCP tools,
LangChain wrappers, and CLI commands all go through this class.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
)
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.managers.index import IndexManager
from markdown_vault_mcp.managers.link import LinkManager
from markdown_vault_mcp.managers.search import SearchManager
from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
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


def _resolve_chunk_strategy(strategy: str | ChunkStrategy) -> ChunkStrategy:
    """Return a concrete ChunkStrategy from a string name or pass-through."""
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


class Collection:
    """Facade over FTS5 index, vector index, and change tracker.

    Instantiate once per collection root.  Call :meth:`build_index` (or let
    lazy initialisation handle it) before querying.
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
        self._links = LinkManager(self)
        self._index_mgr = IndexManager(self)

        # Vector index is loaded lazily (only if embeddings_path is set).
        self._vectors: VectorIndex | None = None

        # Lazy initialisation flag.
        self._initialized = False

        # Serialise concurrent write operations on this instance.
        self._write_lock = threading.RLock()

        # Deferred embedding updates.
        self._dirty_embeddings: set[str] = set()
        self._embedding_flush_timer: threading.Timer | None = None
        self._embedding_flush_lock = threading.Lock()

        # Deferred write callback queue.
        self._callback_queue: queue.Queue[tuple[Path, str, str] | None] = queue.Queue()
        self._callback_worker: threading.Thread | None = None
        self._callback_worker_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def pause_writes(self) -> Iterator[None]:
        """Block all write operations until the context exits."""
        with self._write_lock:
            yield

    def sync_from_remote_before_index(self) -> None:
        """One-time git fetch + ff-only update before build_index()."""
        return self._index_mgr.sync_from_remote_before_index()

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
        """Stop background tasks."""
        if self._git_strategy is not None:
            self._git_strategy.stop()

    def close(self) -> None:
        """Release resources held by the collection."""
        self._flush_dirty_embeddings()

        if self._callback_worker is not None and self._callback_worker.is_alive():
            self._callback_queue.put(None)
            self._callback_worker.join(timeout=30)

        if self._git_strategy is not None:
            self._git_strategy.close()
        if (
            self._on_write is not None
            and self._on_write is not self._git_strategy
            and hasattr(self._on_write, "close")
        ):
            self._on_write.close()  # type: ignore[union-attr]

        self._fts.close()

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
        """Search the collection."""
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
        """Load or return the cached VectorIndex."""
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
        """Read the full content of a document from disk."""
        return self._docs.read(path)

    def list(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
    ) -> list[NoteInfo | AttachmentInfo]:
        """List documents (and optionally attachments) in the collection."""
        return self._search.list_documents(
            folder=folder, pattern=pattern, include_attachments=include_attachments
        )

    def embeddings_status(self) -> dict[str, Any]:
        """Return status information about the vector index."""
        return self._index_mgr.embeddings_status()

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed collection."""
        return self._search.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field."""
        return self._search.list_tags(field=field)

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document."""
        return self._docs.get_toc(path)

    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> list[NoteInfo]:
        """Return the most recently modified documents."""
        return self._search.get_recent(limit=limit, folder=folder)

    def get_backlinks(self, path: str) -> list[BacklinkInfo]:
        """Return all documents that link to the given document."""
        return self._links.get_backlinks(path)

    def get_outlinks(self, path: str) -> list[OutlinkInfo]:
        """Return all links from the given document to other documents."""
        return self._links.get_outlinks(path)

    def get_broken_links(self, *, folder: str | None = None) -> list[BrokenLinkInfo]:
        """Return all links whose target does not exist in the collection."""
        return self._links.get_broken_links(folder=folder)

    def get_similar(self, path: str, *, limit: int = 10) -> list[SearchResult]:
        """Return the most semantically similar chunks from other documents."""
        return self._search.get_similar(path, limit=limit)

    def get_context(
        self,
        path: str,
        *,
        similar_limit: int = 5,
        link_limit: int = 10,
    ) -> NoteContext:
        """Return a consolidated context dossier for a document."""
        return self._search.get_context(
            path, similar_limit=similar_limit, link_limit=link_limit
        )

    def get_orphan_notes(self) -> list[NoteInfo]:
        """Return all documents with no inbound or outbound links."""
        return self._links.get_orphan_notes()

    def get_most_linked(self, *, limit: int = 10) -> list[MostLinkedNote]:
        """Return the documents with the most inbound links."""
        return self._links.get_most_linked(limit=limit)

    def get_connection_path(
        self, source: str, target: str, max_depth: int = 10
    ) -> list[str] | None:
        """Return the shortest undirected path between two notes."""
        return self._links.get_connection_path(source, target, max_depth=max_depth)

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index."""
        return self._index_mgr.build_index(force=force)

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes."""
        return self._index_mgr.reindex()

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index."""
        return self._index_mgr.build_embeddings(force=force)

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
        """Return commits that touched a note or the whole vault."""
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
        """Return the diff of a note between a reference point and HEAD."""
        if self._git_strategy is None:
            return [] if per_commit else ""

        if (since_sha is None) == (since_timestamp is None):
            raise ValueError(
                "Exactly one of 'since_sha' or 'since_timestamp' must be provided"
            )

        if since_sha and not re.match(r"^[0-9a-f]{4,40}$", since_sha):
            raise ValueError(f"Invalid commit SHA: {since_sha}")

        abs_path = self._validate_path(path)
        return self._git_strategy.get_file_diff(
            self._source_dir,
            abs_path,
            ref=since_sha,
            per_commit=per_commit,
            since_timestamp=since_timestamp,
            limit=limit if per_commit else None,
        )

    def stats(self) -> CollectionStats:
        """Return collection-wide statistics."""
        self._ensure_initialized()

        rows = self._fts.list_notes()
        doc_count = len(rows)
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
    # Internal helpers (delegates)
    # ------------------------------------------------------------------

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

    def _update_vector_index(self, note: ParsedNote) -> None:
        """Mark a document for deferred embedding update."""
        return self._index_mgr.update_vector_index(note)

    def _schedule_embedding_flush(self) -> None:
        """Schedule a deferred flush of dirty embeddings."""
        return self._index_mgr.schedule_embedding_flush()

    def _flush_dirty_embeddings(self) -> None:
        """Re-embed all dirty documents and save the vector index once."""
        return self._index_mgr.flush_dirty_embeddings()

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read the binary content of a non-.md attachment."""
        return self._docs.read_attachment(path)

    def write_attachment(
        self, path: str, content: bytes, if_match: str | None = None
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment."""
        return self._docs.write_attachment(path, content, if_match=if_match)

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a document."""
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
        """Patch a section of a document."""
        return self._docs.edit(
            path,
            old_text=old_text,
            new_text=new_text,
            if_match=if_match,
            line_start=line_start,
            line_end=line_end,
        )

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment."""
        return self._docs.delete(path, if_match=if_match)

    def rename(
        self,
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        *,
        update_links: bool = False,
    ) -> RenameResult:
        """Rename or move a document or attachment."""
        return self._docs.rename(
            old_path, new_path, if_match=if_match, update_links=update_links
        )
