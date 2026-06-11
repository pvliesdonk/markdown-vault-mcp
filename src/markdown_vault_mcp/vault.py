"""Thin facade tying all markdown-vault-mcp modules together.

:class:`Vault` is the primary public API for the library.  MCP tools,
LangChain wrappers, and CLI commands all go through this class.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING

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
    from pathlib import Path

    from markdown_vault_mcp.git import GitWriteStrategy, PullResult
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.types import (
        WriteCallback,
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


class Vault:
    """Facade over FTS5 index, vector index, and change tracker.

    Instantiate once per vault root.  The read / write / graph / index
    operations live on the four facets, reached through the :attr:`reader` /
    :attr:`writer` / :attr:`graph` / :attr:`index` accessors (e.g.
    ``vault.reader.search(...)``); this class itself exposes only
    construction, those accessors, and lifecycle.

    Callers must invoke :meth:`IndexFacet.build_index` before bucket-3
    relational/FTS-backed queries (:meth:`GraphFacet.get_backlinks`,
    :meth:`GraphFacet.get_outlinks`, :meth:`ReaderFacet.get_similar`,
    :meth:`ReaderFacet.get_context`, :meth:`GraphFacet.get_connection_path`,
    :meth:`ReaderFacet.get_toc`) or the bucket-4 coordinators
    :meth:`IndexFacet.reindex` and :meth:`IndexFacet.build_embeddings`;
    otherwise :exc:`~markdown_vault_mcp.exceptions.IndexUnavailableError` is
    raised. :meth:`IndexFacet.build_index` must also precede :meth:`start` —
    see :meth:`start` for the rationale.
    Bucket-1 file operations (:meth:`ReaderFacet.read`,
    :meth:`WriterFacet.write`, :meth:`WriterFacet.edit`,
    :meth:`WriterFacet.delete`, :meth:`WriterFacet.rename`,
    :meth:`WriterFacet.write_attachment`) and bucket-2 aggregate queries
    (:meth:`ReaderFacet.search`, :meth:`ReaderFacet.list_documents`,
    :meth:`ReaderFacet.stats`, …) work on an unbuilt index — bucket-1 hits
    disk directly; bucket-2 returns whatever is currently in the index (empty
    on cold start). See issue #525.

    **Index lifecycle (issues #513, #526, #559).** The MCP server
    lifespan submits a :class:`~markdown_vault_mcp.indexing.BuildIndex`
    job to the single-owner
    :class:`~markdown_vault_mcp.indexing.IndexWriter` via
    :meth:`IndexFacet.build_index_async` and yields immediately. On a warm
    restart the persisted FTS completeness sentinel (PR #526) causes
    :meth:`IndexFacet.build_index_async` to return an already-resolved
    ``Future`` in O(1) without touching the writer queue. On a cold
    restart the writer thread runs the job asynchronously while the
    lifespan yields; bucket-3/4 MCP tool *clients* block on the
    :class:`markdown_vault_mcp._server_queryable.needs_queryable`
    decorator, which calls :meth:`IndexFacet.wait_until_queryable` with a
    bounded default timeout
    (``MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S``, default 60s). The
    library stays honest: bucket-3/4 *methods* keep the PR #525
    raise-immediately contract via :meth:`_require_built`.
    Internal callers (lifespan, git pull loop, CLI, direct library
    users) get the raise contract and handle "not ready" with
    caller-appropriate logic — never block.

    **Thread safety (issue #519):** every facet operation and lifecycle method
    is safe to call from any thread, concurrently with other reads and writes
    from any other thread. Index mutations (FTS + vector index) are serialised
    by the single-owner :class:`~markdown_vault_mcp.indexing.IndexWriter`
    thread (#559); file-mutation operations on disk are serialised via
    ``_file_write_lock`` (RLock) so two MCP write tools racing on the
    same path do not tear. ``close()`` is safe from any thread; after
    ``close()`` the vault must not be used. Cross-method atomicity
    (e.g. read-then-write without intervening concurrent write) is the
    caller's responsibility — pass ``if_match=`` to write methods for
    optimistic concurrency. ``fork()`` is not supported. See ``docs/design.md``
    "Vault thread-safety contract" for the underlying per-thread
    SQLite-connection model.

    Args:
        source_dir: Root directory of the markdown vault.
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
        max_attachment_size_mb: Attachment context-size cap in megabytes,
            enforced by the ``read`` / ``write`` / ``fetch`` MCP tools (not by
            the vault library). ``0`` disables the limit (default ``1.0``).
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
        max_chunk_chars: int | None = None,
        max_chunk_chars_override: int | None = None,
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
                max_chunk_words=max_chunk_words,
                max_chunk_chars=max_chunk_chars,
            )
        else:
            # NOTE: When a caller passes an explicit chunk_strategy instance
            # (e.g. HeadingChunker(max_chunk_words=None) for legacy H1/H2-only
            # behaviour), we honour their construction as-is. The Vault-level
            # max_chunk_words only takes effect for the conventional default
            # ("heading" string), so explicit-instance callers retain full control.
            self._chunk_strategy = _resolve_chunk_strategy(chunk_strategy)
        # The derived cap (max_chunk_chars) is passed straight to the chunker
        # above and kept nowhere else — it is deliberately NOT a warm-restart
        # key, so a transient model-context read does not trigger a rebuild.
        # Stable warm-restart keys recorded into FTS meta at build time (#649):
        # the embedding model name and the explicit char-cap override. A change
        # to either rejects the short-circuit and cold-rebuilds.
        self._max_chunk_chars_override = max_chunk_chars_override
        # None when no provider is configured.
        self._embed_model_name: str | None = (
            embedding_provider.model_name if embedding_provider is not None else None
        )
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
        # IndexWriteCoordinator (#576); Vault delegates to it.

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
        self._git_query_mgr = GitQueryManager(
            self._git_strategy,
            self._source_dir,
            attachment_extensions=self._attachment_extensions,
        )
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
            embed_model_name=self._embed_model_name,
            max_chunk_chars_override=self._max_chunk_chars_override,
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
            embed_model_name=self._embed_model_name,
            max_chunk_chars_override=self._max_chunk_chars_override,
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
        # #571: let the puller pause new writes and drain pending commits
        # before a merge so it runs on a clean tree. Wired here (not in start())
        # so the interactive force_pull is covered even when the periodic pull
        # loop is disabled. drain is late-bound to the dispatcher just built.
        if self._git_strategy is not None:
            self._git_strategy.set_write_quiescer(
                pause_writes=self.pause_writes,
                drain_writes=self._write_callback.drain,
            )

        # 4. DocumentManager (mark_paths_dirty routes through the writer)
        self._doc_mgr = DocumentManager(
            fts=self._fts,
            source_dir=self._source_dir,
            write_lock=self._file_write_lock,
            chunk_strategy=self._chunk_strategy,
            read_only=self._read_only,
            exclude_patterns=self._exclude_patterns,
            attachment_extensions=self._attachment_extensions,
            max_note_read_bytes=self._max_note_read_bytes,
            on_write_callback=self._write_callback.fire,
            mark_paths_dirty=self._coordinator.mark_paths_dirty,
        )

        # Facets (#604): thin views over the shared managers/coordinator, exposed via the reader/writer/graph/index accessors.
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

    @property
    def source_dir(self) -> Path:
        """The vault's root directory."""
        return self._source_dir

    @property
    def max_attachment_size_mb(self) -> float:
        """The attachment context-size cap in MB (``0`` = unlimited).

        Enforced by the ``read`` / ``write`` / ``fetch`` MCP tools, not by the
        vault library itself.
        """
        return self._max_attachment_size_mb

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
        """Start background tasks for this Vault (e.g. git pull loop).

        Call :meth:`IndexFacet.build_index` **before** :meth:`start`. The git
        pull loop wires :meth:`IndexFacet.reindex` (bucket 4) as its
        ``on_pull`` callback, and ``reindex`` raises
        :exc:`IndexUnavailableError` on an unbuilt index — so a pull event
        firing before the initial build would crash the loop thread.
        """
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.start(
            repo_path=self._source_dir,
            pull_interval_s=self._git_pull_interval_s,
            on_pull=self._index_facet.reindex,
        )

    def force_pull(self) -> PullResult | None:
        """Pull from the git remote synchronously.

        Thin public facade over :meth:`GitWriteStrategy.force_pull` used by
        the GitHub webhook handler so the strategy stays an implementation detail.

        The strategy self-quiesces around its own merge: it pauses new writes
        (via the :meth:`pause_writes` callable wired in :meth:`__init__` through
        ``set_write_quiescer``) and drains the deferred-commit queue before the
        merge, so a write that landed just before the pull is committed first
        and the merge runs on a clean tree (#571). This facade therefore no
        longer wraps ``pause_writes`` itself.

        Returns:
            :class:`~markdown_vault_mcp.git.PullResult` from the strategy, or
            ``None`` when no git strategy is configured.
        """
        if self._git_strategy is None:
            return None
        # The strategy now self-quiesces (pause + drain) around the merge (#571),
        # so the previous outer pause_writes() wrap here is redundant.
        return self._git_strategy.force_pull()

    def stop(self) -> None:
        """Stop background tasks (e.g. git pull loop) without closing the vault.

        Safe to call multiple times.  A no-op if no pull loop was started.
        The SQLite connection and write callback remain open; only the pull
        loop thread is signalled to stop.
        """
        if self._git_strategy is not None:
            self._git_strategy.stop()

    def close(self) -> None:
        """Release resources held by the vault.

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
        """Raise :exc:`IndexUnavailableError` if :meth:`IndexFacet.build_index` has not run."""
        self._coordinator.require_built()

    @property
    def _vectors(self) -> VectorIndex | None:
        """Bridge property: vector index is owned by SearchManager."""
        return self._search_mgr.vectors

    @_vectors.setter
    def _vectors(self, value: VectorIndex | None) -> None:
        self._search_mgr.vectors = value

    # ------------------------------------------------------------------
    # Path validation helpers
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
