"""Thin facade tying all markdown-vault-mcp modules together.

:class:`Collection` is the primary public API for the library.  MCP tools,
LangChain wrappers, and CLI commands all go through this class.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.facets import (
    GraphFacet,
    IndexFacet,
    ReaderFacet,
    WriterFacet,
)
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.indexing import IndexWriteCoordinator
from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
)
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.write_callback import WriteCallbackDispatcher

if TYPE_CHECKING:
    from collections.abc import Iterator
    from concurrent.futures import Future
    from pathlib import Path

    from markdown_vault_mcp.git import GitWriteStrategy, PullResult
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.types import (
        AttachmentContent,
        AttachmentInfo,
        BacklinkInfo,
        BrokenLinkInfo,
        CollectionStats,
        CommitDiff,
        DeleteResult,
        EditResult,
        GroupedResult,
        HistoryEntry,
        IndexStats,
        MostLinkedNote,
        NoteContent,
        NoteContext,
        NoteInfo,
        OutlinkInfo,
        ReindexResult,
        RenameResult,
        WriteCallback,
        WriteResult,
    )
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

_DEFAULT_STATE_SUBDIR = ".markdown_vault_mcp"
_DEFAULT_STATE_FILENAME = "state.json"


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


class Collection:
    """Facade over FTS5 index, vector index, and change tracker.

    Instantiate once per collection root.  Callers must invoke
    :meth:`build_index` before bucket-3 relational/FTS-backed queries
    (:meth:`get_backlinks`, :meth:`get_outlinks`, :meth:`get_similar`,
    :meth:`get_context`, :meth:`get_connection_path`, :meth:`get_toc`)
    or the bucket-4 coordinators :meth:`reindex` and
    :meth:`build_embeddings`; otherwise
    :exc:`~markdown_vault_mcp.exceptions.IndexUnavailableError` is raised.
    :meth:`build_index` must also precede :meth:`start` — see
    :meth:`start` for the rationale.
    Bucket-1 file operations (:meth:`read`, :meth:`write`, :meth:`edit`,
    :meth:`delete`, :meth:`rename`, :meth:`write_attachment`) and bucket-2
    aggregate queries (:meth:`search`, :meth:`list`, :meth:`stats`, …)
    work on an unbuilt index — bucket-1 hits disk directly; bucket-2
    returns whatever is currently in the index (empty on cold start).
    See issue #525.

    **Index lifecycle (issues #513, #526, #559).** The MCP server
    lifespan submits a :class:`~markdown_vault_mcp.indexing.BuildIndex`
    job to the single-owner
    :class:`~markdown_vault_mcp.indexing.IndexWriter` via
    :meth:`build_index_async` and yields immediately. On a warm
    restart the persisted FTS completeness sentinel (PR #526) causes
    :meth:`build_index_async` to return an already-resolved
    ``Future`` in O(1) without touching the writer queue. On a cold
    restart the writer thread runs the job asynchronously while the
    lifespan yields; bucket-3/4 MCP tool *clients* block on the
    :class:`markdown_vault_mcp._server_queryable.needs_queryable`
    decorator, which calls :meth:`wait_until_queryable` with a
    bounded default timeout
    (``MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S``, default 60s). The
    library stays honest: bucket-3/4 *methods* keep the PR #525
    raise-immediately contract via :meth:`_require_built`.
    Internal callers (lifespan, git pull loop, CLI, direct library
    users) get the raise contract and handle "not ready" with
    caller-appropriate logic — never block.

    **Thread safety (issue #519):** every public method on this class is safe
    to call from any thread, concurrently with other reads and writes from
    any other thread. Index mutations (FTS + vector index) are serialised
    by the single-owner :class:`~markdown_vault_mcp.indexing.IndexWriter`
    thread (#559); file-mutation operations on disk are serialised via
    ``_file_write_lock`` (RLock) so two MCP write tools racing on the
    same path do not tear. ``close()`` is safe from any thread; after
    ``close()`` the collection must not be used. Cross-method atomicity
    (e.g. read-then-write without intervening concurrent write) is the
    caller's responsibility — pass ``if_match=`` to write methods for
    optimistic concurrency. ``fork()`` is not supported. See ``docs/design.md``
    "Collection thread-safety contract" for the underlying per-thread
    SQLite-connection model.

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
        exclude_patterns: Glob patterns (relative to *source_dir*) for files
            and directories to exclude from indexing.
        attachment_extensions: Allowlist of extensions (without leading dot)
            for binary attachments.  ``["*"]`` accepts all extensions.
        max_attachment_size_mb: Maximum binary attachment size in megabytes.
            ``0`` disables the limit (default ``1.0``).
        max_note_read_bytes: Maximum bytes returned by full-document reads.
            ``0`` disables the limit (default ``262144``, i.e. 256 KB).
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
        max_attachment_size_mb: float = 1.0,
        max_note_read_bytes: int = 262144,
        chunks_per_file: int = 2,
        snippet_words: int = 200,
        length_downweight_alpha: float = 0.25,
        max_chunk_words: int = 400,
    ) -> None:
        self._source_dir = source_dir
        self._index_path = index_path
        self._embeddings_path = embeddings_path
        self._embedding_provider = embedding_provider
        self._read_only = read_only
        self._indexed_frontmatter_fields: list[str] = indexed_frontmatter_fields or []
        self._required_frontmatter = required_frontmatter
        # Only inject max_chunk_words when the caller has not provided a
        # custom ChunkStrategy instance or an explicit string name override.
        if isinstance(chunk_strategy, str) and chunk_strategy == "heading":
            self._chunk_strategy: ChunkStrategy = HeadingChunker(
                max_chunk_words=max_chunk_words
            )
        else:
            # NOTE: When a caller passes an explicit chunk_strategy instance
            # (e.g. HeadingChunker(max_chunk_words=None) for legacy H1/H2-only
            # behaviour), we honour their construction as-is. The Collection-level
            # max_chunk_words only takes effect for the conventional default
            # ("heading" string), so explicit-instance callers retain full control.
            self._chunk_strategy = _resolve_chunk_strategy(chunk_strategy)
        self._on_write = on_write
        self._git_strategy = git_strategy
        self._git_pull_interval_s = git_pull_interval_s
        self._exclude_patterns = exclude_patterns
        self._attachment_extensions = attachment_extensions
        self._max_attachment_size_mb = max_attachment_size_mb
        self._max_note_read_bytes = max_note_read_bytes

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

        # Build-readiness state, the IndexWriter thread, async build
        # orchestration, status/drain, and dirty routing are owned by the
        # IndexWriteCoordinator (#576); Collection delegates to it.

        # Lock for file-mutation atomicity only (#559). The IndexWriter
        # thread is the serialization point for index mutations; this lock
        # serialises ONLY the read-modify-write of files in DocumentManager
        # so two MCP write tools racing on the same path don't tear.
        self._file_write_lock = threading.RLock()

        # Manager modules (dependency-injected, no back-reference).
        from markdown_vault_mcp.managers.document import DocumentManager
        from markdown_vault_mcp.managers.index import IndexManager
        from markdown_vault_mcp.managers.link import LinkManager
        from markdown_vault_mcp.managers.search import SearchManager

        # 1. LinkManager (no deps)
        self._link_mgr = LinkManager(fts=self._fts, source_dir=self._source_dir)
        # 2. IndexManager (needs fts, tracker — NOT search_mgr)
        #    get_vectors/set_vectors use late-binding lambdas that capture
        #    self._search_mgr; they are only called at runtime after all
        #    managers are constructed.  No write_lock — the IndexWriter
        #    thread is the sole mutator of indices (#559).
        self._index_mgr = IndexManager(
            fts=self._fts,
            tracker=self._tracker,
            source_dir=self._source_dir,
            embeddings_path=self._embeddings_path,
            embedding_provider=self._embedding_provider,
            chunk_strategy=self._chunk_strategy,
            exclude_patterns=self._exclude_patterns,
            required_frontmatter=self._required_frontmatter,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields,
            # Late-binding closures: self._search_mgr is assigned below and
            # only accessed at call-time, not during IndexManager.__init__.
            get_vectors=lambda: self._search_mgr.vectors,
            set_vectors=lambda v: setattr(self._search_mgr, "vectors", v),
        )
        # Index-write orchestration: owns the single-owner IndexWriter
        # thread + the build-readiness state machine (#576).  Constructed
        # after IndexManager (it routes jobs to it) and before SearchManager
        # (whose rebuild_embeddings callback targets the coordinator).
        self._coordinator = IndexWriteCoordinator(
            fts=self._fts,
            index_mgr=self._index_mgr,
            index_path=self._index_path,
            file_write_lock=self._file_write_lock,
        )
        # 3. SearchManager (receives IndexManager callbacks via constructor)
        self._search_mgr = SearchManager(
            fts=self._fts,
            source_dir=self._source_dir,
            embeddings_path=self._embeddings_path,
            embedding_provider=self._embedding_provider,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields,
            exclude_patterns=self._exclude_patterns,
            attachment_extensions=self._attachment_extensions,
            link_manager=self._link_mgr,
            # rebuild_embeddings is invoked from SearchManager._load_vectors when a
            # VectorIndexCompatibilityError fires (embedding model upgrade).  The
            # coordinator routes it through the writer thread, preserving the
            # single-owner invariant (#559): only the writer thread mutates indexes.
            rebuild_embeddings=self._coordinator.rebuild_embeddings,
            chunks_per_file=chunks_per_file,
            snippet_words=snippet_words,
            length_downweight_alpha=length_downweight_alpha,
        )
        # Deferred write callback (issue #175): the git-commit on_write
        # callback runs on a background worker so write methods return after
        # the FTS update.  Constructed before DocumentManager, whose
        # ``on_write_callback`` is wired to ``fire`` (#599).
        self._write_callback = WriteCallbackDispatcher(self._on_write)

        # 4. DocumentManager (mark_paths_dirty routes through the writer)
        self._doc_mgr = DocumentManager(
            fts=self._fts,
            source_dir=self._source_dir,
            write_lock=self._file_write_lock,
            chunk_strategy=self._chunk_strategy,
            read_only=self._read_only,
            exclude_patterns=self._exclude_patterns,
            attachment_extensions=self._attachment_extensions,
            max_attachment_size_mb=self._max_attachment_size_mb,
            max_note_read_bytes=self._max_note_read_bytes,
            on_write_callback=self._write_callback.fire,
            mark_paths_dirty=self._coordinator.mark_paths_dirty,
        )

        # Facets (#604): thin views grouping the formerly-flat surface,
        # constructed once over the shared managers/coordinator. The flat
        # methods below delegate to them (addition before removal).
        self._writer_facet = WriterFacet(self._doc_mgr)
        self._graph_facet = GraphFacet(self._link_mgr, self._require_built)
        self._reader_facet = ReaderFacet(
            search_mgr=self._search_mgr,
            doc_mgr=self._doc_mgr,
            index_mgr=self._index_mgr,
            fts=self._fts,
            git_strategy=self._git_strategy,
            source_dir=self._source_dir,
            require_built=self._require_built,
            validate_path=self._validate_path,
            embedding_provider=self._embedding_provider,
            embeddings_path=self._embeddings_path,
            attachment_extensions=self._attachment_extensions,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields,
        )
        self._index_facet = IndexFacet(self._coordinator)

    # ------------------------------------------------------------------
    # Facets (#604)
    # ------------------------------------------------------------------

    @property
    def reader(self) -> ReaderFacet:
        """Read-only facet: search, read, list, toc, similar, stats, history."""
        return self._reader_facet

    @property
    def writer(self) -> WriterFacet:
        """Document-mutation facet: write, edit, delete, rename, attachments."""
        return self._writer_facet

    @property
    def graph(self) -> GraphFacet:
        """Link-graph facet: backlinks, outlinks, broken, orphans, paths."""
        return self._graph_facet

    @property
    def index(self) -> IndexFacet:
        """Index facet: build/reindex/embeddings, readiness, writer status."""
        return self._index_facet

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def pause_writes(self) -> Iterator[None]:
        """Block file-mutation write operations until the context exits.

        Holds the :attr:`_file_write_lock` so concurrent
        :class:`DocumentManager` write/edit/delete/rename calls block on
        the lock until the context exits. Index mutations on the
        :class:`IndexWriter` thread continue unaffected — the writer
        thread does not contend on this lock.  Reads and search remain
        unblocked at the Python level.
        """
        with self._file_write_lock:
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
        """Start background tasks for this Collection (e.g. git pull loop).

        Call :meth:`build_index` **before** :meth:`start`. The git pull
        loop wires :meth:`reindex` (bucket 4) as its ``on_pull`` callback,
        and ``reindex`` raises :exc:`IndexUnavailableError` on an unbuilt
        index — so a pull event firing before the initial build would
        crash the loop thread.
        """
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.start(
            repo_path=self._source_dir,
            pull_interval_s=self._git_pull_interval_s,
            pause_writes=self.pause_writes,
            on_pull=self.reindex,
        )

    def force_pull(self) -> PullResult | None:
        """Pull from the git remote synchronously.

        Thin public facade over :meth:`GitWriteStrategy.force_pull` used by
        the GitHub webhook handler so the strategy stays an implementation detail.

        Acquires :meth:`pause_writes` for the duration of the pull so that new
        MCP writes cannot write to disk while git is modifying the working tree
        (``git merge --ff-only`` or ``git rebase`` overwrites files in-place).
        This prevents the race where a write hits disk during the merge and
        the git checkout then silently discards it.

        Note: writes that have *already* completed (file on disk, callback
        queued but not yet processed by the background worker) are still subject
        to a narrower race — see issue #571 for the full fix.

        Returns:
            :class:`~markdown_vault_mcp.git.PullResult` from the strategy, or
            ``None`` when no git strategy is configured.
        """
        if self._git_strategy is None:
            return None
        with self.pause_writes():
            return self._git_strategy.force_pull()

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
        # 0. Close the coordinator FIRST: it joins the legacy background-build
        # thread (whose worker submits to the writer) and THEN closes the
        # single-owner IndexWriter, draining pending jobs.  Must precede the
        # FTS close below — the writer's drain touches FTS (#576).  The
        # hasattr guard covers __init__ failing before _coordinator was set.
        if hasattr(self, "_coordinator"):
            self._coordinator.close(timeout=30.0)

        # 1. Deferred embedding updates are flushed by the IndexWriter
        # before its close() returns; no further flush needed here (#559).

        # 2. Drain the write-callback queue (git commits).
        self._write_callback.close(timeout=30.0)

        # 3. Close git strategy (flush push, etc.).
        if self._git_strategy is not None:
            self._git_strategy.close()
        if (
            self._on_write is not None
            and self._on_write is not self._git_strategy
            and hasattr(self._on_write, "close")
        ):
            self._on_write.close()

        # 4. Close SQLite.
        self._fts.close()

    # ------------------------------------------------------------------
    # Indexing readiness (issue #525)
    # ------------------------------------------------------------------

    def _require_built(self) -> None:
        """Raise :exc:`IndexUnavailableError` if :meth:`build_index` has not run."""
        self._coordinator.require_built()

    def is_queryable(self) -> bool:
        """Return True when the FTS index is queryable.

        Delegates to :meth:`IndexFacet.is_queryable`.
        """
        return self.index.is_queryable()

    def start_background_build_index(self) -> None:
        """Spawn a daemon thread that runs :meth:`build_index` to completion.

        Delegates to :meth:`IndexFacet.start_background_build_index`.
        """
        self.index.start_background_build_index()

    def should_use_background_build(self) -> bool:
        """Return True iff the lifespan should route to the background build.

        Delegates to :meth:`IndexFacet.should_use_background_build`.
        """
        return self.index.should_use_background_build()

    def is_drained(self) -> bool:
        """Return True iff the IndexWriter has no pending or in-flight work.

        Delegates to :meth:`IndexFacet.is_drained`.
        """
        return self.index.is_drained()

    def write_generation(self) -> int:
        """Return the writer's monotonic completion counter.

        Delegates to :meth:`IndexFacet.write_generation`.
        """
        return self.index.write_generation()

    def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Block until :meth:`is_drained`, or until *timeout*.

        Delegates to :meth:`IndexFacet.wait_for_drain`.
        """
        return self.index.wait_for_drain(timeout)

    def get_index_status(self) -> dict[str, Any]:
        """Return a non-blocking snapshot of build + writer state.

        Delegates to :meth:`IndexFacet.get_index_status`.
        """
        return self.index.get_index_status()

    def wait_until_queryable(self, timeout: float | None = None) -> None:
        """Block until the FTS index is queryable, or raise.

        Delegates to :meth:`IndexFacet.wait_until_queryable`.
        """
        self.index.wait_until_queryable(timeout)

    @property
    def _vectors(self) -> VectorIndex | None:
        """Bridge property: vector index is owned by SearchManager."""
        return self._search_mgr.vectors

    @_vectors.setter
    def _vectors(self, value: VectorIndex | None) -> None:
        self._search_mgr.vectors = value

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
        chunks_per_file: int | None = None,
        snippet_words: int | None = None,
    ) -> list[GroupedResult]:
        """Search the collection.

        Delegates to :meth:`ReaderFacet.search`.
        """
        return self.reader.search(
            query,
            limit=limit,
            mode=mode,
            filters=filters,
            folder=folder,
            chunks_per_file=chunks_per_file,
            snippet_words=snippet_words,
        )

    # ------------------------------------------------------------------
    # Read / list
    # ------------------------------------------------------------------

    def read(self, path: str, *, section: str | None = None) -> NoteContent | None:
        """Read the full content of a document from disk.

        Delegates to :meth:`ReaderFacet.read`.
        """
        return self.reader.read(path, section=section)

    def list_documents(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
    ) -> list[NoteInfo | AttachmentInfo]:
        """List documents (and optionally attachments) in the collection.

        Delegates to :meth:`ReaderFacet.list_documents`.
        """
        return self.reader.list_documents(
            folder=folder, pattern=pattern, include_attachments=include_attachments
        )

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index.

        Delegates to :meth:`IndexFacet.build_index`.
        """
        return self.index.build_index(force=force)

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Delegates to :meth:`IndexFacet.reindex`.
        """
        return self.index.reindex()

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Delegates to :meth:`IndexFacet.build_embeddings`.
        """
        return self.index.build_embeddings(force=force)

    def build_index_async(self, *, force: bool = False) -> Future[IndexStats]:
        """Submit a full FTS index build and return the Future.

        Delegates to :meth:`IndexFacet.build_index_async`.
        """
        return self.index.build_index_async(force=force)

    def reindex_async(self) -> Future[ReindexResult]:
        """Submit an incremental FTS reindex and return the Future.

        Delegates to :meth:`IndexFacet.reindex_async`.
        """
        return self.index.reindex_async()

    def build_embeddings_async(self, *, force: bool = False) -> Future[int]:
        """Submit a vector index build and return the Future.

        Delegates to :meth:`IndexFacet.build_embeddings_async`.
        """
        return self.index.build_embeddings_async(force=force)

    def embeddings_status(self) -> dict[str, Any]:
        """Return status information about the vector index.

        Delegates to :meth:`ReaderFacet.embeddings_status`.
        """
        return self.reader.embeddings_status()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed collection.

        Delegates to :meth:`ReaderFacet.list_folders`.
        """
        return self.reader.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field.

        Delegates to :meth:`ReaderFacet.list_tags`.
        """
        return self.reader.list_tags(field)

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document.

        Delegates to :meth:`ReaderFacet.get_toc`.
        """
        return self.reader.get_toc(path)

    def get_backlinks(self, path: str) -> list[BacklinkInfo]:
        """Return all documents that link to the given document.

        Delegates to :meth:`GraphFacet.get_backlinks`.
        """
        return self.graph.get_backlinks(path)

    def get_outlinks(self, path: str) -> list[OutlinkInfo]:
        """Return all links from the given document to other documents.

        Delegates to :meth:`GraphFacet.get_outlinks`.
        """
        return self.graph.get_outlinks(path)

    def get_broken_links(self, *, folder: str | None = None) -> list[BrokenLinkInfo]:
        """Return all links whose target does not exist in the collection.

        Delegates to :meth:`GraphFacet.get_broken_links`.
        """
        return self.graph.get_broken_links(folder=folder)

    def get_similar(
        self,
        path: str,
        *,
        limit: int = 10,
        chunks_per_file: int | None = None,
    ) -> list[GroupedResult]:
        """Return semantically similar documents grouped by file.

        Delegates to :meth:`ReaderFacet.get_similar`.
        """
        return self.reader.get_similar(
            path, limit=limit, chunks_per_file=chunks_per_file
        )

    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> list[NoteInfo]:
        """Return the most recently modified documents.

        Delegates to :meth:`ReaderFacet.get_recent`.
        """
        return self.reader.get_recent(limit=limit, folder=folder)

    def get_context(
        self,
        path: str,
        *,
        similar_limit: int = 5,
        link_limit: int = 10,
    ) -> NoteContext:
        """Return a consolidated context dossier for a document.

        Delegates to :meth:`ReaderFacet.get_context`.
        """
        return self.reader.get_context(
            path, similar_limit=similar_limit, link_limit=link_limit
        )

    def get_orphan_notes(self) -> list[NoteInfo]:
        """Return all documents with no inbound or outbound links.

        Delegates to :meth:`GraphFacet.get_orphan_notes`.
        """
        return self.graph.get_orphan_notes()

    def get_most_linked(self, *, limit: int = 10) -> list[MostLinkedNote]:
        """Return the documents with the most inbound links.

        Delegates to :meth:`GraphFacet.get_most_linked`.
        """
        return self.graph.get_most_linked(limit=limit)

    def get_connection_path(
        self, source: str, target: str, max_depth: int = 10
    ) -> list[str] | None:
        """Return the shortest undirected path between two notes.

        Delegates to :meth:`GraphFacet.get_connection_path`.
        """
        return self.graph.get_connection_path(source, target, max_depth=max_depth)

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

        Delegates to :meth:`ReaderFacet.get_history`.
        """
        return self.reader.get_history(path, since, until, limit)

    def get_diff(
        self,
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
    ) -> str | list[CommitDiff]:
        """Return the diff of a note between a reference point and HEAD.

        Delegates to :meth:`ReaderFacet.get_diff`.
        """
        return self.reader.get_diff(
            path,
            since_sha=since_sha,
            since_timestamp=since_timestamp,
            per_commit=per_commit,
            limit=limit,
        )

    def stats(self) -> CollectionStats:
        """Return collection-wide statistics.

        Delegates to :meth:`ReaderFacet.stats`.
        """
        return self.reader.stats()

    # ------------------------------------------------------------------
    # Write operations (delegated to DocumentManager)
    # ------------------------------------------------------------------

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
        from markdown_vault_mcp.utils import validate_path

        return validate_path(path, self._source_dir)

    def _validate_attachment_path(self, path: str) -> Path:
        """Resolve and validate a non-.md attachment path."""
        return self._doc_mgr._validate_attachment_path(path)

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read the binary content of a non-.md attachment.

        Delegates to :meth:`ReaderFacet.read_attachment`.
        """
        return self.reader.read_attachment(path)

    def write_attachment(
        self,
        path: str,
        content: bytes,
        if_match: str | None = None,
        *,
        skip_size_cap: bool = False,
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment.

        Delegates to :meth:`WriterFacet.write_attachment`.
        """
        return self.writer.write_attachment(
            path, content, if_match=if_match, skip_size_cap=skip_size_cap
        )

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a document.

        Delegates to :meth:`WriterFacet.write`.
        """
        return self.writer.write(
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

        Delegates to :meth:`WriterFacet.edit`.
        """
        return self.writer.edit(
            path,
            old_text=old_text,
            new_text=new_text,
            if_match=if_match,
            line_start=line_start,
            line_end=line_end,
        )

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment.

        Delegates to :meth:`WriterFacet.delete`.
        """
        return self.writer.delete(path, if_match=if_match)

    def rename(
        self,
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        *,
        update_links: bool = False,
    ) -> RenameResult:
        """Rename or move a document or attachment.

        Delegates to :meth:`WriterFacet.rename`.
        """
        return self.writer.rename(
            old_path, new_path, if_match=if_match, update_links=update_links
        )
