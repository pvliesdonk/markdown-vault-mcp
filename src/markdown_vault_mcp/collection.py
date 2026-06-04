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
        from markdown_vault_mcp.managers.git_query import GitQueryManager
        from markdown_vault_mcp.managers.index import IndexManager
        from markdown_vault_mcp.managers.link import LinkManager
        from markdown_vault_mcp.managers.search import SearchManager

        # 1. LinkManager (no deps)
        self._link_mgr = LinkManager(fts=self._fts, source_dir=self._source_dir)
        # 1b. GitQueryManager (git history/diff reads; needs git_strategy + source_dir)
        self._git_query_mgr = GitQueryManager(self._git_strategy, self._source_dir)
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
        self._reader_facet = ReaderFacet(
            search_mgr=self._search_mgr,
            doc_mgr=self._doc_mgr,
            git_query_mgr=self._git_query_mgr,
            require_built=self._require_built,
        )
        self._writer_facet = WriterFacet(self._doc_mgr)
        self._graph_facet = GraphFacet(
            link_mgr=self._link_mgr, require_built=self._require_built
        )
        self._index_facet = IndexFacet(
            coordinator=self._coordinator, index_mgr=self._index_mgr
        )

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
        """Return True when the FTS index is queryable (precondition snapshot).

        A captured build error does NOT demote queryability: it is
        diagnostic state surfaced via :meth:`get_index_status`, not a gate.
        """
        return self._index_facet.is_queryable()

    def start_background_build_index(self) -> None:
        """Spawn a daemon thread that runs :meth:`build_index` to completion.

        .. deprecated:: 1.28
           Superseded by :meth:`build_index_async`. Retained for legacy tests.
        """
        self._index_facet.start_background_build_index()

    def should_use_background_build(self) -> bool:
        """Return True iff the lifespan should route to the background build.

        .. deprecated:: 1.28
           Retained for legacy tests; the lifespan no longer branches on it.
        """
        return self._index_facet.should_use_background_build()

    def is_drained(self) -> bool:
        """Return True iff the IndexWriter has no pending or in-flight work.

        Reflects the moment of call only; pair with :meth:`write_generation`
        to detect a complete write cycle inside a read window.
        """
        return self._index_facet.is_drained()

    def write_generation(self) -> int:
        """Return the writer's monotonic completion counter.

        Increments once per completed job. Pair with :meth:`is_drained` to
        detect a write cycle inside a read window.
        """
        return self._index_facet.write_generation()

    def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Block until :meth:`is_drained`, or until *timeout* (best-effort)."""
        return self._index_facet.wait_for_drain(timeout)

    def get_index_status(self) -> dict[str, Any]:
        """Return a non-blocking eleven-key snapshot of build + writer state.

        Keys: ``status`` (``"queryable"`` | ``"building"`` | ``"failed"``),
        ``documents_indexed``, ``documents_indexed_error``, ``error``,
        ``last_reindex_error``, ``last_build_embeddings_error``, plus
        ``queue_depth``, ``in_flight``, ``dirty_paths``, ``dirty_embeddings``,
        ``write_generation`` merged from the writer. A captured build error
        appears in ``error`` as diagnostic context without demoting a
        ``queryable`` status; ``documents_indexed_error`` carries a SQLite
        read failure (``documents_indexed`` stays ``0``) (#583).
        """
        return self._index_facet.get_index_status()

    def wait_until_queryable(self, timeout: float | None = None) -> None:
        """Block until the FTS index is queryable, or raise.

        A captured build error does NOT block here; it surfaces as
        ``IndexUnavailableError(reason="build_failed")`` and is also readable
        via :meth:`get_index_status`. Library bucket-3/4 methods use
        :meth:`_require_built` instead, which raises immediately.

        Raises:
            IndexUnavailableError: timeout expired (``reason="timeout"``), a
                build ran and failed (``reason="build_failed"``), or no build
                was ever scheduled (``reason="never_built"``).
        """
        self._index_facet.wait_until_queryable(timeout)

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

        Args:
            query: Search string.
            limit: Maximum number of files (not chunks) to return.
            mode: ``"keyword"`` for BM25 FTS5, ``"semantic"`` for cosine
                similarity, or ``"hybrid"`` for Reciprocal Rank Fusion of both.
            filters: Dict of ``{frontmatter_key: value}`` pairs (AND semantics).
                Only works for fields in ``indexed_frontmatter_fields``.
            folder: If provided, restrict results to documents in this folder
                (and its sub-folders).
            chunks_per_file: Maximum number of sections returned per file.
                ``None`` uses the server default configured at startup.
            snippet_words: Width of the snippet window in words.  ``0`` returns
                the full chunk.  ``None`` uses the server default.

        Returns:
            List of :class:`~markdown_vault_mcp.types.GroupedResult` ordered
            by descending file score (max of section scores).  Each result
            wraps one document with up to ``chunks_per_file`` sections.

        Raises:
            ValueError: If *mode* is ``"semantic"`` or ``"hybrid"`` but no
                embedding provider or embeddings path is configured.
        """
        return self._reader_facet.search(
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

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).
            section: When provided, return only the section whose heading
                matches *section* exactly (case-sensitive). Pass the
                ``heading`` value from a ``search`` result unchanged for
                guaranteed match. ``None`` (the default) returns the whole
                document. Raises :exc:`ValueError` if the section is not found.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent` instance, or ``None``
            if the file does not exist.
        """
        return self._reader_facet.read(path, section=section)

    def list_documents(
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
        return self._reader_facet.list_documents(
            folder=folder, pattern=pattern, include_attachments=include_attachments
        )

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index.

        Warm restarts (existing populated index, ``force=False``) are an O(1)
        no-op keyed on FTS state. ``force=True`` drops and rebuilds; config
        changes require ``force=True`` to apply (see issue #525).

        Returns:
            :class:`~markdown_vault_mcp.types.IndexStats` describing what was indexed.
        """
        return self._index_facet.build_index(force=force)

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Returns:
            :class:`~markdown_vault_mcp.types.ReindexResult` with counts applied.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
        """
        return self._index_facet.reindex()

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk.

        Returns:
            Total number of chunks embedded.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If ``embedding_provider`` or ``embeddings_path`` is unset.
        """
        return self._index_facet.build_embeddings(force=force)

    def build_index_async(self, *, force: bool = False) -> Future[IndexStats]:
        """Submit a full FTS index build and return the Future.

        Caller may ``.result()`` to wait or fire-and-forget. Warm-restart
        short-circuit returns an already-resolved Future without queuing a
        job, mirroring :meth:`build_index`.

        Args:
            force: When ``True``, drop and rebuild the index unconditionally.

        Returns:
            ``concurrent.futures.Future`` carrying the :class:`IndexStats`.
        """
        return self._index_facet.build_index_async(force=force)

    def reindex_async(self) -> Future[ReindexResult]:
        """Submit an incremental FTS reindex and return the Future.

        Does not require :meth:`build_index` first — the writer's FIFO queue
        orders any earlier :class:`BuildIndex` before this job. Writer-thread
        failures are surfaced via :meth:`get_index_status` (#561).
        """
        return self._index_facet.reindex_async()

    def build_embeddings_async(self, *, force: bool = False) -> Future[int]:
        """Submit a vector index build and return the Future.

        Does not require :meth:`build_index` first — FIFO ordering runs any
        earlier :class:`BuildIndex` first. Writer-thread failures are surfaced
        via :meth:`get_index_status` (#561).
        """
        return self._index_facet.build_embeddings_async(force=force)

    def embeddings_status(self) -> dict[str, Any]:
        """Return status information about the vector index.

        Returns:
            Dict with keys ``provider``, ``chunk_count``, ``path``,
            ``available``.
        """
        return self._index_facet.embeddings_status()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed collection.

        Returns:
            Sorted list of folder strings (``""`` for the collection root).
        """
        return self._reader_facet.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field.

        If *field* was not in ``indexed_frontmatter_fields``, returns ``[]``.

        Args:
            field: Frontmatter key to query (default: ``"tags"``).

        Returns:
            Sorted list of distinct value strings.
        """
        return self._reader_facet.list_tags(field)

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document.

        Queries the FTS sections table for headings and prepends the document
        title as a synthetic H1 entry. The result depends on the FTS index, so
        cold-start callers must build the index first (bucket 3).

        Args:
            path: Relative path to the document (e.g. ``"notes/intro.md"``).

        Returns:
            List of ``{"heading": str, "level": int}`` dicts ordered by
            position, with the document title prepended as level 1.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        return self._reader_facet.get_toc(path)

    def get_backlinks(
        self, path: str, *, limit: int | None = None
    ) -> list[BacklinkInfo]:
        """Return all documents that link to the given document.

        Args:
            path: Relative path of the target document
                (e.g. ``"notes/topic.md"``).
            limit: Maximum number of results to return.  ``None`` (default)
                means unlimited.

        Returns:
            List of :class:`~markdown_vault_mcp.types.BacklinkInfo` objects
            for each document that contains a link pointing to ``path``.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        return self._graph_facet.get_backlinks(path, limit=limit)

    def get_outlinks(self, path: str, *, limit: int | None = None) -> list[OutlinkInfo]:
        """Return all links from the given document to other documents.

        The ``exists`` field on each :class:`~markdown_vault_mcp.types.OutlinkInfo`
        indicates whether the target document is currently indexed.

        Args:
            path: Relative path of the source document
                (e.g. ``"notes/topic.md"``).
            limit: Maximum number of results to return.  ``None`` (default)
                means unlimited.

        Returns:
            List of :class:`~markdown_vault_mcp.types.OutlinkInfo` objects for
            each link originating from ``path``.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        return self._graph_facet.get_outlinks(path, limit=limit)

    def get_broken_links(self, *, folder: str | None = None) -> list[BrokenLinkInfo]:
        """Return all links whose target does not exist in the collection.

        Args:
            folder: If provided, restrict to source documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.BrokenLinkInfo` objects.
        """
        return self._graph_facet.get_broken_links(folder=folder)

    def get_similar(
        self,
        path: str,
        *,
        limit: int = 10,
        chunks_per_file: int | None = None,
    ) -> list[GroupedResult]:
        """Return semantically similar documents grouped by file.

        See :meth:`SearchManager.get_similar` for details.  Returns
        :class:`~markdown_vault_mcp.types.GroupedResult` objects ordered by
        descending file score; each result wraps one document with up to
        ``chunks_per_file`` sections.

        Args:
            path: Relative path of the reference document.
            limit: Maximum number of files to return.
            chunks_per_file: Maximum sections per result file.

        Returns:
            List of grouped results.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
        """
        return self._reader_facet.get_similar(
            path, limit=limit, chunks_per_file=chunks_per_file
        )

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
        return self._reader_facet.get_recent(limit=limit, folder=folder)

    def get_context(
        self,
        path: str,
        *,
        similar_limit: int = 5,
        link_limit: int = 10,
    ) -> NoteContext:
        """Return a consolidated context dossier for a document.

        Combines backlinks, outlinks, similar notes, folder peers, and
        indexed frontmatter tags into a single response, saving the caller
        multiple round trips.

        Args:
            path: Relative path of the document (e.g. ``"notes/topic.md"``).
            similar_limit: Maximum number of similar notes to include.
            link_limit: Maximum number of backlinks and outlinks to include.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContext` object.  Its
            ``similar`` field is a list of
            :class:`~markdown_vault_mcp.types.GroupedResult` entries, each
            with exactly one section (chunks_per_file=1) so the dossier
            stays compact.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        return self._reader_facet.get_context(
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
        return self._graph_facet.get_orphan_notes()

    def get_most_linked(self, *, limit: int = 10) -> list[MostLinkedNote]:
        """Return the documents with the most inbound links.

        Args:
            limit: Maximum number of results to return. Default 10.

        Returns:
            List of :class:`~markdown_vault_mcp.types.MostLinkedNote` ordered
            by backlink_count descending.
        """
        return self._graph_facet.get_most_linked(limit=limit)

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
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If *source* or *target* is not found in the index.
        """
        return self._graph_facet.get_connection_path(
            source, target, max_depth=max_depth
        )

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
                commit whose committer date equals either endpoint is included
                in the result.
            limit: Maximum number of commits to return.  Clamped to
                ``[1, 100]``.  Defaults to ``20``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.HistoryEntry` ordered
            newest-first.  Empty list when the vault has no git history or
            the note has no commits in the given range.  The
            ``paths_changed`` field on each entry is populated for vault-wide
            queries (``path=None``); it is always empty for single-note
            queries, since the path is already determined by the query
            arguments — callers know which file the commit touched without
            needing it echoed back.

        Raises:
            ValueError: If *path* is provided but fails path validation.
        """
        return self._reader_facet.get_history(
            path, since=since, until=until, limit=limit
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
            since_timestamp: ISO 8601 datetime string, resolved via
                ``git rev-list --before=<ts> -1 HEAD`` to the most recent
                commit at or before that instant.  Boundary is
                **inclusive**: a commit whose committer date equals
                *since_timestamp* IS the resolved ref.  Mutually exclusive
                with *since_sha*.
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
        return self._reader_facet.get_diff(
            path,
            since_sha=since_sha,
            since_timestamp=since_timestamp,
            per_commit=per_commit,
            limit=limit,
        )

    def stats(self) -> CollectionStats:
        """Return collection-wide statistics.

        Delegates to :meth:`SearchManager.stats` via the reader facet.
        """
        return self._reader_facet.stats()

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

        Delegates to :meth:`DocumentManager.read_attachment`.
        """
        return self._reader_facet.read_attachment(path)

    def write_attachment(
        self,
        path: str,
        content: bytes,
        if_match: str | None = None,
        *,
        skip_size_cap: bool = False,
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment.

        Delegates to :meth:`DocumentManager.write_attachment`.  Pass
        ``skip_size_cap=True`` from callers that have their own size
        gate (e.g. the ``create_upload_link`` receiver path, which has
        already validated against ``MARKDOWN_VAULT_MCP_UPLOAD_MAX_BYTES``);
        leave ``False`` for base64 callers of the MCP ``write`` tool.
        """
        return self._writer_facet.write_attachment(
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

        Creates intermediate directories as needed.  If *frontmatter* is
        provided, it is serialised as a YAML header at the top of the file.

        Args:
            path: Relative document path (e.g. ``"notes/topic.md"``).
            content: Markdown body (excluding frontmatter).
            frontmatter: Optional frontmatter dict serialised as a YAML header.
            if_match: Optional etag from a previous :meth:`read` call.  When
                provided, the write is only performed if the current file hash
                matches this value, preventing overwrites of concurrent
                modifications.  Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            ValueError: If *path* escapes the source directory.
        """
        return self._writer_facet.write(
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

        Replaces the first occurrence of *old_text* with *new_text*, or
        replaces the line range [*line_start*, *line_end*] when line numbers
        are given instead.

        Args:
            path: Relative document path.
            old_text: Exact text to replace (must occur exactly once).
                Mutually exclusive with *line_start* / *line_end*.
            new_text: Replacement text (may be empty to delete *old_text*).
            if_match: Optional etag for optimistic concurrency; see
                :meth:`write`.
            line_start: 1-based start line for line-range mode.
            line_end: 1-based end line (inclusive) for line-range mode.

        Returns:
            :class:`~markdown_vault_mcp.types.EditResult`.

        Raises:
            EditConflictError: If *old_text* is not found or appears more than
                once.
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            ValueError: If *path* escapes the source directory.
        """
        return self._writer_facet.edit(
            path,
            old_text=old_text,
            new_text=new_text,
            if_match=if_match,
            line_start=line_start,
            line_end=line_end,
        )

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment.

        Removes the file from disk and purges its entries from the FTS and
        vector indices.

        Args:
            path: Relative path of the document or attachment to remove.
            if_match: Optional etag for optimistic concurrency; see
                :meth:`write`.

        Returns:
            :class:`~markdown_vault_mcp.types.DeleteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            DocumentNotFoundError: If *path* does not exist.
        """
        return self._writer_facet.delete(path, if_match=if_match)

    def rename(
        self,
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        *,
        update_links: bool = False,
    ) -> RenameResult:
        """Rename or move a document or attachment.

        Moves the file on disk and updates the FTS / vector indices.  When
        *update_links* is ``True``, all wikilinks and markdown links in other
        documents that pointed to *old_path* are rewritten to *new_path*.

        Args:
            old_path: Current relative path of the document or attachment.
            new_path: Desired relative path after the move.
            if_match: Optional etag for optimistic concurrency; see
                :meth:`write`.
            update_links: When ``True``, rewrite internal links across the
                vault to reflect the new path.  Defaults to ``False``.

        Returns:
            :class:`~markdown_vault_mcp.types.RenameResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            DocumentNotFoundError: If *old_path* does not exist.
            ValueError: If *old_path* or *new_path* escapes the source
                directory.
        """
        return self._writer_facet.rename(
            old_path, new_path, if_match=if_match, update_links=update_links
        )
