"""Manager for FTS and Vector index lifecycle operations."""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.fts_index import _derive_folder
from markdown_vault_mcp.scanner import parse_note, scan_directory
from markdown_vault_mcp.types import (
    IndexStats,
    ParsedNote,
    ReindexResult,
)

if TYPE_CHECKING:
    from markdown_vault_mcp.collection import Collection

logger = logging.getLogger(__name__)

# Maximum chunks per embedding provider call.
_EMBEDDING_BATCH_SIZE = 4

# Seconds between automatic background flushes of dirty embeddings to disk.
_EMBEDDING_FLUSH_INTERVAL = 30


class IndexManager:
    """Handles indexing lifecycle, reindexing, and embedding builds."""

    def __init__(self, collection: Collection) -> None:
        self._collection = collection

    def sync_from_remote_before_index(self) -> None:
        """One-time git fetch + ff-only update before build_index()."""
        if (
            self._collection._git_strategy is None
            or self._collection._git_pull_interval_s <= 0
        ):
            return
        self._collection._git_strategy.sync_once(self._collection._source_dir)

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index."""
        # Check if index already has data and we are not forcing.
        if not force and self._collection._initialized:
            existing = self._collection._fts.list_notes()
            if existing:
                logger.debug(
                    "build_index: index already populated (%d docs), skipping",
                    len(existing),
                )
                return IndexStats(
                    documents_indexed=len(existing),
                    chunks_indexed=0,
                    skipped=0,
                )

        if force:
            # Drop all data by rebuilding from an empty scan then re-populate.
            logger.info("build_index(force=True): dropping and rebuilding index")
            # Delete all existing documents.
            for row in self._collection._fts.list_notes():
                self._collection._fts.delete_by_path(row["path"])

        logger.info("build_index: scanning %s", self._collection._source_dir)

        notes = list(
            scan_directory(
                self._collection._source_dir,
                required_frontmatter=self._collection._required_frontmatter,
                chunk_strategy=self._collection._chunk_strategy,
                exclude_patterns=self._collection._exclude_patterns,
            )
        )

        total_chunks = 0
        errored = 0
        with self._collection._write_lock:
            for note in notes:
                try:
                    total_chunks += self._collection._fts.upsert_note(note)
                except (sqlite3.Error, OSError) as exc:
                    errored += 1
                    logger.warning(
                        "build_index: failed to index %s — %s", note.path, exc
                    )

            # Purge stale excluded docs from a persistent index.
            indexed_paths = {note.path for note in notes}
            if self._collection._exclude_patterns:
                if (
                    self._collection._vectors is None
                    and self._collection._embedding_provider is not None
                    and self._collection._embeddings_path is not None
                ):
                    self._collection._load_vectors()

                purged = 0
                for row in self._collection._fts.list_notes():
                    if row[
                        "path"
                    ] not in indexed_paths and self._collection._is_path_excluded(
                        row["path"]
                    ):
                        self._collection._fts.delete_by_path(row["path"])
                        if self._collection._vectors is not None:
                            self._collection._vectors.delete_by_path(row["path"])
                        purged += 1

                if (
                    purged
                    and self._collection._vectors is not None
                    and self._collection._embeddings_path is not None
                ):
                    self._collection._vectors.save(self._collection._embeddings_path)

            # Resolve vault-wide wikilinks.
            self._collection._fts.resolve_vault_wikilinks()

        all_files = list(self._collection._source_dir.glob("**/*.md"))
        skipped = len(all_files) - len(notes)

        # Update tracker state.
        self._collection._tracker.update_state(notes)

        self._collection._initialized = True
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
        return IndexStats(
            documents_indexed=len(notes) - errored,
            chunks_indexed=total_chunks,
            skipped=max(skipped, 0),
        )

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes."""
        self._collection._ensure_initialized()

        # Phase 1: scan.
        changes = self._collection._tracker.detect_changes(self._collection._source_dir)
        logger.info(
            "reindex: %d added, %d modified, %d deleted, %d unchanged",
            len(changes.added),
            len(changes.modified),
            len(changes.deleted),
            changes.unchanged,
        )

        parsed: list[tuple[str, ParsedNote]] = []
        for path in changes.added + changes.modified:
            if self._collection._is_path_excluded(path):
                logger.debug("reindex: excluding %s (matched exclude pattern)", path)
                continue

            abs_path = self._collection._source_dir / path
            try:
                note = parse_note(
                    abs_path,
                    self._collection._source_dir,
                    self._collection._chunk_strategy,
                )
            except (UnicodeDecodeError, OSError) as exc:
                logger.warning("reindex: skipping %s — %s", path, exc)
                continue
            except Exception as exc:
                logger.warning(
                    "reindex: skipping %s — parse error (%s)",
                    path,
                    exc,
                    exc_info=True,
                )
                continue

            if self._collection._required_frontmatter:
                missing = [
                    f
                    for f in self._collection._required_frontmatter
                    if f not in note.frontmatter
                ]
                if missing:
                    logger.info(
                        "reindex: skipping %s — missing frontmatter: %s", path, missing
                    )
                    continue

            parsed.append((path, note))

        # Phase 2: apply mutations.
        with self._collection._write_lock:
            # Delete removed documents.
            for path in changes.deleted:
                self._collection._fts.delete_by_path(path)
                if self._collection._vectors is not None:
                    self._collection._vectors.delete_by_path(path)

            # Purge stale excluded docs.
            stale_excluded = 0
            if self._collection._exclude_patterns:
                if (
                    self._collection._vectors is None
                    and self._collection._embedding_provider is not None
                    and self._collection._embeddings_path is not None
                ):
                    self._collection._load_vectors()

                for row in self._collection._fts.list_notes():
                    if self._collection._is_path_excluded(row["path"]):
                        self._collection._fts.delete_by_path(row["path"])
                        if self._collection._vectors is not None:
                            self._collection._vectors.delete_by_path(row["path"])
                        stale_excluded += 1
                if stale_excluded:
                    logger.info(
                        "reindex: purged %d stale excluded document(s)",
                        stale_excluded,
                    )

            # Upsert parsed notes.
            indexed_added = 0
            indexed_modified = 0
            added_set = set(changes.added)

            for path, note in parsed:
                try:
                    self._collection._fts.upsert_note(note)
                except (sqlite3.Error, OSError) as exc:
                    logger.warning("reindex: failed to index %s — %s", path, exc)
                    continue
                if path in added_set:
                    indexed_added += 1
                else:
                    indexed_modified += 1

                # Update vector index for changed notes if loaded.
                if (
                    self._collection._vectors is not None
                    and self._collection._embeddings_path is not None
                ):
                    self._collection._vectors.delete_by_path(note.path)
                    texts = [c.content for c in note.chunks]
                    meta = [
                        {
                            "path": note.path,
                            "title": note.title,
                            "folder": _derive_folder(note.path),
                            "heading": c.heading,
                            "content": c.content,
                        }
                        for c in note.chunks
                    ]
                    if texts:
                        self._collection._vectors.add(texts, meta)

            # Persist updated vector index.
            if (
                self._collection._vectors is not None
                and self._collection._embeddings_path is not None
            ):
                self._collection._vectors.save(self._collection._embeddings_path)

            # Re-resolve vault-wide wikilinks.
            self._collection._fts.resolve_vault_wikilinks()

            # Update tracker state.
            state_notes: list[ParsedNote] = [
                ParsedNote(
                    path=r["path"],
                    frontmatter={},
                    title=r["title"],
                    chunks=[],
                    content_hash=r["content_hash"],
                    modified_at=r["modified_at"],
                )
                for r in self._collection._fts.list_notes()
            ]
            self._collection._tracker.update_state(state_notes)

        return ReindexResult(
            added=indexed_added,
            modified=indexed_modified,
            deleted=len(changes.deleted),
            unchanged=changes.unchanged,
        )

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index."""
        self._collection._ensure_initialized()
        self._collection._require_vectors()

        assert self._collection._embeddings_path is not None
        assert self._collection._embedding_provider is not None

        if force:
            from markdown_vault_mcp.vector_index import VectorIndex

            self._collection._vectors = VectorIndex(
                self._collection._embedding_provider
            )
        else:
            # Load persisted vectors (or create empty) so we can check count.
            self._collection._load_vectors()
            if self._collection._vectors and self._collection._vectors.count > 0:
                logger.info(
                    "build_embeddings: index already exists (%d chunks), skipping",
                    self._collection._vectors.count,
                )
                return self._collection._vectors.count

        rows = self._collection._fts.list_notes()
        num_notes = len(rows)
        logger.info("build_embeddings: parsing %d notes into chunks", num_notes)
        texts: list[str] = []
        meta: list[dict[str, Any]] = []

        for i, row in enumerate(rows, 1):
            path = row["path"]
            title = row["title"]
            folder = row["folder"]
            # Re-parse to get chunks with content.
            abs_path = self._collection._source_dir / path
            try:
                note = parse_note(
                    abs_path,
                    self._collection._source_dir,
                    self._collection._chunk_strategy,
                )
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
                    }
                )
            if i % 100 == 0 or i == num_notes:
                logger.info(
                    "build_embeddings: parsed %d/%d notes (%d chunks so far)",
                    i,
                    num_notes,
                    len(texts),
                )

        total = len(texts)
        with self._collection._write_lock:
            for start in range(0, total, _EMBEDDING_BATCH_SIZE):
                end = min(start + _EMBEDDING_BATCH_SIZE, total)
                assert self._collection._vectors is not None
                self._collection._vectors.add(texts[start:end], meta[start:end])
                logger.info(
                    "build_embeddings: embedded chunks %d-%d of %d",
                    start + 1,
                    end,
                    total,
                )

            if total > 0:
                assert self._collection._vectors is not None
                self._collection._vectors.save(self._collection._embeddings_path)
                logger.info("build_embeddings: embedded and saved %d chunks", total)
            else:
                logger.info("build_embeddings: nothing to embed")
        return total

    def update_vector_index(self, note: ParsedNote) -> None:
        """Mark a document for deferred embedding update."""
        if (
            self._collection._embeddings_path is None
            or self._collection._embedding_provider is None
        ):
            return
        with self._collection._embedding_flush_lock:
            self._collection._dirty_embeddings.add(note.path)
        self.schedule_embedding_flush()

    def schedule_embedding_flush(self) -> None:
        """Schedule a deferred flush of dirty embeddings."""
        with self._collection._embedding_flush_lock:
            if self._collection._embedding_flush_timer is not None:
                self._collection._embedding_flush_timer.cancel()
            self._collection._embedding_flush_timer = threading.Timer(
                _EMBEDDING_FLUSH_INTERVAL,
                self.flush_dirty_embeddings,
            )
            self._collection._embedding_flush_timer.daemon = True
            self._collection._embedding_flush_timer.start()

    def flush_dirty_embeddings(self) -> None:
        """Re-embed all dirty documents and save the vector index once."""
        if (
            self._collection._embeddings_path is None
            or self._collection._embedding_provider is None
        ):
            return

        with self._collection._embedding_flush_lock:
            # Cancel pending timer (we're flushing now).
            if self._collection._embedding_flush_timer is not None:
                self._collection._embedding_flush_timer.cancel()
                self._collection._embedding_flush_timer = None

            # Atomically swap the dirty set.
            if not self._collection._dirty_embeddings:
                return
            paths = self._collection._dirty_embeddings.copy()
            self._collection._dirty_embeddings.clear()

        # Phase 1: parse and embed OUTSIDE _write_lock.
        pre_embedded: list[
            tuple[str, list[list[float]] | None, list[dict[str, Any]] | None]
        ] = []
        for path in paths:
            abs_path = self._collection._source_dir / path
            if abs_path.is_file() and path.endswith(".md"):
                try:
                    note = parse_note(
                        abs_path,
                        self._collection._source_dir,
                        self._collection._chunk_strategy,
                    )
                    texts = [c.content for c in note.chunks]
                    meta = [
                        {
                            "path": note.path,
                            "title": note.title,
                            "folder": _derive_folder(note.path),
                            "heading": c.heading,
                            "content": c.content,
                        }
                        for c in note.chunks
                    ]
                    if texts:
                        raw_vecs = self._collection._embedding_provider.embed(texts)
                        pre_embedded.append((path, raw_vecs, meta))
                    else:
                        pre_embedded.append((path, None, None))
                except (UnicodeDecodeError, OSError) as exc:
                    logger.warning("Deferred embedding failed for %s: %s", path, exc)
                    pre_embedded.append((path, None, None))
            else:
                pre_embedded.append((path, None, None))

        # Phase 2: mutate vector index under _write_lock.
        with self._collection._write_lock:
            vectors = self._collection._load_vectors()

            for path, embedded_vecs, meta_list in pre_embedded:
                vectors.delete_by_path(path)
                if embedded_vecs is not None and meta_list:
                    vectors.add_vectors(embedded_vecs, meta_list)

            vectors.save(self._collection._embeddings_path)
            logger.debug("Flushed deferred embeddings for %d paths", len(paths))
