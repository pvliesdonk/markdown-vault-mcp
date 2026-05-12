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
import math
import mimetypes
import re as _re
import sqlite3
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar

from markdown_vault_mcp.types import (
    AttachmentInfo,
    BacklinkInfo,
    GroupedResult,
    NoteContext,
    NoteInfo,
    OutlinkInfo,
    SectionHit,
)
from markdown_vault_mcp.utils import (
    effective_attachment_extensions,
    fts_row_to_note_info,
    is_path_excluded,
    validate_path,
)

if TYPE_CHECKING:
    import builtins
    from collections.abc import Callable

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.managers.link import LinkManager
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.types import FTSResult
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

# RRF constant — standard value recommended in the original paper.
_RRF_K = 60

# Regex for extracting query tokens (alphanumeric sequences).
_QUERY_TOKEN_RE = _re.compile(r"[A-Za-z0-9]+")

# Maximum folder peers returned by get_context().
_CONTEXT_FOLDER_PEERS_LIMIT = 20


class _ScorableRow(Protocol):
    """Row contract consumed by the length-downweight helper.

    Both :class:`~markdown_vault_mcp.types.FTSResult` and the local
    :class:`_SemanticRow` adapter satisfy this Protocol structurally; no
    nominal subclassing required.  All callers are dataclasses so
    :func:`dataclasses.replace` is used to produce adjusted-score copies
    without mutating the input.
    """

    score: float
    chunk_count: int


_ScorableT = TypeVar("_ScorableT", bound=_ScorableRow)


def _apply_length_downweight(
    rows: list[_ScorableT], *, alpha: float
) -> list[_ScorableT]:
    """Re-rank ``rows`` by ``score / (1 + alpha * log(chunk_count))``.

    Returns a new list sorted by descending adjusted score; input is not
    mutated.  Callers must pass dataclass instances (every caller in this
    codebase already does) so :func:`dataclasses.replace` can produce the
    adjusted-score copies.
    """
    if alpha <= 0 or not rows:
        return list(rows)

    adjusted: list[tuple[_ScorableT, float]] = []
    for row in rows:
        chunk_count = max(1, row.chunk_count)
        # log(1) = 0 -> factor = 1 -> no change for single-chunk docs.
        factor = 1.0 + alpha * math.log(chunk_count)
        new_score = row.score / factor
        # Protocols can't promise __dataclass_fields__; the helper's
        # contract is "callers pass dataclasses" (FTSResult / _SemanticRow
        # both are), enforced at runtime by replace() itself.
        new_row = _dc_replace(row, score=new_score)  # type: ignore[type-var]
        adjusted.append((new_row, new_score))

    adjusted.sort(key=lambda t: t[1], reverse=True)
    return [r for r, _ in adjusted]


class _GroupableRow(Protocol):
    """Row contract consumed by :func:`_group_by_path`.

    Adds ``heading`` and ``start_line`` to the cap-helper's contract so
    grouped output preserves section identity and breaks score ties
    deterministically.  ``start_line`` defaults to ``0`` for legacy vector
    rows loaded from older .json sidecars.
    """

    path: str
    heading: str | None
    score: float
    start_line: int


_GroupableT = TypeVar("_GroupableT", bound=_GroupableRow)


