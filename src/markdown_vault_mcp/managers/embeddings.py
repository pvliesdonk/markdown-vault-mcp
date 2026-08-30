"""Vector-embedding lifecycle manager, composed into IndexManager (#1157).

Owns the vector sidecar's whole lifecycle — cold build, boot convergence,
inline re-embedding during reindex, the deferred two-phase flush, and status
reporting. Extracted verbatim from
:class:`~markdown_vault_mcp.managers.index.IndexManager` so the FTS pipeline
and the embedding pipeline each have a single reason to change; the shared
helpers that stayed behind (`_is_path_excluded`,
`_discover_indexable_candidates`) are injected as callables, following the
#736 `_vector_loader` precedent.
"""

from __future__ import annotations

import json
import logging
import stat as stat_module
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

import yaml

from markdown_vault_mcp.embed_text import is_embeddable
from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError
from markdown_vault_mcp.fts_index import _derive_folder
from markdown_vault_mcp.managers._vector_loader import load_or_self_heal
from markdown_vault_mcp.scanner import parse_note

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from markdown_vault_mcp.embed_text import EmbedTextBuilder
    from markdown_vault_mcp.interfaces import KeywordIndex, VectorStore
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.scanner import ChunkStrategy
    from markdown_vault_mcp.types import ParsedNote

logger = logging.getLogger(__name__)

# Maximum chunks per embedding provider call.  Keeps memory bounded during
# build_embeddings() — FastEmbed/ONNX can allocate pathologically large buffers
# when the entire corpus is sent in one batch (see issue #159).
_EMBEDDING_BATCH_SIZE = 4

# Disposition codes returned by EmbeddingsManager._embed_note_inline so the
# caller can log an accurate aggregate: a provider failure is caught before the
# index is touched (old vectors kept), whereas a dimension mismatch is caught
# after delete_by_path has run (old vectors removed) — the two must not share a
# "vectors kept" message (#935).
_EMBED_OK = 0  # embedded (or nothing to embed)
_EMBED_KEPT = 1  # provider failed before mutation — existing vectors preserved
_EMBED_DROPPED = 2  # dimension mismatch after delete — existing vectors removed


