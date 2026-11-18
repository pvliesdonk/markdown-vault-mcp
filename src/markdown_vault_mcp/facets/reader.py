"""Reader facet: the read-only query surface (#604).

A thin view exposing search, document reads, listing, table-of-contents,
similarity, recent, context, stats, attachment reads, and git history, diff
and revision reads.
Methods delegate to :class:`SearchManager`, :class:`DocumentManager`, or
:class:`GitQueryManager`; OKF stats also include the vault's write-state
projection. The bucket-3 methods
(:meth:`ReaderFacet.get_toc`,
:meth:`ReaderFacet.get_similar`, :meth:`ReaderFacet.get_context`) gate on the index-readiness callback
first. Part of the ``vault.py`` facade decomposition (#576); reached via
the ``Vault.reader`` accessor.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from markdown_vault_mcp.bundle_inventory import BundleInspectionRequest, BundleInspector
from markdown_vault_mcp.okf import OKF_RESERVED_FILENAMES
from markdown_vault_mcp.okf_navigation import (
    FolderNavigation,
    authored_body,
    read_authored,
    synthesize,
)
from markdown_vault_mcp.utils.content_kind import is_note

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Any, Literal

    from markdown_vault_mcp.bundle_inventory import (
        BundleEntry,
        BundleInspection,
        BundleTreeRequest,
        ParseState,
        SearchState,
        TypeState,
    )
    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.git_query import GitQueryManager
    from markdown_vault_mcp.managers.search import SearchManager
    from markdown_vault_mcp.okf import OkfAuditReport
    from markdown_vault_mcp.query_types import (
        NoteAggregate,
        NoteCount,
        NoteGrouping,
        NoteQuery,
        NoteQueryPage,
        PathQuery,
        PathSearchResult,
    )
    from markdown_vault_mcp.types import (
        AttachmentContent,
        AttachmentInfo,
        CommitDiff,
        DocumentMeta,
        GroupedResult,
        HistoryEntry,
        NoteContent,
        NoteContext,
        NoteInfo,
        RevisionContent,
        SubtreeToc,
        TocEntry,
        VaultStats,
    )


def _selected(
    entry: BundleEntry,
    type_state: TypeState | None,
    parse_state: ParseState | None,
    search_state: SearchState | None,
) -> bool:
    if entry.role == "directory" and entry.state in {"unavailable", "missing"}:
        return True
    if entry.path.rsplit("/", 1)[-1] in OKF_RESERVED_FILENAMES:
        return False
    if entry.state in {"unavailable", "missing"}:
        return (
            entry.role in {"unknown", "concept_candidate"}
            and is_note(entry.path)
            and (search_state is None or entry.search_state == search_state)
        )
    return (
        entry.role == "concept_candidate"
        and (type_state is None or entry.type_state in {type_state, "not_inspected"})
        and (parse_state is None or entry.parse_state in {parse_state, "not_inspected"})
        and (search_state is None or entry.search_state == search_state)
    )


class ReaderFacet:
    """Read-only queries over the shared managers."""

    def __init__(
        self,
        *,
        search_mgr: SearchManager,
        doc_mgr: DocumentManager,
        git_query_mgr: GitQueryManager,
        require_built: Callable[[], None],
        okf_audit: Callable[[], OkfAuditReport] | None = None,
        okf_write_state: Callable[[], tuple[bool, bool]] | None = None,
    ) -> None:
        """Hold the managers the read methods delegate to.

        Args:
            search_mgr: Search / list / similarity / context / recent / stats
                queries.
            doc_mgr: Document and attachment reads, table-of-contents.
            git_query_mgr: Git history / diff / revision reads.
            require_built: Index-readiness gate for the bucket-3 methods.
            okf_audit: Bound OKF conformance audit (#962), or ``None`` when
                the composition root does not provide one.
            okf_write_state: Configured and effective write-layer projection.
        """
        self._search_mgr = search_mgr
        self._doc_mgr = doc_mgr
        self._git_query_mgr = git_query_mgr
        self._require_built = require_built
        self._okf_audit = okf_audit
        self._okf_write_state = okf_write_state

    def inspect_bundle(self, request: BundleInspectionRequest) -> BundleInspection:
        """Inspect one explicitly selected bundle directory before or after indexing.

        Args:
            request: Explicit bundle root, relative directory and bounded entry limit.

        Returns:
            Live disk observations with available per-entry index state.
        """
        return self._doc_mgr.inspect_bundle(request)

    def okf_navigation(
        self, folder: str, *, entry_limit: int = 100
    ) -> FolderNavigation:
        """Read authored navigation or synthesize a bounded folder listing.

        Args:
            folder: Vault-relative folder, or empty for the vault root.
            entry_limit: Maximum immediate entries visited, from 1 to 1000.

        Returns:
            Navigation body, source, scope state and enumeration observations.
        """
        inspection = self.inspect_bundle(
            BundleInspectionRequest(root=folder, entry_limit=entry_limit)
        )
        index_path = f"{folder}/index.md" if folder else "index.md"
        result = FolderNavigation(
            folder,
            inspection.state,
            None,
            None,
            index_path,
            None,
            (),
            inspection.enumeration,
        )
        if inspection.state != "inspected":
            return result
        manager = self._doc_mgr
        inspector = BundleInspector(
            manager._source_dir,
            manager._exclude_patterns or (),
            manager._artifacts.extensions(),
            manager._max_note_read_bytes,
            manager._inspection_policy,
        )
        try:
            inspector._prepare_policy(manager._source_dir.resolve(strict=True))
            if inspector._internal(index_path):
                return replace(result, reason="internal")
        except (OSError, RuntimeError):
            return replace(result, reason="io_error")
        text, reason = read_authored(
            manager._source_dir, folder, manager._max_note_read_bytes
        )
        if reason is not None:
            return replace(result, reason=reason)
        if text is not None:
            return replace(result, source="authored", body=authored_body(text))
        return replace(
            result,
            source="synthesized",
            body=synthesize(folder, inspection),
            entries=inspection.entries,
        )

    def inspect_bundle_tree(self, request: BundleTreeRequest) -> BundleInspection:
        """Inspect a page of a selected subtree before or after indexing.

        Args:
            request: Selection, page limit, and optional path cursor.

        Returns:
            Live observations and an optional continuation cursor.
        """
        return self._doc_mgr.inspect_bundle(request)

    def okf_inventory(
        self,
        request: BundleTreeRequest,
        *,
        type_state: TypeState | None = None,
        parse_state: ParseState | None = None,
        search_state: SearchState | None = None,
    ) -> BundleInspection:
        """Inspect one page of concept candidates without building an index.

        Unknown type or parse state does not rule out a match. Search-state
        filters match exactly, except directory boundaries needed to continue.

        Args:
            request: Explicit root, page limit and optional continuation.
            type_state: Filter by observed type presence.
            parse_state: Filter by metadata parsing outcome.
            search_state: Filter by observed index admission.

        Returns:
            The bounded page with matching concepts and the original continuation.
        """
        page = self.inspect_bundle_tree(replace(request, entry_limit=1000))
        matching = tuple(
            entry
            for entry in page.entries
            if _selected(entry, type_state, parse_state, search_state)
        )
        truncated = len(matching) > request.entry_limit
        entries = matching[: request.entry_limit]
        return replace(
            page,
            request=request,
            entries=entries,
            next_cursor=entries[-1].path if truncated else page.next_cursor,
            enumeration=(
                "entry_limit"
                if truncated and page.enumeration != "unavailable"
                else page.enumeration
            ),
        )

    def okf_validate(self) -> OkfAuditReport:
        """Run the OKF bundle-conformance audit (design §4; #962).

        Disk-based and index-independent: usable before the index is
        built and before the vault declares itself a bundle (the audit is
        the "should I declare?" tool of the migration ratchet).

        Returns:
            The audit report.

        Raises:
            RuntimeError: If no audit callable was wired (direct facet
                construction without the composition root).
        """
        if self._okf_audit is None:
            raise RuntimeError("okf_validate requires a wired audit callable")
        return self._okf_audit()

    def search_paths(self, request: PathQuery) -> PathSearchResult:
        """Discover indexed paths with bounded optional typo matching.

        Args:
            request: Literal query, population predicates and output cap.

        Returns:
            Metadata matches with separate approximate coverage and truncation.
        """
        self._require_built()
        return self._search_mgr.search_paths(request)

    def query_documents(self, spec: NoteQuery) -> NoteQueryPage:
        """Return a bounded exact query page after index readiness.

        Args:
            spec: Indexed predicates and page presentation.

        Returns:
            Page items and the exhaustive matching-note total.
        """
        self._require_built()
        return self._search_mgr.query_documents(spec)

    def count_documents_matching(self, spec: NoteQuery) -> NoteCount:
        """Count all matching notes after index readiness.

        Args:
            spec: Indexed predicates.

        Returns:
            Exact matching-note total.
        """
        self._require_built()
        return self._search_mgr.count_documents_matching(spec)

    def aggregate_documents(
        self, spec: NoteQuery, grouping: NoteGrouping
    ) -> NoteAggregate:
        """Group all matching notes after index readiness.

        Args:
            spec: Indexed predicates.
            grouping: Grouping axis, field and bounded bucket presentation.

        Returns:
            Exact population total and bounded bucket counts.
        """
        self._require_built()
        return self._search_mgr.aggregate_documents(spec, grouping)

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] | None = None,
        filters: Mapping[str, str | None] | None = None,
        folder: str | None = None,
        chunks_per_file: int | None = None,
        snippet_words: int | None = None,
    ) -> list[GroupedResult]:
        """Search the vault.

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
            EmbeddingsNotConfiguredError: If *mode* is ``"semantic"`` or
                ``"hybrid"`` but no embedding provider is configured.
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
                matches *section* (case-sensitive; internal whitespace is
                collapsed before comparison). Pass the ``heading`` value from
                a ``search`` result unchanged for guaranteed match. ``None``
                (the default) returns the whole document. Raises
                :exc:`ValueError` if the section is not found.

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent` instance, or ``None``
            if there is no file at *path*.

        Raises:
            DocumentUnreadableError: If the file exists but cannot be read or
                parsed (#1608).
            InvalidRequestError: If *path* holds a NUL byte (#1636), or
                *section* is empty or names no heading.
        """
        return self._doc_mgr.read(path, section=section)

    def read_revision(
        self, path: str, revision: str, *, section: str | None = None
    ) -> RevisionContent:
        """Read a document's content as it stood at a git revision (#1137).

        Resolution is by note, not by path: pass the path the note has today,
        and git's own add/rename records are walked back to *revision*.  Where
        they do not connect the two — a name since reused by a different note,
        a delete-and-recreate, a rename git cannot detect — this raises rather
        than returning content that belongs to another note.

        A note that no longer exists on disk is still readable this way, which
        is how a deleted note is recovered.

        Args:
            path: Relative document path as it is named today.
            revision: Commit SHA, from :meth:`get_history` or from an
                overwriting :meth:`WriterFacet.write`'s ``previous_revision``.
            section: When provided, return only that section of the historical
                content, matched as :meth:`read` matches it on disk.

        Returns:
            A :class:`~markdown_vault_mcp.types.RevisionContent`.  Unlike
            :meth:`read` it carries no ``etag``: that describes the note as it
            is now, and feeding one from a revision read back as ``if_match``
            would assert a read the caller never made.

        Raises:
            InvalidRequestError: If the vault is not git-backed, *revision* is
                unusable, *path* is not a note, the note's identity cannot be
                traced to that revision, the content is over the read cap, or
                *section* is empty or not found there.
            DocumentUnreadableError: If the content there is not valid UTF-8,
                or is a Git LFS pointer.
            ValueError: If git itself fails.
        """
        return self._git_query_mgr.read_at_revision(path, revision, section=section)

    def get_metadata(self, path: str) -> DocumentMeta | None:
        """Return indexed metadata (title/folder/frontmatter) without a read.

        Unlike :meth:`read`, this hits only the index — no file I/O, no
        ``max_note_read_bytes`` cap — so it is the right call for consumers that
        need a label rather than the document body (e.g. graph node rendering).

        Args:
            path: Relative document path.

        Returns:
            A :class:`~markdown_vault_mcp.types.DocumentMeta`, or ``None`` if the
            document is not indexed.
        """
        return self._search_mgr.get_metadata(path)

    def list_documents(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
        filters: Mapping[str, str | None] | None = None,
    ) -> list[NoteInfo | AttachmentInfo]:
        """List documents (and optionally attachments) in the vault.

        Delegates to :meth:`SearchManager.list`.

        Args:
            folder: If provided, only return documents in this folder (and
                sub-folders).
            pattern: Unix glob matched against the relative path using
                :func:`fnmatch.fnmatch`.  Example: ``"Journal/*.md"``.
            include_attachments: When ``True``, also return non-.md files
                that match the attachment allowlist.  Each
                :class:`~markdown_vault_mcp.types.AttachmentInfo` entry
                includes ``kind="attachment"`` and ``mime_type``.
            filters: Optional ``{frontmatter_key: value}`` equality filters
                (AND semantics). On an active OKF bundle, ``status`` /
                ``stale`` / ``trust_tier`` carry OKF semantics. Any filter
                excludes attachments (they carry no frontmatter).

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` (and
            optionally :class:`~markdown_vault_mcp.types.AttachmentInfo`)
            objects.
        """
        return self._search_mgr.list(
            folder=folder,
            pattern=pattern,
            include_attachments=include_attachments,
            filters=filters,
        )

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed vault.

        Returns:
            Sorted list of folder strings (``""`` for the vault root).
        """
        return self._search_mgr.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field.

        Args:
            field: Frontmatter key to query (default: ``"tags"``).

        Returns:
            Sorted list of distinct value strings.

        Raises:
            InvalidRequestError: If *field* is not in
                ``indexed_frontmatter_fields``.
        """
        return self._search_mgr.list_tags(field)

    def get_toc(
        self,
        path: str,
        *,
        max_level: int | None = None,
        max_notes: int = 200,
    ) -> list[TocEntry] | SubtreeToc:
        """Return a table of contents for a note or a folder subtree.

        Note paths (ending in ``.md``) return a flat
        :class:`~markdown_vault_mcp.types.TocEntry` list with the title as a
        synthetic H1. Folder paths return a
        :class:`~markdown_vault_mcp.types.SubtreeToc`. The result
        depends on the FTS index, so cold-start callers must build the index
        first (bucket 3).

        Args:
            path: Note path or folder prefix.
            max_level: Drop headings with ``level`` above this (both modes).
            max_notes: Folder mode cap on distinct notes (default 200).

        Returns:
            Note path → ``list[TocEntry]`` with the document title prepended
            as a synthetic H1. Folder path → :class:`~markdown_vault_mcp.types.SubtreeToc`.

        Raises:
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
            DocumentNotFoundError: Note path with no document.
            InvalidRequestError: Invalid folder path or argument.
        """
        self._require_built()
        return self._doc_mgr.get_toc(path, max_level=max_level, max_notes=max_notes)

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
        folder: str | None = None,
        filters: dict[str, str] | None = None,
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
            folder: Optional folder to restrict results to (exact match or
                sub-folder prefix).
            filters: Optional ``{frontmatter_key: value}`` equality filters,
                ANDed; applied post-hoc against full frontmatter.

        Returns:
            List of grouped results.

        Raises:
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
            EmbeddingsNotConfiguredError: If the vault has no embeddings.
        """
        self._require_built()
        return self._search_mgr.get_similar(
            path,
            limit=limit,
            chunks_per_file=chunks_per_file,
            folder=folder,
            filters=filters,
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
            IndexUnavailableError: If :meth:`IndexFacet.build_index` has not been called.
            DocumentNotFoundError: If no document exists at the given path.
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
        """Return commits that touched a note, attachment, folder, or the whole vault.

        When *path* is ``None``, queries the full vault history.  Returns an
        empty list for vaults whose source directory is not inside a git
        repository.

        Args:
            path: A ``.md`` note or a configured attachment (e.g.
                ``assets/x.png``) scopes to that file; an existing directory
                (e.g. ``"guides"``) scopes to its subtree; ``None`` returns
                vault-wide history.
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
            queries (``path=None``) and directory queries (the subtree files
            the commit touched); it is always empty for single-note queries,
            since the path is already determined by the query arguments —
            callers know which file the commit touched without needing it
            echoed back.

        Raises:
            InvalidRequestError: If *path* is provided but fails path validation
                (unsupported extension or path traversal).
        """
        return self._git_query_mgr.get_history(
            path, since=since, until=until, limit=limit
        )

    def get_diff(
        self,
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
    ) -> str | list[CommitDiff]:
        """Return the diff of a note or attachment between a ref and HEAD.

        Exactly one of *since_sha* or *since_timestamp* must be supplied.

        Args:
            path: A ``.md`` note or a configured attachment (e.g.
                ``assets/x.png``) to diff.  Unsupported extensions raise
                ``ValueError``.
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
            ``True``.  For an attachment that git reports as binary, a
            ``--stat`` summary is returned instead of a unified patch; a text
            attachment returns a full unified diff, and ``.md`` notes are
            unchanged.  Returns an empty string / empty list when the file has
            no changes in the given range, or when the vault's source
            directory is not inside a git repository.  Per-commit
            (``per_commit=True``) attachment diffs are rename-aware (a copied
            file renders as an add).

        Raises:
            InvalidRequestError: If not exactly one of *since_sha* /
                *since_timestamp* is supplied, *since_sha* is not 4-64
                lowercase hex digits or names no commit, or *path* has an
                extension that is neither ``.md`` nor a configured attachment
                type.
            ValueError: If a git subprocess fails for any other reason.
        """
        return self._git_query_mgr.get_diff(
            path,
            since_sha=since_sha,
            since_timestamp=since_timestamp,
            per_commit=per_commit,
            limit=limit,
        )

    def stats(self) -> VaultStats:
        """Return vault-wide statistics.

        Delegates to :meth:`SearchManager.stats`.

        Returns:
            :class:`~markdown_vault_mcp.types.VaultStats` snapshot.
        """
        return self._search_mgr.stats()

    def okf_stats(self) -> dict[str, Any] | None:
        """Return the OKF statistics section, or ``None`` when inactive.

        Adds the vault's write state to :meth:`SearchManager.okf_stats`.

        Returns:
            The OKF aggregate dict (mode, declared version, type histogram,
            status/trust breakdowns, stale count, and ``write_configured``
            and ``write_effective`` when wired), or ``None`` when OKF read
            semantics are not active.
        """
        section = self._search_mgr.okf_stats()
        if section is not None and self._okf_write_state is not None:
            configured, effective = self._okf_write_state()
            section["write_configured"] = configured
            section["write_effective"] = effective
        return section

    def attachment_size(self, path: str) -> int:
        """Return an attachment's on-disk byte size without reading it.

        Delegates to :meth:`DocumentManager.attachment_size`.
        """
        return self._doc_mgr.attachment_size(path)

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read the binary content of a non-.md attachment.

        Delegates to :meth:`DocumentManager.read_attachment`.
        """
        return self._doc_mgr.read_attachment(path)
