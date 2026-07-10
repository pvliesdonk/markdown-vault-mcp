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

from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError
from markdown_vault_mcp.managers._vector_loader import load_or_self_heal
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
    from collections.abc import Callable

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.managers.link import LinkManager
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


class _FolderBoostableRow(Protocol):
    """Row contract consumed by the folder-boost helper.

    :class:`~markdown_vault_mcp.types.FTSResult`, :class:`_SemanticRow`, and
    :class:`_GroupableFTS` all satisfy this Protocol structurally.  All
    callers are dataclasses so :func:`dataclasses.replace` is used to
    produce adjusted-score copies without mutating the input.
    """

    folder: str
    score: float


_FolderBoostableT = TypeVar("_FolderBoostableT", bound=_FolderBoostableRow)


def _folder_weight(folder: str, weights: dict[str, float]) -> float:
    """Return the weight of the deepest configured prefix matching *folder*.

    A prefix ``K`` matches folder ``F`` when ``F == K`` or ``F`` starts with
    ``K + "/"`` (boundary match, so ``"Project"`` never matches
    ``"Projects"``).  When several prefixes match, the longest (deepest)
    one wins.  No match returns ``1.0``.
    """
    best_key: str | None = None
    for key in weights:
        if (folder == key or folder.startswith(key + "/")) and (
            best_key is None or len(key) > len(best_key)
        ):
            best_key = key
    return 1.0 if best_key is None else weights[best_key]


def _apply_folder_boost(
    rows: list[_FolderBoostableT], *, weights: dict[str, float] | None
) -> list[_FolderBoostableT]:
    """Scale positive row scores by their folder's configured weight.

    Returns a new list re-sorted by descending adjusted score; input rows
    are not mutated (adjusted rows are :func:`dataclasses.replace` copies).
    Only positive scores are scaled — a negative score (possible only for
    raw cosine similarities) is left untouched so a demoting weight cannot
    accidentally promote it.  Empty/absent *weights* is the identity.
    """
    if not weights or not rows:
        return list(rows)

    out: list[_FolderBoostableT] = []
    for row in rows:
        weight = _folder_weight(row.folder, weights)
        if weight != 1.0 and row.score > 0:
            # Protocols can't promise __dataclass_fields__; the helper's
            # contract is "callers pass dataclasses" (FTSResult /
            # _SemanticRow / _GroupableFTS all are), enforced at runtime by
            # replace() itself.
            row = _dc_replace(row, score=row.score * weight)  # type: ignore[type-var]
        out.append(row)
    out.sort(key=lambda r: r.score, reverse=True)
    return out


class _GroupableRow(Protocol):
    """Row contract consumed by :func:`_group_by_path`.

    Adds ``heading``, ``start_line`` and ``section_id`` to the cap-helper's
    contract so grouped output preserves section identity and breaks score
    ties deterministically.  ``start_line`` defaults to ``0`` for legacy
    vector rows loaded from older .json sidecars; ``section_id`` is the
    final tie-break (the ``sections`` rowid) and is ``0`` for any channel
    that cannot resolve it (vector rows, legacy indices).
    """

    path: str
    heading: str | None
    score: float
    start_line: int
    section_id: int


_GroupableT = TypeVar("_GroupableT", bound=_GroupableRow)


def _group_by_path(
    rows: list[_GroupableT], *, chunks_per_file: int, file_limit: int
) -> list[list[_GroupableT]]:
    """Collapse score-desc rows into file groups.

    Walks ``rows`` (assumed already sorted DESC by score) and emits a list
    of groups.  Each group is a list of rows sharing the same ``path``,
    capped at ``chunks_per_file`` rows.  At most ``file_limit`` groups are
    returned.  Sections within a group are sorted ``(score DESC,
    start_line ASC, section_id ASC)`` so ties surface in document order.
    The ``section_id`` key (the ``sections`` rowid) makes the order fully
    deterministic even when chunks share a ``start_line`` — e.g. word-split
    fragments of a single oversize source line, which the chunker emits
    with identical ``start_line`` values.

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

    # Sort each group's sections by (score DESC, start_line ASC, section_id
    # ASC) so ties within a file surface in document order — section_id is
    # the final tie-break for chunks sharing a start_line.
    return [
        sorted(groups[p], key=lambda r: (-r.score, r.start_line, r.section_id))
        for p in order
    ]


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
    """Adapter row for vector search results so they expose .score / .chunk_count.

    ``section_id`` is always ``0``: the vector store keys chunks by metadata,
    not by ``sections`` rowid, and vector scores essentially never tie
    (distinct embeddings → distinct cosine), so the tie-break never engages
    for a pure semantic channel.  The field exists only to satisfy the
    :class:`_GroupableRow` protocol.
    """

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    chunk_count: int
    start_line: int = 0
    section_id: int = 0


@dataclass
class _GroupableFTS:
    """Adapter row exposing title/folder/content/start_line/section_id to
    _group_by_path for the keyword and hybrid channels."""

    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float
    start_line: int
    section_id: int = 0


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
        vectors = self._load_vectors()
        candidate_limit = max(limit * (chunks_per_file + 4), _SEMANTIC_CANDIDATE_FLOOR)
        raw = vectors.search(query, limit=candidate_limit)

        filtered = self._post_filter_semantic_rows(raw, folder=folder, filters=filters)

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
                section_id=0,  # vector store has no sections rowid; see dataclass
            )
            for r in filtered
        ]

        downweighted = _apply_length_downweight(
            rows, alpha=self._length_downweight_alpha
        )
        downweighted = _apply_folder_boost(downweighted, weights=self._folder_weights)
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
                section_id=0,  # vector store has no sections rowid; see dataclass
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

        # FTS results are walked before vector results, so chunk_meta's
        # setdefault keeps the keyword channel's section_id (a real sections
        # rowid >= 1) for any chunk present in both channels; a vector-only
        # chunk keeps section_id=0.  Both are correct: the keyword rowid is
        # the authoritative tie-break value, and vector-only ties are
        # measure-zero (distinct embeddings -> distinct cosine scores).
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
                    "section_id": fr.section_id,
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
                    "section_id": vr.section_id,
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

        exts = self._effective_attachment_extensions()
        attachments: list[AttachmentInfo] = []

        # Attachment scan runs without any lock — result is a best-effort
        # snapshot and is not atomic with the FTS note listing above.
        for abs_path in self._source_dir.rglob("*", **GLOB_SYMLINK_KWARGS):
            if not abs_path.is_file():
                continue
            if abs_path.suffix.lower() == ".md":
                continue
            suffix = abs_path.suffix.lstrip(".").lower()
            if "*" not in exts and suffix not in exts:
                continue
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
                section_id=0,  # vector store has no sections rowid; see dataclass
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
