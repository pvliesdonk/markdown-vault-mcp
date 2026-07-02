"""Index build, reindex, embedding, and deferred-flush manager.

Handles FTS index construction, incremental reindexing via
:class:`~markdown_vault_mcp.tracker.ChangeTracker`, vector embedding
lifecycle, and the two-phase deferred embedding flush — all with
dependency injection and no back-reference to :class:`Vault`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

import yaml

from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError
from markdown_vault_mcp.fts_index import _derive_folder, should_optimize
from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.managers._vector_loader import load_or_self_heal
from markdown_vault_mcp.scanner import parse_note, scan_directory
from markdown_vault_mcp.types import IndexStats, ParsedNote, ReindexResult, SkippedFile
from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.fs import GLOB_SYMLINK_KWARGS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.scanner import ChunkStrategy
    from markdown_vault_mcp.tracker import ChangeTracker
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

# Maximum chunks per embedding provider call.  Keeps memory bounded during
# build_embeddings() — FastEmbed/ONNX can allocate pathologically large buffers
# when the entire corpus is sent in one batch (see issue #159).
_EMBEDDING_BATCH_SIZE = 4


class IndexManager:
    """Manages index building, reindexing, and embedding lifecycle.

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
            :class:`~markdown_vault_mcp.vector_index.VectorIndex` (or
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
    """

    def __init__(
        self,
        fts: FTSIndex,
        tracker: ChangeTracker,
        source_dir: Path,
        *,
        embeddings_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        chunk_strategy: ChunkStrategy,
        exclude_patterns: list[str] | None = None,
        required_frontmatter: list[str] | None = None,
        indexed_frontmatter_fields: list[str] | None = None,
        get_vectors: Callable[[], VectorIndex | None],
        set_vectors: Callable[[VectorIndex | None], None],
        embed_model_name: str | None = None,
        max_chunk_chars_override: int | None = None,
    ) -> None:
        self._fts = fts
        self._tracker = tracker
        self._source_dir = source_dir
        self._embeddings_path = embeddings_path
        self._embedding_provider = embedding_provider
        self._chunk_strategy = chunk_strategy
        self._exclude_patterns = exclude_patterns
        self._required_frontmatter = required_frontmatter
        self._indexed_frontmatter_fields: list[str] = indexed_frontmatter_fields or []
        self._get_vectors = get_vectors
        self._set_vectors = set_vectors
        # Chunking provenance recorded into FTS meta after a successful build
        # (#649): the shared chunker's char cap derives from the embedding
        # model (or an explicit override), so a change to either stable input
        # invalidates FTS chunk boundaries.
        self._embed_model_name = embed_model_name
        self._max_chunk_chars_override = max_chunk_chars_override

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

    def _require_vectors(self) -> None:
        """Raise :class:`EmbeddingsNotConfiguredError` if embeddings are unconfigured."""
        if self._embedding_provider is None or self._embeddings_path is None:
            raise EmbeddingsNotConfiguredError(
                "Embeddings require both 'embedding_provider' and "
                "'embeddings_path' to be configured."
            )

    def _load_vectors(self) -> VectorIndex:
        """Load or return the cached VectorIndex, self-healing corrupt sidecars.

        Delegates to
        :func:`markdown_vault_mcp.managers._vector_loader.load_or_self_heal`;
        see there for the full self-heal contract.

        Returns:
            A :class:`~markdown_vault_mcp.vector_index.VectorIndex` instance.

        Raises:
            RuntimeError: If called without a prior ``_require_vectors()``
                (``_embedding_provider`` or ``_embeddings_path`` is ``None``).
            ValueError: If a self-heal rebuild fails to produce a usable index.
        """
        vectors = self._get_vectors()
        if vectors is not None:
            return vectors
        if self._embeddings_path is None or self._embedding_provider is None:
            raise RuntimeError(
                "_require_vectors() must be called before _load_vectors()"
            )
        return load_or_self_heal(
            embeddings_path=self._embeddings_path,
            embedding_provider=self._embedding_provider,
            get_vectors=self._get_vectors,
            set_vectors=self._set_vectors,
            rebuild=lambda: self.build_embeddings(force=True),
            logger=logger,
        )

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

        logger.info("build_index: scanning %s", self._source_dir)

        skip_reasons: dict[str, dict[str, str]] = {}

        def _collect_skip(sf: SkippedFile) -> None:
            skip_reasons[sf.path] = {"category": sf.category, "detail": sf.detail}

        notes = list(
            scan_directory(
                self._source_dir,
                required_frontmatter=self._required_frontmatter,
                chunk_strategy=self._chunk_strategy,
                exclude_patterns=self._exclude_patterns,
                on_skip=_collect_skip,
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
        if self._exclude_patterns:
            vectors = self._get_vectors()
            if (
                vectors is None
                and self._embedding_provider is not None
                and self._embeddings_path is not None
            ):
                self._load_vectors()
                vectors = self._get_vectors()

            rows = self._fts.list_notes()
            purged = 0
            for row in rows:
                if row["path"] not in indexed_paths and self._is_path_excluded(
                    row["path"]
                ):
                    self._fts.delete_by_path(row["path"])
                    if vectors is not None:
                        vectors.delete_by_path(row["path"])
                    purged += 1

            if purged and vectors is not None and self._embeddings_path is not None:
                vectors.save(self._embeddings_path)

            # A bulk purge (e.g. exclude patterns newly configured on an
            # existing index, issue #255) leaves dead FTS5 segments behind;
            # merge them away when the purge crossed the optimize threshold.
            if should_optimize(purged, len(rows)):
                self._fts.optimize()

        # Count how many files were skipped due to required_frontmatter.
        all_files = list(self._source_dir.glob("**/*.md", **GLOB_SYMLINK_KWARGS))
        skipped = len(all_files) - len(notes)

        # Resolve vault-wide wikilinks now that all documents are indexed.
        self._fts.resolve_vault_wikilinks()

        # Record skipped files (excluded, missing frontmatter, unparseable)
        # in tracker state too, so the first reindex() — including the boot
        # reconciliation pass after a cold build (#665) — does not re-report
        # them as added and re-log every skip.
        skipped_state: dict[str, str] = {}
        for abs_path in all_files:
            if not abs_path.is_file():
                continue
            try:
                rel_str = abs_path.relative_to(self._source_dir).as_posix()
            except ValueError:
                # Symlink target outside the vault — mirror detect_changes.
                logger.warning("File outside source_dir, skipping: %s", abs_path)
                continue
            if rel_str in indexed_paths:
                continue
            try:
                skipped_state[rel_str] = compute_file_hash(abs_path)
            except OSError as exc:
                # Possibly transient — leave unrecorded so the next scan
                # retries the file. If the path was an already-surfaced skip,
                # its reason is clamped away by update_state; warn so the
                # transient loss is observable rather than silent (#802).
                if rel_str in skip_reasons:
                    logger.warning(
                        "build_index_surfaced_skip_dropped path=%s err=%s",
                        rel_str,
                        exc,
                    )
                else:
                    logger.debug(
                        "build_index_skip_hash_failed path=%s err=%s", rel_str, exc
                    )

        # Update tracker state so reindex() knows the baseline.
        self._tracker.update_state(
            notes, skipped=skipped_state, skip_reasons=skip_reasons
        )

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

        # Record the embedding model + explicit override so a later warm
        # restart can reject the short-circuit on a model/override change
        # (#649). Paired with the completeness sentinel the coordinator sets
        # after this returns.
        self._fts.set_chunking_meta(
            model=self._embed_model_name,
            max_chunk_chars_override=self._max_chunk_chars_override,
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
        write/edit/delete/rename operations route through the writer's
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

        # Files seen this scan but deliberately not indexed (#665).  Recording
        # their content hash in tracker state means an unchanged skipped file
        # is neither re-parsed nor re-reported (or re-logged) on the next
        # scan; it is only re-evaluated when its content changes.  Transient
        # I/O errors are NOT recorded, so those files retry on every scan.
        newly_skipped: dict[str, str] = {}
        newly_skip_reasons: dict[str, dict[str, str]] = {}

        def _record_skip(
            rel_path: str, abs_file: Path, category: str, detail: str
        ) -> None:
            """Record a deterministically skipped file's hash and reason (#775)."""
            try:
                newly_skipped[rel_path] = compute_file_hash(abs_file)
            except OSError as exc:
                # File vanished/unreadable since parse — treat as transient:
                # record neither the hash nor the reason so it retries.
                logger.debug("reindex_skip_hash_failed path=%s err=%s", rel_path, exc)
                return
            newly_skip_reasons[rel_path] = {"category": category, "detail": detail}

        # Pre-parse notes outside the lock to minimise lock hold time.
        parsed: list[tuple[str, ParsedNote]] = []
        # Excluded paths are filtered inside detect_changes (before hashing,
        # #257), so they never reach this loop; the only skips recorded here
        # are parse/decode/unexpected failures and missing-frontmatter below.
        for path in changes.added + changes.modified:
            abs_path = self._source_dir / path

            try:
                note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
            except OSError as exc:
                # Possibly transient — do not record; retry on the next scan.
                logger.warning("reindex: skipping %s — %s", path, exc)
                continue
            except UnicodeDecodeError as exc:
                logger.warning("reindex: skipping %s — %s", path, exc)
                _record_skip(path, abs_path, "encoding_error", str(exc))
                continue
            except yaml.YAMLError as exc:
                logger.warning("reindex: skipping %s — parse error (%s)", path, exc)
                _record_skip(path, abs_path, "parse_error", str(exc))
                continue
            except Exception as exc:
                logger.error(
                    "reindex: skipping %s — unexpected error (%s)",
                    path,
                    exc,
                    exc_info=True,
                )
                _record_skip(path, abs_path, "internal_error", str(exc))
                continue

            if self._required_frontmatter:
                missing = [
                    f for f in self._required_frontmatter if f not in note.frontmatter
                ]
                if missing:
                    logger.info(
                        "reindex: skipping %s — missing frontmatter: %s",
                        path,
                        missing,
                    )
                    newly_skipped[path] = note.content_hash
                    newly_skip_reasons[path] = {
                        "category": "missing_frontmatter",
                        "detail": f"missing: {missing}",
                    }
                    continue

            parsed.append((path, note))

        # Phase 2: apply mutations (writer is sole mutator; no lock needed).
        vectors = self._get_vectors()

        # Corpus size before this purge pass, for the optimize threshold.
        docs_before_purge = self._fts.count_documents()

        deleted_purged = 0
        for path in changes.deleted:
            deleted_purged += self._fts.delete_by_path(path)
            if vectors is not None:
                vectors.delete_by_path(path)

        # Purge stale excluded docs (issue #255).
        stale_excluded = 0
        if self._exclude_patterns:
            if (
                vectors is None
                and self._embedding_provider is not None
                and self._embeddings_path is not None
            ):
                self._load_vectors()
                vectors = self._get_vectors()

            for row in self._fts.list_notes():
                if self._is_path_excluded(row["path"]):
                    self._fts.delete_by_path(row["path"])
                    if vectors is not None:
                        vectors.delete_by_path(row["path"])
                    stale_excluded += 1
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

            if vectors is not None and self._embeddings_path is not None:
                vectors.delete_by_path(note.path)
                texts = [c.content for c in note.chunks]
                meta = [
                    {
                        "path": note.path,
                        "title": note.title,
                        "folder": _derive_folder(note.path),
                        "heading": c.heading,
                        "content": c.content,
                        "start_line": c.start_line,
                    }
                    for c in note.chunks
                ]
                if texts:
                    vectors.add(texts, meta)

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
        if (
            vectors is not None
            and self._embeddings_path is not None
            and vector_index_changed
        ):
            vectors.save(self._embeddings_path)

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

        return ReindexResult(
            added=indexed_added,
            modified=indexed_modified,
            deleted=len(changes.deleted),
            unchanged=changes.unchanged,
            skipped=changes.skipped_unchanged + len(newly_skipped),
        )

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build or converge the vector index against the FTS index.

        Without ``force``, a non-empty persisted vector index is diffed
        against the chunks currently in the FTS ``sections`` table and
        reconciled (#665): chunks present in the FTS index but missing
        from the vector index are embedded and added, vectors for
        documents no longer in the FTS index are removed, and documents
        whose indexed content changed are re-embedded.  After every call
        the vector index mirrors the FTS chunk set, so external changes
        picked up by the boot reconciliation reindex (while no vector
        index was loaded) cannot accumulate as permanent semantic-search
        drift.  Work scales with the size of the diff, not the size of
        the vault.  An empty vector index falls through to the full cold
        build below.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk (e.g. after changing the embedding
                model).

        Returns:
            Number of chunks successfully embedded. Any provider exception on
            a batch (a token-context rejection being the motivating case, but
            also transient API/network errors) is logged and the batch
            skipped, so this may be less than the total number of chunks
            attempted; if every batch is skipped, ``0`` is returned and no
            vectors are saved.  On the convergence path this counts only the
            newly embedded chunks — a fully converged index returns ``0``.

        Raises:
            EmbeddingsNotConfiguredError: If ``embedding_provider`` or
                ``embeddings_path`` is not configured. A ``ValueError``
                subclass, so callers may catch either; narrow to it to let
                genuine internal ``ValueError``s surface (#774).
        """
        self._require_vectors()

        # _require_vectors() guarantees these are not None.
        if self._embeddings_path is None or self._embedding_provider is None:
            raise RuntimeError(
                "_require_vectors() must be called before build_embeddings()"
            )

        from markdown_vault_mcp.vector_index import VectorIndex

        if force:
            vi = VectorIndex(self._embedding_provider)
            self._set_vectors(vi)
        else:
            self._load_vectors()
            vectors = self._get_vectors()
            if vectors is None:
                raise ValueError("Failed to load vector index after _load_vectors()")
            if vectors.count > 0:
                return self._converge_embeddings(vectors)
            # Empty index — fall through to the full cold build.

        rows = self._fts.list_notes()
        num_notes = len(rows)
        logger.info("build_embeddings: parsing %d notes into chunks", num_notes)
        texts: list[str] = []
        meta: list[dict[str, Any]] = []

        for i, row in enumerate(rows, 1):
            path = row["path"]
            title = row["title"]
            folder = row["folder"]
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
                        "start_line": chunk.start_line,
                    }
                )
            if i % 100 == 0 or i == num_notes:
                logger.info(
                    "build_embeddings: parsed %d/%d notes (%d chunks so far)",
                    i,
                    num_notes,
                    len(texts),
                )

        vectors = self._get_vectors()
        if vectors is None:
            raise ValueError("Vector index unexpectedly None after initialisation")
        total = len(texts)
        # Per-batch detail at DEBUG (loud, opt-in via -v); INFO carries only a
        # bounded decile heartbeat with an ETA so operators can track progress
        # without thousands of lines per build (#311).
        started = time.monotonic()
        last_decile = 0
        # A batch that exceeds the model's token context (e.g. a strict
        # provider returning HTTP 400) is logged and skipped rather than
        # aborting the whole build; ``embedded`` tracks chunks actually
        # vectorised, while decile progress runs over attempted chunks (#649).
        embedded = 0
        for start in range(0, total, _EMBEDDING_BATCH_SIZE):
            end = min(start + _EMBEDDING_BATCH_SIZE, total)
            try:
                vectors.add(texts[start:end], meta[start:end])
                embedded += end - start
            except Exception as exc:
                # Broad by design: providers raise heterogeneous types for an
                # oversized batch (RuntimeError, httpx errors, ...). Log the
                # traceback so a genuinely unexpected error caught here is still
                # diagnosable rather than reduced to a one-line message.
                logger.warning(
                    "build_embeddings_skip_batch chunks=%d-%d of %d err=%s",
                    start + 1,
                    end,
                    total,
                    exc,
                    exc_info=True,
                )
            logger.debug(
                "build_embeddings: embedded chunks %d-%d of %d",
                start + 1,
                end,
                total,
            )
            decile = end * 10 // total
            if decile > last_decile:
                last_decile = decile
                elapsed = time.monotonic() - started
                rate = end / elapsed if elapsed > 0 else 0.0
                remaining = (total - end) / rate if rate > 0 else 0.0
                logger.info(
                    "build_embeddings: %d%% (%d/%d chunks, %.0fs elapsed, ~%.0fs remaining)",
                    decile * 10,
                    end,
                    total,
                    elapsed,
                    remaining,
                )

        if embedded > 0:
            vectors.save(self._embeddings_path)
            logger.info("build_embeddings: embedded and saved %d chunks", embedded)
        elif total > 0:
            # Every batch was skipped (e.g. provider down for the whole build,
            # or a dimension mismatch). Surface it loudly rather than as the
            # benign "nothing to embed" so an operator can tell an empty vault
            # apart from a wholesale embedding failure (#649).
            logger.warning(
                "build_embeddings_all_batches_failed total=%d (no vectors saved)",
                total,
            )
        else:
            logger.info("build_embeddings: nothing to embed")
        return embedded

    def _converge_embeddings(self, vectors: VectorIndex) -> int:
        """Reconcile a non-empty vector index with the FTS chunk set (#665).

        Chunk identity is the ``(title, heading, content)`` multiset per
        document path — exactly the metadata stored alongside each vector
        row, so the diff needs no file re-parsing.  Documents present only
        in the vector index (deleted or newly excluded while no server
        ran) lose their vectors; documents missing from it are embedded;
        documents whose chunk multiset differs in any way (modified
        content, changed title, re-chunked boundaries) are re-embedded in
        full.  The sidecar is saved only when something actually changed.

        A provider failure while embedding one document's chunks skips
        that document — its existing vectors are left intact — and the
        remaining documents still converge; the next call retries
        (mirroring the cold build's per-batch resilience, #649).  This is
        also what makes a failed boot ``BuildEmbeddings`` job self-heal:
        the drift it left behind is just a larger diff for the next run.

        Thread-safety: runs on the single-owner
        :class:`~markdown_vault_mcp.indexing.IndexWriter` thread via
        :meth:`build_embeddings`, so no internal lock is required.

        Args:
            vectors: The loaded, non-empty vector index to reconcile.

        Returns:
            Number of chunks newly embedded (``0`` on an already-converged
            index).
        """
        # build_embeddings() ran _require_vectors() before dispatching here.
        if self._embeddings_path is None or self._embedding_provider is None:
            raise RuntimeError(
                "_require_vectors() must be called before _converge_embeddings()"
            )

        def _signature(
            rows: list[dict[str, Any]],
        ) -> Counter[tuple[Any, Any, Any, Any]]:
            # start_line participates so that line-shift-only edits (content
            # identical, positions moved) still refresh vector metadata; the
            # cost is re-embedding that one doc's chunks, and embeddings are
            # deterministic for identical content.
            return Counter(
                (
                    r.get("title"),
                    r.get("heading"),
                    r.get("content"),
                    r.get("start_line"),
                )
                for r in rows
            )

        fts_by_path: dict[str, list[dict[str, Any]]] = {}
        for row in self._fts.list_chunks():
            fts_by_path.setdefault(row["path"], []).append(row)
        vec_by_path = vectors.chunks_by_path()

        stale_paths = [p for p in vec_by_path if p not in fts_by_path]
        missing_paths: list[str] = []
        refresh_paths: list[str] = []
        up_to_date = 0
        for path, rows in fts_by_path.items():
            existing = vec_by_path.get(path)
            if existing is None:
                missing_paths.append(path)
            elif _signature(rows) != _signature(existing):
                refresh_paths.append(path)
            else:
                up_to_date += len(rows)

        added = 0
        removed = 0
        failed = 0
        # Embed per document, in bounded batches (#159), so a provider
        # failure affects exactly one document: its old vectors stay
        # untouched and every other document still converges.
        for path in missing_paths + refresh_paths:
            rows = fts_by_path[path]
            texts = [r["content"] for r in rows]
            raw: list[list[float]] = []
            try:
                for start in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
                    raw.extend(
                        self._embedding_provider.embed(
                            texts[start : start + _EMBEDDING_BATCH_SIZE]
                        )
                    )
            except Exception as exc:
                # Broad by design: providers raise heterogeneous types for
                # an oversized batch or transient outage (RuntimeError,
                # httpx errors, ...). Keep the traceback diagnosable.
                failed += len(texts)
                logger.warning(
                    "build_embeddings_converge_skip_doc path=%s chunks=%d err=%s",
                    path,
                    len(texts),
                    exc,
                    exc_info=True,
                )
                continue
            removed += vectors.delete_by_path(path)
            added += vectors.add_vectors(raw, rows)

        for path in stale_paths:
            removed += vectors.delete_by_path(path)

        if added or removed:
            vectors.save(self._embeddings_path)

        if failed:
            logger.warning(
                "build_embeddings_converge_failed_chunks total=%d "
                "(existing vectors kept; retried on the next run)",
                failed,
            )
        logger.info(
            "build_embeddings_converged added=%d removed=%d up_to_date=%d",
            added,
            removed,
            up_to_date,
        )
        return added

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

        vectors = self._get_vectors()
        count = 0
        if vectors is not None:
            count = vectors.count
        else:
            # Derive sidecar paths the way VectorIndex.load/save do
            # (Path.with_suffix), so an EMBEDDINGS_PATH that carries an
            # extension resolves to the real files instead of {path}.npy.npy
            # and misreporting chunk_count=0 (#736).
            npy_path = self._embeddings_path.with_suffix(".npy")
            if npy_path.exists():
                json_path = self._embeddings_path.with_suffix(".json")
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
    # Deferred embedding flush
    # ------------------------------------------------------------------

    def process_dirty_paths(self, paths: set[str]) -> None:
        """Re-parse each path and update FTS, skipping per-path failures (#559).

        After all paths are processed, ``resolve_vault_wikilinks()`` runs
        once over the whole vault so newly-added, edited, deleted, and
        renamed documents all leave the link graph consistent — this
        mirrors the behavior that the pre-#559 inline DocumentManager
        callsites delivered (write/edit/delete/rename each ended with
        ``resolve_vault_wikilinks()``).

        Per-path file-read failures (``OSError``, ``UnicodeDecodeError``),
        malformed-frontmatter errors (``yaml.YAMLError``), and chunker
        validation failures (``ValueError``) are caught, logged at
        WARNING, and skipped so a single bad note does not starve the
        rest — matching the coverage in :meth:`flush_dirty_embeddings`.
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
                    if abs_path.is_file() and path.endswith(".md"):
                        note = parse_note(
                            abs_path, self._source_dir, self._chunk_strategy
                        )
                        if self._required_frontmatter and not all(
                            k in (note.frontmatter or {})
                            for k in self._required_frontmatter
                        ):
                            self._fts.delete_by_path(path)
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
        The writer thread is the sole mutator of the vector index, so this
        method runs without any internal lock.

        Per-path parse failures (``UnicodeDecodeError``, ``OSError``,
        ``yaml.YAMLError``, ``ValueError`` from the chunk strategy) DO
        NOT delete existing vectors for that path — the failed entry is
        skipped entirely in Phase 2, leaving prior embeddings intact.
        Only successful re-parses with empty chunk lists (note exists
        but contains no embeddable content), or paths that have been
        removed/are no longer ``.md`` files, result in vector deletion.
        Other exceptions (sqlite3 errors, programming bugs,
        embedding-provider errors) propagate to the writer's Future.

        Args:
            paths: Paths to re-embed (relative to source_dir).
        """
        if self._embeddings_path is None or self._embedding_provider is None:
            return
        if not paths:
            return

        # Phase 1: parse and embed.  Each entry is
        # (path, vectors_or_None, meta_or_None, failed_flag).
        # failed=True means parse failed → Phase 2 must NOT delete the
        # existing vectors for this path (silent-data-loss guard).
        pre_embedded: list[
            tuple[str, list[list[float]] | None, list[dict[str, Any]] | None, bool]
        ] = []
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
                            "start_line": c.start_line,
                        }
                        for c in note.chunks
                    ]
                    if texts:
                        raw_vecs = self._embedding_provider.embed(texts)
                        pre_embedded.append((path, raw_vecs, meta, False))
                    else:
                        # Successful parse, no chunks → delete is correct.
                        pre_embedded.append((path, None, None, False))
                except (UnicodeDecodeError, OSError, yaml.YAMLError, ValueError) as exc:
                    logger.warning("Deferred embedding failed for %s: %s", path, exc)
                    # Parse failed → leave existing vectors intact.
                    pre_embedded.append((path, None, None, True))
            else:
                # File removed or not a .md file → delete is correct.
                pre_embedded.append((path, None, None, False))

        # Phase 2: mutate vector index (writer is sole mutator; no lock needed).
        # Short-circuit when every entry failed parse: there is nothing
        # to mutate, and calling _load_vectors() in that case could
        # trigger a full vector rebuild via its
        # VectorIndexCompatibilityError handler — an expensive no-op
        # for a flush that has no real work to do.
        if not any(not entry[3] for entry in pre_embedded):
            return
        vectors = self._load_vectors()
        for entry in pre_embedded:
            entry_path, entry_vecs, entry_meta, entry_failed = entry
            if entry_failed:
                # Parse failure → keep prior embeddings; do not touch vectors.
                continue
            vectors.delete_by_path(entry_path)
            if entry_vecs is not None and entry_meta:
                vectors.add_vectors(entry_vecs, entry_meta)
        vectors.save(self._embeddings_path)
        logger.debug("Flushed deferred embeddings for %d paths", len(paths))
