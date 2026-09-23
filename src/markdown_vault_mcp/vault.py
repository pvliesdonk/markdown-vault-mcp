"""Thin facade tying all markdown-vault-mcp modules together.

:class:`Vault` is the primary public API for the library.  MCP tools,
LangChain wrappers, and CLI commands all go through this class.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import threading
from typing import TYPE_CHECKING

# _DEFAULT_STATE_* are re-exported here for backwards compatibility (the
# state-path default historically lived in this module; domain.py still
# imports it from here).
from markdown_vault_mcp.config_sections.vault_settings import (
    _DEFAULT_STATE_FILENAME as _DEFAULT_STATE_FILENAME,
)
from markdown_vault_mcp.config_sections.vault_settings import (
    _DEFAULT_STATE_SUBDIR as _DEFAULT_STATE_SUBDIR,
)
from markdown_vault_mcp.config_sections.vault_settings import (
    VaultSettings as VaultSettings,  # re-export: settings-first construction (#1158)
)
from markdown_vault_mcp.conventions import ConventionsResolver
from markdown_vault_mcp.embed_text import EmbedTextBuilder
from markdown_vault_mcp.facets import (
    GraphFacet,
    IndexFacet,
    ReaderFacet,
    SummarizeFacet,
    WriterFacet,
)
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.indexing import IndexWriteCoordinator
from markdown_vault_mcp.indexing.head_reconciler import IndexHeadReconciler
from markdown_vault_mcp.okf import (
    OkfAuditReport,
    OkfDetector,
    ReservedFrontmatterPolicy,
    audit_bundle,
)
from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
)
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.utils.content_kind import (
    canonical_attachment_extensions,
    effective_attachment_extensions,
)
from markdown_vault_mcp.write_callback import WriteCallbackDispatcher

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from markdown_vault_mcp._commit_scope import CommitScope
    from markdown_vault_mcp.git import PullResult, VersionedStore
    from markdown_vault_mcp.indexing.head_reconciler import ReconcileOutcome
    from markdown_vault_mcp.interfaces import VectorStore
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.summarizer import Summarizer
    from markdown_vault_mcp.types import (
        WriteCallback,
    )

logger = logging.getLogger(__name__)


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
    optimistic concurrency. ``fork()`` is not supported. See ``docs/design/design.md``
    "Vault thread-safety contract" for the underlying per-thread
    SQLite-connection model.

    **Construction (#1225).** Pass ``source_dir`` and optional
    :class:`~markdown_vault_mcp.config_sections.vault_settings.VaultSettings`.
    All configuration knobs belong on ``settings``; the five collaborators
    remain explicit keyword arguments. Omitting settings uses
    ``VaultSettings()``: read-only, with no chunk overlap. These library
    defaults remain independent of the server's operator defaults.

    Args:
        source_dir: Root directory of the markdown vault.
        settings: Configuration settings. ``None`` uses ``VaultSettings()``.
        embedding_provider: Provider used to generate embeddings; required
            when ``settings.embeddings_path`` is set.
        summarizer: Optional summarization backend. Without one the
            :attr:`summarizer` accessor raises.
        git_strategy: Optional strategy for background Git tasks, started
            via :meth:`start`.
        on_write: Callback invoked after successful writes; see
            :obj:`~markdown_vault_mcp.types.WriteCallback`.
        chunk_strategy: ``"heading"`` (default), ``"whole"``, or a custom
            :class:`~markdown_vault_mcp.scanner.ChunkStrategy` instance.
    """

    # The public boundary keeps the root, settings, and five collaborators explicit.
    def __init__(  # noqa: PLR0913, RUF100
        self,
        *,
        source_dir: Path,
        settings: VaultSettings | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        summarizer: Summarizer | None = None,
        git_strategy: VersionedStore | None = None,
        on_write: WriteCallback | None = None,
        chunk_strategy: str | ChunkStrategy = "heading",
    ) -> None:
        if settings is None:
            settings = VaultSettings()
        self._source_dir = source_dir
        self._embedding_provider = embedding_provider
        self._summarizer = summarizer
        self._on_write = on_write
        self._git_strategy = git_strategy
        self._configure(settings, chunk_strategy)
        self._build_managers(settings)
        self._build_facets(settings)

    def _configure(
        self, settings: VaultSettings, chunk_strategy: str | ChunkStrategy
    ) -> None:
        """Normalise settings into the vault's configuration attributes.

        Owns the construction-time derivations: OKF detection and the
        indexed-field extension, chunker construction, the conventions-file
        exclude derivation, and the state-path default.

        Args:
            settings: The resolved construction settings.
            chunk_strategy: ``"heading"`` (default), ``"whole"``, or a custom
                :class:`~markdown_vault_mcp.scanner.ChunkStrategy` instance.

        Raises:
            ValueError: If *chunk_strategy* is an unrecognised string name,
                or ``settings.conventions_file`` contains fnmatch
                metacharacters.
        """
        self._index_path = settings.index_path
        self._embeddings_path = settings.embeddings_path
        self._embedding_batch_size = settings.embedding_batch_size
        self._read_only = settings.read_only
        self._write_protect_existing = settings.write_protect_existing
        # OKF detection (disk probe, index-independent). When read semantics
        # are active at construction time, the OKF scalar keys join the
        # effective indexed-field set so `document_tags` carries them; the
        # extended set also feeds the chunking provenance string, so the
        # startup where detection first flips cold-rebuilds the tag table
        # once (design okf.md §3). A vault that declares OKF mid-session
        # gains annotations immediately but indexed OKF tags only on the
        # next startup.
        self._okf = OkfDetector(self._source_dir, settings.okf_mode)
        configured_field_count = len(settings.indexed_frontmatter_fields or [])
        self._indexed_frontmatter_fields: list[str] = settings.effective_indexed_fields(
            okf_active=self._okf.state().active
        )
        extended = self._indexed_frontmatter_fields[configured_field_count:]
        if extended:
            logger.info("okf_indexed_fields_extended fields=%s", ",".join(extended))
        self._required_frontmatter = (
            list(settings.required_frontmatter)
            if settings.required_frontmatter is not None
            else None
        )
        # Only inject max_chunk_words when the caller has not provided a
        # custom ChunkStrategy instance or an explicit string name override.
        if isinstance(chunk_strategy, str) and chunk_strategy == "heading":
            self._chunk_strategy: ChunkStrategy = HeadingChunker(
                max_chunk_words=settings.max_chunk_words,
                max_chunk_chars=settings.max_chunk_chars,
                chunk_overlap_words=settings.chunk_overlap_words,
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
        self._max_chunk_chars_override = settings.max_chunk_chars_override
        # None when no provider is configured.
        self._embed_model_name: str | None = (
            self._embedding_provider.model_name
            if self._embedding_provider is not None
            else None
        )
        # Curated-ranking knobs. ONE shared EmbedTextBuilder is constructed
        # here and threaded to every embedding site so hot, cold, converge,
        # and flush paths all produce identical embedding input.
        self._title_field = settings.title_field
        self._searchable_frontmatter_fields: list[str] = list(
            settings.searchable_frontmatter_fields or []
        )
        self._embed_builder = EmbedTextBuilder(
            embed_context=settings.embed_context,
            searchable_fields=tuple(self._searchable_frontmatter_fields),
        )
        self._git_pull_interval_s = settings.git_pull_interval_s
        # The resolver prunes its folder walk with the *configured* patterns
        # only — the derived patterns from effective_exclude_patterns() would
        # otherwise exclude the convention files themselves.
        self._conventions = ConventionsResolver(
            self._source_dir,
            settings.conventions_file,
            exclude_patterns=settings.exclude_patterns,
        )
        # Convention files are index-excluded but stay disk-readable; the
        # derivation (both fnmatch forms) and the metacharacter validation
        # live on VaultSettings.
        effective_excludes = settings.effective_exclude_patterns()
        self._exclude_patterns: list[str] | None = (
            list(effective_excludes) if effective_excludes is not None else None
        )
        self._attachment_extensions = settings.attachment_extensions
        self._max_attachment_size_mb = settings.max_attachment_size_mb
        self._max_note_read_bytes = settings.max_note_read_bytes
        self._summarize_max_notes = settings.summarize_max_notes
        self._summarize_max_input_chars = settings.summarize_max_input_chars
        # Default state path: {source_dir}/.markdown_vault_mcp/state.json
        self._state_path = settings.effective_state_path(self._source_dir)

    def _build_managers(self, settings: VaultSettings) -> None:
        """Construct the index/tracker sub-modules and the manager layer.

        Args:
            settings: The resolved construction settings (search-ranking
                knobs and the OKF write toggle are consumed here).
        """
        # Sub-module construction.
        db_path: Path | str = (
            self._index_path if self._index_path is not None else ":memory:"
        )
        self._fts = FTSIndex(
            db_path=db_path,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields or None,
            searchable_frontmatter_fields=self._searchable_frontmatter_fields or None,
            fts_weights=settings.fts_weights,
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
        # 1b. GitQueryManager (git history/diff/revision reads; needs
        #     git_strategy + source_dir + the note read cap)
        self._git_query_mgr = GitQueryManager(
            self._git_strategy,
            self._source_dir,
            attachment_extensions=self._attachment_extensions,
            max_note_read_bytes=self._max_note_read_bytes,
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
            title_field=self._title_field,
            attachment_extensions=self._attachment_extensions,
            embed_text_builder=self._embed_builder,
            embedding_batch_size=self._embedding_batch_size,
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
            title_field=self._title_field,
            searchable_fields=",".join(self._searchable_frontmatter_fields),
            indexed_frontmatter_fields=",".join(self._indexed_frontmatter_fields),
            attachment_extensions=canonical_attachment_extensions(
                effective_attachment_extensions(self._attachment_extensions)
            ),
        )
        # 3. SearchManager (receives IndexManager callbacks via constructor)
        self._search_mgr = SearchManager(
            fts=self._fts,
            source_dir=self._source_dir,
            embeddings_path=self._embeddings_path,
            embedding_provider=self._embedding_provider,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields,
            okf_detector=self._okf,
            exclude_patterns=self._exclude_patterns,
            attachment_extensions=self._attachment_extensions,
            link_manager=self._link_mgr,
            # rebuild_embeddings is invoked from SearchManager._load_vectors when a
            # VectorIndexCompatibilityError fires (embedding model upgrade).  The
            # coordinator routes it through the writer thread, preserving the
            # single-owner invariant (#559): only the writer thread mutates indexes.
            rebuild_embeddings=self._coordinator.rebuild_embeddings,
            chunks_per_file=settings.chunks_per_file,
            snippet_words=settings.snippet_words,
            length_downweight_alpha=settings.length_downweight_alpha,
            default_mode=settings.default_search_mode,
            folder_weights=settings.folder_weights,
            embed_text_format=self._embed_builder.format_token(),
        )
        # Deferred write callback (issue #175): the git-commit on_write
        # callback runs on a background worker so write methods return after
        # the disk write and index submission.  Constructed before DocumentManager, whose
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
        from markdown_vault_mcp._okf_write import (
            build_okf_write_enrich,
            package_version,
        )

        # OKF enforced-write enrichment (#964): None (zero overhead) unless
        # OKF_WRITE is on; when on, it stamps provenance and clears verification
        # for write/edit on an OKF-active vault.
        self._okf_write_enrich = build_okf_write_enrich(
            okf_write=settings.okf_write,
            detector=self._okf,
            version=package_version(),
        )
        self._doc_mgr = DocumentManager(
            fts=self._fts,
            source_dir=self._source_dir,
            write_lock=self._file_write_lock,
            chunk_strategy=self._chunk_strategy,
            read_only=self._read_only,
            write_protect_existing=self._write_protect_existing,
            exclude_patterns=self._exclude_patterns,
            attachment_extensions=self._attachment_extensions,
            max_note_read_bytes=self._max_note_read_bytes,
            on_write_callback=self._write_callback.fire,
            mark_paths_dirty=self._coordinator.mark_paths_dirty,
            sync_index=self._coordinator.prepare_index_read,
            title_field=self._title_field,
            okf_write_enrich=self._okf_write_enrich,
        )

    def _build_facets(self, settings: VaultSettings) -> None:
        """Construct the facet layer over the managers/coordinator (#604).

        Args:
            settings: The resolved construction settings (the OKF write
                toggle gates the convention maintainer).
        """
        from markdown_vault_mcp.managers.okf_migrate import OkfMigrationManager

        # Facets (#604): thin views over the shared managers/coordinator, exposed via the reader/writer/graph/index accessors.
        self._reader_facet = ReaderFacet(
            search_mgr=self._search_mgr,
            doc_mgr=self._doc_mgr,
            git_query_mgr=self._git_query_mgr,
            require_built=self._require_built,
            okf_audit=self._okf_audit,
        )
        # The reserved files the OKF generators write are subject to this
        # vault's own required-frontmatter gate, so the generators need the
        # gate's terms to produce files that survive it (#1174, #1175).
        reserved_frontmatter = ReservedFrontmatterPolicy(
            title_field=self._title_field,
            required_fields=tuple(self._required_frontmatter or ()),
        )
        self._okf_migrate = OkfMigrationManager(
            doc_mgr=self._doc_mgr,
            link_mgr=self._link_mgr,
            search_mgr=self._search_mgr,
            git_query_mgr=self._git_query_mgr,
            require_built=self._require_built,
            reserved_frontmatter=reserved_frontmatter,
        )
        # OKF enforced-write convention maintenance (#964, phase 5b): built only
        # under OKF_WRITE (mirroring the enricher gating). On a successful
        # write/edit it appends to the folder's log.md and refreshes its
        # index.md as secondary writes; failures degrade to a WARNING.
        self._okf_convention = None
        if settings.okf_write:
            from markdown_vault_mcp._okf_convention import ConventionMaintainer

            self._okf_convention = ConventionMaintainer(
                doc_mgr=self._doc_mgr,
                okf_migrate=self._okf_migrate,
                detector=self._okf,
                write_lock=self._file_write_lock,
                reserved_frontmatter=reserved_frontmatter,
            )
        self._writer_facet = WriterFacet(
            self._doc_mgr,
            okf_migrate=self._okf_migrate,
            convention_maintainer=self._okf_convention,
            # The overwrite breadcrumb (#1137) reads git state, so it is the
            # git-query manager's answer — bound here rather than handing the
            # write facet the store itself.
            previous_revision=self._git_query_mgr.committed_revision,
        )
        self._graph_facet = GraphFacet(
            link_mgr=self._link_mgr,
            search_mgr=self._search_mgr,
            require_built=self._require_built,
            okf_detector=self._okf,
        )
        self._index_facet = IndexFacet(
            coordinator=self._coordinator, index_mgr=self._index_mgr
        )
        # #1532: reindex until the index reflects the git HEAD, whatever moved
        # it, and let the strategy report the commits of the server's own
        # writes, which the index already holds.
        self._head_reconciler: IndexHeadReconciler | None = None
        if self._git_strategy is not None:
            self._head_reconciler = IndexHeadReconciler(
                read_head=self.git_head,
                # Late-bound so the reindex seen is whatever the facet holds now.
                reindex=lambda: self._index_facet.reindex(),
                pause_writes=self.pause_writes,
            )
            self._git_strategy.set_commit_observer(
                self._head_reconciler.note_own_commit
            )
        # Summarize facet is present only when a backend was supplied (the
        # summarize tool is otherwise hidden at the server layer). Promotion
        # of slow calls to pollable background jobs is owned by the pvl-core
        # jobs subsystem at the server layer, not by the vault (#1033).
        self._summarize_facet: SummarizeFacet | None = None
        if self._summarizer is not None:
            from markdown_vault_mcp.managers.summarize import SummarizeManager

            summarize_mgr = SummarizeManager(
                doc_mgr=self._doc_mgr,
                summarizer=self._summarizer,
                max_notes=self._summarize_max_notes,
                max_input_chars=self._summarize_max_input_chars,
            )
            self._summarize_facet = SummarizeFacet(
                summarize_mgr=summarize_mgr,
                require_built=self._require_built,
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
        """Document-mutation facet: write/edit/append/delete/rename/attachments."""
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
    def summarizer(self) -> SummarizeFacet:
        """Summarize facet: LLM-backed note/subtree summarization.

        Raises:
            RuntimeError: If no summarization backend was configured (set
                ``OPENAI_API_KEY`` or an OpenAI-compatible base URL and
                install the ``[summarize]`` extra).
        """
        if self._summarize_facet is None:
            raise RuntimeError(
                "Summarization is not configured. Set OPENAI_API_KEY (or an "
                "OpenAI-compatible base URL) and install the SDK with: "
                "pip install 'markdown-vault-mcp[summarize]'"
            )
        return self._summarize_facet

    @property
    def conventions(self) -> ConventionsResolver:
        """Folder-conventions resolver (disk-read, index-independent)."""
        return self._conventions

    @property
    def okf(self) -> OkfDetector:
        """OKF detection probe (disk-read, index-independent)."""
        return self._okf

    def _okf_audit(self) -> OkfAuditReport:
        """Run the OKF conformance audit over this vault (#962).

        Bound into :class:`ReaderFacet` so the audit uses the vault's
        effective exclude patterns as its whitelist and reports the live
        detection state.
        """
        return audit_bundle(
            self._source_dir,
            exclude_patterns=self._exclude_patterns,
            detector=self._okf,
        )

    @property
    def exclude_patterns(self) -> list[str] | None:
        """The effective exclusion patterns, including derived ones.

        Contains the configured patterns plus the conventions-file patterns
        derived in ``__init__`` — the list the scanner, reconcile, and
        incremental index paths actually enforce.
        """
        return self._exclude_patterns

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
        :class:`DocumentManager` document-mutation calls block on
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
        No reindex is triggered here: on a cold start build_index() will scan
        the updated working tree, but on a warm restart build_index_async()
        short-circuits in O(1) on the existing FTS sentinel and scans
        nothing. In that case the boot reindex (gated by
        ``config.boot_reindex``, see #1535) is what actually indexes the tree
        this pull just updated — with it disabled, the server accepts the
        index at the resulting HEAD and only reindexes once HEAD moves on.
        """
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.sync_once(self._source_dir)

    def start(self) -> None:
        """Start background tasks for this Vault (e.g. git pull loop).

        Call :meth:`IndexFacet.build_index` (or submit it) **before**
        :meth:`start`. The git pull loop reconciles the index with HEAD after
        every tick through :meth:`reconcile_index_with_head` (#1532); a tick
        that finds the index still unbuilt defers to the next one.
        """
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.start(
            repo_path=self._source_dir,
            pull_interval_s=self._git_pull_interval_s,
            on_tick=self._reconcile_on_tick,
        )

    def _reconcile_on_tick(self) -> None:
        """Pull-loop hook: reconcile the index with HEAD after every tick."""
        self.reconcile_index_with_head(source="pull_loop")

    def git_head(self) -> str | None:
        """Return the working tree's current git HEAD.

        Returns:
            The HEAD revision, or ``None`` without a git strategy or when git
            cannot read one.
        """
        if self._git_strategy is None:
            return None
        try:
            return self._git_strategy.head_sha(self._source_dir)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            logger.debug("git_head_unreadable path=%s", self._source_dir, exc_info=True)
            return None

    def reconcile_index_with_head(self, *, source: str) -> ReconcileOutcome:
        """Reindex when the index does not yet reflect the current git HEAD.

        Level-triggered (#1532): a reindex lost after a pull (the index still
        building, a writer error, a pull reported as not applied although
        HEAD advanced) is retried by the next call instead of waiting for the
        next pull that moves HEAD.  Called by the pull loop on every tick, by
        webhook deliveries and by the ``git_sync`` tool.

        Args:
            source: Which caller asked, for the log line.

        Returns:
            What happened; ``"current"`` without a git strategy.
        """
        if self._head_reconciler is None:
            return "current"
        return self._head_reconciler.reconcile(source=source)

    def mark_index_reconciled(self, head: str | None) -> None:
        """Record that the index reflects *head* without reindexing.

        Used by the server's startup once the boot reindex has covered the
        tree at *head*, or when that reindex is disabled by configuration.

        Args:
            head: The HEAD the index reflects.
        """
        if self._head_reconciler is not None:
            self._head_reconciler.mark_reconciled(head)

    def force_pull(self) -> PullResult | None:
        """Pull from the git remote synchronously.

        Thin public facade over :meth:`~markdown_vault_mcp.git.Syncer.force_pull`
        used by the GitHub webhook handler so the store stays an implementation
        detail.

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

    def end_commit_scope(self, scope: CommitScope) -> None:
        """Close a tool call's commit scope, grouping its writes into one commit.

        Called by
        :class:`~markdown_vault_mcp._commit_scope.CommitScopeMiddleware` when a
        tool call returns. Never blocks: the marker is queued behind the writes
        that call fired, and the dispatcher flushes it in order.

        Args:
            scope: The scope to close.
        """
        self._write_callback.end_scope(scope)

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
    def _vectors(self) -> VectorStore | None:
        """Bridge property: vector index is owned by SearchManager."""
        return self._search_mgr.vectors

    @_vectors.setter
    def _vectors(self, value: VectorStore | None) -> None:
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
