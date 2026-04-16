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

from markdown_vault_mcp.types import (
    AttachmentInfo,
    BacklinkInfo,
    NoteContext,
    NoteInfo,
    OutlinkInfo,
    SearchResult,
    SimilarItem,
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
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

# RRF constant — standard value recommended in the original paper.
_RRF_K = 60

# Maximum folder peers returned by get_context().
_CONTEXT_FOLDER_PEERS_LIMIT = 20


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
    ) -> list[SearchResult]:
        """Search the collection.

        Args:
            query: Search string.
            limit: Maximum number of results to return.
            mode: ``"keyword"`` for BM25 FTS5, ``"semantic"`` for cosine
                similarity, or ``"hybrid"`` for Reciprocal Rank Fusion of
                both.
            filters: Dict of ``{frontmatter_key: value}`` pairs (AND
                semantics).  Only works for fields in
                ``indexed_frontmatter_fields``.
            folder: If provided, restrict results to documents in this
                folder (and its sub-folders).

        Returns:
            List of :class:`~markdown_vault_mcp.types.SearchResult` ordered
            by relevance.

        Raises:
            ValueError: If *mode* is ``"semantic"`` or ``"hybrid"`` but no
                embedding provider or embeddings path is configured.
        """
        if mode == "keyword":
            return self._keyword_search(
                query, limit=limit, filters=filters, folder=folder
            )

        if mode == "semantic":
            self._require_vectors()
            return self._semantic_search(
                query, limit=limit, filters=filters, folder=folder
            )

        # hybrid
        self._require_vectors()
        return self._hybrid_search(query, limit=limit, filters=filters, folder=folder)

    def _keyword_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None,
        folder: str | None,
    ) -> list[SearchResult]:
        fts_results = self._fts.search(
            query, limit=limit, filters=filters, folder=folder
        )
        return [
            SearchResult(
                path=r.path,
                title=r.title,
                folder=r.folder,
                heading=r.heading,
                content=r.content,
                score=r.score,
                search_type="keyword",
                frontmatter=self._get_frontmatter(r.path),
            )
            for r in fts_results
        ]

    def _semantic_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None = None,
        folder: str | None = None,
    ) -> list[SearchResult]:
        # Flush deferred embedding updates so results are consistent.
        self._flush_embeddings()
        vectors = self._load_vectors()
        # Fetch extra candidates so post-filtering still yields *limit* results.
        candidate_limit = max(limit * 3, 30) if (folder or filters) else limit
        raw = vectors.search(query, limit=candidate_limit)

        results: list[SearchResult] = []
        for r in raw:
            if len(results) >= limit:
                break

            # Apply folder prefix filter.
            if folder is not None:
                r_folder = r.get("folder", "")
                if r_folder != folder and not r_folder.startswith(folder + "/"):
                    continue

            # Apply tag filters: check FTS index for each required tag.
            if filters:
                note_row = self._fts.get_note(r["path"])
                if note_row is None:
                    continue
                fm_raw = note_row.get("frontmatter_json")
                fm: dict[str, Any] = {}
                if fm_raw:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        fm = json.loads(fm_raw)
                match = True
                for key, value in filters.items():
                    fm_val = fm.get(key)
                    if fm_val is None:
                        match = False
                        break
                    # Support both scalar and list values.
                    if isinstance(fm_val, list):
                        if str(value) not in [str(v) for v in fm_val]:
                            match = False
                            break
                    else:
                        if str(fm_val) != str(value):
                            match = False
                            break
                if not match:
                    continue

            results.append(
                SearchResult(
                    path=r["path"],
                    title=r["title"],
                    folder=r["folder"],
                    heading=r.get("heading"),
                    content=r["content"],
                    score=r["score"],
                    search_type="semantic",
                    frontmatter=self._get_frontmatter(r["path"]),
                )
            )
        return results

    def _hybrid_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None,
        folder: str | None,
    ) -> list[SearchResult]:
        """RRF merge of keyword and semantic results.

        Each result set is ranked independently.  Merged score:
        ``1 / (k + rank)`` where k=60.  Results appearing in both sets
        have their scores summed.  Returns top *limit* by total RRF score.
        """
        # Fetch more candidates than needed so RRF has enough to rank.
        candidate_limit = max(limit * 2, 20)

        # Flush deferred embedding updates so results are consistent.
        self._flush_embeddings()

        fts_results = self._fts.search(
            query, limit=candidate_limit, filters=filters, folder=folder
        )
        vectors = self._load_vectors()
        vec_results = vectors.search(query, limit=candidate_limit)

        # Build a key for deduplication: (path, heading) identifies a chunk.
        # Use a dict to accumulate RRF scores and store metadata.
        rrf_scores: dict[tuple[str, str | None], float] = {}
        # Store the best metadata dict keyed by (path, heading).
        chunk_meta: dict[tuple[str, str | None], dict[str, Any]] = {}

        for rank, r in enumerate(fts_results, start=1):
            key = (r.path, r.heading)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            if key not in chunk_meta:
                chunk_meta[key] = {
                    "path": r.path,
                    "title": r.title,
                    "folder": r.folder,
                    "heading": r.heading,
                    "content": r.content,
                    "search_type": "keyword",
                }

        for rank, vr in enumerate(vec_results, start=1):
            # Apply folder prefix filter to semantic results.
            if folder is not None:
                vr_folder = vr.get("folder", "")
                if vr_folder != folder and not vr_folder.startswith(folder + "/"):
                    continue

            # Apply tag filters to semantic results via frontmatter lookup.
            if filters:
                note_row = self._fts.get_note(vr["path"])
                if note_row is None:
                    continue
                fm_raw = note_row.get("frontmatter_json")
                fm: dict[str, Any] = {}
                if fm_raw:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        fm = json.loads(fm_raw)
                skip = False
                for fk, fv in filters.items():
                    fm_val = fm.get(fk)
                    if fm_val is None:
                        skip = True
                        break
                    if isinstance(fm_val, list):
                        if str(fv) not in [str(v) for v in fm_val]:
                            skip = True
                            break
                    else:
                        if str(fm_val) != str(fv):
                            skip = True
                            break
                if skip:
                    continue

            heading = vr.get("heading")
            vkey = (vr["path"], heading)
            rrf_scores[vkey] = rrf_scores.get(vkey, 0.0) + 1.0 / (_RRF_K + rank)
            if vkey not in chunk_meta:
                chunk_meta[vkey] = {
                    "path": vr["path"],
                    "title": vr["title"],
                    "folder": vr["folder"],
                    "heading": heading,
                    "content": vr["content"],
                    "search_type": "semantic",
                }

        # Sort by descending RRF score, take top limit.
        sorted_keys = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)[
            :limit
        ]

        return [
            SearchResult(
                path=chunk_meta[k]["path"],
                title=chunk_meta[k]["title"],
                folder=chunk_meta[k]["folder"],
                heading=chunk_meta[k]["heading"],
                content=chunk_meta[k]["content"],
                score=rrf_scores[k],
                search_type=chunk_meta[k]["search_type"],
                frontmatter=self._get_frontmatter(chunk_meta[k]["path"]),
            )
            for k in sorted_keys
        ]

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

    def get_similar(self, path: str, *, limit: int = 10) -> builtins.list[SearchResult]:
        """Return the most semantically similar chunks from other documents.

        Uses the stored embedding vectors for ``path`` (averaged across
        chunks) to compute cosine similarity against all other documents.
        No re-embedding is needed.  Results are at chunk granularity —
        the same document may appear multiple times if it has many chunks.

        Args:
            path: Relative path of the reference document.
            limit: Maximum number of results to return.

        Returns:
            List of :class:`~markdown_vault_mcp.types.SearchResult` objects
            ordered by descending similarity.  Returns ``[]`` when
            embeddings are not configured or the document has no stored
            vectors.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._validate_path(path)
        if self._fts.get_note(path) is None:
            raise ValueError(f"Document not found: {path}")

        if self._embedding_provider is None or self._embeddings_path is None:
            return []

        self._load_vectors()
        if self._vectors is None or self._vectors.count == 0:
            return []

        raw_results = self._vectors.search_by_path(path, limit=limit)
        return [
            SearchResult(
                path=r["path"],
                title=r.get("title", ""),
                folder=r.get("folder", ""),
                heading=r.get("heading"),
                content=r.get("content", ""),
                score=r.get("score", 0.0),
                search_type="semantic",
                frontmatter=self._get_frontmatter(r["path"]),
            )
            for r in raw_results
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

        # Similar notes — empty if embeddings not configured or
        # similar_limit is 0.
        similar_dicts: list[SimilarItem] = []
        if (
            similar_limit > 0
            and self._embedding_provider is not None
            and self._embeddings_path is not None
        ):
            self._load_vectors()
            if self._vectors is not None and self._vectors.count > 0:
                raw = self._vectors.search_by_path(path, limit=similar_limit)
                similar_dicts = [
                    SimilarItem(
                        path=r["path"],
                        title=r["title"],
                        score=r["score"],
                    )
                    for r in raw
                ]

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
            similar=similar_dicts,
            folder_notes=folder_notes,
            tags=tags,
        )
