from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from markdown_vault_mcp.utils.serialization import toc_payload
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_deps import get_vault
from .._server_queryable import needs_queryable
from ._common import _maybe_wait_for_drain, _staleness_result


def register(mcp: FastMCP) -> None:
    """Register read/query tools on *mcp*."""

    @mcp.tool(
        icons=_TOOL_ICONS["search"],
        annotations={
            "title": "Search Vault",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def search(
        query: str,
        limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
        folder: str | None = None,
        filters: dict[str, str] | None = None,
        chunks_per_file: int | None = None,
        snippet_words: int | None = None,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Find documents matching a query using full-text or semantic search.

        Search the vault. Default mode is "keyword" (FTS5/BM25). Pass
        mode="hybrid" when 'stats' shows semantic_search_available=True —
        hybrid fuses keyword and vector results for best quality. Use
        mode="semantic" for pure vector similarity.

        The 'content' field in each result is a snippet by default, not the
        full document. Use read(path, section=heading) to retrieve the full
        text of a specific section.

        Args:
            query: Natural language or keyword query string.
            limit: Maximum results to return (default 10).
            mode: "keyword" uses FTS5/BM25 for exact terms. "semantic" uses
                vector similarity (requires embeddings). "hybrid" fuses both
                via reciprocal rank fusion — best quality when available.
            folder: Restrict to documents under this folder path (e.g.
                "Journal"). Must match a value from 'list_folders'.
                Use folder="" for root-level (top-level) documents only.
            filters: Filter by indexed frontmatter field values, e.g.
                {"cluster": "craft", "tags": "pacing"}. Only fields listed
                in indexed_frontmatter_fields (see 'stats') can be filtered.
                Multiple filters are ANDed. For list fields (e.g. tags),
                this checks membership — {"tags": "pacing"} matches any
                document where "pacing" appears in the tags list.
            chunks_per_file: Maximum number of sections to return per file
                (default 2).  Set to 1 to get only the top-ranked section
                per file.  Must be >= 1.
            snippet_words: Width of the snippet window in words. Omit to use
                the server default. Set to 0 to return full chunk content.
                Use read(path, section=heading) for full section recovery.
            wait_for_pending_writes: When True, wait until your recent
                write/edit/delete/rename operations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a write/edit/delete/rename whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of result dicts ranked by file relevance. Each contains:

            - path (str): Relative path of the document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - score (float): File-level score = max(section.score).
              Higher = better match.  BM25 (keyword) or cosine (semantic/
              hybrid); not comparable across modes.
            - search_type (str): "keyword", "semantic", or "hybrid".
            - frontmatter (dict): Parsed YAML frontmatter of the document.
            - sections (list[dict]): Up to ``chunks_per_file`` best-matching
              sections, each with:

              - heading (str | None): Section heading or null for intro.
              - content (str): Matched snippet (or full chunk if
                snippet_words=0).  Call read(path, section=heading) for
                the full section text.
              - score (float): Chunk-level score for this section.

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.

        Also useful for finding merge candidates during triage — if a
        close match exists for a new capture, prefer merging over
        creating a near-duplicate.

        Raises:
            EmbeddingsNotConfiguredError: If mode is "semantic" or "hybrid" and
                no embedding provider is configured (a ``ValueError`` subclass).
        """
        drained = await _maybe_wait_for_drain(vault, wait_for_pending_writes, "search")
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(
            vault.reader.search,
            query,
            limit=limit,
            mode=mode,
            folder=folder,
            filters=filters,
            chunks_per_file=chunks_per_file,
            snippet_words=snippet_words,
        )
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["read"],
        annotations={
            "title": "Read Note",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def read(
        path: str,
        section: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Read the full content of a document or attachment by path.

        For .md documents: returns content (the full raw file including
        frontmatter), plus the parsed frontmatter, title, and folder.
        For attachments (pdf, png, etc.): returns base64-encoded binary content
        and MIME type. Use 'list_documents(include_attachments=True)' to
        discover attachment paths. Use 'stats' to see allowed extensions.

        Do not guess paths — look them up first via 'search' or 'list_documents'.

        To recover the full text of a specific section returned by 'search',
        pass section=heading (the value from the result's 'heading' field).

        **Context cost:** every byte returned counts against the LLM's
        context budget. Reads above ``MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES``
        (default 256 KB for ``.md``) or
        ``MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB`` (default 1 MB for
        binaries) raise ``ValueError``. For partial markdown reads, pass
        ``section=heading`` (use the ``heading`` field from a ``search()``
        result).

        Args:
            path: Relative path to the document or attachment
                (e.g. "Journal/note.md" or "assets/diagram.pdf").
                Case-sensitive.
            section: When provided, return the whole section whose heading
                matches *section* — every paragraph, list, and sub-section
                from the heading up to the next heading at the same or higher
                level (case-sensitive; internal whitespace is collapsed before
                comparison). Pass the ``heading`` value from a ``search``
                result unchanged for guaranteed match. ``None`` (the default)
                returns the whole document. Ignored for non-.md paths.

        Returns:
            For .md: dict with path, title, folder, content (the full raw
            file including frontmatter, or the section text when section= is
            given), frontmatter (dict —
            empty {} when section= is provided; call read(path) without
            section= to get the full document's frontmatter),
            modified_at (Unix timestamp), etag (SHA-256 hex str or null).
            For attachments: dict with path, mime_type (str or null),
            size_bytes (int), content_base64 (str), modified_at (Unix timestamp),
            etag (SHA-256 hex str or null).
            The 'etag' value can be passed as 'if_match' to write, edit,
            delete, or rename to guard against concurrent modifications.

        Raises:
            ValueError: If no file exists at the given path, the extension is
                not in the attachment allowlist, the file exceeds
                ``MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB``, or the requested
                section heading is not found.
        """
        if not path.endswith(".md"):
            cap_mb = vault.max_attachment_size_mb
            if cap_mb > 0:
                size = await asyncio.to_thread(vault.reader.attachment_size, path)
                limit = int(cap_mb * 1024 * 1024)
                if size > limit:
                    raise ValueError(
                        f"Attachment {path!r} is {size} bytes "
                        f"({size / 1024 / 1024:.1f} MB), exceeds "
                        f"MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB ({cap_mb} MB). "
                        f"Increase MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB if "
                        f"you need the bytes in context."
                    )
            attachment = await asyncio.to_thread(vault.reader.read_attachment, path)
            return asdict(attachment)
        note = await asyncio.to_thread(vault.reader.read, path, section=section)
        if note is None:
            raise ValueError(f"Document not found: {path}")
        return asdict(note)

    @mcp.tool(
        icons=_TOOL_ICONS["list_documents"],
        annotations={
            "title": "List Documents",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_documents(
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """List documents (and optionally attachments) in the vault.

        Use this to enumerate documents when you need a complete listing, not
        ranked search results. For finding documents by content, use 'search'.
        Does NOT include body content — call 'read' for full text.

        Args:
            folder: Return only documents in this folder (e.g. "Journal").
                Use folder="" for root-level (top-level) documents only.
            pattern: Unix glob matched against relative paths (e.g.
                "Journal/*.md", "**/*meeting*.md").
            include_attachments: When True, also returns non-.md files (PDFs,
                images, etc.) that match the configured allowlist. Each
                attachment entry includes kind="attachment" and mime_type.
                Default False (notes only).
            wait_for_pending_writes: When True, wait until your recent
                write/edit/delete/rename operations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a write/edit/delete/rename whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of info dicts. Every entry has a 'kind' field.
            Notes: path, title, folder, frontmatter, modified_at, kind="note".
            Attachments (when include_attachments=True): path, folder,
            mime_type, size_bytes, modified_at, kind="attachment".
            Body content is not included in either case.

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "list_documents"
        )
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(
            vault.reader.list_documents,
            folder=folder,
            pattern=pattern,
            include_attachments=include_attachments,
        )
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["list_folders"],
        annotations={
            "title": "List Folders",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_folders(
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[str]:
        """List all folder paths that contain documents.

        Call this to discover valid folder names before filtering 'search' or
        'list_documents' by folder. The root folder (top-level documents) is
        represented as an empty string "".

        Args:
            wait_for_pending_writes: When True, wait until your recent
                write/edit/delete/rename operations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a write/edit/delete/rename whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            Sorted list of folder paths, e.g. ["", "Journal", "Projects"].
            Pass any of these as the 'folder' argument to 'search' or
            'list_documents'.

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "list_folders"
        )
        gen_before = vault.index.write_generation()
        folders = await asyncio.to_thread(vault.reader.list_folders)
        return _staleness_result(
            vault, folders, drained_on_request=drained, gen_before=gen_before
        )

    @mcp.tool(
        icons=_TOOL_ICONS["list_tags"],
        annotations={
            "title": "List Tags",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_tags(
        field: str = "tags",
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[str]:
        """List all distinct values for a frontmatter field across the vault.

        Use this to discover valid filter values before calling 'search' with
        the 'filters' argument. Only fields listed in indexed_frontmatter_fields
        (see 'stats') are indexed — querying other fields returns an empty list.

        Args:
            field: Frontmatter field name to enumerate (default "tags"). Must
                be one of the values in indexed_frontmatter_fields (from 'stats')
                — passing any other field silently returns an empty list, not an
                error.
            wait_for_pending_writes: When True, wait until your recent
                write/edit/delete/rename operations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a write/edit/delete/rename whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            Sorted list of distinct string values, e.g.
            ["craft", "pacing", "worldbuilding"]. Use these as values in the
            'filters' dict when calling 'search'.

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "list_tags"
        )
        gen_before = vault.index.write_generation()
        values = await asyncio.to_thread(vault.reader.list_tags, field)
        return _staleness_result(
            vault, values, drained_on_request=drained, gen_before=gen_before
        )

    @mcp.tool(
        icons=_TOOL_ICONS["stats"],
        annotations={
            "title": "Vault Stats",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def stats(
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Get an overview of the vault's size, capabilities, and configuration.

        Call this at the start of a session to understand what the vault
        contains and what search modes are available. The
        'semantic_search_available' field tells you whether mode="semantic" or
        mode="hybrid" can be used in 'search'.

        Args:
            wait_for_pending_writes: When True, wait until your recent
                write/edit/delete/rename operations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a write/edit/delete/rename whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            Dict with the following fields:

            - document_count (int): Total number of indexed documents.
            - chunk_count (int): Total number of indexed text chunks.
            - folder_count (int): Total number of folders containing documents.
            - semantic_search_available (bool): True if mode="semantic" or
              mode="hybrid" can be used in 'search'.
            - indexed_frontmatter_fields (list[str]): Field names usable as
              'filters' in 'search' and as 'field' in 'list_tags'.
            - attachment_extensions (list[str]): Allowed non-.md extensions.
            - link_count (int): Total number of indexed links. 0 may mean no
              links exist or link tracking not yet built (call 'reindex').
            - broken_link_count (int): Links pointing to missing documents.
              Call 'get_broken_links' if non-zero.
            - orphan_count (int): Notes with no inbound or outbound links.
              Call 'get_orphan_notes' if non-zero.

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(vault, wait_for_pending_writes, "stats")
        gen_before = vault.index.write_generation()
        result = await asyncio.to_thread(vault.reader.stats)
        return _staleness_result(
            vault, asdict(result), drained_on_request=drained, gen_before=gen_before
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_similar"],
        annotations={
            "title": "Similar Notes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def get_similar(
        path: str,
        limit: int = 10,
        chunks_per_file: int | None = None,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Find notes most semantically similar to the given document.

        Uses stored embedding vectors — no re-embedding needed. The
        reference document is excluded from results. Requires semantic
        search to be configured (check 'stats' for
        semantic_search_available). Returns an empty list if embeddings are
        not configured (check 'embeddings_status') or the document has no
        stored vectors (call 'build_embeddings' to embed missing chunks).

        Args:
            path: Relative path of the reference document (e.g.
                "notes/topic.md"). Case-sensitive.
            limit: Maximum number of similar notes to return (default 10).
            chunks_per_file: Maximum sections returned per file (default 2).
                Set to 1 for one best section per file.  Must be >= 1.
            wait_for_pending_writes: When True, wait until your recent
                write/edit/delete/rename operations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a write/edit/delete/rename whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of result dicts ranked by file similarity. Each contains:

            - path (str): Relative path of the similar document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - score (float): File-level cosine similarity (max of section
              scores), 0.0-1.0; higher = more similar.
            - search_type (str): Always "semantic".
            - frontmatter (dict): Parsed YAML frontmatter.
            - sections (list[dict]): Up to chunks_per_file best-matching
              sections, each with:

              - heading (str | None): Section heading or null for intro.
              - content (str): Matched chunk text.
              - score (float): Chunk-level score for this section.

            Index freshness is reported out-of-band in the response's
            ``_meta.index_stale`` field — True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (``wait_for_pending_writes`` timing out, a write completing inside the
            read window, or non-idle at response time), False otherwise.

        Useful for finding link candidates that aren't yet wikilinked — the
        vault's organic graph is almost always denser than its explicit one.
        See the ``propose-links`` prompt for a full vault-wide sweep.

        Raises:
            ValueError: If no document exists at the given path.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_similar"
        )
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(
            vault.reader.get_similar,
            path,
            limit=limit,
            chunks_per_file=chunks_per_file,
        )
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_toc"],
        annotations={
            "title": "Table of Contents",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def get_toc(
        path: str,
        max_level: int | None = None,
        max_notes: int = 200,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Heading outline for a single note or a whole folder subtree.

        If 'path' ends in '.md' it is a note: returns a flat ordered list of
        {heading, level} (the title as a synthetic H1). Otherwise 'path' is a
        folder: returns {path, notes, truncated} where 'notes' is an ordered
        list of {path, title, headings} aggregating every note under the
        subtree. Mirrors the 'toc://vault/{path}' resource, adding the
        max_level / max_notes controls below.

        Args:
            path: Note path ("a/b.md") or folder prefix ("a/b").
            max_level: Drop headings deeper than this level (e.g. 2 keeps
                H1-H2); must be >= 1. The synthetic H1 title always survives.
                Default None returns all levels.
            max_notes: Folder mode only — cap on distinct notes (default 200,
                must be >= 1). When more notes match, the first max_notes (by
                path) are returned and 'truncated' is True.
            wait_for_pending_writes: When True, wait until recent
                write/edit/delete/rename operations are applied to the index
                before answering. Default False answers from the current
                index; inspect '_meta.index_stale' to tell whether a write was
                still in flight. Bounded by a server timeout (default 60s).

        Returns:
            Note mode: list of {heading (str), level (int)}.
            Folder mode: {path (str), notes (list[{path, title, headings}]),
            truncated (bool)}. Empty/nonexistent folder → empty 'notes'.

            Index freshness is reported out-of-band in '_meta.index_stale'.

        Raises:
            ValueError: Note path with no document; invalid folder path.
        """
        drained = await _maybe_wait_for_drain(vault, wait_for_pending_writes, "get_toc")
        gen_before = vault.index.write_generation()
        data = await asyncio.to_thread(
            vault.reader.get_toc,
            path,
            max_level=max_level,
            max_notes=max_notes,
        )
        payload = toc_payload(data)
        return _staleness_result(
            vault,
            payload,
            drained_on_request=drained,
            gen_before=gen_before,
            force_result_wrap=True,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_recent"],
        annotations={
            "title": "Recent Notes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_recent(
        limit: int = 20,
        folder: str | None = None,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Get the most recently modified notes in the vault.

        Returns notes ordered by file modification time (most recent first).
        Useful for surfacing recently changed content without a search query —
        for example to summarize recent activity or resume work on recently
        edited notes.

        Args:
            limit: Maximum number of notes to return (default 20).
            folder: Optional folder filter. When provided, only returns
                notes from this folder (e.g. "Journal").
            wait_for_pending_writes: When True, wait until your recent
                write/edit/delete/rename operations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a write/edit/delete/rename whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of note info dicts, each with: path, title, folder,
            frontmatter, modified_at (Unix timestamp), kind ("note").

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_recent"
        )
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(
            vault.reader.get_recent, limit=limit, folder=folder
        )
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_context"],
        annotations={
            "title": "Note Context",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def get_context(
        path: str,
        similar_limit: int = 5,
        link_limit: int = 10,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Get a consolidated context dossier for a document.

        Replaces separate calls to 'get_backlinks', 'get_outlinks', and
        'get_similar' when you need more than one.

        Returns everything useful about a note in one call: its metadata,
        backlinks (documents that link to it), outlinks (documents it links
        to), semantically similar notes, other notes in the same folder, and
        indexed frontmatter tags. Use this instead of making 4-5 separate
        tool calls when you need a full picture of a note's place in the
        vault.

        Args:
            path: Relative path of the document (e.g. "notes/topic.md").
                Case-sensitive.
            similar_limit: Maximum number of similar notes to include
                (default 5). Pass 0 to skip the similarity lookup — do this
                when 'stats' shows semantic_search_available=False (embeddings
                are not configured).
            link_limit: Maximum number of backlinks and outlinks to include
                each (default 10).
            wait_for_pending_writes: When True, wait until your recent
                write/edit/delete/rename operations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a write/edit/delete/rename whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            Dict with the note context. Fields:

            - path (str): Relative path of the document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - frontmatter (dict): Parsed YAML frontmatter.
            - modified_at (float): Unix timestamp of last modification.
            - backlinks (list): Documents linking to this note. List of dicts,
              each with:

              - source_path (str): Path of the document containing the link.
              - source_title (str): Title of the source document.
              - link_text (str): The clickable text of the link.
              - link_type (str): One of "markdown", "wikilink", or "reference".
              - fragment (str | None): Heading anchor (e.g. "#section"), or null.
              - raw_target (str): Literal link target as written in the source.

            - outlinks (list): Links from this note. List of dicts, each with:

              - target_path (str): Path of the linked document.
              - link_text (str): The clickable text of the link.
              - link_type (str): One of "markdown", "wikilink", or "reference".
              - fragment (str | None): Heading anchor (e.g. "#section"), or null.
              - raw_target (str): Literal link target as written in the source.
              - exists (bool): True if the target document is indexed.

            - similar (list): Semantically similar notes, field-collapsed by
              file (chunks_per_file=1 for compact dossiers).  List of dicts,
              each with:

              - path (str): Relative path of the similar document.
              - title (str): Document title.
              - folder (str): Parent folder path.
              - score (float): File-level cosine similarity 0.0-1.0 = score
                of the best matching section.
              - search_type (str): Always "semantic".
              - frontmatter (dict): Parsed YAML frontmatter.
              - sections (list): Single best-matching section, each with
                heading (str|null), content (str), score (float).
                Call get_similar(path, chunks_per_file=N) for more sections.

            - folder_notes (list[str]): Paths of other notes in the same
              folder (up to 20). Plain strings, not dicts.
            - tags (dict[str, list[str]]): Indexed frontmatter field →
              distinct values for this note.

            Index freshness is reported out-of-band in the response's
            ``_meta.index_stale`` field — True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (``wait_for_pending_writes`` timing out, a write completing inside the
            read window, or non-idle at response time), False otherwise.

        The ``similar`` field in the response surfaces notes that may warrant
        explicit links to the context note but don't yet — a common input to
        manual or automated link proposal.

        Raises:
            ValueError: If no document exists at the given path.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_context"
        )
        gen_before = vault.index.write_generation()
        result = await asyncio.to_thread(
            vault.reader.get_context,
            path,
            similar_limit=similar_limit,
            link_limit=link_limit,
        )
        return _staleness_result(
            vault, asdict(result), drained_on_request=drained, gen_before=gen_before
        )
