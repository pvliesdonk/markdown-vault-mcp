"""Reader facet: the read-only query surface (#604).

A thin view exposing search, document reads, listing, table-of-contents,
similarity, recent, context, stats, embeddings status, attachment reads, and
git history/diff. It is the widest facet — the catch-all for reads — and so
spans several collaborators (SearchManager, DocumentManager, IndexManager,
FTSIndex, the git strategy) plus the index-readiness gate. Part of the
``collection.py`` facade decomposition (#576); the flat ``Collection`` read
methods delegate here. The bucket-3 methods (:meth:`get_toc`,
:meth:`get_similar`, :meth:`get_context`) gate on the readiness callback.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from markdown_vault_mcp.types import CollectionStats
from markdown_vault_mcp.utils import effective_attachment_extensions

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Any, Literal

    from markdown_vault_mcp.fts_index import FTSIndex
    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.index import IndexManager
    from markdown_vault_mcp.managers.search import SearchManager
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.types import (
        AttachmentContent,
        AttachmentInfo,
        CommitDiff,
        GroupedResult,
        HistoryEntry,
        NoteContent,
        NoteContext,
        NoteInfo,
    )


class ReaderFacet:
    """Read-only queries over the shared managers, fts, git, and config values."""

    def __init__(
        self,
        *,
        search_mgr: SearchManager,
        doc_mgr: DocumentManager,
        index_mgr: IndexManager,
        fts: FTSIndex,
        git_strategy: GitWriteStrategy | None,
        source_dir: Path,
        require_built: Callable[[], None],
        validate_path: Callable[[str], Path],
        embedding_provider: EmbeddingProvider | None,
        embeddings_path: Path | None,
        attachment_extensions: list[str] | None,
        indexed_frontmatter_fields: list[str],
    ) -> None:
        """Hold the collaborators the read methods delegate to.

        Args:
            search_mgr: Search / list / similarity / context / recent queries.
            doc_mgr: Document and attachment reads, table-of-contents.
            index_mgr: Vector-index status (:meth:`embeddings_status`).
            fts: FTS index, queried by :meth:`stats`.
            git_strategy: Git strategy backing history/diff, or ``None``.
            source_dir: Vault root, passed to git history/diff.
            require_built: Index-readiness gate for the bucket-3 methods.
            validate_path: Resolves+validates a relative path inside the vault.
            embedding_provider: Configured provider, or ``None`` (for stats).
            embeddings_path: Vector sidecar base path, or ``None`` (for stats).
            attachment_extensions: Configured attachment allowlist (for stats).
            indexed_frontmatter_fields: Indexed frontmatter keys (for stats).
        """
        self._search_mgr = search_mgr
        self._doc_mgr = doc_mgr
        self._index_mgr = index_mgr
        self._fts = fts
        self._git_strategy = git_strategy
        self._source_dir = source_dir
        self._require_built = require_built
        self._validate_path = validate_path
        self._embedding_provider = embedding_provider
        self._embeddings_path = embeddings_path
        self._attachment_extensions = attachment_extensions
        self._indexed_frontmatter_fields = indexed_frontmatter_fields

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
                similarity, or ``"hybrid"`` for Reciprocal Rank Fusion of both.
            filters: Dict of ``{frontmatter_key: value}`` pairs (AND semantics).
                Only works for fields in ``indexed_frontmatter_fields``.
            folder: If provided, restrict results to documents in this folder
                (and its sub-folders).
            chunks_per_file: Maximum number of sections returned per file.
                ``None`` uses the server default configured at startup.
            snippet_words: Width of the snippet window in words.  ``0`` returns
                the full chunk.  ``None`` uses the server default.

        Returns:
            List of :class:`~markdown_vault_mcp.types.GroupedResult` ordered
            by descending file score (max of section scores).  Each result
            wraps one document with up to ``chunks_per_file`` sections.

        Raises:
            ValueError: If *mode* is ``"semantic"`` or ``"hybrid"`` but no
                embedding provider or embeddings path is configured.
        """
        return self._search_mgr.search(
            query,
            limit=limit,
            mode=mode,
            filters=filters,
            folder=folder,
            chunks_per_file=chunks_per_file,
            snippet_words=snippet_words,
        )

    def read(self, path: str, *, section: str | None = None) -> NoteContent | None:
        """Read the full content of a document from disk.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).
            section: When provided, return only the section whose heading
                matches *section* exactly (case-sensitive). Pass the
                ``heading`` value from a ``search`` result unchanged for
                guaranteed match. ``None`` (the default) returns the whole
                document. Raises :exc:`ValueError` if the section is not found.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent` instance, or ``None``
            if the file does not exist.
        """
        return self._doc_mgr.read(path, section=section)

    def list_documents(
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
        return self._search_mgr.list(
            folder=folder, pattern=pattern, include_attachments=include_attachments
        )

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed collection.

        Returns:
            Sorted list of folder strings (``""`` for the collection root).
        """
        return self._search_mgr.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field.

        If *field* was not in ``indexed_frontmatter_fields``, returns ``[]``.

        Args:
            field: Frontmatter key to query (default: ``"tags"``).

        Returns:
            Sorted list of distinct value strings.
        """
        return self._search_mgr.list_tags(field)

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document.

        Queries the FTS sections table for headings and prepends the document
        title as a synthetic H1 entry. The result depends on the FTS index, so
        cold-start callers must build the index first (bucket 3).

        Args:
            path: Relative path to the document (e.g. ``"notes/intro.md"``).

        Returns:
            List of ``{"heading": str, "level": int}`` dicts ordered by
            position, with the document title prepended as level 1.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        self._require_built()
        return self._doc_mgr.get_toc(path)

    def get_recent(
        self, *, limit: int = 20, folder: str | None = None
    ) -> list[NoteInfo]:
        """Return the most recently modified documents.

        Args:
            limit: Maximum number of documents to return.
            folder: If provided, restrict to documents in this folder
                (exact match or sub-folder prefix).

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` objects
            ordered by modification time (most recent first).
        """
        return self._search_mgr.get_recent(limit=limit, folder=folder)

    def get_similar(
        self,
        path: str,
        *,
        limit: int = 10,
        chunks_per_file: int | None = None,
    ) -> list[GroupedResult]:
        """Return semantically similar documents grouped by file.

        See :meth:`SearchManager.get_similar` for details.  Returns
        :class:`~markdown_vault_mcp.types.GroupedResult` objects ordered by
        descending file score; each result wraps one document with up to
        ``chunks_per_file`` sections.

        Args:
            path: Relative path of the reference document.
            limit: Maximum number of files to return.
            chunks_per_file: Maximum sections per result file.

        Returns:
            List of grouped results.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
        """
        self._require_built()
        return self._search_mgr.get_similar(
            path, limit=limit, chunks_per_file=chunks_per_file
        )

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
            path: Relative path of the document (e.g. ``"notes/topic.md"``).
            similar_limit: Maximum number of similar notes to include.
            link_limit: Maximum number of backlinks and outlinks to include.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContext` object.  Its
            ``similar`` field is a list of
            :class:`~markdown_vault_mcp.types.GroupedResult` entries, each
            with exactly one section (chunks_per_file=1) so the dossier
            stays compact.

        Raises:
            IndexUnavailableError: If :meth:`build_index` has not been called.
            ValueError: If no document exists at the given path.
        """
        self._require_built()
        return self._search_mgr.get_context(
            path, similar_limit=similar_limit, link_limit=link_limit
        )

    def get_history(
        self,
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
    ) -> list[HistoryEntry]:
        """Return commits that touched a note or the whole vault.

        When *path* is ``None``, queries the full vault history.  Returns an
        empty list for vaults whose source directory is not inside a git
        repository.

        Args:
            path: Vault-relative path of the note to filter on (e.g.
                ``"notes/alpha.md"``).  Must end with ``.md``.  ``None``
                returns vault-wide history.
            since: ISO 8601 datetime string or git date expression (e.g.
                ``"1 week ago"``).  Passed as ``--since`` to ``git log``.
                ``None`` disables the filter.
            until: ISO 8601 datetime string or git date expression, passed as
                ``--until`` to ``git log``.  ``None`` disables the filter.
                Both ``since`` and ``until`` boundaries are **inclusive**: a
                commit whose committer date equals either endpoint is included
                in the result.
            limit: Maximum number of commits to return.  Clamped to
                ``[1, 100]``.  Defaults to ``20``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.HistoryEntry` ordered
            newest-first.  Empty list when the vault has no git history or
            the note has no commits in the given range.  The
            ``paths_changed`` field on each entry is populated for vault-wide
            queries (``path=None``); it is always empty for single-note
            queries, since the path is already determined by the query
            arguments — callers know which file the commit touched without
            needing it echoed back.

        Raises:
            ValueError: If *path* is provided but fails path validation.
        """
        if self._git_strategy is None:
            return []
        abs_path: Path | None = None
        if path is not None:
            abs_path = self._validate_path(path)
        return self._git_strategy.get_file_history(
            self._source_dir, abs_path, since, limit, until=until
        )

    def get_diff(
        self,
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
    ) -> str | list[CommitDiff]:
        """Return the diff of a note between a reference point and HEAD.

        Exactly one of *since_sha* or *since_timestamp* must be supplied.

        Args:
            path: Vault-relative path of the note to diff.  Must end with
                ``.md``.
            since_sha: A commit SHA (full or abbreviated, at least 4 hex
                digits) to diff from.  Mutually exclusive with
                *since_timestamp*.
            since_timestamp: ISO 8601 datetime string, resolved via
                ``git rev-list --before=<ts> -1 HEAD`` to the most recent
                commit at or before that instant.  Boundary is
                **inclusive**: a commit whose committer date equals
                *since_timestamp* IS the resolved ref.  Mutually exclusive
                with *since_sha*.
            per_commit: When ``False`` (default), return a single unified diff
                string from the reference point to HEAD.  When ``True``,
                return one :class:`~markdown_vault_mcp.types.CommitDiff` per
                intervening commit.
            limit: When *per_commit* is ``True``, cap the number of
                intervening commits returned to the *limit* most recent ones.
                Clamped to ``[1, 100]``.  ``None`` (the default) means
                unbounded (still bounded by the underlying ``since..HEAD``
                range).  Silently ignored when *per_commit* is ``False``.

        Returns:
            A unified diff string when *per_commit* is ``False``, or a list of
            :class:`~markdown_vault_mcp.types.CommitDiff` when *per_commit* is
            ``True``.  Returns an empty string / empty list when the note has
            no changes in the given range.

        Raises:
            ValueError: If exactly one of *since_sha* / *since_timestamp* is
                not supplied, *since_sha* contains invalid characters, or the
                resolved ref is not found in history.
        """
        if self._git_strategy is None:
            return [] if per_commit else ""

        if (since_sha is None) == (since_timestamp is None):
            raise ValueError(
                "Exactly one of 'since_sha' or 'since_timestamp' must be provided"
            )

        abs_path = self._validate_path(path)

        if since_sha is not None and not re.fullmatch(r"[0-9a-f]{4,40}", since_sha):
            raise ValueError(
                f"Invalid SHA {since_sha!r}: must be 4-40 lowercase hex digits"
            )

        return self._git_strategy.get_file_diff(
            self._source_dir,
            abs_path,
            ref=since_sha,
            per_commit=per_commit,
            since_timestamp=since_timestamp,
            limit=limit if per_commit else None,
        )

    def stats(self) -> CollectionStats:
        """Return collection-wide statistics.

        Returns:
            :class:`~markdown_vault_mcp.types.CollectionStats` snapshot.
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

        exts = effective_attachment_extensions(self._attachment_extensions)
        attachment_extensions = ["*"] if "*" in exts else sorted(exts)

        return CollectionStats(
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

    def embeddings_status(self) -> dict[str, Any]:
        """Return status information about the vector index.

        Returns:
            Dict with keys ``provider``, ``chunk_count``, ``path``,
            ``available``.
        """
        return self._index_mgr.embeddings_status()

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read the binary content of a non-.md attachment.

        Delegates to :meth:`DocumentManager.read_attachment`.
        """
        return self._doc_mgr.read_attachment(path)
