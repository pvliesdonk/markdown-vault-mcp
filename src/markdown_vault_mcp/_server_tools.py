"""MCP tool registrations for the markdown-vault-mcp server.

Call :func:`register_tools` after constructing the :class:`~fastmcp.FastMCP`
instance in :func:`~markdown_vault_mcp.mcp_server.create_server`.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
from dataclasses import asdict
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import EditConflictError

from ._icons import _TOOL_ICONS
from ._server_deps import get_collection

logger = logging.getLogger(__name__)

_ALLOWED_FETCH_SCHEMES = frozenset({"http", "https"})

# SSRF protection: block private/reserved IP ranges.
_FETCH_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "metadata.google.internal"}
)


def _is_private_url(url: str) -> bool:
    """Return True if *url* targets a private, loopback, or link-local address.

    Uses :mod:`ipaddress` for IP-based hosts and a hostname blocklist for
    well-known internal names. This is a best-effort SSRF guard — it does
    **not** prevent DNS rebinding attacks.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if hostname in _FETCH_BLOCKED_HOSTNAMES:
        return True

    try:
        addr = ipaddress.ip_address(hostname)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_unspecified  # 0.0.0.0 — is_private misses this on Python 3.10
        )
    except ValueError:
        # Not an IP literal — allow (could be a public hostname).
        return False