def _group_by_path(
    rows: list[_GroupableT], *, chunks_per_file: int, file_limit: int
) -> list[list[_GroupableT]]:
    """Collapse score-desc rows into file groups.

    Walks ``rows`` (assumed already sorted DESC by score) and emits a list
    of groups.  Each group is a list of rows sharing the same ``path``,
    capped at ``chunks_per_file`` rows.  At most ``file_limit`` groups are
    returned.  Sections within a group are sorted ``(score DESC,
    start_line ASC)`` so ties surface in document order.

    Args:
        rows: Rows pre-sorted by descending score.
        chunks_per_file: Maximum rows per group; must be >= 1.
        file_limit: Maximum number of groups emitted.

    Returns:
        List of groups; outer order = file rank (best file first).

    Raises:
        ValueError: If ``chunks_per_file`` < 1.
    """
    if chunks_per_file < 1:
        raise ValueError(f"chunks_per_file must be >= 1, got {chunks_per_file}")

    groups: dict[str, list[_GroupableT]] = {}
    order: list[str] = []
    for row in rows:
        existing = groups.get(row.path)
        if existing is None:
            if len(order) >= file_limit:
                continue
            order.append(row.path)
            groups[row.path] = [row]
        elif len(existing) < chunks_per_file:
            existing.append(row)

    # Sort each group's sections by (score DESC, start_line ASC) so ties
    # within a file surface in document order.
    return [sorted(groups[p], key=lambda r: (-r.score, r.start_line)) for p in order]


def _compute_snippet_for_semantic(
    content: str, query: str, *, snippet_words: int
) -> str:
    """Pick a ``snippet_words``-wide window from ``content``.

    Returns the full content when ``snippet_words`` is 0, when the chunk is
    already shorter, or as a fallback when no query tokens overlap (in which
    case the first ``snippet_words`` words are returned with a trailing
    ellipsis).

    Uses simple case-insensitive substring matching on alphanumeric tokens.
    """
    if snippet_words <= 0:
        return content

    words = content.split()
    if len(words) <= snippet_words:
        return content

    # Tokenize the query into both the joined-per-word form (matches our
    # content normalization, e.g. "isn't" → "isnt") AND the individual
    # alphanumeric runs (matches per-token content words, e.g. "se-cura"
    # → {"se", "cura"} so a chunk that mentions "cura" alone still hits).
    query_tokens: set[str] = set()
    for word in query.split():
        runs = _QUERY_TOKEN_RE.findall(word)
        if not runs:
            continue
        # Joined form: runs concatenated.
        query_tokens.add("".join(runs).lower())
        # Individual runs: each alphanumeric span.
        query_tokens.update(r.lower() for r in runs)
    query_tokens.discard("")
    if not query_tokens:
        return " ".join(words[:snippet_words]) + "…"

    # Normalise each word: keep alphanumeric chars, lower-case, fall back to
    # the lowercased original if no alphanumeric chars were found.
    lower_words = [
        "".join(_QUERY_TOKEN_RE.findall(w)).lower() or w.lower() for w in words
    ]

    # Sliding window: maintain best_start / best_score, update incrementally.
    best_start = 0
    best_score = sum(1 for w in lower_words[:snippet_words] if w in query_tokens)
    cur_score = best_score
    for i in range(1, len(words) - snippet_words + 1):
        if lower_words[i - 1] in query_tokens:
            cur_score -= 1
        if lower_words[i + snippet_words - 1] in query_tokens:
            cur_score += 1
        if cur_score > best_score:
            best_score = cur_score
            best_start = i

    if best_score == 0:
        # No literal overlap anywhere — fall back to first-N words.
        return " ".join(words[:snippet_words]) + "…"

    snippet = " ".join(words[best_start : best_start + snippet_words])
    if best_start > 0:
        snippet = "…" + snippet
    if best_start + snippet_words < len(words):
        snippet = snippet + "…"
    return snippet


@dataclass
class _SemanticRow:
    """Adapter row for vector search results so they expose .score / .chunk_count."""

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    chunk_count: int
    start_line: int = 0


