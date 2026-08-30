"""Index build, reindex, and deferred FTS-refresh manager.

Handles FTS index construction and incremental reindexing via
:class:`~markdown_vault_mcp.tracker.ChangeTracker` — all with dependency
injection and no back-reference to :class:`Vault`. The vector-embedding
lifecycle (cold build, convergence, inline embedding, deferred flush,
status) lives in the composed
:class:`~markdown_vault_mcp.managers.embeddings.EmbeddingsManager`
(#1157); :class:`IndexManager` keeps thin delegations so callers see one
unchanged surface.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

import yaml

from markdown_vault_mcp.embed_text import EmbedTextBuilder
from markdown_vault_mcp.fts_index import should_optimize
from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.managers.embeddings import (
    _EMBED_DROPPED,
    _EMBED_KEPT,
    _EMBEDDING_BATCH_SIZE,
    EmbeddingsManager,
)
from markdown_vault_mcp.scanner import (
    CategorizedSkip,
    parse_note,
    parse_note_categorized,
    scan_directory,
)
from markdown_vault_mcp.types import IndexStats, ParsedNote, ReindexResult, SkippedFile
from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.fs import iter_markdown_files

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from markdown_vault_mcp.interfaces import KeywordGraphIndex, VectorStore
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.scanner import ChunkStrategy
    from markdown_vault_mcp.tracker import ChangeTracker

logger = logging.getLogger(__name__)


class IndexManager:
    """Manages index building and reindexing; composes the embedding lifecycle.

    The vector-embedding lifecycle lives in the composed
    :class:`~markdown_vault_mcp.managers.embeddings.EmbeddingsManager`
    (#1157); the embedding-related constructor arguments below are forwarded
    to it, and thin delegations (:meth:`build_embeddings`,
    :meth:`embeddings_status`, :meth:`flush_dirty_embeddings`) keep this
    class's public surface unchanged.

    Args:
        fts: The FTS index to populate and query.
        tracker: Hash-based change tracker for incremental reindexing.
        source_dir: Absolute path to the vault root directory.
        embeddings_path: Base path for ``.npy`` / ``.json`` sidecar files.
            ``None`` disables embedding support.
        embedding_provider: Provider used to generate embeddings.
        chunk_strategy: Strategy for splitting documents into chunks.
        exclude_patterns: Glob patterns for paths to exclude from indexing.
        required_frontmatter: If provided, documents missing any listed
            field are excluded from the index entirely.
        indexed_frontmatter_fields: Frontmatter keys promoted to the
            ``document_tags`` table for structured filtering.
        get_vectors: Callback returning the current
            :class:`~markdown_vault_mcp.interfaces.VectorStore` (or
            ``None``).
        set_vectors: Callback to set the vector index on the owner.
        embed_model_name: Embedding model name in force at build time, or
            ``None`` when no provider is configured. Recorded into FTS meta
            after a successful build so a later warm-restart can detect a
            model change (#649).
        max_chunk_chars_override: The explicit operator char-cap override in
            force at build time, or ``None`` when the cap was derived from the
            model context. Recorded into FTS meta alongside the model as a
            stable warm-restart key (#649).
        title_field: Frontmatter key consulted first when resolving document
            titles; threaded to every ``parse_note``/``scan_directory`` call
            and recorded into FTS meta as a warm-restart key.
        embed_text_builder: Shared
            :class:`~markdown_vault_mcp.embed_text.EmbedTextBuilder` used at
            every embedding site so hot, cold, converge, and flush paths all
            produce identical embedding input. ``None`` constructs the
            default (v1 no-op) builder.
        embedding_batch_size: Maximum number of chunk texts sent to the
            embedding provider per call in the cold-build, convergence, and
            inline-reindex paths. Defaults to the module constant
            ``_EMBEDDING_BATCH_SIZE``.
    """

    def __init__(
        self,
        fts: KeywordGraphIndex,
        tracker: ChangeTracker,
        source_dir: Path,
        *,
        embeddings_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        chunk_strategy: ChunkStrategy,
        exclude_patterns: list[str] | None = None,
        required_frontmatter: list[str] | None = None,
        indexed_frontmatter_fields: list[str] | None = None,
        get_vectors: Callable[[], VectorStore | None],
        set_vectors: Callable[[VectorStore | None], None],
        embed_model_name: str | None = None,
        max_chunk_chars_override: int | None = None,
        title_field: str = "title",
        embed_text_builder: EmbedTextBuilder | None = None,
        embedding_batch_size: int = _EMBEDDING_BATCH_SIZE,
    ) -> None:
        self._fts = fts
        self._tracker = tracker
        self._source_dir = source_dir
        self._chunk_strategy = chunk_strategy
        self._exclude_patterns = exclude_patterns
        self._required_frontmatter = required_frontmatter
        self._indexed_frontmatter_fields: list[str] = indexed_frontmatter_fields or []
        self._get_vectors = get_vectors
        # Chunking provenance recorded into FTS meta after a successful build
        # (#649): the shared chunker's char cap derives from the embedding
        # model (or an explicit override), so a change to either stable input
        # invalidates FTS chunk boundaries.
        self._embed_model_name = embed_model_name
        self._max_chunk_chars_override = max_chunk_chars_override
        self._title_field = title_field
        self._embed_builder = embed_text_builder or EmbedTextBuilder()
        # Composed vector-lifecycle collaborator (#1157). The shared path
        # helpers are injected as callables (per the #736 precedent) so the
        # exclusion and discovery semantics stay defined here.
        self._embeddings = EmbeddingsManager(
            fts=fts,
            source_dir=source_dir,
            embeddings_path=embeddings_path,
            embedding_provider=embedding_provider,
            chunk_strategy=chunk_strategy,
            get_vectors=get_vectors,
            set_vectors=set_vectors,
            title_field=title_field,
            embed_text_builder=self._embed_builder,
            embedding_batch_size=embedding_batch_size,
            is_path_excluded=self._is_path_excluded,
            discover_candidates=self._discover_indexable_candidates,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_path_excluded(self, path: str) -> bool:
        """Check whether *path* matches any configured exclude pattern.

        Args:
            path: Relative POSIX path string.

        Returns:
            ``True`` if the path matches any pattern in
            ``self._exclude_patterns``.
        """
        return is_path_excluded(path, self._exclude_patterns)

    def _discover_indexable_candidates(
        self, *, on_walk_error: Callable[[OSError], None] | None = None
    ) -> list[tuple[Path, str]]:
        """Discover markdown files eligible for indexing, with relative paths.

        Applies the per-file :meth:`_is_path_excluded` filter as the correctness
        layer: ``iter_markdown_files`` prunes only directory-shaped exclude
        patterns, so a file-shaped exclude (such as ``notes/*`` or ``*.draft.md``)
        still needs the per-file check. Excluded files are therefore invisible
        everywhere — neither skip-counted nor recorded in ``skipped_state``
        (#257/#832) — matching ``detect_changes``. Non-files (such as broken
        symlinks) are dropped.

        Returns:
            ``(absolute_path, relative_posix_path)`` pairs for non-excluded
            files, in discovery order.
        """
        candidates: list[tuple[Path, str]] = []
        for abs_path in iter_markdown_files(
            self._source_dir,
            self._exclude_patterns,
            on_error=on_walk_error,
        ):
            if not abs_path.is_file():
                continue
            # iter_markdown_files yields paths built as source_dir / rel, so
            # relative_to always succeeds (no outside-source_dir guard needed).
            rel_str = abs_path.relative_to(self._source_dir).as_posix()
            if self._is_path_excluded(rel_str):
                continue
            candidates.append((abs_path, rel_str))
        return candidates

    def _load_vectors(self) -> VectorStore:
        """Load or return the cached vector store, self-healing corrupt sidecars.

        Thin delegation to
        :meth:`~markdown_vault_mcp.managers.embeddings.EmbeddingsManager._load_vectors`
        (#1157); see there for the full contract.

        Returns:
            The loaded :class:`~markdown_vault_mcp.interfaces.VectorStore`.
        """
        return self._embeddings._load_vectors()

    def _embed_note_inline(self, vectors: VectorStore, note: ParsedNote) -> int:
        """Embed one changed note's chunks inline, resiliently (#930).

        Thin delegation to
        :meth:`~markdown_vault_mcp.managers.embeddings.EmbeddingsManager._embed_note_inline`
        (#1157); see there for the full contract.

        Args:
            vectors: The loaded vector index to mutate.
            note: The parsed note whose chunks to (re-)embed.

        Returns:
            One of the ``_EMBED_OK`` / ``_EMBED_KEPT`` / ``_EMBED_DROPPED``
            disposition codes defined in ``managers/embeddings.py``.
        """
        return self._embeddings._embed_note_inline(vectors, note)

    # ------------------------------------------------------------------
    # Shared indexing helpers
    # ------------------------------------------------------------------

    def _record_skip_hash(
        self,
        skipped: dict[str, str],
        rel_path: str,
        abs_path: Path,
        *,
        event: str,
        surfaced: bool,
    ) -> bool:
        """Record ``compute_file_hash(abs_path)`` into ``skipped[rel_path]``.

        The shared hash-or-retry policy behind skip recording (#775/#802):
        an ``OSError`` is possibly transient, so nothing is recorded and the
        next scan retries the file. When the skip was already *surfaced*
        (a reason was recorded for it), the drop is logged at WARNING so
        the transient loss is observable rather than silent (#802);
        otherwise at DEBUG.

        Args:
            skipped: Target ``{rel_path: content_hash}`` map (mutated).
            rel_path: Vault-relative path of the skipped file.
            abs_path: Absolute path to hash.
            event: Log event prefix (``"build_index"`` / ``"reindex"``).
            surfaced: Whether a skip reason was already recorded for it.

        Returns:
            ``True`` when the hash was recorded, ``False`` on ``OSError``.
        """
        try:
            skipped[rel_path] = compute_file_hash(abs_path)
        except OSError as exc:
            if surfaced:
                logger.warning(
                    "%s_surfaced_skip_dropped path=%s err=%s", event, rel_path, exc
                )
            else:
                logger.debug("%s_skip_hash_failed path=%s err=%s", event, rel_path, exc)
            return False
        return True

    def _tombstone_skip(
        self,
        skip: SkippedFile,
        abs_path: Path,
        *,
        content_hash: str | None = None,
    ) -> None:
        """Record a surfaced skip as an FTS tombstone row (#1129).

        The single skip-recording choke point shared by all three pipelines
        (:meth:`build_index`, :meth:`reindex`, :meth:`process_dirty_paths`),
        so the hash/mtime provenance is computed consistently with
        :meth:`_record_skip_hash`: a transient ``OSError`` while reading the
        file records nothing — the file retries on the next scan, matching
        the tracker's transient policy. Any pre-existing row for the path
        (a stale live row included) is replaced inside the upsert.

        Args:
            skip: The surfaced skip to record.
            abs_path: Absolute path of the skipped file, hashed/stat'ed when
                *content_hash* needs computing.
            content_hash: Hash of the exact bytes that were evaluated, when
                the caller already has it (avoids a second, raceable read).
        """
        try:
            if content_hash is None:
                content_hash = compute_file_hash(abs_path)
            modified_at = abs_path.stat().st_mtime
        except OSError as exc:
            logger.debug("tombstone_skip_read_failed path=%s err=%s", skip.path, exc)
            return
        self._fts.upsert_tombstone(
            skip, content_hash=content_hash, modified_at=modified_at
        )

    def _sync_tombstones(self) -> None:
        """Drop tombstone rows whose skip left the tracker registry (#1129).

        The tracker's ``skip_reasons`` map stays authoritative for what is
        currently skipped; tombstones mirror it in the FTS index. A skipped
        file that is deleted from disk (or newly excluded) leaves the
        registry silently on the next scan — it was never indexed, so no
        ``deleted`` event fires — and its tombstone must not linger as a
        phantom candidate. Called after ``update_state`` so the re-read
        registry reflects this pass.
        """
        reasons = self._tracker.skip_reasons()
        for row in self._fts.list_tombstones():
            if row["path"] not in reasons:
                self._fts.delete_by_path(row["path"])

    def _purge_stale_excluded(
        self,
        vectors: VectorStore | None,
        *,
        keep_paths: set[str] | None = None,
    ) -> tuple[int, VectorStore | None]:
        """Delete indexed rows that now match ``exclude_patterns`` (#255).

        Shared by :meth:`build_index` and :meth:`reindex`, carrying the
        embedding-leak invariant (#255/#257): a purged document leaves both
        the FTS index and the vector sidecar. Stale rows are identified
        BEFORE force-loading the vector sidecar — exclude_patterns is
        non-empty on every default-configured vault now (the derived
        conventions-file patterns), so an unconditional load would defeat
        lazy vector loading (and, on the reindex path, flip the per-note
        loop into inline embedding, changing provider-failure semantics
        from converge-and-skip to raise; see test_embedding_convergence).

        Persisting the vector index, logging, and FTS optimize accounting
        stay with the callers — they deliberately differ between the two
        pipelines.

        Args:
            vectors: The currently-loaded vector index handle, or ``None``.
            keep_paths: Paths (re)indexed this pass — never purged, even
                when they match a pattern (:meth:`build_index` passes the
                fresh scan result).

        Returns:
            Tuple ``(purged_count, vectors)``; *vectors* may have been
            lazily loaded, so callers must adopt the returned handle.
        """
        if not self._exclude_patterns:
            return 0, vectors
        stale_paths = [
            row["path"]
            for row in self._fts.list_notes()
            if (keep_paths is None or row["path"] not in keep_paths)
            and self._is_path_excluded(row["path"])
        ]
        if (
            stale_paths
            and vectors is None
            and self._embeddings.embedding_provider is not None
            and self._embeddings.embeddings_path is not None
        ):
            self._load_vectors()
            vectors = self._get_vectors()

        purged = 0
        for stale_path in stale_paths:
            self._fts.delete_by_path(stale_path)
            if vectors is not None:
                vectors.delete_by_path(stale_path)
            purged += 1
        return purged, vectors

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index.

        If the index already contains documents and *force* is ``False``,
        this is a no-op.  ``force=True`` drops all existing data and rebuilds
        from scratch.

        Note: the caller is responsible for setting any ``_index_built``
        flag after this method returns.

        Args:
            force: When ``True``, drop and rebuild the index unconditionally.

        Returns:
            :class:`~markdown_vault_mcp.types.IndexStats` describing what
            was indexed.
        """
        if force:
            logger.info("build_index(force=True): dropping and rebuilding index")
            for row in self._fts.list_notes():
                self._fts.delete_by_path(row["path"])
            # list_notes() is live-only, so tombstones need their own wipe;
            # the scan below re-creates one per still-skipped candidate.
            for row in self._fts.list_tombstones():
                self._fts.delete_by_path(row["path"])

        logger.info("build_index: scanning %s", self._source_dir)

        skip_reasons: dict[str, dict[str, str]] = {}
        skip_files: dict[str, SkippedFile] = {}

        def _collect_skip(sf: SkippedFile) -> None:
            skip_reasons[sf.path] = {"category": sf.category, "detail": sf.detail}
            skip_files[sf.path] = sf

        notes = list(
            scan_directory(
                self._source_dir,
                required_frontmatter=self._required_frontmatter,
                chunk_strategy=self._chunk_strategy,
                exclude_patterns=self._exclude_patterns,
                on_skip=_collect_skip,
                title_field=self._title_field,
            )
        )

        total_chunks = 0
        errored = 0
        for note in notes:
            try:
                total_chunks += self._fts.upsert_note(note)
            except sqlite3.OperationalError:
                # Database-level failure (e.g. SQLITE_LOCKED retry budget
                # exhausted via FTSIndex._retry_on_locked, #560). Don't
                # silently demote to a per-note warning — propagate so
                # the caller sees the build failed rather than getting a
                # successful-looking IndexStats with everything missing.
                raise
            except Exception:
                errored += 1
                logger.warning(
                    "build_index: failed to index %s",
                    note.path,
                    exc_info=True,
                )

        # Purge stale excluded docs from a persistent index that was built
        # before exclude_patterns were configured (upgrade scenario, #255).
        indexed_paths = {note.path for note in notes}
        docs_before_purge = self._fts.count_documents()
        purged, vectors = self._purge_stale_excluded(
            self._get_vectors(), keep_paths=indexed_paths
        )
        embeddings_path = self._embeddings.embeddings_path
        if purged and vectors is not None and embeddings_path is not None:
            vectors.save(embeddings_path)

        # A bulk purge (e.g. exclude patterns newly configured on an
        # existing index, issue #255) leaves dead FTS5 segments behind;
        # merge them away when the purge crossed the optimize threshold.
        if should_optimize(purged, docs_before_purge):
            self._fts.optimize()

        # Count how many files were skipped (e.g. for required_frontmatter).
        # Discovery prunes excluded *subtrees* and then drops any remaining
        # excluded file via the per-file filter, so excluded files are invisible
        # everywhere — never skip-counted or recorded in skipped_state — matching
        # detect_changes and the "excluded files are invisible" contract
        # (#257/#832).
        candidates = self._discover_indexable_candidates()
        skipped = len(candidates) - len(notes)

        # Resolve vault-wide wikilinks now that all documents are indexed.
        self._fts.resolve_vault_wikilinks()

        # Record skipped files (excluded, missing frontmatter, unparseable)
        # in tracker state too, so the first reindex() — including the boot
        # reconciliation pass after a cold build (#665) — does not re-report
        # them as added and re-log every skip.
        skipped_state: dict[str, str] = {}
        for abs_path, rel_str in candidates:
            if rel_str in indexed_paths:
                continue
            # A failed hash is possibly transient — left unrecorded so the
            # next scan retries the file (#802; see _record_skip_hash).
            recorded = self._record_skip_hash(
                skipped_state,
                rel_str,
                abs_path,
                event="build_index",
                surfaced=rel_str in skip_reasons,
            )
            # Surfaced skips are also recorded as FTS tombstone rows (#1129),
            # reusing the hash just computed so both records cover the same
            # bytes. A transient (unrecorded) hash tombstones nothing —
            # tracker and tombstone stay in lockstep.
            if recorded and rel_str in skip_files:
                self._tombstone_skip(
                    skip_files[rel_str],
                    abs_path,
                    content_hash=skipped_state[rel_str],
                )

        # Update tracker state so reindex() knows the baseline.
        self._tracker.update_state(
            notes, skipped=skipped_state, skip_reasons=skip_reasons
        )
        self._sync_tombstones()

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

        # Record the build provenance (model, char-cap override, curated
        # fields) so a later warm restart can reject the short-circuit on a
        # genuine option change (#649, #927). Paired with the completeness
        # sentinel the coordinator sets after this returns.
        self._fts.set_chunking_meta(
            model=self._embed_model_name,
            max_chunk_chars_override=self._max_chunk_chars_override,
            title_field=self._title_field,
            searchable_fields=",".join(self._embed_builder.searchable_fields),
            indexed_frontmatter_fields=",".join(self._indexed_frontmatter_fields),
        )
        return IndexStats(
            documents_indexed=len(notes) - errored,
            chunks_indexed=total_chunks,
            skipped=max(skipped, 0),
        )

    # ------------------------------------------------------------------
    # Incremental reindex
    # ------------------------------------------------------------------

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Uses :class:`~markdown_vault_mcp.tracker.ChangeTracker` to detect
        which files have been added, modified, or deleted since the last
        scan.  Only changed files are re-parsed and re-indexed.  Files
        matching ``exclude_patterns`` are skipped, and any previously indexed
        documents that now match the patterns are purged.

        Thread-safety: this method runs on the single-owner
        :class:`~markdown_vault_mcp.indexing.IndexWriter` thread (#559), so
        no internal lock is required.  Concurrent
        document mutations route through the writer's
        FIFO queue and serialise against this job.

        Returns:
            :class:`~markdown_vault_mcp.types.ReindexResult` with counts
            of changes applied.
        """
        # Phase 1: scan filesystem (read-only walk + hashing). Excluded
        # subtrees are filtered before hashing so they neither churn the
        # tracker nor get reported as changes (#257).
        changes = self._tracker.detect_changes(
            self._source_dir, exclude_patterns=self._exclude_patterns
        )
        logger.info(
            "reindex: %d added, %d modified, %d deleted, %d unchanged, %d skipped",
            len(changes.added),
            len(changes.modified),
            len(changes.deleted),
            changes.unchanged,
            changes.skipped_unchanged,
        )

        # Pre-parse notes outside the lock to minimise lock hold time.
        parsed, newly_skipped, newly_skip_reasons = self._parse_changed_notes(
            changes.added + changes.modified
        )

        # Phase 2: apply mutations (writer is sole mutator; no lock needed).
        vectors = self._get_vectors()

        # Corpus size before this purge pass, for the optimize threshold.
        docs_before_purge = self._fts.count_documents()

        deleted_purged = 0
        for path in changes.deleted:
            deleted_purged += self._fts.delete_by_path(path)
            if vectors is not None:
                vectors.delete_by_path(path)

        # Purge stale excluded docs (issue #255); the shared helper defers
        # the vector-sidecar load until stale rows are confirmed, keeping
        # the per-note loop below on its lazy-load (converge-and-skip)
        # semantics — see test_embedding_convergence.
        stale_excluded, vectors = self._purge_stale_excluded(vectors)
        if stale_excluded:
            logger.info(
                "reindex: purged %d stale excluded document(s)",
                stale_excluded,
            )

        # A bulk purge leaves dead FTS5 segments behind; merge them away
        # when this pass crossed the optimize threshold (issue #255
        # follow-up: the exclusion upgrade path can purge most of the
        # corpus at boot and bloat the index file with dead segments).
        if should_optimize(deleted_purged + stale_excluded, docs_before_purge):
            self._fts.optimize()

        indexed_added, indexed_modified = self._upsert_parsed_notes(
            parsed, vectors, added_paths=set(changes.added)
        )

        # Persist the vector index only when this pass actually mutated it.
        # An empty-diff reindex (0 added/modified/deleted) that purged no
        # stale-excluded entries touches no vectors, so re-serialising the
        # whole index to disk every run is pure churn — particularly under a
        # file watcher that may fire reindex repeatedly (#720). ``stale_excluded``
        # must be included: purging excluded docs calls ``vectors.delete_by_path``
        # above, mutating the in-memory index even when the file diff is empty.
        vector_index_changed = bool(
            indexed_added or indexed_modified or changes.deleted or stale_excluded
        )
        embeddings_path = self._embeddings.embeddings_path
        if vectors is not None and embeddings_path is not None and vector_index_changed:
            vectors.save(embeddings_path)

        # Re-resolve vault-wide wikilinks.
        self._fts.resolve_vault_wikilinks()

        # Rebuild tracker state from current FTS index contents.
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
        self._tracker.update_state(
            state_notes, skipped=newly_skipped, skip_reasons=newly_skip_reasons
        )
        self._sync_tombstones()

        return ReindexResult(
            added=indexed_added,
            modified=indexed_modified,
            deleted=len(changes.deleted),
            unchanged=changes.unchanged,
            skipped=changes.skipped_unchanged + len(newly_skipped),
        )

    def _parse_changed_notes(
        self, paths: list[str]
    ) -> tuple[list[tuple[str, ParsedNote]], dict[str, str], dict[str, dict[str, str]]]:
        """Parse changed files for :meth:`reindex`, recording surfaced skips.

        Files seen this scan but deliberately not indexed (#665) have their
        content hash recorded in tracker state, so an unchanged skipped
        file is neither re-parsed nor re-reported (or re-logged) on the
        next scan; it is only re-evaluated when its content changes.
        Transient I/O errors are NOT recorded, so those files retry on
        every scan. Excluded paths are filtered inside ``detect_changes``
        (before hashing, #257), so they never reach this loop; the only
        skips recorded here are parse/decode/unexpected failures and
        missing frontmatter, categorized by
        :func:`~markdown_vault_mcp.scanner.parse_note_categorized`.

        Args:
            paths: Vault-relative paths of added + modified files.

        Returns:
            Tuple ``(parsed, newly_skipped, newly_skip_reasons)``:
            successfully parsed ``(path, note)`` pairs, the skipped-file
            hash map, and the skip-reason map for the tracker.
        """
        parsed: list[tuple[str, ParsedNote]] = []
        newly_skipped: dict[str, str] = {}
        newly_skip_reasons: dict[str, dict[str, str]] = {}

        for path in paths:
            abs_path = self._source_dir / path
            outcome = parse_note_categorized(
                abs_path,
                self._source_dir,
                self._chunk_strategy,
                rel_path=path,
                title_field=self._title_field,
                required_frontmatter=self._required_frontmatter,
                log_context="reindex",
            )
            if outcome is None:
                # Possibly transient (already logged) — retry next scan.
                continue
            if isinstance(outcome, CategorizedSkip):
                sf = outcome.skip
                if sf.category == "missing_frontmatter":
                    logger.info(
                        "reindex: skipping %s — missing frontmatter (%s)",
                        path,
                        sf.detail,
                    )
                if outcome.content_hash is not None:
                    # Parsing succeeded (missing frontmatter): record the
                    # hash of the exact bytes that were evaluated — a fresh
                    # disk read could capture newer, now-valid content and
                    # stick the file in the skip state.
                    newly_skipped[path] = outcome.content_hash
                    recorded = True
                else:
                    recorded = self._record_skip_hash(
                        newly_skipped, path, abs_path, event="reindex", surfaced=False
                    )
                if recorded:
                    newly_skip_reasons[path] = {
                        "category": sf.category,
                        "detail": sf.detail,
                    }
                    # Tombstone the skip in FTS (#1129). This *replaces* any
                    # stale live row: a file that becomes unparseable stops
                    # serving its last-good content from search/read instead
                    # of serving it indefinitely. Reuses the hash recorded
                    # above so tracker and tombstone cover the same bytes.
                    self._tombstone_skip(sf, abs_path, content_hash=newly_skipped[path])
                continue
            parsed.append((path, outcome))

        return parsed, newly_skipped, newly_skip_reasons

    def _upsert_parsed_notes(
        self,
        parsed: list[tuple[str, ParsedNote]],
        vectors: VectorStore | None,
        *,
        added_paths: set[str],
    ) -> tuple[int, int]:
        """Upsert parsed notes into FTS (and inline-embed when loaded).

        Inline embedding runs only when the vector sidecar is already
        loaded — :meth:`reindex` deliberately keeps provider failures on
        the converge-and-skip path otherwise (see the purge comments).
        When it does run, a provider failure for one note is logged and
        skipped rather than aborting the whole reindex (#930): the FTS
        refresh must commit for every changed note (so structured filters
        on freshly-edited frontmatter work immediately) even while the
        embedding backend is timing out, and the skipped note's vectors
        converge on the next ``build_embeddings`` pass.

        Args:
            parsed: ``(path, note)`` pairs from :meth:`_parse_changed_notes`.
            vectors: The (possibly lazily-loaded) vector index, or ``None``.
            added_paths: Paths counted as added rather than modified.

        Returns:
            Tuple ``(indexed_added, indexed_modified)``.
        """
        indexed_added = 0
        indexed_modified = 0
        embed_kept = 0
        embed_dropped = 0
        for path, note in parsed:
            try:
                self._fts.upsert_note(note)
            except Exception:
                logger.warning("reindex: failed to index %s", path, exc_info=True)
                continue
            if path in added_paths:
                indexed_added += 1
            else:
                indexed_modified += 1

            if vectors is not None and self._embeddings.embeddings_path is not None:
                outcome = self._embed_note_inline(vectors, note)
                if outcome == _EMBED_KEPT:
                    embed_kept += 1
                elif outcome == _EMBED_DROPPED:
                    embed_dropped += 1
        if embed_kept:
            logger.warning(
                "reindex_inline_embed_failed_docs total=%d "
                "(existing vectors kept; retried on the next build_embeddings)",
                embed_kept,
            )
        if embed_dropped:
            logger.warning(
                "reindex_inline_embed_dropped_docs total=%d "
                "(vectors removed; re-embedded on the next build_embeddings)",
                embed_dropped,
            )
        return indexed_added, indexed_modified

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build or converge the vector index against the FTS index.

        Thin delegation to
        :meth:`~markdown_vault_mcp.managers.embeddings.EmbeddingsManager.build_embeddings`
        (#1157); see there for the full convergence/cold-build contract.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk (e.g. after changing the embedding
                model).

        Returns:
            Number of chunks successfully embedded (``0`` on an
            already-converged index).

        Raises:
            EmbeddingsNotConfiguredError: If ``embedding_provider`` or
                ``embeddings_path`` is not configured.
        """
        return self._embeddings.build_embeddings(force=force)

    def skipped_files(self) -> list[SkippedFile]:
        """Return files dropped from the index for a surfaced reason (#775).

        Reads the tracker's persisted ``skip_reasons`` map and returns one
        :class:`~markdown_vault_mcp.types.SkippedFile` per path, sorted by
        path. Covers the deterministic, non-excluded skips (parse / encoding /
        missing-frontmatter / internal-error); exclude-pattern and
        transient-``OSError`` skips are intentionally absent.

        Returns:
            Path-sorted list of :class:`SkippedFile`. Empty when nothing was
            skipped for a surfaced reason.
        """
        reasons = self._tracker.skip_reasons()
        return [
            SkippedFile(
                path=path,
                category=reason.get("category", ""),
                detail=reason.get("detail", ""),
            )
            for path, reason in sorted(reasons.items())
        ]

    def embeddings_status(self) -> dict[str, Any]:
        """Return status information about the vector index.

        Thin delegation to
        :meth:`~markdown_vault_mcp.managers.embeddings.EmbeddingsManager.embeddings_status`
        (#1157).

        Returns:
            Dict with keys ``provider``, ``chunk_count``, ``path``,
            ``available``.
        """
        return self._embeddings.embeddings_status()

    # ------------------------------------------------------------------
    # Deferred embedding flush
    # ------------------------------------------------------------------

    def process_dirty_paths(self, paths: set[str]) -> None:
        """Re-parse each path and update FTS, skipping per-path failures (#559).

        After all paths are processed, ``resolve_vault_wikilinks()`` runs
        once over the whole vault so newly-added, edited, deleted, and
        renamed documents all leave the link graph consistent — this
        mirrors the behavior that the pre-#559 inline DocumentManager
        callsites delivered (every document mutation ended with
        ``resolve_vault_wikilinks()``).

        Per-path file-read failures (``OSError``, ``UnicodeDecodeError``),
        malformed-frontmatter errors (``yaml.YAMLError``), and chunker
        validation failures (``ValueError``) are caught, logged at
        WARNING, and skipped so a single bad note does not starve the
        rest — matching the coverage in :meth:`flush_dirty_embeddings`.
        A ``yaml.YAMLError`` additionally replaces the note's stale row with
        a ``parse_error`` tombstone, and a note missing required frontmatter
        is tombstoned rather than deleted, so FTS absence keeps meaning
        "not a candidate" (#1129).
        When the parse failure stems from the file disappearing between
        the ``is_file()`` check and ``parse_note()``, the stale FTS row
        is deleted so keyword/hybrid search results stay consistent with
        what :meth:`flush_dirty_embeddings` will do to the vector index.
        Other exceptions — notably ``sqlite3.OperationalError``
        (classified by PR #555's ``IndexUnavailableReason`` discriminator
        at the caller boundary), ``sqlite3.DatabaseError``, ``MemoryError``,
        and programming bugs — propagate to the writer's Future so the
        caller learns instead of seeing a silent skip. The
        ``resolve_vault_wikilinks()`` call runs in a ``finally`` so the
        link graph is always restored to a consistent state, even on
        per-path failures.
        """
        if not paths:
            return
        try:
            for path in paths:
                abs_path = self._source_dir / path
                try:
                    if self._is_path_excluded(path):
                        # Excluded paths (e.g. convention files) never enter
                        # the index, whichever producer marked them dirty —
                        # this is the choke point every dirty path flows
                        # through. Deleting is a no-op when the path was
                        # never indexed and purges stale rows left from
                        # before the exclusion existed.
                        self._fts.delete_by_path(path)
                        continue
                    if abs_path.is_file() and path.endswith(".md"):
                        note = parse_note(
                            abs_path,
                            self._source_dir,
                            self._chunk_strategy,
                            title_field=self._title_field,
                        )
                        missing = [
                            k
                            for k in (self._required_frontmatter or [])
                            if k not in (note.frontmatter or {})
                        ]
                        if missing:
                            # Tombstone rather than delete (#1129): the file
                            # is still a candidate, so its row must stay
                            # present (invisibly) instead of becoming
                            # indistinguishable from a deletion. The parsed
                            # note's own hash/mtime cover the exact bytes
                            # that were evaluated (#888 pattern).
                            self._fts.upsert_tombstone(
                                SkippedFile(
                                    path=path,
                                    category="missing_frontmatter",
                                    detail=f"missing: {missing}",
                                ),
                                content_hash=note.content_hash,
                                modified_at=note.modified_at,
                            )
                            continue
                        self._fts.upsert_note(note)
                    else:
                        self._fts.delete_by_path(path)
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    logger.warning(
                        "process_dirty_paths: skipping %s: %s",
                        path,
                        exc,
                    )
                    # File-disappeared race: parse_note() opened the
                    # file after is_file() succeeded but the file was
                    # then removed (or replaced with something that
                    # raises one of the caught exceptions on read).
                    # Drop the stale FTS row so search results match
                    # what flush_dirty_embeddings will do to the
                    # vector index — otherwise the deleted document
                    # lingers in keyword/hybrid search until a full
                    # reindex.
                    if not abs_path.is_file():
                        try:
                            self._fts.delete_by_path(path)
                        except Exception:
                            logger.exception(
                                "process_dirty_paths: failed to delete "
                                "stale FTS row for %s",
                                path,
                            )
                    continue
                except yaml.YAMLError as exc:
                    logger.warning(
                        "process_dirty_paths: skipping %s (malformed frontmatter): %s",
                        path,
                        exc,
                    )
                    # Tombstone instead of keeping the stale row (#1129): a
                    # note edited into unparseable frontmatter must stop
                    # serving its last-good content. A transient read failure
                    # inside the helper records nothing (row kept; the next
                    # scan retries).
                    self._tombstone_skip(
                        SkippedFile(path=path, category="parse_error", detail=str(exc)),
                        abs_path,
                    )
                    continue
                # sqlite3 / programming-bug exceptions propagate: fail the
                # job so the writer's Future surfaces them (PR #555's
                # reason discriminator handles OperationalError
                # classification at the caller boundary).
        finally:
            # Always restore link-graph consistency, even on per-path failures.
            try:
                self._fts.resolve_vault_wikilinks()
            except Exception:
                logger.exception("process_dirty_paths: resolve_vault_wikilinks failed")

    def flush_dirty_embeddings(self, paths: set[str]) -> None:
        """Re-embed each path in the snapshot and save the vector index once.

        Called only by the IndexWriter's ``FlushDirtyEmbeddings`` runner.
        Thin delegation to
        :meth:`~markdown_vault_mcp.managers.embeddings.EmbeddingsManager.flush_dirty_embeddings`
        (#1157); see there for the two-phase parse/mutate contract.

        Args:
            paths: Paths to re-embed (relative to source_dir).
        """
        self._embeddings.flush_dirty_embeddings(paths)