def register_tools(mcp: FastMCP, *, transport: str = "stdio") -> None:
    """Register all MCP tools on *mcp*.

    Args:
        mcp: The :class:`~fastmcp.FastMCP` instance to register tools on.
        transport: The MCP transport in use.  ``create_download_link`` is
            only registered for non-stdio transports (``"sse"`` or
            ``"http"``), because stdio has no HTTP server.
    """

    # --- Read-only tools (always visible) ---

    @mcp.tool(
        icons=_TOOL_ICONS["search"],
        annotations={
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
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """Find documents matching a query using full-text or semantic search.

        Search the collection. Default mode is "keyword" (FTS5/BM25). Pass
        mode="hybrid" when 'stats' shows semantic_search_available=True —
        hybrid fuses keyword and vector results for best quality. Use
        mode="semantic" for pure vector similarity.

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

        Returns:
            List of result dicts ranked by relevance. Each contains:

            - path (str): Relative path of the document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - heading (str | None): Section heading of the matched chunk,
              or null for the document intro.
            - content (str): Matched chunk text (not the full document).
            - score (float): BM25 relevance score (keyword mode) or cosine
              similarity 0.0-1.0 (semantic/hybrid); higher = better match.
            - search_type (str): "keyword" or "semantic".
            - frontmatter (dict): Parsed YAML frontmatter of the document.

        Raises:
            ValueError: If mode is "semantic" or "hybrid" and no embedding
                provider is configured.
        """
        results = await asyncio.to_thread(
            collection.search,
            query,
            limit=limit,
            mode=mode,
            folder=folder,
            filters=filters,
        )
        return [asdict(r) for r in results]

    @mcp.tool(
        icons=_TOOL_ICONS["read"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def read(
        path: str,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Read the full content of a document or attachment by path.

        For .md documents: returns markdown body, frontmatter, title, folder.
        For attachments (pdf, png, etc.): returns base64-encoded binary content
        and MIME type. Use 'list_documents(include_attachments=True)' to
        discover attachment paths. Use 'stats' to see allowed extensions.

        Do not guess paths — look them up first via 'search' or 'list_documents'.

        Args:
            path: Relative path to the document or attachment
                (e.g. "Journal/note.md" or "assets/diagram.pdf").
                Case-sensitive.

        Returns:
            For .md: dict with path, title, folder, content (markdown body),
            frontmatter (dict), modified_at (Unix timestamp),
            etag (SHA-256 hex str or null).
            For attachments: dict with path, mime_type (str or null),
            size_bytes (int), content_base64 (str), modified_at (Unix timestamp),
            etag (SHA-256 hex str or null).
            The 'etag' value can be passed as 'if_match' to write, edit,
            delete, or rename to guard against concurrent modifications.

        Raises:
            ValueError: If no file exists at the given path, the extension is
                not in the attachment allowlist, or the file exceeds the size
                limit.
        """
        if not path.endswith(".md"):
            attachment = await asyncio.to_thread(collection.read_attachment, path)
            return asdict(attachment)
        note = await asyncio.to_thread(collection.read, path)
        if note is None:
            raise ValueError(f"Document not found: {path}")
        return asdict(note)

    @mcp.tool(
        icons=_TOOL_ICONS["list_documents"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_documents(
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """List documents (and optionally attachments) in the collection.

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

        Returns:
            List of info dicts. Every entry has a 'kind' field.
            Notes: path, title, folder, frontmatter, modified_at, kind="note".
            Attachments (when include_attachments=True): path, folder,
            mime_type, size_bytes, modified_at, kind="attachment".
            Body content is not included in either case.
        """
        results = await asyncio.to_thread(
            collection.list,
            folder=folder,
            pattern=pattern,
            include_attachments=include_attachments,
        )
        return [asdict(r) for r in results]

    @mcp.tool(
        icons=_TOOL_ICONS["list_folders"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_folders(
        collection: Collection = Depends(get_collection),
    ) -> list[str]:
        """List all folder paths that contain documents.

        Call this to discover valid folder names before filtering 'search' or
        'list_documents' by folder. The root folder (top-level documents) is
        represented as an empty string "".

        Returns:
            Sorted list of folder paths, e.g. ["", "Journal", "Projects"].
            Pass any of these as the 'folder' argument to 'search' or
            'list_documents'.
        """
        return await asyncio.to_thread(collection.list_folders)

    @mcp.tool(
        icons=_TOOL_ICONS["list_tags"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_tags(
        field: str = "tags",
        collection: Collection = Depends(get_collection),
    ) -> list[str]:
        """List all distinct values for a frontmatter field across the collection.

        Use this to discover valid filter values before calling 'search' with
        the 'filters' argument. Only fields listed in indexed_frontmatter_fields
        (see 'stats') are indexed — querying other fields returns an empty list.

        Args:
            field: Frontmatter field name to enumerate (default "tags"). Must
                be one of the values in indexed_frontmatter_fields (from 'stats')
                — passing any other field silently returns an empty list, not an
                error.

        Returns:
            Sorted list of distinct string values, e.g.
            ["craft", "pacing", "worldbuilding"]. Use these as values in the
            'filters' dict when calling 'search'.
        """
        return await asyncio.to_thread(collection.list_tags, field)

    @mcp.tool(
        icons=_TOOL_ICONS["stats"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def stats(
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Get an overview of the collection's size, capabilities, and configuration.

        Call this at the start of a session to understand what the collection
        contains and what search modes are available. The
        'semantic_search_available' field tells you whether mode="semantic" or
        mode="hybrid" can be used in 'search'.

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
        """
        result = await asyncio.to_thread(collection.stats)
        return asdict(result)

    @mcp.tool(
        icons=_TOOL_ICONS["embeddings_status"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def embeddings_status(
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Check the embedding provider configuration and vector index status.

        Use this to diagnose why semantic search is unavailable. Embeddings
        are built automatically on startup when configured, so chunk_count
        should normally match the FTS chunk count from 'stats'. If it is
        lower, call 'build_embeddings' (without force) to embed the missing
        chunks. Use 'build_embeddings' with force=True only to rebuild from
        scratch after changing the embedding model.

        Returns:
            Dict with the following fields:

            - available (bool): True if semantic search can be used in 'search'.
            - provider (str | None): Provider class name when configured
              (e.g. "OllamaProvider"), or null if not configured.
            - chunk_count (int): Number of chunks currently in the vector index.
            - path (str | None): Vector index file path when persisted, or null.
        """
        return await asyncio.to_thread(collection.embeddings_status)

    # --- Link tools (read-only) ---

    @mcp.tool(
        icons=_TOOL_ICONS["get_backlinks"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_backlinks(
        path: str,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """Find all documents that link TO the given document (backlinks).

        Use this to discover which notes reference a particular document.
        For a full picture of a note's place in the vault (backlinks,
        outlinks, similar notes, folder peers), use 'get_context' instead
        of calling this separately. Call 'get_backlinks' directly when you
        only need the inbound link list.
        Backlinks reveal implicit relationships that search alone cannot
        surface — they show what other authors considered relevant to this
        document.

        Args:
            path: Relative path of the target document (e.g.
                "notes/topic.md"). Case-sensitive.

        Returns:
            List of dicts, each with:

            - source_path (str): Path of the document containing the link.
            - source_title (str): Title of the source document.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of "markdown", "wikilink", or "reference".
            - fragment (str | None): Heading anchor (e.g. "#section"), or null.
            - raw_target (str): Literal link target as written in the source.

        Raises:
            ValueError: If no document exists at the given path.
        """
        results = await asyncio.to_thread(collection.get_backlinks, path)
        return [asdict(r) for r in results]

    @mcp.tool(
        icons=_TOOL_ICONS["get_outlinks"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_outlinks(
        path: str,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """Find all links FROM the given document to other documents (outlinks).

        Use this to see what a document references. For a full picture of
        a note's place in the vault, use 'get_context' instead of calling
        this separately. Call 'get_outlinks' directly when you only need
        the outbound link list. Each result includes an 'exists' flag —
        False means the link is broken (the target is missing from the
        collection).

        Args:
            path: Relative path of the source document (e.g.
                "notes/topic.md"). Case-sensitive.

        Returns:
            List of dicts, each with:

            - target_path (str): Path of the linked document.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of "markdown", "wikilink", or "reference".
            - fragment (str | None): Heading anchor (e.g. "#section"), or null.
            - raw_target (str): Literal link target as written in the source.
            - exists (bool): True if the target document is indexed.

        Raises:
            ValueError: If no document exists at the given path.
        """
        results = await asyncio.to_thread(collection.get_outlinks, path)
        return [asdict(r) for r in results]

    @mcp.tool(
        icons=_TOOL_ICONS["get_broken_links"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_broken_links(
        folder: str | None = None,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """Find all links that point to non-existent documents (broken links).

        Use this to audit link health across the collection. Call this when
        'stats' shows broken_link_count > 0, or after a 'rename' that did
        not use update_links=True, to see what links were left pointing to
        the old path. A broken link means the target path does not match any
        indexed document — the referenced note may have been deleted, renamed,
        or never created.

        Args:
            folder: Optional folder filter. When provided, only checks
                links from documents in this folder (e.g. "Journal").
                Without this, checks all documents.

        Returns:
            List of dicts, each with:

            - source_path (str): Path of the document containing the broken link.
            - source_title (str): Title of the source document.
            - target_path (str): The missing target path.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of "markdown", "wikilink", or "reference".
            - fragment (str | None): Heading anchor (e.g. "#section"), or null.
            - raw_target (str): Literal link target as written in the source.
        """
        results = await asyncio.to_thread(collection.get_broken_links, folder=folder)
        return [asdict(r) for r in results]

    # --- Similarity tools (read-only) ---

    @mcp.tool(
        icons=_TOOL_ICONS["get_similar"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_similar(
        path: str,
        limit: int = 10,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """Find notes most semantically similar to the given document.

        Uses stored embedding vectors — no re-embedding needed. The
        reference document is excluded from results. Requires semantic
        search to be configured (check 'stats' for
        semantic_search_available). Returns an empty list if embeddings
        are not configured (check 'embeddings_status') or the document has
        no stored vectors (call 'build_embeddings' to embed missing chunks).

        Args:
            path: Relative path of the reference document (e.g.
                "notes/topic.md"). Case-sensitive.
            limit: Maximum number of similar notes to return (default 10).

        Returns:
            List of result dicts ranked by similarity. Each contains:

            - path (str): Relative path of the similar document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - heading (str | None): Section heading of the most similar chunk.
            - content (str): Most similar chunk text.
            - score (float): Cosine similarity, 0.0-1.0; higher = more similar.
            - search_type (str): Always "semantic".
            - frontmatter (dict): Parsed YAML frontmatter.

        Raises:
            ValueError: If no document exists at the given path.
        """
        results = await asyncio.to_thread(collection.get_similar, path, limit=limit)
        return [asdict(r) for r in results]

    # --- Recently modified ---

    @mcp.tool(
        icons=_TOOL_ICONS["get_recent"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_recent(
        limit: int = 20,
        folder: str | None = None,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """Get the most recently modified notes in the collection.

        Returns notes ordered by file modification time (most recent first).
        Useful for surfacing recently changed content without a search query —
        for example to summarize recent activity or resume work on recently
        edited notes.

        Args:
            limit: Maximum number of notes to return (default 20).
            folder: Optional folder filter. When provided, only returns
                notes from this folder (e.g. "Journal").

        Returns:
            List of note info dicts, each with: path, title, folder,
            frontmatter, modified_at (Unix timestamp), kind ("note").
        """
        results = await asyncio.to_thread(
            collection.get_recent, limit=limit, folder=folder
        )
        return [asdict(r) for r in results]

    # --- Context dossier ---

    @mcp.tool(
        icons=_TOOL_ICONS["get_context"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_context(
        path: str,
        similar_limit: int = 5,
        link_limit: int = 10,
        collection: Collection = Depends(get_collection),
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

        Returns:
            Dict with the following fields:

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

            - similar (list): Semantically similar notes. List of dicts, each
              with:

              - path (str): Relative path of the similar document.
              - title (str): Document title.
              - score (float): Cosine similarity, 0.0-1.0; higher = more similar.

            - folder_notes (list[str]): Paths of other notes in the same
              folder (up to 20). Plain strings, not dicts.
            - tags (dict[str, list[str]]): Indexed frontmatter field →
              distinct values for this note.

        Raises:
            ValueError: If no document exists at the given path.
        """
        result = await asyncio.to_thread(
            collection.get_context,
            path,
            similar_limit=similar_limit,
            link_limit=link_limit,
        )
        return asdict(result)

    @mcp.tool(
        icons=_TOOL_ICONS["get_orphan_notes"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_orphan_notes(
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """Return all notes with no inbound or outbound links.

        WARNING: returns ALL orphans with no limit — check 'stats' for
        orphan_count before calling on large vaults.

        An orphan note has no backlinks (no other note links to it) and no
        outlinks (it links to nothing). Call this when 'stats' shows
        orphan_count > 0. Useful for finding isolated notes that may need to
        be connected to the rest of the vault or removed.

        Returns:
            List of dicts ordered by path, each with:

            - path (str): Relative path of the orphan note.
            - title (str): Title of the note.
            - folder (str): Folder containing the note.
            - frontmatter (dict): Parsed YAML frontmatter.
            - modified_at (float): Unix timestamp of last modification.
            - kind (str): Always "note".
        """
        results = await asyncio.to_thread(collection.get_orphan_notes)
        return [asdict(r) for r in results]

    @mcp.tool(
        icons=_TOOL_ICONS["get_most_linked"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_most_linked(
        limit: int = 10,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """Return the documents with the most inbound links, ranked by backlink count.

        Useful for discovering hub notes — frequently-referenced notes that are
        likely key concepts in the vault. For the specific documents that link to
        a particular note, use 'get_backlinks' instead.

        Args:
            limit: Maximum number of results to return. Default 10.

        Returns:
            List of dicts with path (str), title (str), and backlink_count (int
            — number of distinct source documents linking to this note), ordered
            by backlink_count descending.
        """
        results = await asyncio.to_thread(collection.get_most_linked, limit=limit)
        return [asdict(r) for r in results]

    @mcp.tool(
        icons=_TOOL_ICONS["get_connection_path"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_connection_path(
        source: str,
        target: str,
        max_depth: int = 10,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Find the shortest connection path between two notes in the link graph.

        Treats links as undirected — a link from A to B or B to A both count
        as a connection. Uses BFS; max_depth is clamped to [1, 10].

        Useful for discovering how two seemingly unrelated notes are connected
        through the vault's link structure (the "six degrees of separation" for
        your notes).

        Args:
            source: Vault-relative path of the starting note (e.g. 'Ideas/spark.md').
            target: Vault-relative path of the destination note.
            max_depth: Maximum number of hops to search. Default 10, max 10.

        Returns:
            A dict with the following fields:

            - `found` (bool): Whether a path was found within `max_depth` hops.
            - `path` (list[str]): Ordered list of note paths from source to target,
              or an empty list if not found.
            - `hops` (int): Number of edges in the path (`len(path) - 1`), or -1 if
              not found.
        """
        result: list[str] | None = await asyncio.to_thread(
            collection.get_connection_path, source, target, max_depth
        )

        if result is None:
            return {"found": False, "path": [], "hops": -1}
        return {"found": True, "path": result, "hops": len(result) - 1}

    # --- Git history tools ---

    @mcp.tool(
        icons=_TOOL_ICONS["get_history"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_history(
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
        collection: Collection = Depends(get_collection),
    ) -> list[dict[str, Any]]:
        """List commits that touched a note or the whole vault.

        Only available for git-backed vaults. Use 'stats' to check
        whether git is configured, or call this and handle the error.

        Args:
            path: Vault-relative path of the note to filter on (e.g.
                "notes/alpha.md"). Must end with ".md". Omit (or pass null)
                for vault-wide commit history.
            since: ISO 8601 datetime string ("2026-04-01T00:00:00") or a git
                date expression ("1 week ago"). Passed as --since to git log.
                Omit for full history.
            until: ISO 8601 datetime string or git date expression, passed as
                --until to git log. Both 'since' and 'until' boundaries are
                inclusive: a commit whose author date equals either endpoint
                is included in the result. Omit to disable the upper bound.
            limit: Maximum number of commits to return. Default 20, max 100.

        Returns:
            List of commit dicts, newest-first. Each contains:

            - sha (str): Full 40-character commit SHA.
            - short_sha (str): 7-character abbreviated SHA.
            - timestamp (str): ISO 8601 author timestamp.
            - author (str): Committer name and email, e.g. "Name <email>".
            - message (str): First line of the commit message.
            - paths_changed (list[str]): Files touched by the commit.
              Populated for vault-wide queries. Always [] for single-note
              queries (the queried note path is implicit).

        Raises:
            ToolError: If the path is invalid.
        """
        try:
            results = await asyncio.to_thread(
                collection.get_history,
                path,
                since=since,
                until=until,
                limit=limit,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return [asdict(r) for r in results]

    @mcp.tool(
        icons=_TOOL_ICONS["get_diff"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_diff(
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Return the diff of a note between a reference point and HEAD.

        Only available for git-backed vaults. Exactly one of 'since_sha' or
        'since_timestamp' must be provided. Use 'get_history' first to find
        commit SHAs.

        Args:
            path: Vault-relative path of the note to diff (e.g. "notes/alpha.md").
                Must end with ".md".
            since_sha: A commit SHA (full or abbreviated, at least 4 hex digits)
                to diff from. Mutually exclusive with since_timestamp.
            since_timestamp: ISO 8601 datetime string. Resolved via
                `git rev-list --before=<ts>` to the most recent commit at or
                before that instant — boundary is inclusive, so a commit
                whose author date equals since_timestamp IS the resolved ref.
                Mutually exclusive with since_sha.
            per_commit: When False (default), return a single unified diff from
                the reference point to HEAD. When True, return one diff per
                intervening commit.
            limit: When per_commit=True, cap the number of intervening commits
                returned to the `limit` most recent ones. Clamped to [1, 100].
                Defaults to null (unbounded — still bounded by the underlying
                since..HEAD range). Ignored when per_commit=False. Useful for
                keeping per-commit responses within context budgets when
                auditing long histories.

        Returns:
            When per_commit=False: dict with a single field:

            - diff (str): Unified diff from the reference to HEAD. Empty
              string when there are no changes. May include a truncation
              notice if the diff exceeds 50 KB.

            When per_commit=True: list of commit dicts, newest-first, each
            containing:

            - sha (str): Full commit SHA.
            - short_sha (str): Abbreviated SHA.
            - timestamp (str): ISO 8601 author timestamp.
            - message (str): First line of commit message.
            - diff (str): Unified diff for this commit.

        Raises:
            ToolError: If neither or both reference parameters are supplied,
                the SHA is invalid, or the reference commit is not found.
        """
        try:
            result = await asyncio.to_thread(
                collection.get_diff,
                path,
                since_sha=since_sha,
                since_timestamp=since_timestamp,
                per_commit=per_commit,
                limit=limit,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if isinstance(result, list):
            return [asdict(r) for r in result]
        return {"diff": result}

    # --- Index management tools ---

    @mcp.tool(
        icons=_TOOL_ICONS["reindex"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def reindex(
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Sync the search index with files changed on disk by an external process.

        Only needed when files are modified outside this server — for example,
        by a text editor, a sync tool, or another process writing directly to
        the vault directory. Do NOT call this after using 'write', 'edit',
        'delete', or 'rename' — those tools update the index immediately as
        part of the operation.

        After reindexing, changed documents are automatically re-embedded. To
        rebuild all embeddings from scratch (e.g. after changing the embedding
        model), use 'build_embeddings' with force=True instead.

        Returns:
            Dict with counts: added, modified, deleted, unchanged.
        """
        result = await asyncio.to_thread(collection.reindex)
        return asdict(result)

    @mcp.tool(
        icons=_TOOL_ICONS["build_embeddings"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def build_embeddings(
        force: bool = False,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Rebuild vector embeddings for semantic and hybrid search.

        Embeddings are built automatically on startup, so this is normally
        not needed. Use force=True to rebuild from scratch after changing
        the embedding model. Without force, skips if embeddings already exist.

        Args:
            force: When True, discards existing embeddings and rebuilds from
                scratch. Use only if the embedding model has changed.
                When False (default), only embeds chunks not yet in the
                vector index (incremental — does not skip if any exist).

        Returns:
            Dict with chunks_embedded: number of chunks newly embedded.
        """
        count = await asyncio.to_thread(collection.build_embeddings, force=force)
        return {"chunks_embedded": count}

    # --- Write tools (tag-based visibility) ---

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["write"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def write(
        path: str,
        content: str = "",
        frontmatter: dict[str, Any] | None = None,
        content_base64: str = "",
        if_match: str | None = None,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Create or overwrite a document or attachment.

        For .md documents: uses 'content' (markdown body) and optional
        'frontmatter'. WARNING: replaces the entire file — use 'edit'
        for targeted changes. The search index is updated immediately;
        do not call 'reindex' afterward.

        For attachments (pdf, png, etc.): uses 'content_base64' (base64-
        encoded binary). 'content' and 'frontmatter' are ignored.
        Parent directories are created automatically for both.

        Args:
            path: Relative path (e.g. "Journal/note.md" or
                "assets/photo.png"). Extension determines handling.
            content: Full markdown body for .md files (excluding
                frontmatter). Ignored for attachments.
            frontmatter: Optional YAML frontmatter dict for .md files,
                e.g. {"title": "My Note", "tags": ["draft"]}.
                Ignored for attachments.
            content_base64: Base64-encoded binary content for attachment
                files. Required when path is not .md.
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the write only proceeds if the file has not
                been modified since that read (optimistic concurrency).
                Omit to write unconditionally.

        Returns:
            Dict with path (str) and created (bool — true if new file,
            false if overwrite).

        Raises:
            ValueError: If content_base64 is missing/invalid for
                attachments, or the content exceeds the size limit.
            McpError: If if_match is provided and the file has been
                modified (ConcurrentModificationError).
        """
        if not path.endswith(".md"):
            if not content_base64:
                raise ValueError(
                    f"content_base64 is required for non-.md attachments: {path}"
                )
            try:
                raw_bytes = base64.b64decode(content_base64)
            except Exception as exc:
                raise ValueError(f"Invalid base64 in content_base64: {exc}") from exc
            result = await asyncio.to_thread(
                collection.write_attachment, path, raw_bytes, if_match=if_match
            )
            return asdict(result)
        result = await asyncio.to_thread(
            collection.write, path, content, frontmatter=frontmatter, if_match=if_match
        )
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["edit"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def edit(
        path: str,
        old_text: str | None = None,
        new_text: str = "",
        if_match: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Make a targeted text replacement in an existing .md note (not supported for attachments).

        Three edit modes:
        - **Exact match** (old_text only): pass a portion of the file as
          old_text — must appear exactly once. Frontmatter can be edited.
        - **Line-range** (line_start + line_end, no old_text): replace the
          specified lines with new_text. Lines are 1-based (matching
          'read' output). Recommended: pass if_match for safety.
        - **Scoped match** (old_text + line_start/line_end): search for
          old_text within the line range only — useful when old_text
          appears multiple times in the file.

        When exact match fails, a normalized comparison is attempted
        (Unicode NFC, dash/quote normalization, whitespace collapsing).
        If a unique normalized match is found, it is used and
        match_type='normalized' is returned.

        Always call 'read' first to get the current text and line numbers.
        The search index is updated immediately; do not call 'reindex'.

        Args:
            path: Relative path to the document.
            old_text: Text to replace. Must appear exactly once in the
                document or line range. Get this via 'read'. Optional
                when using line-range mode.
            new_text: Replacement text. May be longer or shorter.
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the edit only proceeds if the file has not
                been modified since that read (optimistic concurrency).
            line_start: First line to replace (1-based, inclusive).
                Must be provided together with line_end.
            line_end: Last line to replace (1-based, inclusive).
                Must be provided together with line_start.

        Returns:
            - **path** (str): path of the edited document.
            - **replacements** (int): always 1.
            - **match_type** (str): ``'exact'`` or ``'normalized'``.

        Raises:
            ValueError: If parameter combination is invalid, or line
                numbers are out of range.
            EditConflictError: If old_text is not found or appears more
                than once.
        """
        try:
            result = await asyncio.to_thread(
                collection.edit,
                path,
                old_text=old_text,
                new_text=new_text,
                if_match=if_match,
                line_start=line_start,
                line_end=line_end,
            )
            return asdict(result)
        except EditConflictError as exc:
            parts = [str(exc)]
            if exc.closest_match_line is not None:
                parts.append(f"closest_match_line: {exc.closest_match_line}")
            if exc.first_diff_char is not None:
                parts.append(f"first_diff_at_char: {exc.first_diff_char}")
            if exc.expected_snippet is not None:
                parts.append(f"expected: {exc.expected_snippet!r}")
            if exc.found_snippet is not None:
                parts.append(f"found: {exc.found_snippet!r}")
            raise ToolError("\n".join(parts)) from exc

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["delete"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
    )
    async def delete(
        path: str,
        if_match: str | None = None,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Permanently delete a document or attachment.

        For .md documents: removes the file and immediately updates all search
        indices — do not call 'reindex' afterward.
        For attachments: only the file is deleted (no index to update).
        IRREVERSIBLE unless git history exists. Confirm the path with
        the user before calling.

        Args:
            path: Relative path to the document or attachment to delete.
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the deletion only proceeds if the file has
                not been modified since that read (optimistic concurrency).
                Omit to delete unconditionally.

        Returns:
            Dict with path (str) of the deleted file.

        Raises:
            DocumentNotFoundError: If no file exists at the given path.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
        """
        result = await asyncio.to_thread(collection.delete, path, if_match=if_match)
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["rename"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def rename(
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        update_links: bool = False,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Rename or move a document or attachment. When renaming a .md note,
        always pass update_links=True to rewrite links in other documents
        that point to the old path — omitting this leaves those links broken.

        For .md documents: the file and its search index entries are updated
        immediately — do not call 'reindex' afterward.
        For attachments: only the file is moved (no index update needed).
        Parent directories for new_path are created automatically.

        Args:
            old_path: Current relative path (e.g. "drafts/idea.md"
                or "assets/old.png").
            new_path: Target relative path (e.g. "projects/idea.md"
                or "assets/new.png"). Fails if new_path already exists.
            if_match: Optional etag obtained from a previous 'read' call
                for old_path. When provided, the rename only proceeds if
                the file has not been modified since that read (optimistic
                concurrency). Omit to rename unconditionally.
            update_links: When True, all .md documents that link to old_path
                are also updated so their links point to new_path. Replacement
                is best-effort — failures are logged but do not prevent the
                rename. Default False; set True whenever renaming a .md note
                (omitting this leaves backlinks pointing to the old path).

        Returns:
            Dict with old_path (str), new_path (str), and updated_links (int)
            counting the number of source documents whose links were updated.

        Raises:
            DocumentNotFoundError: If old_path does not exist.
            DocumentExistsError: If new_path already exists.
            ValueError: If the path fails traversal validation.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
        """
        result = await asyncio.to_thread(
            collection.rename,
            old_path,
            new_path,
            if_match=if_match,
            update_links=update_links,
        )
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["fetch"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            # Treat like write — calling twice with the same inputs is safe
            # (overwrites with same content). Remote content may change between
            # calls, but repeated invocations do not cause harm.
            "idempotentHint": True,
        },
    )
    async def fetch(
        url: str,
        path: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
        timeout_s: float = 30.0,
        collection: Collection = Depends(get_collection),
    ) -> dict[str, Any]:
        """Download a file from a URL and save it to the vault.

        Fetches content from an HTTP/HTTPS URL and writes it as a note or
        attachment. Designed for MCP-to-MCP file transfer when content is
        too large to pass through the LLM context window.

        For .md paths: the response is decoded as UTF-8 text and saved as
        a markdown note with optional frontmatter. The search index is
        updated immediately.

        For other paths: the response is saved as a binary attachment.
        The existing attachment size limit applies.

        Args:
            url: Source URL to download from. Only http:// and https://
                schemes are allowed. Private/loopback IPs are blocked.
                Redirects are NOT followed (SSRF protection).
            path: Destination path in the vault (e.g. "notes/report.md"
                or "assets/diagram.png"). Extension determines handling:
                .md for notes, anything else for attachments.
            frontmatter: Optional YAML frontmatter dict for .md files,
                e.g. {"title": "Report", "source": "http://..."}. Ignored
                for attachments.
            if_match: Optional etag from a previous 'read' call for
                optimistic concurrency. Omit to write unconditionally.
            timeout_s: Download timeout in seconds (default 30). Increase
                for large files on slow connections.

        Returns:
            Dict with:
            - path (str): vault path of the written file
            - created (bool): true if new file, false if overwrite
            - content_length (int): bytes downloaded
            - content_type (str or null): Content-Type from the response

        Raises:
            ValueError: If the URL scheme is not http/https, the download
                exceeds the size limit, or the response cannot be decoded.
            ImportError: If httpx is not installed.
        """
        # Validate URL scheme (SSRF protection).
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_FETCH_SCHEMES:
            raise ValueError(
                f"Only http and https URLs are allowed, got {parsed.scheme!r}"
            )
        if _is_private_url(url):
            raise ValueError(
                "URLs targeting private, loopback, or link-local addresses "
                "are not allowed."
            )

        # Conditional import — httpx is an optional dependency.
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "The 'fetch' tool requires 'httpx'. Install it with:\n"
                "  pip install 'markdown-vault-mcp[all]'\n"
                "  # or: pip install httpx"
            ) from None

        # Determine size limit (attachments only). This pre-check enforces
        # the limit during streaming so we abort early without buffering the
        # entire payload. write_attachment() has a redundant check that
        # covers the non-fetch code path.
        is_markdown = path.endswith(".md")
        # pylint: disable=protected-access  # No public API for size limit;
        # MCP layer is a trusted consumer of Collection internals.
        max_bytes = (
            0
            if is_markdown or collection._max_attachment_size_mb <= 0
            else int(collection._max_attachment_size_mb * 1024 * 1024)
        )

        # Stream download — enforce size limit as chunks arrive.
        chunks: list[bytes] = []
        downloaded = 0
        async with (
            httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            content_type = response.headers.get("content-type")
            async for chunk in response.aiter_bytes(chunk_size=65536):
                downloaded += len(chunk)
                if max_bytes > 0 and downloaded > max_bytes:
                    raise ValueError(
                        f"Download exceeded the attachment size limit "
                        f"of {collection._max_attachment_size_mb} MB "
                        f"({max_bytes} bytes). Raise "
                        "MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB or "
                        "set it to 0 to disable the limit."
                    )
                chunks.append(chunk)

        raw_bytes = b"".join(chunks)
        content_length = downloaded

        # Redact userinfo and query string to avoid logging credentials
        # (pre-signed URLs, API tokens, embedded passwords).
        _parsed_log = urlparse(url)
        _safe_url = urlunparse(
            _parsed_log._replace(
                netloc=(
                    f"{_parsed_log.hostname}:{_parsed_log.port}"
                    if _parsed_log.port
                    else (_parsed_log.hostname or "")
                ),
                query="",
                fragment="",
            )
        )
        logger.info(
            "fetch: downloaded %d bytes from %s → %s",
            content_length,
            _safe_url,
            path,
        )

        # Dispatch to the appropriate write method.
        if is_markdown:
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                ct = content_type or "unknown"
                raise ValueError(
                    f"Response body is not valid UTF-8 (content-type: {ct}). "
                    "Only UTF-8 encoded responses can be saved as .md notes."
                ) from exc
            result = await asyncio.to_thread(
                collection.write,
                path,
                text,
                frontmatter=frontmatter,
                if_match=if_match,
            )
        else:
            result = await asyncio.to_thread(
                collection.write_attachment,
                path,
                raw_bytes,
                if_match=if_match,
            )

        return {
            **asdict(result),
            "content_length": content_length,
            "content_type": content_type,
        }

    # create_download_link is only available on HTTP transports —
    # stdio has no HTTP server to host the artifact endpoint.
    if transport != "stdio":
        _register_download_link_tool(mcp)


def _register_download_link_tool(mcp: FastMCP) -> None:
    """Register the ``create_download_link`` tool on *mcp*.

    Separated from :func:`register_tools` so it can be conditionally
    called only when an HTTP transport is active.

    Args:
        mcp: The :class:`~fastmcp.FastMCP` instance to register the tool on.
    """
    import json
    import mimetypes
    import os

    from markdown_vault_mcp.config import _ENV_PREFIX

    @mcp.tool(
        icons=_TOOL_ICONS["create_download_link"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def create_download_link(
        path: str,
        ttl_seconds: int = 300,
        collection: Collection = Depends(get_collection),
    ) -> str:
        """Create a one-time download URL for a vault file.

        Creates a temporary HTTP endpoint that serves the file once,
        then invalidates the link. Use this to pass files to other
        MCP servers (e.g., save an image to another vault, attach to
        email) without routing binary data through the LLM context
        window.

        Works for both notes (.md) and attachments (any allowed
        extension).

        Requires ``MARKDOWN_VAULT_MCP_BASE_URL`` to be configured.
        Only available on HTTP transport (not stdio).

        Args:
            path: Vault-relative path to the file (e.g.
                ``"notes/report.md"`` or ``"assets/diagram.png"``).
            ttl_seconds: Link lifetime in seconds (default 300 / 5
                minutes).

        Returns:
            JSON-encoded string with the following fields:

            - download_url (str): One-time HTTP URL to download the file.
            - expires_in_seconds (int): Link lifetime (equals ttl_seconds).
            - path (str): Vault-relative path of the served file.
            - content_type (str): MIME type of the file.

        Raises:
            ValueError: If ``MARKDOWN_VAULT_MCP_BASE_URL`` is not
                configured, the path does not exist, or the path
                fails validation.
        """
        if ttl_seconds <= 0:
            msg = "ttl_seconds must be a positive integer"
            raise ValueError(msg)

        # Validate BASE_URL is configured
        base_url = os.environ.get(f"{_ENV_PREFIX}_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            msg = (
                "MARKDOWN_VAULT_MCP_BASE_URL is required for download links. "
                "Set it to the public base URL of this server "
                "(e.g. https://mcp.example.com)."
            )
            raise ValueError(msg)

        # Validate the path exists in the vault (also checks traversal).
        if path.endswith(".md"):
            abs_path = await asyncio.to_thread(collection._validate_path, path)
            content_type = "text/markdown; charset=utf-8"
        else:
            abs_path = await asyncio.to_thread(
                collection._validate_attachment_path, path
            )
            mime, _ = mimetypes.guess_type(path)
            content_type = mime or "application/octet-stream"

        if not abs_path.is_file():
            raise ValueError(f"File not found: {path}")

        from markdown_vault_mcp.artifacts import get_artifact_store

        store = get_artifact_store()
        token = store.create_token(path, ttl_seconds=ttl_seconds)

        download_url = f"{base_url}/artifacts/{token}"
        result = {
            "download_url": download_url,
            "expires_in_seconds": ttl_seconds,
            "path": path,
            "content_type": content_type,
        }
        logger.info(
            "Created download link for path=%r ttl=%ds",
            path,
            ttl_seconds,
        )
        return json.dumps(result, indent=2)
