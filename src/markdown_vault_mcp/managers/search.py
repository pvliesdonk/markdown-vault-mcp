"""Manager for document search and listing operations."""

from __future__ import annotations

import contextlib
import fnmatch
import json
import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from markdown_vault_mcp.exceptions import DocumentNotFoundError
from markdown_vault_mcp.types import (
    AttachmentInfo,
    BacklinkInfo,
    NoteContext,
    NoteInfo,
    OutlinkInfo,
    SearchResult,
    SimilarItem,
)

if TYPE_CHECKING:
    from markdown_vault_mcp.collection import Collection
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant (k=60 is standard).
_RRF_K = 60

# Max notes to include from the same folder in NoteContext.
_CONTEXT_FOLDER_PEERS_LIMIT = 20


class SearchManager:
    """Handles keyword (FTS5), semantic (vector), and hybrid search, and listings."""

    def __init__(self, collection: Collection) -> None:
        self._collection = collection

    def list_documents(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
    ) -> list[NoteInfo | AttachmentInfo]:
        """List documents (and optionally attachments) in the collection."""
        self._collection._ensure_initialized()

        rows = self._collection._fts.list_notes(folder=folder)
        notes: list[NoteInfo | AttachmentInfo] = [
            NoteInfo.from_fts_row(row) for row in rows
        ]

        if pattern:
            notes = [n for n in notes if fnmatch.fnmatch(n.path, pattern)]

        if not include_attachments:
            return notes

        exts = self._collection._effective_attachment_extensions()
        source_resolved = self._collection._source_dir.resolve()
        attachments: list[AttachmentInfo] = []

        # Attachment scan runs outside _write_lock — result is a best-effort
        # snapshot and is not atomic with the FTS note listing above.
        scan_root = self._collection._source_dir
        if folder:
            scan_root = self._collection._source_dir / folder
            if not scan_root.is_dir():
                return notes

        for abs_path in scan_root.rglob("*"):
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
            if any(part.startswith(".") for part in rel.parts):
                continue
            if self._collection._is_path_excluded(rel.as_posix()):
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
                    "_list_attachments: skipping %s — stat error (%s)", abs_path, exc
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

    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> list[NoteInfo]:
        """Return the most recently modified documents."""
        self._collection._ensure_initialized()
        rows = self._collection._fts.get_recent(limit=limit, folder=folder)
        return [NoteInfo.from_fts_row(row) for row in rows]

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed collection."""
        self._collection._ensure_initialized()
        return self._collection._fts.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field."""
        self._collection._ensure_initialized()
        return self._collection._fts.list_field_values(field)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
        filters: dict[str, str] | None = None,
        folder: str | None = None,
    ) -> list[SearchResult]:
        """Search the collection."""
        self._collection._ensure_initialized()

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

    def get_similar(self, path: str, *, limit: int = 10) -> list[SearchResult]:
        """Return the most semantically similar chunks from other documents."""
        self._collection._ensure_initialized()
        self._collection._validate_path(path)
        if self._collection._fts.get_note(path) is None:
            raise DocumentNotFoundError(f"Document not found: {path}")

        if (
            self._collection._embedding_provider is None
            or self._collection._embeddings_path is None
        ):
            return []

        vectors = self._load_vectors()
        if vectors is None or vectors.count == 0:
            return []

        raw_results = vectors.search_by_path(path, limit=limit)
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

    def _require_vectors(self) -> None:
        """Raise ValueError if semantic search is not configured."""
        if (
            self._collection._embedding_provider is None
            or self._collection._embeddings_path is None
        ):
            raise ValueError(
                "Semantic search requires both 'embedding_provider' and "
                "'embeddings_path' to be configured."
            )

    def _load_vectors(self) -> VectorIndex:
        """Load or return the cached VectorIndex."""
        return self._collection._load_vectors()

    def _keyword_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None,
        folder: str | None,
    ) -> list[SearchResult]:
        fts_results = self._collection._fts.search(
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
        self._collection._flush_dirty_embeddings()
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
                note_row = self._collection._fts.get_note(r["path"])
                if note_row is None:
                    continue
                fm_raw = note_row.get("frontmatter_json")
                fm_data: dict[str, Any] = {}
                if fm_raw:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        fm_data = json.loads(fm_raw)
                match = True
                for key, value in filters.items():
                    fm_val = fm_data.get(key)
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
        """RRF merge of keyword and semantic results."""
        # Fetch more candidates than needed so RRF has enough to rank.
        # Use a larger buffer when filters are active to combat rank bias.
        candidate_limit = (
            max(limit * 3, 30) if (folder or filters) else max(limit * 2, 20)
        )

        # Flush deferred embedding updates so results are consistent.
        self._collection._flush_dirty_embeddings()

        fts_results = self._collection._fts.search(
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

        for rank, vec_r in enumerate(vec_results, start=1):
            # Apply folder prefix filter to semantic results.
            if folder is not None:
                r_folder = vec_r.get("folder", "")
                if r_folder != folder and not r_folder.startswith(folder + "/"):
                    continue

            # Apply tag filters to semantic results via frontmatter lookup.
            if filters:
                note_row = self._collection._fts.get_note(vec_r["path"])
                if note_row is None:
                    continue
                fm_raw = note_row.get("frontmatter_json")
                fm_data: dict[str, Any] = {}
                if fm_raw:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        fm_data = json.loads(fm_raw)
                skip = False
                for filter_key, filter_value in filters.items():
                    fm_val = fm_data.get(filter_key)
                    if fm_val is None:
                        skip = True
                        break
                    if isinstance(fm_val, list):
                        if str(filter_value) not in [str(v) for v in fm_val]:
                            skip = True
                            break
                    else:
                        if str(fm_val) != str(filter_value):
                            skip = True
                            break
                if skip:
                    continue

            vec_heading = vec_r.get("heading")
            vec_key = (vec_r["path"], vec_heading)
            rrf_scores[vec_key] = rrf_scores.get(vec_key, 0.0) + 1.0 / (_RRF_K + rank)
            if vec_key not in chunk_meta:
                chunk_meta[vec_key] = {
                    "path": vec_r["path"],
                    "title": vec_r["title"],
                    "folder": vec_r["folder"],
                    "heading": vec_heading,
                    "content": vec_r["content"],
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

    def _get_frontmatter(self, path: str) -> dict[str, Any]:
        """Return the frontmatter dict for a document from the FTS index."""
        row = self._collection._fts.get_note(path)
        if row is None:
            return {}
        raw = row.get("frontmatter_json")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return {}
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "_get_frontmatter: invalid JSON for %s — %s", row.get("path"), exc
            )
            return {}

    def get_context(
        self,
        path: str,
        *,
        similar_limit: int = 5,
        link_limit: int = 10,
    ) -> NoteContext:
        """Return a consolidated context dossier for a document."""
        import sqlite3

        self._collection._ensure_initialized()
        self._collection._validate_path(path)
        row = self._collection._fts.get_note(path)
        if row is None:
            raise DocumentNotFoundError(f"Document not found: {path}")

        fm_data = self._get_frontmatter(path)

        # Backlinks — capped at link_limit; graceful if links table absent.
        try:
            backlinks = self._collection._fts.get_backlinks(path, limit=link_limit)
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
                "get_context: failed to retrieve backlinks for %s: %s", path, exc
            )
            backlink_objs = []

        # Outlinks — capped at link_limit; graceful if links table absent.
        try:
            outlinks = self._collection._fts.get_outlinks(path, limit=link_limit)
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
                "get_context: failed to retrieve outlinks for %s: %s", path, exc
            )
            outlink_objs = []

        # Similar notes — empty if embeddings not configured or similar_limit is 0.
        similar_dicts: list[SimilarItem] = []
        if (
            similar_limit > 0
            and self._collection._embedding_provider is not None
            and self._collection._embeddings_path is not None
        ):
            vectors = self._load_vectors()
            if vectors is not None and vectors.count > 0:
                raw = vectors.search_by_path(path, limit=similar_limit)
                similar_dicts = [
                    SimilarItem(
                        path=r["path"],
                        title=r["title"],
                        score=r["score"],
                    )
                    for r in raw
                ]

        # Folder peers — other notes in the same folder, capped at limit.
        folder = row["folder"]
        folder_rows = self._collection._fts.list_notes(folder=folder)
        folder_notes = [r["path"] for r in folder_rows if r["path"] != path][
            :_CONTEXT_FOLDER_PEERS_LIMIT
        ]

        # Tags — indexed frontmatter fields present on this document.
        tags: dict[str, list[str]] = {}
        for field in self._collection._indexed_frontmatter_fields:
            value = fm_data.get(field)
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
            frontmatter=fm_data,
            modified_at=row["modified_at"],
            backlinks=backlink_objs,
            outlinks=outlink_objs,
            similar=similar_dicts,
            folder_notes=folder_notes,
            tags=tags,
        )
