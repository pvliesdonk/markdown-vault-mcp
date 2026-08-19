from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_queryable import needs_queryable
from ..domain import get_vault
from ._common import _maybe_wait_for_drain, _staleness_result


def register(mcp: FastMCP) -> None:
    """Register link-graph tools on *mcp*."""

    @mcp.tool(
        icons=_TOOL_ICONS["get_backlinks"],
        annotations={
            "title": "Backlinks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def get_backlinks(
        path: str,
        limit: int | None = None,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
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
            limit: Maximum number of backlinks to return. Omitted (the
                default) returns all.
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of backlink dicts, each with:

            - source_path (str): Path of the document containing the link.
            - source_title (str): Title of the source document.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of "markdown", "wikilink", or "reference".
            - fragment (str | None): Heading anchor (e.g. "#section"), or null.
            - raw_target (str): Literal link target as written in the source.

            Index freshness is reported out-of-band in the response's
            ``_meta.index_stale`` field — True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (``wait_for_pending_writes`` timing out, a write completing inside the
            read window, or non-idle at response time), False when the data
            is current as of response time.

        Combine with ``get_similar`` to find connection gaps — notes that are
        semantically close to the target but not yet linked. Respect folder
        conventions (see 'get_conventions') before proposing such links —
        some folders are self-contained by design.

        Raises:
            ValueError: If no document exists at the given path.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_backlinks"
        )
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(vault.graph.get_backlinks, path, limit=limit)
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_outlinks"],
        annotations={
            "title": "Outlinks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def get_outlinks(
        path: str,
        limit: int | None = None,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Find all links FROM the given document to other documents (outlinks).

        Use this to see what a document references. For a full picture of
        a note's place in the vault, use 'get_context' instead of calling
        this separately. Call 'get_outlinks' directly when you only need
        the outbound link list. Each result includes an 'exists' flag —
        False means the link is broken (the target is missing from the
        vault).

        Args:
            path: Relative path of the source document (e.g.
                "notes/topic.md"). Case-sensitive.
            limit: Maximum number of outlinks to return. Omitted (the
                default) returns all.
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of outlink dicts, each with:

            - target_path (str): Path of the linked document.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of "markdown", "wikilink", or "reference".
            - fragment (str | None): Heading anchor (e.g. "#section"), or null.
            - raw_target (str): Literal link target as written in the source.
            - exists (bool): True if the target document is indexed.

            Index freshness is reported out-of-band in the response's
            ``_meta.index_stale`` field — True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (``wait_for_pending_writes`` timing out, a write completing inside the
            read window, or non-idle at response time), False when the data
            is current as of response time.

        Combine with ``get_similar`` to find connection gaps — notes the
        source is semantically close to but hasn't linked yet. Respect folder
        conventions (see 'get_conventions') before proposing such links —
        some folders are self-contained by design.

        Raises:
            ValueError: If no document exists at the given path.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_outlinks"
        )
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(vault.graph.get_outlinks, path, limit=limit)
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_broken_links"],
        annotations={
            "title": "Broken Links",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_broken_links(
        folder: str | None = None,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Find all links that point to non-existent documents (broken links).

        Use this to audit link health across the vault. Call this when
        'stats' shows broken_link_count > 0, or after a 'rename' that did
        not use update_links=True, to see what links were left pointing to
        the old path. A broken link means the target path does not match any
        indexed document — the referenced note may have been deleted, renamed,
        or never created.

        Args:
            folder: Optional folder filter. When provided, only checks
                links from documents in this folder (e.g. "Journal").
                Use folder="" for root-level (top-level) documents only.
                Without this, checks all documents.
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of dicts, each with:

            - source_path (str): Path of the document containing the broken link.
            - source_title (str): Title of the source document.
            - target_path (str): The missing target path.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of "markdown", "wikilink", or "reference".
            - fragment (str | None): Heading anchor (e.g. "#section"), or null.
            - raw_target (str): Literal link target as written in the source.

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_broken_links"
        )
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(vault.graph.get_broken_links, folder=folder)
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_orphan_notes"],
        annotations={
            "title": "Orphan Notes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_orphan_notes(
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Return all notes with no inbound or outbound links.

        WARNING: returns ALL orphans with no limit — check 'stats' for
        orphan_count before calling on large vaults.

        An orphan note has no backlinks (no other note links to it) and no
        outlinks (it links to nothing). Call this when 'stats' shows
        orphan_count > 0. Useful for finding isolated notes that may need to
        be connected to the rest of the vault or removed.

        Args:
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of dicts ordered by path, each with:

            - path (str): Relative path of the orphan note.
            - title (str): Title of the note.
            - folder (str): Folder containing the note.
            - frontmatter (dict): Parsed YAML frontmatter.
            - modified_at (float): Unix timestamp of last modification.
            - kind (str): Always "note".

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_orphan_notes"
        )
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(vault.graph.get_orphan_notes)
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_most_linked"],
        annotations={
            "title": "Most-Linked Notes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_most_linked(
        limit: int = 10,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Return the documents with the most inbound links, ranked by backlink count.

        Useful for discovering hub notes — frequently-referenced notes that are
        likely key concepts in the vault. For the specific documents that link to
        a particular note, use 'get_backlinks' instead.

        Args:
            limit: Maximum number of results to return. Default 10.
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of dicts with path (str), title (str), and backlink_count (int
            — number of distinct source documents linking to this note), ordered
            by backlink_count descending.

            Index freshness rides in the response's ``_meta.index_stale``
            field — True when the IndexWriter was non-idle, a write completed
            inside the read window, or ``wait_for_pending_writes`` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_most_linked"
        )
        gen_before = vault.index.write_generation()
        results = await asyncio.to_thread(vault.graph.get_most_linked, limit=limit)
        return _staleness_result(
            vault,
            [asdict(r) for r in results],
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_connection_path"],
        annotations={
            "title": "Connection Path",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def get_connection_path(
        source: str,
        target: str,
        max_depth: int = 10,
        wait_for_pending_writes: bool = False,
        vault: Vault = Depends(get_vault),
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
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (e.g. right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index — almost
                always already up to date; inspect the response's
                ``_meta.index_stale`` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            Dict with the connection-path result. Fields:

            - `found` (bool): Whether a path was found within `max_depth` hops.
            - `path` (list[str]): Ordered list of note paths from source to target,
              or an empty list if not found.
            - `hops` (int): Number of edges in the path (`len(path) - 1`), or -1 if
              not found.

            Index freshness is reported out-of-band in the response's
            ``_meta.index_stale`` field — True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (``wait_for_pending_writes`` timing out, a write completing inside the
            read window, or non-idle at response time), False otherwise.
        """
        drained = await _maybe_wait_for_drain(
            vault, wait_for_pending_writes, "get_connection_path"
        )
        gen_before = vault.index.write_generation()
        result: list[str] | None = await asyncio.to_thread(
            vault.graph.get_connection_path, source, target, max_depth
        )

        if result is None:
            inner: dict[str, Any] = {"found": False, "path": [], "hops": -1}
        else:
            inner = {"found": True, "path": result, "hops": len(result) - 1}
        return _staleness_result(
            vault, inner, drained_on_request=drained, gen_before=gen_before
        )