class EmbeddingsManager:
    """Manages the vector-embedding lifecycle for one vault.

    Composed into :class:`~markdown_vault_mcp.managers.index.IndexManager`,
    which keeps thin delegations so the split is invisible outside
    ``managers/``. The FTS index is the source of truth for what to embed;
    the shared path helpers are injected as callables so the exclusion and
    discovery semantics stay defined in one place.

    Args:
        fts: The FTS index queried as the source of truth for what to embed.
        source_dir: Absolute path to the vault root directory.
        embeddings_path: Base path for ``.npy`` / ``.json`` sidecar files.
            ``None`` disables embedding support.
        embedding_provider: Provider used to generate embeddings.
        chunk_strategy: Strategy for splitting documents into chunks.
        get_vectors: Callback returning the current
            :class:`~markdown_vault_mcp.interfaces.VectorStore` (or
            ``None``).
        set_vectors: Callback to set the vector index on the owner.
        title_field: Frontmatter key consulted first when resolving document
            titles; threaded to every ``parse_note`` call.
        embed_text_builder: Shared
            :class:`~markdown_vault_mcp.embed_text.EmbedTextBuilder` used at
            every embedding site so hot, cold, converge, and flush paths all
            produce identical embedding input.
        embedding_batch_size: Maximum number of chunk texts sent to the
            embedding provider per call in the cold-build, convergence, and
            inline-reindex paths. Defaults to the module constant
            ``_EMBEDDING_BATCH_SIZE``.
        is_path_excluded: Injected exclusion check (the owner's
            ``_is_path_excluded``), so both pipelines share one policy.
        discover_candidates: Injected source-tree discovery (the owner's
            ``_discover_indexable_candidates``), used by the authoritative
            empty-build check's source walk.
    """

    def __init__(
        self,
        *,
        fts: KeywordIndex,
        source_dir: Path,
        embeddings_path: Path | None,
        embedding_provider: EmbeddingProvider | None,
        chunk_strategy: ChunkStrategy,
        get_vectors: Callable[[], VectorStore | None],
        set_vectors: Callable[[VectorStore | None], None],
        title_field: str,
        embed_text_builder: EmbedTextBuilder,
        embedding_batch_size: int = _EMBEDDING_BATCH_SIZE,
        is_path_excluded: Callable[[str], bool],
        discover_candidates: Callable[..., list[tuple[Path, str]]],
    ) -> None:
        self._fts = fts
        self._source_dir = source_dir
        self._embeddings_path = embeddings_path
        self._embedding_provider = embedding_provider
        self._chunk_strategy = chunk_strategy
        self._get_vectors = get_vectors
        self._set_vectors = set_vectors
        self._title_field = title_field
        self._embed_builder = embed_text_builder
        self._embedding_batch_size = embedding_batch_size
        self._is_path_excluded = is_path_excluded
        self._discover_candidates = discover_candidates

    # ------------------------------------------------------------------
    # Shared-path accessors (read by IndexManager's FTS pipeline)
    # ------------------------------------------------------------------

    @property
    def embeddings_path(self) -> Path | None:
        """Base path for the vector sidecar files (``None`` when disabled)."""
        return self._embeddings_path

    @property
    def embedding_provider(self) -> EmbeddingProvider | None:
        """The configured embedding provider (``None`` when disabled)."""
        return self._embedding_provider

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed_inputs(
        self,
        *,
        path: str,
        title: str,
        folder: str,
        frontmatter: dict[str, Any],
        chunks: list[Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Build the (texts, metadata) pair for one document's chunks.

        Every embedding site (hot reindex, cold build, deferred flush) goes
        through this helper so the shared
        :class:`~markdown_vault_mcp.embed_text.EmbedTextBuilder` is applied
        uniformly — a site that embedded raw content while another embedded
        enriched text would make the boot convergence pass "heal" enriched
        vectors back to plain. ``meta['content']`` stays the raw chunk
        content (snippets/RRF display it); the fields preamble is stored
        under the ``preamble`` key (``""`` for non-first chunks).

        Args:
            path: Vault-relative document path.
            title: Resolved document title.
            folder: Pre-derived folder string.
            frontmatter: Parsed frontmatter dict.
            chunks: The document's chunks, in document order.

        Returns:
            ``(texts, metadata)`` lists of equal length, one entry per
            *embeddable* chunk. Chunks whose built text is blank are
            omitted (#1087), so both lists may be shorter than *chunks* —
            and empty for a body-less note, which every caller already
            handles as "no vectors for this path".
        """
        fields_text = self._embed_builder.fields_text(frontmatter)
        texts: list[str] = []
        meta: list[dict[str, Any]] = []
        for i, chunk in enumerate(chunks):
            is_first = i == 0
            text = self._embed_builder.build(
                title=title,
                heading=chunk.heading,
                content=chunk.content,
                fields_text=fields_text,
                is_first_chunk=is_first,
            )
            if not is_embeddable(text):
                # Nothing to embed, and sending it is actively harmful: an
                # empty input string is a hard HTTP 400 that fails the whole
                # batch (#1087). The cold build batches across documents, so
                # one body-less note would take up to _embedding_batch_size
                # unrelated chunks down with it. The chunk stays keyword-
                # searchable through FTS; only its vector is skipped.
                #
                # ``is_first`` deliberately stays bound to the document's
                # real chunk 0 rather than shifting to the first *surviving*
                # chunk: the preamble belongs to chunk 0, and a chunk 0 that
                # carries a preamble is never blank in the first place.
                continue
            texts.append(text)
            meta.append(
                {
                    "path": path,
                    "title": title,
                    "folder": folder,
                    "heading": chunk.heading,
                    "content": chunk.content,
                    "start_line": chunk.start_line,
                    "preamble": fields_text if is_first else "",
                }
            )
        return texts, meta

    def _require_vectors(self) -> None:
        """Raise :class:`EmbeddingsNotConfiguredError` if embeddings are unconfigured."""
        if self._embedding_provider is None or self._embeddings_path is None:
            raise EmbeddingsNotConfiguredError(
                "Embeddings require both 'embedding_provider' and "
                "'embeddings_path' to be configured."
            )

    def _empty_embedding_build_is_authoritative(self, indexed_paths: set[str]) -> bool:
        """Return whether an empty FTS-derived embedding build is complete.

        A forced FTS rebuild can omit a source that failed parsing, leaving no
        row for :meth:`build_embeddings` to re-parse. Before replacing an old
        sidecar with an empty one, inspect only source candidates absent from
        FTS: since #1129, every deliberate skip (any surfaced category) leaves
        a tombstone row, so a candidate is accounted for iff it has one — no
        per-candidate re-parse needed. A candidate with neither a live row
        nor a tombstone is an FTS-population gap, and an incomplete source
        walk can hide candidates entirely (walk incompleteness is not
        representable as tombstones), so both still veto the empty save.
        """
        walk_incomplete = False

        def _record_walk_error(_exc: OSError) -> None:
            nonlocal walk_incomplete
            walk_incomplete = True

        candidates = self._discover_candidates(on_walk_error=_record_walk_error)
        if walk_incomplete:
            logger.warning("build_embeddings_source_walk_incomplete (no vectors saved)")
            return False

        for _abs_path, path in candidates:
            if path in indexed_paths:
                continue
            if self._fts.get_tombstone(path) is not None:
                # Deliberately skipped — the absence is recorded, not a gap.
                continue
            logger.warning(
                "build_embeddings_unindexed_source path=%s (no vectors saved)",
                path,
            )
            return False
        return True

    def _confirm_stale_vector_paths(self, stale_paths: list[str]) -> list[str]:
        """Return the stale sidecar paths whose FTS absence is authoritative.

        A path present in the vector sidecar but absent from the FTS index is
        reclaimable only when the absence means "not an index candidate".
        Since #1129 that is a direct check: a deliberately skipped candidate
        has a tombstone row, so a path is reclaimable iff it has a tombstone
        OR its source is no longer a candidate at all (excluded by pattern,
        or gone from disk — one ``stat`` replaces the former full
        re-parse/walk). The conservative default stands (#1130): a source
        still on disk with neither a live row nor a tombstone is a build gap
        — the FTS gap, not the source, is the anomaly — so its vectors are
        kept for the pass that next sees it.

        An unavailable source *root* (unmounted volume, not-yet-populated
        checkout) would make every per-path stat read as "gone" and wipe the
        whole sidecar, so it confirms nothing — the cheap root check replaces
        the former walk-incompleteness guard for this method.

        Args:
            stale_paths: Sidecar paths absent from the FTS chunk listing.

        Returns:
            The subset safe to reclaim, in input order.
        """
        if stale_paths and not self._source_dir.is_dir():
            logger.warning(
                "build_embeddings_converge_source_root_unavailable kept=%d "
                "(no vectors removed)",
                len(stale_paths),
            )
            return []
        confirmed: list[str] = []
        for path in stale_paths:
            if self._fts.get_tombstone(path) is not None:
                confirmed.append(path)
                continue
            if self._is_path_excluded(path) or self._stale_source_gone(path):
                # Deleted or excluded — the reclaim convergence exists for.
                confirmed.append(path)
                continue
            logger.warning(
                "build_embeddings_converge_kept path=%s "
                "(source present but absent from FTS; vectors kept)",
                path,
            )
        return confirmed

    def _stale_source_gone(self, path: str) -> bool:
        """Return whether a stale sidecar path's source file is gone from disk.

        ``FileNotFoundError`` / ``NotADirectoryError`` mean the source is
        genuinely not there (or no longer a file), so its vectors are
        reclaimable. Any other ``OSError`` — a permission flap, an I/O error
        — is possibly transient and reads as *present*, so the conservative
        keep applies and the next pass re-checks (#1130).
        """
        try:
            st = (self._source_dir / path).stat()
        except (FileNotFoundError, NotADirectoryError):
            return True
        except OSError as exc:
            logger.debug(
                "build_embeddings_converge_stat_failed path=%s err=%s", path, exc
            )
            return False
        return not stat_module.S_ISREG(st.st_mode)

    def _load_vectors(self) -> VectorStore:
        """Load or return the cached vector store, self-healing corrupt sidecars.

        Delegates to
        :func:`markdown_vault_mcp.managers._vector_loader.load_or_self_heal`;
        see there for the full self-heal contract.

        Returns:
            The loaded :class:`~markdown_vault_mcp.interfaces.VectorStore`.

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
            embed_text_format=self._embed_builder.format_token(),
        )

    # ------------------------------------------------------------------
    # Inline reindex embedding
    # ------------------------------------------------------------------

    def _embed_note_inline(self, vectors: VectorStore, note: ParsedNote) -> int:
        """Embed one changed note's chunks inline, resiliently (#930).

        Mirrors the cold-build / convergence contract: chunks are embedded
        in bounded batches (``embedding_batch_size``, defaulting to
        :data:`_EMBEDDING_BATCH_SIZE`) — not the whole
        note in one oversized request — and a provider failure (a request
        timeout being the motivating case, but also token-context rejection
        or a transient outage) is logged and swallowed, leaving the note's
        existing vectors untouched. Embedding runs to completion *before*
        the index is mutated, so a mid-note failure can neither drop the
        old vectors nor leave a partial row set.

        The index mutation is likewise guarded (#935): an embedding-dimension
        mismatch raises :class:`ValueError` from
        :meth:`~markdown_vault_mcp.vector_index.VectorIndex.add_vectors`, and
        that too must skip only this one note rather than abort the whole
        reindex loop. The two failure dispositions differ, though — a provider
        failure is caught *before* the index is touched (old vectors kept),
        while a dimension mismatch is caught *after* ``delete_by_path`` (old
        vectors removed) — so they return distinct codes for accurate
        aggregate logging. Either way the note is left for the next
        ``build_embeddings`` convergence pass to re-embed (its FTS row differs
        from the stale vector, so the signature diff refreshes it).

        Args:
            vectors: The loaded vector index to mutate.
            note: The parsed note whose chunks to (re-)embed.

        Returns:
            :data:`_EMBED_OK` on success (including an empty chunk set),
            :data:`_EMBED_KEPT` when the provider failed before any mutation
            (existing vectors preserved), or :data:`_EMBED_DROPPED` when the
            vector mutation was rejected after the stale vectors were removed.
        """
        if self._embedding_provider is None:
            return _EMBED_OK
        texts, meta = self._embed_inputs(
            path=note.path,
            title=note.title,
            folder=_derive_folder(note.path),
            frontmatter=note.frontmatter,
            chunks=note.chunks,
        )
        if not texts:
            # No embeddable content — drop any stale vectors for the path.
            vectors.delete_by_path(note.path)
            return _EMBED_OK
        raw: list[list[float]] = []
        try:
            for start in range(0, len(texts), self._embedding_batch_size):
                raw.extend(
                    self._embedding_provider.embed(
                        texts[start : start + self._embedding_batch_size]
                    )
                )
        except Exception as exc:
            # Broad by design: providers raise heterogeneous types for a
            # timeout or oversized batch (RuntimeError, httpx errors, ...).
            # Keep the traceback diagnosable while the reindex carries on.
            # Caught before any mutation, so the note's old vectors are kept.
            logger.warning(
                "reindex_inline_embed_skip_doc path=%s chunks=%d err=%s",
                note.path,
                len(texts),
                exc,
                exc_info=True,
            )
            return _EMBED_KEPT
        try:
            vectors.delete_by_path(note.path)
            vectors.add_vectors(raw, meta)
        except ValueError as exc:
            # A dimension mismatch (add_vectors, vector_index.py) must not
            # abort the reindex loop with the note half-updated (#935).
            # Narrow to ValueError so genuine programming errors still
            # surface; delete_by_path has already run, so the note's old
            # vectors are gone until the next convergence pass re-embeds it.
            logger.warning(
                "reindex_inline_embed_dim_mismatch path=%s chunks=%d err=%s",
                note.path,
                len(texts),
                exc,
                exc_info=True,
            )
            return _EMBED_DROPPED
        return _EMBED_OK

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
            vectors are saved. A successful build with no embeddable chunks
            persists an empty index so stale sidecar vectors cannot survive a
            forced rebuild. On the convergence path this counts only the newly
            embedded chunks — a fully converged index returns ``0``.

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
            vi = VectorIndex(
                self._embedding_provider,
                embed_text_format=self._embed_builder.format_token(),
            )
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
        indexed_paths = {row["path"] for row in rows}
        texts, meta, had_parse_failure = self._collect_cold_build_inputs(rows)

        vectors = self._get_vectors()
        if vectors is None:
            raise ValueError("Vector index unexpectedly None after initialisation")
        total = len(texts)
        embedded = self._embed_cold_build_batches(vectors, texts, meta)

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
        elif had_parse_failure:
            logger.warning(
                "build_embeddings_parse_failures_with_no_inputs (no vectors saved)"
            )
        elif not self._empty_embedding_build_is_authoritative(indexed_paths):
            logger.warning(
                "build_embeddings_empty_not_authoritative (no vectors saved)"
            )
        else:
            vectors.save(self._embeddings_path)
            logger.info("build_embeddings: saved empty index")
        return embedded

    def _collect_cold_build_inputs(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[str], list[dict[str, Any]], bool]:
        """Re-parse every FTS-listed note into cold-build embedding inputs.

        Args:
            rows: The ``list_notes()`` rows naming what the FTS index holds.

        Returns:
            ``(texts, meta, had_parse_failure)`` — the accumulated embedding
            inputs across all parseable notes, and whether any note failed to
            re-parse (logged and skipped, so the caller can distinguish an
            empty vault from a build with gaps).
        """
        num_notes = len(rows)
        logger.info("build_embeddings: parsing %d notes into chunks", num_notes)
        texts: list[str] = []
        meta: list[dict[str, Any]] = []
        had_parse_failure = False

        for i, row in enumerate(rows, 1):
            path = row["path"]
            title = row["title"]
            folder = row["folder"]
            abs_path = self._source_dir / path
            try:
                note = parse_note(
                    abs_path,
                    self._source_dir,
                    self._chunk_strategy,
                    title_field=self._title_field,
                )
            except (UnicodeDecodeError, OSError, yaml.YAMLError) as exc:
                logger.warning("build_embeddings: skipping %s — %s", path, exc)
                had_parse_failure = True
                continue
            note_texts, note_meta = self._embed_inputs(
                path=path,
                title=title,
                folder=folder,
                frontmatter=note.frontmatter,
                chunks=note.chunks,
            )
            texts.extend(note_texts)
            meta.extend(note_meta)
            if i % 100 == 0 or i == num_notes:
                logger.info(
                    "build_embeddings: parsed %d/%d notes (%d chunks so far)",
                    i,
                    num_notes,
                    len(texts),
                )
        return texts, meta, had_parse_failure

    def _embed_cold_build_batches(
        self,
        vectors: VectorStore,
        texts: list[str],
        meta: list[dict[str, Any]],
    ) -> int:
        """Embed the cold-build inputs in bounded batches, skipping failures.

        Per-batch detail logs at DEBUG (loud, opt-in via ``-v``); INFO carries
        only a bounded decile heartbeat with an ETA so operators can track
        progress without thousands of lines per build (#311). A batch that
        exceeds the model's token context (e.g. a strict provider returning
        HTTP 400) is logged and skipped rather than aborting the whole build
        (#649).

        Args:
            vectors: The vector index to populate.
            texts: Embedding input texts, one per chunk.
            meta: Metadata rows parallel to *texts*.

        Returns:
            Number of chunks actually vectorised; decile progress runs over
            attempted chunks.
        """
        total = len(texts)
        started = time.monotonic()
        last_decile = 0
        embedded = 0
        for start in range(0, total, self._embedding_batch_size):
            end = min(start + self._embedding_batch_size, total)
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
        return embedded

    def _converge_embeddings(self, vectors: VectorStore) -> int:
        """Reconcile a non-empty vector index with the FTS chunk set (#665).

        Chunk identity is the ``(title, heading, content)`` multiset per
        document path — exactly the metadata stored alongside each vector
        row, so the diff needs no file re-parsing.  Chunks whose embedding
        input is blank are excluded from that multiset before the diff runs
        (#1087), so a body-less note is simply a path with no embeddable
        chunks: it never enters the comparison, and any vectors it still has
        are reclaimed through the stale-path branch.  A sidecar written by a
        provider that *did* accept an empty input sees one re-embed of the
        affected documents and converges after it.  Documents present only
        in the vector index lose their vectors when that absence is
        authoritative — deleted, newly excluded, or deliberately skipped
        (which since #1129 leaves a tombstone row).  Paths FTS has no rows
        for at all are confirmed via :meth:`_confirm_stale_vector_paths`
        (tombstone lookup, exclusion check, or a source ``stat``); an
        unconfirmed path — source on disk, no live row, no tombstone: a
        build gap — keeps its vectors for the pass that next sees its
        source (#1130).
        Documents missing from the vector index are embedded; documents
        whose chunk multiset differs in any way (modified content, changed
        title, re-chunked boundaries) are re-embedded in full.  The sidecar
        is saved only when something actually changed.

        A provider failure while embedding one document's chunks skips
        that document — its existing vectors are left intact — and the
        remaining documents still converge; the next call retries
        (mirroring the cold build's per-batch resilience, #649).  This is
        also what makes a failed boot ``BuildEmbeddings`` job self-heal:
        the drift it left behind is just a larger diff for the next run.

        The vector mutation is guarded the same way (#935): an
        embedding-dimension mismatch raises :class:`ValueError` from
        :meth:`~markdown_vault_mcp.vector_index.VectorIndex.add_vectors`
        and skips only that document rather than aborting the pass. Unlike
        a provider failure (caught before the index is touched), this fires
        *after* ``delete_by_path`` has run, so the document's stale vectors
        are removed — tallied under the separate ``dropped`` counter (logged
        as ``build_embeddings_converge_dropped_chunks``) and re-embedded on
        the next run.

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

        fts_by_path, stale_paths, missing_paths, refresh_paths, up_to_date = (
            self._diff_converge_state(vectors)
        )

        added, removed, failed, dropped = self._reembed_converge_documents(
            vectors, fts_by_path, missing_paths + refresh_paths
        )

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
        if dropped:
            logger.warning(
                "build_embeddings_converge_dropped_chunks total=%d "
                "(vectors removed; re-embedded on the next run)",
                dropped,
            )
        logger.info(
            "build_embeddings_converged added=%d removed=%d up_to_date=%d",
            added,
            removed,
            up_to_date,
        )
        return added

    def _diff_converge_state(
        self, vectors: VectorStore
    ) -> tuple[dict[str, list[dict[str, Any]]], list[str], list[str], list[str], int]:
        """Diff the FTS chunk set against the sidecar for convergence.

        Builds each FTS row's embedding text and preamble (first chunk only,
        from the document frontmatter) so the signature diff detects offline
        summary-only edits; the vector side stores the preamble per row.

        Args:
            vectors: The loaded, non-empty vector index to diff against.

        Returns:
            ``(fts_by_path, stale_paths, missing_paths, refresh_paths,
            up_to_date)`` — the embeddable FTS rows grouped by path (each row
            carrying its precomputed ``embed_text``), the confirmed-stale
            sidecar paths to reclaim, the paths to embed or re-embed, and the
            count of already-converged chunks.
        """

        def _signature(
            rows: list[dict[str, Any]],
        ) -> Counter[tuple[Any, Any, Any, Any, Any]]:
            # start_line participates so that line-shift-only edits (content
            # identical, positions moved) still refresh vector metadata; the
            # cost is re-embedding that one doc's chunks, and embeddings are
            # deterministic for identical content. The preamble participates
            # so an offline summary-only edit (content identical, searchable
            # frontmatter changed) still re-embeds; ``or ""`` normalisation
            # keeps legacy vector rows without the key equal to fresh rows
            # under default (v1) config.
            return Counter(
                (
                    r.get("title"),
                    r.get("heading"),
                    r.get("content"),
                    r.get("start_line"),
                    r.get("preamble") or "",
                )
                for r in rows
            )

        # Compute each FTS row's preamble from the document frontmatter
        # (first chunk only) so the signature diff detects offline
        # summary-only edits; the vector side stores it per row.
        fts_by_path: dict[str, list[dict[str, Any]]] = {}
        # Every path FTS has chunk rows for, *before* the embeddable filter:
        # a path present here but absent from fts_by_path is indexed with
        # nothing embeddable (body-less, #1087), which reclaims without the
        # source-side confirmation genuine FTS absence needs (#1130).
        fts_chunk_paths: set[str] = set()
        for row in self._fts.list_chunks():
            fts_chunk_paths.add(row["path"])
            row["preamble"] = (
                self._embed_builder.fields_text(row.get("frontmatter_json"))
                if row.get("is_first_chunk")
                else ""
            )
            text = self._embed_builder.build(
                title=row["title"],
                heading=row["heading"],
                content=row["content"],
                fields_text=row["preamble"],
                is_first_chunk=bool(row.get("is_first_chunk")),
            )
            if not is_embeddable(text):
                # Same filter as _embed_inputs (#1087), but it has to be
                # applied *here* rather than at the embed call below.
                # _signature() compares this row set against the sidecar's
                # rows, so a blank chunk dropped only at embed time would
                # leave the two multisets permanently unequal and re-embed
                # the whole document on every convergence pass. Filtering at
                # the source keeps the comparison, the embed inputs, the
                # metadata and the up_to_date tally on one row set.
                continue
            # Carried on the row so the embed loop below does not rebuild it.
            # Safe to attach: the sidecar metadata is assembled from named
            # keys, and _signature() reads only the identity keys, so this
            # never leaks into a vector row or perturbs the diff.
            row["embed_text"] = text
            fts_by_path.setdefault(row["path"], []).append(row)
        vec_by_path = vectors.chunks_by_path()

        stale_paths = [p for p in vec_by_path if p not in fts_by_path]
        # Sidecar paths FTS has no chunk rows for cannot be reclaimed on row
        # absence alone: they must carry a skip tombstone, be excluded, or be
        # gone from disk (#1129); anything else is a build gap whose vectors
        # are kept (#1130).
        unconfirmed = [p for p in stale_paths if p not in fts_chunk_paths]
        if unconfirmed:
            kept = set(unconfirmed) - set(self._confirm_stale_vector_paths(unconfirmed))
            if kept:
                stale_paths = [p for p in stale_paths if p not in kept]
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
        return fts_by_path, stale_paths, missing_paths, refresh_paths, up_to_date

    def _reembed_converge_documents(
        self,
        vectors: VectorStore,
        fts_by_path: dict[str, list[dict[str, Any]]],
        paths: list[str],
    ) -> tuple[int, int, int, int]:
        """Re-embed the missing/refresh documents, one document at a time.

        Embeds per document, in bounded batches (#159), so a provider failure
        affects exactly one document: its old vectors stay untouched and every
        other document still converges.

        Args:
            vectors: The vector index to mutate.
            fts_by_path: Embeddable FTS rows grouped by path, each row carrying
                its precomputed ``embed_text``.
            paths: The missing + refresh paths to (re-)embed, in order.

        Returns:
            ``(added, removed, failed, dropped)`` chunk tallies for the
            caller's aggregate logging.
        """
        # _converge_embeddings() re-checked configuration before dispatching
        # here; re-narrow for the type checker without a type: ignore.
        provider = self._embedding_provider
        if provider is None:
            raise RuntimeError(
                "_require_vectors() must be called before _converge_embeddings()"
            )
        added = 0
        removed = 0
        failed = 0
        dropped = 0
        for path in paths:
            rows = fts_by_path[path]
            texts = [r["embed_text"] for r in rows]
            # Vector metadata keeps the canonical row shape (plus preamble);
            # the list_chunks-only keys (frontmatter_json, is_first_chunk)
            # must not leak into the sidecar.
            metas = [
                {
                    "path": r["path"],
                    "title": r["title"],
                    "folder": r["folder"],
                    "heading": r["heading"],
                    "content": r["content"],
                    "start_line": r["start_line"],
                    "preamble": r["preamble"],
                }
                for r in rows
            ]
            raw: list[list[float]] = []
            try:
                for start in range(0, len(texts), self._embedding_batch_size):
                    raw.extend(
                        provider.embed(
                            texts[start : start + self._embedding_batch_size]
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
            try:
                removed += vectors.delete_by_path(path)
                added += vectors.add_vectors(raw, metas)
            except ValueError as exc:
                # A dimension mismatch (add_vectors, vector_index.py) must
                # skip only this document, not abort the whole convergence
                # pass (#935). Narrow to ValueError so genuine programming
                # errors still surface. delete_by_path has already run, so
                # this document's vectors are removed (unlike the provider
                # branch above); tracked separately for an accurate log.
                dropped += len(texts)
                logger.warning(
                    "build_embeddings_converge_dim_mismatch path=%s chunks=%d err=%s",
                    path,
                    len(texts),
                    exc,
                    exc_info=True,
                )
                continue
        return added, removed, failed, dropped

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

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
            # and misreporting chunk_count=0 (#819).
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

        pre_embedded = self._pre_embed_dirty_paths(paths, self._embedding_provider)

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

    def _pre_embed_dirty_paths(
        self, paths: set[str], provider: EmbeddingProvider
    ) -> list[tuple[str, list[list[float]] | None, list[dict[str, Any]] | None, bool]]:
        """Phase 1 of the deferred flush: parse and embed each dirty path.

        Each entry is ``(path, vectors_or_None, meta_or_None, failed_flag)``.
        ``failed=True`` means parse failed → Phase 2 must NOT delete the
        existing vectors for this path (silent-data-loss guard).

        Args:
            paths: Paths to re-embed (relative to source_dir).
            provider: The (already narrowed, non-``None``) embedding provider.

        Returns:
            One entry per input path, in iteration order.
        """
        pre_embedded: list[
            tuple[str, list[list[float]] | None, list[dict[str, Any]] | None, bool]
        ] = []
        for path in paths:
            abs_path = self._source_dir / path
            if self._is_path_excluded(path):
                # Excluded paths (e.g. convention files) never get vectors,
                # mirroring the FTS guard in process_dirty_paths → delete.
                pre_embedded.append((path, None, None, False))
            elif abs_path.is_file() and path.endswith(".md"):
                try:
                    note = parse_note(
                        abs_path,
                        self._source_dir,
                        self._chunk_strategy,
                        title_field=self._title_field,
                    )
                    texts, meta = self._embed_inputs(
                        path=note.path,
                        title=note.title,
                        folder=_derive_folder(note.path),
                        frontmatter=note.frontmatter,
                        chunks=note.chunks,
                    )
                    if texts:
                        raw_vecs = provider.embed(texts)
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
        return pre_embedded