@dataclass
class _GroupableFTS:
    """Adapter row exposing title/folder/content/start_line to _group_by_path
    for the keyword channel."""

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    start_line: int


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
        flush_embeddings: Callback to flush deferred embedding updates
            before semantic search.
        rebuild_embeddings: Callback to rebuild all embeddings from scratch
            (used when vector index compatibility fails).
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
        flush_embeddings: Callable[[], None] | None = None,
        rebuild_embeddings: Callable[[], None] | None = None,
        chunks_per_file: int = 2,
        snippet_words: int = 200,
        length_downweight_alpha: float = 0.25,
    ) -> None:
        self._fts = fts
        self._source_dir = source_dir
        self._embeddings_path = embeddings_path
        self._embedding_provider = embedding_provider
        self._indexed_frontmatter_fields: list[str] = indexed_frontmatter_fields or []
        self._exclude_patterns = exclude_patterns
        self._attachment_extensions = attachment_extensions
        self._link_manager = link_manager
        self._flush_embeddings = flush_embeddings or (lambda: None)
        self._rebuild_embeddings = rebuild_embeddings or (lambda: None)
        self._chunks_per_file = chunks_per_file
        self._snippet_words = snippet_words
        self._length_downweight_alpha = length_downweight_alpha

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

        if self._embeddings_path is None or self._embedding_provider is None:
            raise RuntimeError(
                "_require_vectors() must be called before _load_vectors()"
            )

        npy_path = Path(str(self._embeddings_path) + ".npy")
        if npy_path.exists():
            try:
                self._vectors = VectorIndex.load(
                    self._embeddings_path, self._embedding_provider
                )
                logger.info("Loaded vector index from %s", self._embeddings_path)
            except VectorIndexCompatibilityError as exc:
                logger.warning("%s Rebuilding embeddings.", exc)
                self._rebuild_embeddings()
                if self._vectors is None:
                    raise ValueError(
                        "Failed to rebuild vector index after a compatibility error."
                    ) from exc
        else:
            self._vectors = VectorIndex(self._embedding_provider)
            logger.info("No vector index on disk; created empty VectorIndex")

        return self._vectors

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
        raw = row.get("frontmatter_json")
        if not raw:
            return {}
        try:
            result: dict[str, Any] = json.loads(raw)
            return result
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "_get_frontmatter: invalid JSON for %s — %s",
                row.get("path"),
                exc,
            )
            return {}

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
        """Search the collection.

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
            ValueError: If *mode* is ``"semantic"`` or ``"hybrid"`` but no
                embedding provider or embeddings path is configured.
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
        groupable: list[_GroupableFTS] = [
            _GroupableFTS(
                path=r.path,
                title=r.title,
                folder=r.folder,
                heading=r.heading,
                content=r.content,
                score=r.score,
                start_line=r.start_line,
            )
            for r in downweighted
        ]
        groups = _group_by_path(
            groupable, chunks_per_file=chunks_per_file, file_limit=limit
        )

        if snippet_words > 0:
            survivor_rows = [r for g in groups for r in g]
            survivor_keys = {(r.path, r.heading) for r in survivor_rows}
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

        out: list[GroupedResult] = []
        for group in groups:
            sections: list[SectionHit] = []
            for r in group:
                key = (r.path, r.heading)
                if key in snippets_by_key:
                    content = snippets_by_key[key]
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
            out.append(
                GroupedResult(
                    path=head.path,
                    title=head.title,
                    folder=head.folder,
                    score=max(s.score for s in sections),
                    search_type="keyword",
                    frontmatter=self._get_frontmatter(head.path),
                    sections=sections,
                )
            )
        return out

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
        self._flush_embeddings()
        vectors = self._load_vectors()
        candidate_limit = max(limit * (chunks_per_file + 4), 50)
        raw = vectors.search(query, limit=candidate_limit)

        filtered: list[dict[str, Any]] = []
        for r in raw:
            if folder is not None:
                r_folder = r.get("folder", "")
                if r_folder != folder and not r_folder.startswith(folder + "/"):
                    continue
            if filters and not self._row_matches_filters(r["path"], filters):
                continue
            filtered.append(r)

        chunk_counts = self._fts.get_chunk_counts({r["path"] for r in filtered})
        rows: list[_SemanticRow] = [
            _SemanticRow(
                path=r["path"],
                title=r["title"],
                folder=r["folder"],
                heading=r.get("heading"),
                content=r["content"],
                score=r["score"],
                chunk_count=chunk_counts.get(r["path"], 1),
                start_line=int(r.get("start_line", 0)),
            )
            for r in filtered
        ]

        downweighted = _apply_length_downweight(
            rows, alpha=self._length_downweight_alpha
        )
        groups = _group_by_path(
            downweighted, chunks_per_file=chunks_per_file, file_limit=limit
        )

        out: list[GroupedResult] = []
        for group in groups:
            sections = [
                SectionHit(
                    heading=r.heading,
                    content=_compute_snippet_for_semantic(
                        r.content, query, snippet_words=snippet_words
                    ),
                    score=r.score,
                )
                for r in group
            ]
            head = group[0]
            out.append(
                GroupedResult(
                    path=head.path,
                    title=head.title,
                    folder=head.folder,
                    score=max(s.score for s in sections),
                    search_type="semantic",
                    frontmatter=self._get_frontmatter(head.path),
                    sections=sections,
                )
            )
        return out

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
        self._flush_embeddings()
        candidate_limit = max(limit * (chunks_per_file + 4), 50)

        fts_raw: list[FTSResult] = self._fts.search(
            query,
            limit=candidate_limit,
            filters=filters,
            folder=folder,
            snippet_words=None,
        )
        fts_results: list[FTSResult] = _apply_length_downweight(
            fts_raw, alpha=self._length_downweight_alpha
        )

        vectors = self._load_vectors()
        vec_raw = vectors.search(query, limit=candidate_limit)
        vec_filtered: list[dict[str, Any]] = []
        for r in vec_raw:
            if folder is not None:
                r_folder = r.get("folder", "")
                if r_folder != folder and not r_folder.startswith(folder + "/"):
                    continue
            if filters and not self._row_matches_filters(r["path"], filters):
                continue
            vec_filtered.append(r)

        vec_chunk_counts = self._fts.get_chunk_counts({r["path"] for r in vec_filtered})
        vec_rows: list[_SemanticRow] = [
            _SemanticRow(
                path=r["path"],
                title=r["title"],
                folder=r["folder"],
                heading=r.get("heading"),
                content=r["content"],
                score=r["score"],
                chunk_count=vec_chunk_counts.get(r["path"], 1),
                start_line=int(r.get("start_line", 0)),
            )
            for r in vec_filtered
        ]
        vec_rows = _apply_length_downweight(
            vec_rows, alpha=self._length_downweight_alpha
        )

        rrf_scores: dict[tuple[str, str | None], float] = {}
        chunk_meta: dict[tuple[str, str | None], dict[str, Any]] = {}
        keyword_keys: set[tuple[str, str | None]] = set()
        vec_keys: set[tuple[str, str | None]] = set()

        for rank, fr in enumerate(fts_results, start=1):
            key = (fr.path, fr.heading)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            keyword_keys.add(key)
            chunk_meta.setdefault(
                key,
                {
                    "path": fr.path,
                    "title": fr.title,
                    "folder": fr.folder,
                    "heading": fr.heading,
                    "content": fr.content,
                    "search_type": "keyword",
                    "start_line": fr.start_line,
                },
            )

        for rank, vr in enumerate(vec_rows, start=1):
            vkey = (vr.path, vr.heading)
            rrf_scores[vkey] = rrf_scores.get(vkey, 0.0) + 1.0 / (_RRF_K + rank)
            vec_keys.add(vkey)
            chunk_meta.setdefault(
                vkey,
                {
                    "path": vr.path,
                    "title": vr.title,
                    "folder": vr.folder,
                    "heading": vr.heading,
                    "content": vr.content,
                    "search_type": "semantic",
                    "start_line": vr.start_line,
                },
            )

        for key in keyword_keys & vec_keys:
            chunk_meta[key]["search_type"] = "hybrid"

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
            )
            for k in sorted_keys
        ]
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
                candidate_limit=candidate_limit,
            )

        out: list[GroupedResult] = []
        for group in groups:
            sections: list[SectionHit] = []
            for gr in group:
                key = (gr.path, gr.heading)
                meta = chunk_meta[key]
                if key in snippet_map:
                    content = snippet_map[key]
                elif snippet_words > 0:
                    content = _compute_snippet_for_semantic(
                        meta["content"], query, snippet_words=snippet_words
                    )
                else:
                    content = meta["content"]
                sections.append(
                    SectionHit(heading=gr.heading, content=content, score=gr.score)
                )
            head = group[0]
            head_meta = chunk_meta[(head.path, head.heading)]
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
                file_search_type: Literal["keyword", "semantic", "hybrid"] = "hybrid"
            elif in_keyword:
                file_search_type = "keyword"
            else:
                file_search_type = "semantic"
            out.append(
                GroupedResult(
                    path=head.path,
                    title=head_meta["title"],
                    folder=head_meta["folder"],
                    score=max(s.score for s in sections),
                    search_type=file_search_type,
                    frontmatter=self._get_frontmatter(head.path),
                    sections=sections,
                )
            )
        return out

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
        rows = self._fts.list_notes(folder=folder)
        notes: list[NoteInfo | AttachmentInfo] = [
            fts_row_to_note_info(row) for row in rows
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
            # Skip files where any path component starts with ".".
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
                    "_list_attachments: skipping %s — stat error (%s)",
                    abs_path,
                    exc,
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

    def list_folders(self) -> builtins.list[str]:
        """Return all distinct folder values across the indexed collection.

        Returns:
            Sorted list of folder strings (``""`` for the collection root).
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
        raw_results = self._vectors.search_by_path(path, limit=candidate_limit)

        chunk_counts = self._fts.get_chunk_counts({r["path"] for r in raw_results})
        rows: list[_SemanticRow] = [
            _SemanticRow(
                path=r["path"],
                title=r.get("title", ""),
                folder=r.get("folder", ""),
                heading=r.get("heading"),
                content=r.get("content", ""),
                score=r.get("score", 0.0),
                chunk_count=chunk_counts.get(r["path"], 1),
                start_line=int(r.get("start_line", 0)),
            )
            for r in raw_results
        ]
        # Skip downweight: grouping already dedupes multi-chunk docs; see #472.
        downweighted = _apply_length_downweight(rows, alpha=0.0)
        groups = _group_by_path(downweighted, chunks_per_file=eff_cpf, file_limit=limit)

        return [
            GroupedResult(
                path=group[0].path,
                title=group[0].title,
                folder=group[0].folder,
                score=max(r.score for r in group),
                search_type="semantic",
                frontmatter=self._get_frontmatter(group[0].path),
                sections=[
                    SectionHit(heading=r.heading, content=r.content, score=r.score)
                    for r in group
                ],
            )
            for group in groups
        ]

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

        # Backlinks — via LinkManager if available, else direct FTS.
        backlink_objs: list[BacklinkInfo] = []
        if self._link_manager is not None:
            try:
                backlink_objs = self._link_manager.get_backlinks(path, limit=link_limit)
            except (ValueError, sqlite3.OperationalError) as exc:
                logger.warning("get_context: backlinks for %s: %s", path, exc)
        else:
            try:
                backlinks = self._fts.get_backlinks(path, limit=link_limit)
                backlink_objs = [
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

        # Outlinks — via LinkManager if available, else direct FTS.
        outlink_objs: list[OutlinkInfo] = []
        if self._link_manager is not None:
            try:
                outlink_objs = self._link_manager.get_outlinks(path, limit=link_limit)
            except (ValueError, sqlite3.OperationalError) as exc:
                logger.warning("get_context: outlinks for %s: %s", path, exc)
        else:
            try:
                outlinks = self._fts.get_outlinks(path, limit=link_limit)
                outlink_objs = [
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
            except ValueError:
                logger.debug("get_context: get_similar raised for %s, similar=[]", path)

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
