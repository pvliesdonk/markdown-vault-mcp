"""Search, list, and query manager.

Handles all search operations (keyword, semantic, hybrid), document listing,
folder/tag enumeration, recent notes, similar notes, and consolidated
context queries with dependency injection — receives only the FTS index,
source directory, and optional collaborators.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import logging
import mimetypes
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError
from markdown_vault_mcp.managers._ranking import (
    ChannelRow as _ChannelRow,
)
from markdown_vault_mcp.managers._ranking import (
    GroupableFTS as _GroupableFTS,
)
from markdown_vault_mcp.managers._ranking import (
    SemanticRow as _SemanticRow,
)
from markdown_vault_mcp.managers._ranking import (
    apply_folder_boost as _apply_folder_boost,
)
from markdown_vault_mcp.managers._ranking import (
    apply_length_downweight as _apply_length_downweight,
)
from markdown_vault_mcp.managers._ranking import (
    compute_snippet_window as _compute_snippet_for_semantic,
)
from markdown_vault_mcp.managers._ranking import (
    group_by_path as _group_by_path,
)
from markdown_vault_mcp.managers._vector_loader import load_or_self_heal
from markdown_vault_mcp.okf import derive_annotation
from markdown_vault_mcp.types import (
    AttachmentInfo,
    BacklinkInfo,
    DocumentMeta,
    GroupedResult,
    NoteContext,
    NoteInfo,
    OutlinkInfo,
    SectionHit,
    VaultStats,
)
from markdown_vault_mcp.utils import (
    effective_attachment_extensions,
    fts_row_to_note_info,
    is_path_excluded,
    validate_path,
)
from markdown_vault_mcp.utils.fs import GLOB_SYMLINK_KWARGS

if TYPE_CHECKING:
    import builtins
    from collections.abc import Callable, Sequence

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.managers.link import LinkManager
    from markdown_vault_mcp.okf import OkfDetector
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.types import FTSResult
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

# RRF constant — standard value recommended in the original paper.
_RRF_K = 60

# Floor for the vector-search candidate pool, applied by both the semantic and
# the hybrid path. Vector search does a full linear scan and sort regardless of
# limit (see VectorIndex.search), so a wide floor only costs the final slice,
# not extra distance work. It keeps recall of the best-scoring documents from
# depending on how large a limit the caller passed.
_SEMANTIC_CANDIDATE_FLOOR = 1000

# Maximum folder peers returned by get_context().
_CONTEXT_FOLDER_PEERS_LIMIT = 20


def _rrf_accumulate(
    rows: Sequence[_ChannelRow],
    *,
    rrf_scores: dict[tuple[str, str | None], float],
    chunk_meta: dict[tuple[str, str | None], dict[str, Any]],
    channel_keys: set[tuple[str, str | None]],
) -> None:
    """Fold one ranked channel into the Reciprocal Rank Fusion accumulators.

    Each row contributes ``1 / (_RRF_K + rank)`` to its ``(path, heading)``
    key. ``chunk_meta`` keeps the first channel's row payload per key
    (``setdefault``), so fold order decides which channel's metadata wins
    for chunks present in both.

    Args:
        rows: One channel's rows, pre-sorted by descending channel score.
        rrf_scores: Accumulated RRF score per chunk key (mutated).
        chunk_meta: First-seen row payload per chunk key (mutated).
        channel_keys: This channel's chunk-key membership set (mutated).
    """
    for rank, row in enumerate(rows, start=1):
        key = (row.path, row.heading)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
        channel_keys.add(key)
        chunk_meta.setdefault(
            key,
            {
                "title": row.title,
                "folder": row.folder,
                "content": row.content,
                "start_line": row.start_line,
                "section_id": row.section_id,
            },
        )


class SearchManager:
    """Manages search, listing, and query operations against the vault.

    Args:
        fts: The FTS index to query.
        source_dir: Absolute path to the vault root directory.
        embeddings_path: Base path for ``.npy`` / ``.json`` sidecar files.
            ``None`` disables semantic search.
        embedding_provider: Provider used to generate embeddings.
        indexed_frontmatter_fields: Frontmatter keys promoted to
            ``document_tags`` for structured filtering.
        exclude_patterns: Glob patterns for paths to exclude from listing.
        attachment_extensions: Allowed non-.md extensions.  ``None`` uses
            the default set.
        link_manager: Optional :class:`LinkManager` for context queries.
        okf_detector: Optional OKF detection probe; enables
            :meth:`okf_stats`. ``None`` keeps every OKF surface inert.
        rebuild_embeddings: Callback to rebuild all embeddings from scratch.
            Invoked by ``_load_vectors`` on any unrecoverable sidecar fault —
            a provider/model mismatch, an embedding-text format mismatch, a
            row-count mismatch, or a truncated/zero-byte/incomplete sidecar.
        folder_weights: Folder-prefix score multipliers applied to every
            search mode just before file grouping.  ``None`` disables the
            boost.
        embed_text_format: The current embedding-text format token; a
            persisted vector sidecar with a different token routes to
            ``rebuild_embeddings``.
    """

    def __init__(
        self,
        fts: FTSIndex,
        source_dir: Path,
        *,
        embeddings_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        indexed_frontmatter_fields: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        attachment_extensions: list[str] | None = None,
        link_manager: LinkManager | None = None,
        okf_detector: OkfDetector | None = None,
        rebuild_embeddings: Callable[[], None] | None = None,
        chunks_per_file: int = 2,
        snippet_words: int = 200,
        length_downweight_alpha: float = 0.25,
        folder_weights: dict[str, float] | None = None,
        embed_text_format: str = "v1",
    ) -> None:
        self._fts = fts
        self._source_dir = source_dir
        self._embeddings_path = embeddings_path
        self._embedding_provider = embedding_provider
        self._indexed_frontmatter_fields: list[str] = indexed_frontmatter_fields or []
        self._exclude_patterns = exclude_patterns
        self._attachment_extensions = attachment_extensions
        self._link_manager = link_manager
        self._okf = okf_detector
        self._rebuild_embeddings = rebuild_embeddings or (lambda: None)
        self._chunks_per_file = chunks_per_file
        self._snippet_words = snippet_words
        self._length_downweight_alpha = length_downweight_alpha
        self._folder_weights = folder_weights
        self._embed_text_format = embed_text_format

        # Vector index is loaded lazily (only if embeddings_path is set).
        self._vectors: VectorIndex | None = None

    # ------------------------------------------------------------------
    # Vector index property (shared with IndexManager)
    # ------------------------------------------------------------------

    @property
    def vectors(self) -> VectorIndex | None:
        """Return the lazily-loaded vector index, or ``None``."""
        return self._vectors

    @vectors.setter
    def vectors(self, value: VectorIndex | None) -> None:
        self._vectors = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_path(self, path: str) -> None:
        """Validate that *path* ends with ``.md`` and stays inside source_dir.

        Args:
            path: Relative vault path to validate.

        Raises:
            ValueError: If the path does not end with ``.md`` or escapes
                the source directory.
        """
        validate_path(path, self._source_dir)

    def _require_vectors(self) -> None:
        """Raise :class:`EmbeddingsNotConfiguredError` if semantic search is unconfigured."""
        if self._embedding_provider is None or self._embeddings_path is None:
            raise EmbeddingsNotConfiguredError(
                "Semantic search requires both 'embedding_provider' and "
                "'embeddings_path' to be configured."
            )

    def _load_vectors(self) -> VectorIndex:
        """Load or return the cached VectorIndex, self-healing corrupt sidecars.

        Delegates to
        :func:`markdown_vault_mcp.managers._vector_loader.load_or_self_heal`
        over this manager's shared index slot; see there for the full
        self-heal contract.

        Returns:
            A :class:`~markdown_vault_mcp.vector_index.VectorIndex` instance.

        Raises:
            RuntimeError: If called without a prior ``_require_vectors()``
                (``_embedding_provider`` or ``_embeddings_path`` is ``None``).
            ValueError: If a self-heal rebuild fails to produce a usable index.
        """
        if self._vectors is not None:
            return self._vectors
        if self._embeddings_path is None or self._embedding_provider is None:
            raise RuntimeError(
                "_require_vectors() must be called before _load_vectors()"
            )
        return load_or_self_heal(
            embeddings_path=self._embeddings_path,
            embedding_provider=self._embedding_provider,
            get_vectors=lambda: self._vectors,
            set_vectors=lambda v: setattr(self, "_vectors", v),
            rebuild=self._rebuild_embeddings,
            logger=logger,
            embed_text_format=self._embed_text_format,
        )

    def _get_frontmatter(self, path: str) -> dict[str, Any]:
        """Return the frontmatter dict for a document from the FTS index.

        Falls back to an empty dict if the document is not found.

        Args:
            path: Relative document path.

        Returns:
            Parsed frontmatter dict.
        """
        row = self._fts.get_note(path)
        if row is None:
            return {}
        return self._parse_frontmatter_json(row.get("frontmatter_json"), path)

    @staticmethod
    def _parse_frontmatter_json(raw: Any, path: str) -> dict[str, Any]:
        """Decode a ``frontmatter_json`` cell into a dict.

        Returns ``{}`` when the cell is empty or does not parse.

        Args:
            raw: The ``frontmatter_json`` column value.
            path: Document path, for the warning message only.

        Returns:
            Parsed frontmatter dict, or ``{}``.
        """
        if not raw:
            return {}
        try:
            result: dict[str, Any] = json.loads(raw)
            return result
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("invalid frontmatter JSON for %s — %s", path, exc)
            return {}

    def get_metadata(self, path: str) -> DocumentMeta | None:
        """Return lightweight metadata for *path* without reading the document.

        Reads only the FTS ``documents`` row — no file I/O and no
        ``max_note_read_bytes`` cap — so it is the right call for consumers that
        need a title / folder / frontmatter (e.g. graph node labels) rather than
        the document body.

        Args:
            path: Relative document path.

        Returns:
            A :class:`~markdown_vault_mcp.types.DocumentMeta`, or ``None`` if the
            document is not indexed.
        """
        row = self._fts.get_note(path)
        if row is None:
            return None
        return DocumentMeta(
            path=row["path"],
            title=row["title"],
            folder=row["folder"],
            frontmatter=self._parse_frontmatter_json(row.get("frontmatter_json"), path),
        )

    def _row_matches_filters(self, path: str, filters: dict[str, str]) -> bool:
        """Return ``True`` if the document at *path* satisfies all *filters*.

        Looks up frontmatter from the FTS index and checks each key/value
        pair.  List-valued frontmatter fields are matched by membership.

        Args:
            path: Relative document path.
            filters: Dict of ``{frontmatter_key: value}`` pairs.

        Returns:
            ``True`` if the document exists and all filter conditions are met.
        """
        note_row = self._fts.get_note(path)
        if note_row is None:
            return False
        fm_raw = note_row.get("frontmatter_json")
        fm: dict[str, Any] = {}
        if fm_raw:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                fm = json.loads(fm_raw)
        for key, value in filters.items():
            fm_val = fm.get(key)
            if fm_val is None:
                return False
            if isinstance(fm_val, list):
                if str(value) not in [str(v) for v in fm_val]:
                    return False
            else:
                if str(fm_val) != str(value):
                    return False
        return True

    def _post_filter_semantic_rows(
        self,
        raw: builtins.list[dict[str, Any]],
        *,
        folder: str | None,
        filters: dict[str, str] | None,
    ) -> builtins.list[dict[str, Any]]:
        """Apply folder-prefix and frontmatter filters to vector-search rows.

        Vector search carries no structured metadata, so filtering happens
        after the fact: *folder* matches exactly or as a sub-folder prefix,
        and *filters* checks frontmatter via :meth:`_row_matches_filters`
        (any frontmatter key — not limited to ``indexed_frontmatter_fields``).

        *folder* is normalized first (backslashes to slashes, surrounding
        slashes stripped) so a natural ``"3-Resources/"`` does not silently
        match nothing; a value that normalizes to ``""`` means no restriction.

        Args:
            raw: Result dicts from :meth:`VectorIndex.search` /
                :meth:`VectorIndex.search_by_path`.
            folder: Optional folder to restrict results to.
            filters: Optional ``{frontmatter_key: value}`` equality filters.

        Returns:
            The rows that satisfy every condition, original order preserved.
        """
        if folder is not None:
            folder = folder.replace("\\", "/").strip("/") or None
        filtered: builtins.list[dict[str, Any]] = []
        for r in raw:
            if folder is not None:
                r_folder = r.get("folder", "")
                if r_folder != folder and not r_folder.startswith(folder + "/"):
                    continue
            if filters and not self._row_matches_filters(r["path"], filters):
                continue
            filtered.append(r)
        return filtered

    def _effective_attachment_extensions(self) -> frozenset[str]:
        """Return the effective set of allowed attachment extensions.

        Returns:
            Frozenset of lower-case extension strings (without leading dot).
            The special value ``frozenset(["*"])`` means all non-.md files.
        """
        return effective_attachment_extensions(self._attachment_extensions)

    def _is_path_excluded(self, path: str) -> bool:
        """Check whether *path* matches any configured exclude pattern.

        Args:
            path: Relative POSIX path string.

        Returns:
            ``True`` if the path matches any pattern in
            ``self._exclude_patterns``.
        """
        return is_path_excluded(path, self._exclude_patterns)

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
        """Search the vault.

        Args:
            query: Search string.
            limit: Maximum number of files (not chunks) to return.
            mode: ``"keyword"`` for BM25 FTS5, ``"semantic"`` for cosine
                similarity, or ``"hybrid"`` for Reciprocal Rank Fusion of
                both.
            filters: Dict of ``{frontmatter_key: value}`` pairs (AND
                semantics).  Only works for fields in
                ``indexed_frontmatter_fields``.
            folder: If provided, restrict results to documents in this
                folder (and its sub-folders).
            chunks_per_file: Maximum number of sections returned per file.
                ``None`` uses the instance default (``self._chunks_per_file``).
            snippet_words: Width of the FTS5 snippet window in words.
                ``0`` returns full chunk content.  ``None`` uses the instance
                default (``self._snippet_words``).

        Returns:
            List of :class:`~markdown_vault_mcp.types.GroupedResult` ordered
            by descending file score (max of section scores).

        Raises:
            EmbeddingsNotConfiguredError: If *mode* is ``"semantic"`` or
                ``"hybrid"`` but no embedding provider or embeddings path is
                configured (a ``ValueError`` subclass).
        """
        eff_cap = (
            chunks_per_file if chunks_per_file is not None else self._chunks_per_file
        )
        eff_snip = snippet_words if snippet_words is not None else self._snippet_words

        if mode == "keyword":
            return self._keyword_search(
                query,
                limit=limit,
                filters=filters,
                folder=folder,
                chunks_per_file=eff_cap,
                snippet_words=eff_snip,
            )

        if mode == "semantic":
            self._require_vectors()
            return self._semantic_search(
                query,
                limit=limit,
                filters=filters,
                folder=folder,
                chunks_per_file=eff_cap,
                snippet_words=eff_snip,
            )

        # hybrid
        self._require_vectors()
        return self._hybrid_search(
            query,
            limit=limit,
            filters=filters,
            folder=folder,
            chunks_per_file=eff_cap,
            snippet_words=eff_snip,
        )

    def _adapt_semantic_rows(
        self, rows: builtins.list[dict[str, Any]]
    ) -> builtins.list[_SemanticRow]:
        """Adapt raw vector-store rows to the ranking-pipeline row contract.

        Chunk counts are batch-fetched from FTS so the length downweight can
        normalize multi-chunk documents.  Shared by the semantic and hybrid
        channels and :meth:`get_similar`.

        Args:
            rows: Post-filtered dicts from :meth:`VectorIndex.search` /
                :meth:`VectorIndex.search_by_path`.

        Returns:
            :class:`_SemanticRow` adapter rows.
        """
        chunk_counts = self._fts.get_chunk_counts({r["path"] for r in rows})
        return [
            _SemanticRow(
                path=r["path"],
                title=r.get("title", ""),
                folder=r.get("folder", ""),
                heading=r.get("heading"),
                content=r.get("content", ""),
                score=r.get("score", 0.0),
                chunk_count=chunk_counts.get(r["path"], 1),
                start_line=int(r.get("start_line", 0)),
                section_id=0,  # vector store has no sections rowid; see dataclass
            )
            for r in rows
        ]

    def _assemble_grouped_results(
        self,
        groups: Sequence[Sequence[_ChannelRow]],
        *,
        query: str,
        snippet_words: int,
        snippet_map: dict[tuple[str, str | None], str] | None = None,
        search_type: Literal["keyword", "semantic", "hybrid"]
        | Callable[[Sequence[_ChannelRow]], Literal["keyword", "semantic", "hybrid"]],
    ) -> builtins.list[GroupedResult]:
        """Fold file groups into :class:`GroupedResult` objects.

        The shared tail of every search channel: per-section content
        resolution (snippet-map hit → recompute → raw content), file score
        (max of section scores), and frontmatter enrichment.

        Args:
            groups: File groups from :func:`group_by_path`; rows must expose
                ``path`` / ``title`` / ``folder`` / ``heading`` /
                ``content`` / ``score``.
            query: The search query (drives snippet recomputation).
            snippet_words: Snippet window width; ``0`` returns raw content.
            snippet_map: Optional ``{(path, heading): snippet}`` map from
                :meth:`_fetch_snippet_map`; missing keys fall back to
                recomputation.
            search_type: Either a fixed channel label or a callable deriving
                the label per group (the hybrid channel's union rule).

        Returns:
            List of :class:`GroupedResult`, one per group, in group order.
        """
        out: builtins.list[GroupedResult] = []
        for group in groups:
            sections: builtins.list[SectionHit] = []
            for r in group:
                key = (r.path, r.heading)
                if snippet_map and key in snippet_map:
                    content = snippet_map[key]
                elif snippet_words > 0:
                    content = _compute_snippet_for_semantic(
                        r.content, query, snippet_words=snippet_words
                    )
                else:
                    content = r.content
                sections.append(
                    SectionHit(heading=r.heading, content=content, score=r.score)
                )
            head = group[0]
            file_type = search_type(group) if callable(search_type) else search_type
            out.append(
                GroupedResult(
                    path=head.path,
                    title=head.title,
                    folder=head.folder,
                    score=max(s.score for s in sections),
                    search_type=file_type,
                    frontmatter=self._get_frontmatter(head.path),
                    sections=sections,
                )
            )
        return out

    def _keyword_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None,
        folder: str | None,
        chunks_per_file: int,
        snippet_words: int,
    ) -> list[GroupedResult]:
        candidate_limit = max(limit * (chunks_per_file + 4), 50)

        raw = self._fts.search(
            query,
            limit=candidate_limit,
            filters=filters,
            folder=folder,
            snippet_words=None,
        )
        downweighted = _apply_length_downweight(
            raw, alpha=self._length_downweight_alpha
        )
        downweighted = _apply_folder_boost(downweighted, weights=self._folder_weights)
        groupable: list[_GroupableFTS] = [
            _GroupableFTS(
                path=r.path,
                title=r.title,
                folder=r.folder,
                heading=r.heading,
                content=r.content,
                score=r.score,
                start_line=r.start_line,
                section_id=r.section_id,
            )
            for r in downweighted
        ]
        groups = _group_by_path(
            groupable, chunks_per_file=chunks_per_file, file_limit=limit
        )

        if snippet_words > 0:
            survivor_keys = {(r.path, r.heading) for g in groups for r in g}
            snippet_rows = [
                fr for fr in downweighted if (fr.path, fr.heading) in survivor_keys
            ]
            snippets_by_key = self._fetch_snippet_map(
                query,
                snippet_rows,
                snippet_words=snippet_words,
                folder=folder,
                filters=filters,
                candidate_limit=candidate_limit,
            )
        else:
            snippets_by_key = {}

        return self._assemble_grouped_results(
            groups,
            query=query,
            snippet_words=snippet_words,
            snippet_map=snippets_by_key,
            search_type="keyword",
        )

    def _fetch_snippet_map(
        self,
        query: str,
        survivors: list[FTSResult],
        *,
        snippet_words: int,
        folder: str | None,
        filters: dict[str, str] | None,
        candidate_limit: int,
    ) -> dict[tuple[str, str | None], str]:
        """Re-query FTS with snippet projection, restricted to survivor paths.

        Returns a ``{(path, heading): snippet}`` map. Pool is widened to at
        least the caller's initial ``candidate_limit`` (so the snippet re-query
        is never narrower than the ranking query) and scoped via the same
        ``folder`` / ``filters`` so a narrowly-scoped initial search doesn't
        fall back to a global re-query.

        The caller falls back to the survivor's own ``content`` when a key is
        missing from the map (rare FTS rank inversion).

        Args:
            query: The search query string.
            survivors: FTS result rows that survived ranking and capping.
            snippet_words: Width of the FTS5 snippet window.
            folder: Folder restriction forwarded from the original search.
            filters: Frontmatter filters forwarded from the original search.
            candidate_limit: The caller's initial candidate pool size; used as
                a floor so the snippet re-query is at least as wide as the
                ranking query.
        """
        if not survivors:
            return {}
        candidate_n = max(candidate_limit, len(survivors) * 4, 50)
        rows = self._fts.search(
            query,
            limit=candidate_n,
            folder=folder,
            filters=filters,
            snippet_words=snippet_words,
        )
        wanted = {(s.path, s.heading) for s in survivors}
        return {
            (r.path, r.heading): r.content
            for r in rows
            if (r.path, r.heading) in wanted
        }

    def _semantic_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None = None,
        folder: str | None = None,
        chunks_per_file: int,
        snippet_words: int,
    ) -> list[GroupedResult]:
        vectors = self._load_vectors()
        candidate_limit = max(limit * (chunks_per_file + 4), _SEMANTIC_CANDIDATE_FLOOR)
        raw = vectors.search(query, limit=candidate_limit)

        filtered = self._post_filter_semantic_rows(raw, folder=folder, filters=filters)
        rows = self._adapt_semantic_rows(filtered)

        downweighted = _apply_length_downweight(
            rows, alpha=self._length_downweight_alpha
        )
        downweighted = _apply_folder_boost(downweighted, weights=self._folder_weights)
        groups = _group_by_path(
            downweighted, chunks_per_file=chunks_per_file, file_limit=limit
        )

        return self._assemble_grouped_results(
            groups,
            query=query,
            snippet_words=snippet_words,
            search_type="semantic",
        )

    def _hybrid_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None,
        folder: str | None,
        chunks_per_file: int,
        snippet_words: int,
    ) -> list[GroupedResult]:
        """RRF merge of keyword and semantic results, then field-collapse."""
        # The two channels size their candidate pools independently. FTS keeps a
        # floor of 50 (a BM25 query does real work per candidate). The vector
        # channel uses the same full-scan floor as _semantic_search, since it
        # goes through the identical VectorIndex.search and the same recall cap
        # applies: a floor-50 pool drops a document whose best chunk ranks just
        # past 50 from the RRF merge at small limits.
        fts_candidate_limit = max(limit * (chunks_per_file + 4), 50)
        vec_candidate_limit = max(
            limit * (chunks_per_file + 4), _SEMANTIC_CANDIDATE_FLOOR
        )

        fts_raw: list[FTSResult] = self._fts.search(
            query,
            limit=fts_candidate_limit,
            filters=filters,
            folder=folder,
            snippet_words=None,
        )
        fts_results: list[FTSResult] = _apply_length_downweight(
            fts_raw, alpha=self._length_downweight_alpha
        )

        vectors = self._load_vectors()
        vec_raw = vectors.search(query, limit=vec_candidate_limit)
        vec_filtered = self._post_filter_semantic_rows(
            vec_raw, folder=folder, filters=filters
        )
        vec_rows = _apply_length_downweight(
            self._adapt_semantic_rows(vec_filtered),
            alpha=self._length_downweight_alpha,
        )

        rrf_scores: dict[tuple[str, str | None], float] = {}
        chunk_meta: dict[tuple[str, str | None], dict[str, Any]] = {}
        keyword_keys: set[tuple[str, str | None]] = set()
        vec_keys: set[tuple[str, str | None]] = set()

        # FTS results are folded before vector results, so chunk_meta's
        # setdefault keeps the keyword channel's section_id (a real sections
        # rowid >= 1) for any chunk present in both channels; a vector-only
        # chunk keeps section_id=0.  Both are correct: the keyword rowid is
        # the authoritative tie-break value, and vector-only ties are
        # measure-zero (distinct embeddings -> distinct cosine scores).
        _rrf_accumulate(
            fts_results,
            rrf_scores=rrf_scores,
            chunk_meta=chunk_meta,
            channel_keys=keyword_keys,
        )
        _rrf_accumulate(
            vec_rows,
            rrf_scores=rrf_scores,
            chunk_meta=chunk_meta,
            channel_keys=vec_keys,
        )

        sorted_keys = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)

        groupable_rows: list[_GroupableFTS] = [
            _GroupableFTS(
                path=k[0],
                title=chunk_meta[k]["title"],
                folder=chunk_meta[k]["folder"],
                heading=k[1],
                content=chunk_meta[k]["content"],
                score=rrf_scores[k],
                start_line=int(chunk_meta[k].get("start_line", 0)),
                section_id=int(chunk_meta[k].get("section_id", 0)),
            )
            for k in sorted_keys
        ]
        # Post-RRF multiplicative boost: RRF scores are always positive, so
        # the folder weight scales them directly before file grouping.
        groupable_rows = _apply_folder_boost(
            groupable_rows, weights=self._folder_weights
        )
        groups = _group_by_path(
            groupable_rows, chunks_per_file=chunks_per_file, file_limit=limit
        )

        keyword_survivors = [
            r for g in groups for r in g if (r.path, r.heading) in keyword_keys
        ]
        snippet_map: dict[tuple[str, str | None], str] = {}
        if snippet_words > 0 and keyword_survivors:
            survivor_keys = {(r.path, r.heading) for r in keyword_survivors}
            survivor_fts_rows = [
                fts_r
                for fts_r in fts_results
                if (fts_r.path, fts_r.heading) in survivor_keys
            ]
            snippet_map = self._fetch_snippet_map(
                query,
                survivor_fts_rows,
                snippet_words=snippet_words,
                folder=folder,
                filters=filters,
                candidate_limit=fts_candidate_limit,
            )

        def _group_search_type(
            group: Sequence[_ChannelRow],
        ) -> Literal["keyword", "semantic", "hybrid"]:
            # File-level search_type: union over the group's sections.
            # "hybrid" if the group spans both channels — covers both
            # (a) any single section appeared in both channels and
            # (b) some sections keyword-only, others semantic-only.  Set
            # theory makes (a) a subset of (in_keyword AND in_vec), so the
            # single conjunction below catches both cases.  Otherwise the
            # group is single-channel.
            group_keys = {(r.path, r.heading) for r in group}
            in_keyword = bool(group_keys & keyword_keys)
            in_vec = bool(group_keys & vec_keys)
            if in_keyword and in_vec:
                return "hybrid"
            if in_keyword:
                return "keyword"
            return "semantic"

        return self._assemble_grouped_results(
            groups,
            query=query,
            snippet_words=snippet_words,
            snippet_map=snippet_map,
            search_type=_group_search_type,
        )

    # ------------------------------------------------------------------
    # List / enumerate
    # ------------------------------------------------------------------

    def list(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
    ) -> list[NoteInfo | AttachmentInfo]:
        """List documents (and optionally attachments) in the vault.

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
        rows = self._fts.list_notes(folder=folder)
        notes: list[NoteInfo | AttachmentInfo] = [
            fts_row_to_note_info(row) for row in rows
        ]

        if pattern:
            notes = [n for n in notes if fnmatch.fnmatch(n.path, pattern)]

        if not include_attachments:
            return notes

        return notes + self._list_attachments(pattern=pattern, folder=folder)

    def _list_attachments(
        self, *, pattern: str | None, folder: str | None
    ) -> builtins.list[AttachmentInfo]:
        """Scan the filesystem for allowlisted non-.md attachments.

        Attachments are not indexed, so this walks the source tree directly.
        The scan runs without any lock — the result is a best-effort
        snapshot and is not atomic with an FTS note listing.

        Args:
            pattern: Optional Unix glob matched against the relative path.
            folder: Optional folder restriction (exact or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.AttachmentInfo`.
        """
        exts = self._effective_attachment_extensions()
        attachments: builtins.list[AttachmentInfo] = []
        for abs_path in self._source_dir.rglob("*", **GLOB_SYMLINK_KWARGS):
            info = self._attachment_info(
                abs_path, exts=exts, pattern=pattern, folder=folder
            )
            if info is not None:
                attachments.append(info)
        return attachments

    def _attachment_rel_path(self, abs_path: Path) -> Path | None:
        """Vault-relative path of *abs_path*, or ``None`` when out of scope.

        Out of scope: outside ``source_dir``, any dot-prefixed path
        component, or a match against the configured exclude patterns
        (mirrors ``scan_directory`` behaviour).
        """
        try:
            # rglob yields paths anchored at the unresolved self._source_dir;
            # using .resolve() here would mismatch when source_dir is itself
            # a symlink and silently drop every attachment.
            rel = abs_path.relative_to(self._source_dir)
        except ValueError as exc:
            logger.warning(
                "_list_attachments: skipping %s — outside source_dir (%s)",
                abs_path,
                exc,
            )
            return None
        # Skip files where any path component starts with ".".
        if any(part.startswith(".") for part in rel.parts):
            return None
        if self._is_path_excluded(rel.as_posix()):
            return None
        return rel

    def _attachment_info(
        self,
        abs_path: Path,
        *,
        exts: frozenset[str],
        pattern: str | None,
        folder: str | None,
    ) -> AttachmentInfo | None:
        """Classify one filesystem entry as an attachment, or ``None`` to skip.

        Applies the allowlist, scope, glob-pattern, and folder filters in
        order, then stats the file for size/mtime.

        Args:
            abs_path: Absolute path yielded by the source-tree walk.
            exts: Effective attachment-extension allowlist.
            pattern: Optional Unix glob matched against the relative path.
            folder: Optional folder restriction (exact or sub-folder prefix).

        Returns:
            An :class:`~markdown_vault_mcp.types.AttachmentInfo`, or
            ``None`` when the entry is filtered out or unreadable.
        """
        suffix = abs_path.suffix.lstrip(".").lower()
        if (
            not abs_path.is_file()
            or abs_path.suffix.lower() == ".md"
            or ("*" not in exts and suffix not in exts)
        ):
            return None
        rel = self._attachment_rel_path(abs_path)
        if rel is None:
            return None
        rel_path = str(rel)
        if pattern and not fnmatch.fnmatch(rel_path, pattern):
            return None
        rel_folder = str(Path(rel_path).parent)
        if rel_folder == ".":
            rel_folder = ""
        if (
            folder is not None
            and rel_folder != folder
            and not rel_folder.startswith(folder + "/")
        ):
            return None
        try:
            stat = abs_path.stat()
        except OSError as exc:
            logger.warning(
                "_list_attachments: skipping %s — stat error (%s)",
                abs_path,
                exc,
            )
            return None
        mime_type, _ = mimetypes.guess_type(rel_path)
        return AttachmentInfo(
            path=rel_path,
            folder=rel_folder,
            mime_type=mime_type,
            size_bytes=stat.st_size,
            modified_at=stat.st_mtime,
        )

    def list_folders(self) -> builtins.list[str]:
        """Return all distinct folder values across the indexed vault.

        Returns:
            Sorted list of folder strings (``""`` for the vault root).
        """
        return self._fts.list_folders()

    def list_tags(self, field: str = "tags") -> builtins.list[str]:
        """Return all distinct values indexed for a given frontmatter field.

        If *field* was not in ``indexed_frontmatter_fields``, returns ``[]``.

        Args:
            field: Frontmatter key to query (default: ``"tags"``).

        Returns:
            Sorted list of distinct value strings.
        """
        return self._fts.list_field_values(field)

    def stats(self) -> VaultStats:
        """Return vault-wide statistics.

        Returns:
            :class:`~markdown_vault_mcp.types.VaultStats` snapshot.
        """
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

        return VaultStats(
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

    def okf_stats(self) -> dict[str, Any] | None:
        """Return the OKF section of the vault statistics, if active.

        Iterates the same ``documents`` rows :meth:`stats` already counts
        and aggregates the OKF read annotations: a ``type`` histogram (plus
        an untyped count), lifecycle-``status`` and trust-tier breakdowns,
        and the stale-note count. Reserved bundle files (``index.md`` /
        ``log.md``) are ordinary indexed notes and are included.

        Returns:
            The aggregate dict, or ``None`` when no OKF detector is wired
            or OKF read semantics are not active — callers omit the
            ``okf`` key entirely in that case, keeping non-OKF payloads
            byte-identical.
        """
        if self._okf is None:
            return None
        state = self._okf.state()
        if not state.active:
            return None
        types: dict[str, int] = {}
        status: dict[str, int] = {}
        trust: dict[str, int] = {}
        untyped = 0
        stale = 0
        for row in self._fts.list_notes():
            metadata = self._parse_frontmatter_json(
                row.get("frontmatter_json"), row["path"]
            )
            annotation = derive_annotation(metadata)
            note_type = annotation.get("type")
            if note_type is None:
                untyped += 1
            else:
                types[note_type] = types.get(note_type, 0) + 1
            note_status = annotation["status"]
            status[note_status] = status.get(note_status, 0) + 1
            tier = annotation["trust_tier"]
            trust[tier] = trust.get(tier, 0) + 1
            if annotation["stale"]:
                stale += 1
        return {
            "mode": state.mode,
            "declared_version": state.declared_version,
            "types": dict(sorted(types.items())),
            "untyped_count": untyped,
            "status": dict(sorted(status.items())),
            "trust": dict(sorted(trust.items())),
            "stale_count": stale,
        }

    # ------------------------------------------------------------------
    # Recent / similar / context
    # ------------------------------------------------------------------

    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> builtins.list[NoteInfo]:
        """Return the most recently modified documents.

        Args:
            limit: Maximum number of documents to return.
            folder: If provided, restrict to documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` objects
            ordered by modification time (most recent first).
        """
        rows = self._fts.get_recent(limit=limit, folder=folder)
        return [fts_row_to_note_info(row) for row in rows]

    def get_similar(
        self,
        path: str,
        *,
        limit: int = 10,
        chunks_per_file: int | None = None,
        folder: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> builtins.list[GroupedResult]:
        """Return the most semantically similar documents (field-collapsed).

        Uses the stored embedding vectors for ``path`` (averaged across
        chunks) to compute cosine similarity, then collapses chunks of the
        same target document into a single :class:`GroupedResult`.

        Args:
            path: Relative path of the reference document.
            limit: Maximum number of *files* to return.
            chunks_per_file: Maximum sections returned per result file.
                ``None`` uses the instance default.
            folder: If provided, restrict results to documents in this
                folder (exact match or sub-folder prefix).
            filters: Optional ``{frontmatter_key: value}`` equality filters,
                ANDed. Applied post-hoc against each candidate's full
                frontmatter (any key — not limited to
                ``indexed_frontmatter_fields``); list-valued fields match by
                membership.

        Returns:
            List of :class:`~markdown_vault_mcp.types.GroupedResult` ordered
            by descending file score (max of section scores).  Empty list
            when embeddings are not configured or the document has no
            stored vectors.

        Raises:
            ValueError: If no document exists at the given path, or
                ``chunks_per_file`` < 1.
        """
        self._validate_path(path)
        if self._fts.get_note(path) is None:
            raise ValueError(f"Document not found: {path}")

        if self._embedding_provider is None or self._embeddings_path is None:
            return []

        self._load_vectors()
        if self._vectors is None or self._vectors.count == 0:
            return []

        eff_cpf = (
            chunks_per_file if chunks_per_file is not None else self._chunks_per_file
        )
        candidate_limit = max(limit * (eff_cpf + 4), 50)
        if folder is not None or filters:
            # Post-filtering discards candidates; widen the pool so a
            # narrow folder/filter cannot starve the result list.
            candidate_limit = max(candidate_limit * 4, 200)
        raw_results = self._vectors.search_by_path(path, limit=candidate_limit)
        raw_results = self._post_filter_semantic_rows(
            raw_results, folder=folder, filters=filters
        )

        rows = self._adapt_semantic_rows(raw_results)
        # Skip downweight: grouping already dedupes multi-chunk docs; see #472.
        downweighted = _apply_length_downweight(rows, alpha=0.0)
        groups = _group_by_path(downweighted, chunks_per_file=eff_cpf, file_limit=limit)

        # snippet_words=0: similarity results return full section content.
        return self._assemble_grouped_results(
            groups, query="", snippet_words=0, search_type="semantic"
        )

    def _context_backlinks(
        self, path: str, link_limit: int
    ) -> builtins.list[BacklinkInfo]:
        """Collect backlinks for :meth:`get_context` (best effort).

        Uses the :class:`LinkManager` when wired (the production path —
        ``vault.py`` always injects one), else falls back to raw FTS rows.
        Failures are logged and yield an empty list rather than failing the
        whole dossier.
        """
        if self._link_manager is not None:
            try:
                return self._link_manager.get_backlinks(path, limit=link_limit)
            except (ValueError, sqlite3.OperationalError) as exc:
                logger.warning("get_context: backlinks for %s: %s", path, exc)
            return []
        try:
            backlinks = self._fts.get_backlinks(path, limit=link_limit)
            return [
                BacklinkInfo(
                    source_path=r["source_path"],
                    source_title=r["source_title"],
                    link_text=r["link_text"],
                    link_type=r["link_type"],
                    fragment=r["fragment"],
                    raw_target=r["raw_target"],
                )
                for r in backlinks
            ]
        except sqlite3.OperationalError as exc:
            logger.warning(
                "get_context: failed to retrieve backlinks for %s: %s",
                path,
                exc,
            )
        return []

    def _context_outlinks(
        self, path: str, link_limit: int
    ) -> builtins.list[OutlinkInfo]:
        """Collect outlinks for :meth:`get_context` (best effort).

        Mirror of :meth:`_context_backlinks` for the outgoing direction.
        """
        if self._link_manager is not None:
            try:
                return self._link_manager.get_outlinks(path, limit=link_limit)
            except (ValueError, sqlite3.OperationalError) as exc:
                logger.warning("get_context: outlinks for %s: %s", path, exc)
            return []
        try:
            outlinks = self._fts.get_outlinks(path, limit=link_limit)
            return [
                OutlinkInfo(
                    target_path=r["target_path"],
                    link_text=r["link_text"],
                    link_type=r["link_type"],
                    fragment=r["fragment"],
                    exists=bool(r["target_exists"]),
                    raw_target=r["raw_target"],
                )
                for r in outlinks
            ]
        except sqlite3.OperationalError as exc:
            logger.warning(
                "get_context: failed to retrieve outlinks for %s: %s",
                path,
                exc,
            )
        return []

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
            path: Relative path of the document (e.g.
                ``"notes/topic.md"``).
            similar_limit: Maximum number of similar notes to include.
            link_limit: Maximum number of backlinks and outlinks to include.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContext` object.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._validate_path(path)
        row = self._fts.get_note(path)
        if row is None:
            raise ValueError(f"Document not found: {path}")

        frontmatter = self._get_frontmatter(path)

        backlink_objs = self._context_backlinks(path, link_limit)
        outlink_objs = self._context_outlinks(path, link_limit)

        # Similar notes — field-collapsed via shared get_similar core so the
        # dossier never re-applies the cap on top of the cap (#469).  Use
        # chunks_per_file=1 to keep dossiers compact: one best section per
        # file gives the LLM enough to decide drill-worthiness.
        similar_grouped: list[GroupedResult] = []
        if (
            similar_limit > 0
            and self._embedding_provider is not None
            and self._embeddings_path is not None
        ):
            try:
                similar_grouped = self.get_similar(
                    path, limit=similar_limit, chunks_per_file=1
                )
            except ValueError as exc:
                # The outer guard + get_similar's own not-configured check mean
                # this only fires on a genuine internal failure (e.g. a corrupt
                # vector sidecar surfaced by _load_vectors()) — surface it at
                # WARNING instead of silently reducing the dossier to similar=[]
                # (#804). Not broadened to Exception: unexpected types propagate.
                logger.warning("get_context: get_similar failed for %s — %s", path, exc)

        # Folder peers — other notes in the same folder, capped.
        folder = row["folder"]
        folder_rows = self._fts.list_notes(folder=folder)
        folder_notes = [r["path"] for r in folder_rows if r["path"] != path][
            :_CONTEXT_FOLDER_PEERS_LIMIT
        ]

        # Tags — indexed frontmatter fields present on this document.
        tags: dict[str, list[str]] = {}
        for field in self._indexed_frontmatter_fields:
            value = frontmatter.get(field)
            if value is None:
                continue
            if isinstance(value, list):
                tags[field] = [str(v) for v in value]
            else:
                tags[field] = [str(value)]

        return NoteContext(
            path=path,
            title=row["title"],
            folder=folder,
            frontmatter=frontmatter,
            modified_at=row["modified_at"],
            backlinks=backlink_objs,
            outlinks=outlink_objs,
            similar=similar_grouped,
            folder_notes=folder_notes,
            tags=tags,
        )
