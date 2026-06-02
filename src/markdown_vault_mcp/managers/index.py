"""Index build, reindex, embedding, and deferred-flush manager.

Handles FTS index construction, incremental reindexing via
:class:`~markdown_vault_mcp.tracker.ChangeTracker`, vector embedding
lifecycle, and the two-phase deferred embedding flush — all with
dependency injection and no back-reference to :class:`Collection`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from markdown_vault_mcp.fts_index import _derive_folder
from markdown_vault_mcp.scanner import parse_note, scan_directory
from markdown_vault_mcp.types import IndexStats, ParsedNote, ReindexResult
from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.fs import GLOB_SYMLINK_KWARGS

if TYPE_CHECKING:
    from collections.abc import Callable

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
        """Raise ValueError if embedding support is not configured."""
        if self._embedding_provider is None or self._embeddings_path is None:
            raise ValueError(
                "Embeddings require both 'embedding_provider' and "
                "'embeddings_path' to be configured."
            )

    def _load_vectors(self) -> VectorIndex:
        """Load or return the cached VectorIndex.

        Returns:
            A :class:`~markdown_vault_mcp.vector_index.VectorIndex` instance.
        """
        vectors = self._get_vectors()
        if vectors is not None:
            return vectors

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
                vi = VectorIndex.load(self._embeddings_path, self._embedding_provider)
                self._set_vectors(vi)
                logger.info("Loaded vector index from %s", self._embeddings_path)
            except VectorIndexCompatibilityError as exc:
                logger.warning("%s Rebuilding embeddings.", exc)
                self.build_embeddings(force=True)
                if self._get_vectors() is None:
                    raise ValueError(
                        "Failed to rebuild vector index after a compatibility error."
                    ) from exc
        else:
            vi = VectorIndex(self._embedding_provider)
            self._set_vectors(vi)
            logger.info("No vector index on disk; created empty VectorIndex")

        result = self._get_vectors()
        if result is None:
            raise ValueError(
                "Failed to rebuild vector index after a compatibility error."
            )
        return result

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

        notes = list(
            scan_directory(
                self._source_dir,
                required_frontmatter=self._required_frontmatter,
                chunk_strategy=self._chunk_strategy,
                exclude_patterns=self._exclude_patterns,
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

            purged = 0
            for row in self._fts.list_notes():
                if row["path"] not in indexed_paths and self._is_path_excluded(
                    row["path"]
                ):
                    self._fts.delete_by_path(row["path"])
                    if vectors is not None:
                        vectors.delete_by_path(row["path"])
                    purged += 1

            if purged and vectors is not None and self._embeddings_path is not None:
                vectors.save(self._embeddings_path)

        # Count how many files were skipped due to required_frontmatter.
        all_files = list(self._source_dir.glob("**/*.md", **GLOB_SYMLINK_KWARGS))
        skipped = len(all_files) - len(notes)

        # Resolve vault-wide wikilinks now that all documents are indexed.
        self._fts.resolve_vault_wikilinks()

        # Update tracker state so reindex() knows the baseline.
        self._tracker.update_state(notes)

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
        # Phase 1: scan filesystem (read-only walk + hashing).
        changes = self._tracker.detect_changes(self._source_dir)
        logger.info(
            "reindex: %d added, %d modified, %d deleted, %d unchanged",
            len(changes.added),
            len(changes.modified),
            len(changes.deleted),
            changes.unchanged,
        )

        # Pre-parse notes outside the lock to minimise lock hold time.
        parsed: list[tuple[str, ParsedNote]] = []
        for path in changes.added + changes.modified:
            if self._is_path_excluded(path):
                logger.debug("reindex: excluding %s (matched exclude pattern)", path)
                continue

            abs_path = self._source_dir / path
            try:
                note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
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
                    continue

            parsed.append((path, note))

        # Phase 2: apply mutations (writer is sole mutator; no lock needed).
        vectors = self._get_vectors()

        for path in changes.deleted:
            self._fts.delete_by_path(path)
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

        if vectors is not None and self._embeddings_path is not None:
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
        self._tracker.update_state(state_notes)

        return ReindexResult(
            added=indexed_added,
            modified=indexed_modified,
            deleted=len(changes.deleted),
            unchanged=changes.unchanged,
        )

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk.

        Returns:
            Total number of chunks embedded.

        Raises:
            ValueError: If ``embedding_provider`` or ``embeddings_path`` is
                not configured.
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
                logger.info(
                    "build_embeddings: index already exists (%d chunks), skipping",
                    vectors.count,
                )
                return vectors.count

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
        for start in range(0, total, _EMBEDDING_BATCH_SIZE):
            end = min(start + _EMBEDDING_BATCH_SIZE, total)
            vectors.add(texts[start:end], meta[start:end])
            logger.info(
                "build_embeddings: embedded chunks %d-%d of %d",
                start + 1,
                end,
                total,
            )

        if total > 0:
            vectors.save(self._embeddings_path)
            logger.info("build_embeddings: embedded and saved %d chunks", total)
        else:
            logger.info("build_embeddings: nothing to embed")
        return total

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
            npy_path = Path(str(self._embeddings_path) + ".npy")
            if npy_path.exists():
                json_path = Path(str(self._embeddings_path) + ".json")
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
