from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp_pvl_core import tool_boundary

from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_queryable import needs_queryable
from ..domain import get_vault
from ._common import (
    _maybe_wait_for_drain,
    _staleness_result,
    _WaitForPendingWrites,
)


def register(mcp: FastMCP) -> None:
    """Register link-graph tools on *mcp*."""

    @mcp.tool(
        icons=_TOOL_ICONS["get_backlinks"],
        annotations={
            "title": "Backlinks",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def get_backlinks(
        path: str,
        limit: int | None = None,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """List the notes that link to a note.

        Use get_context when you also need its outlinks or similar notes.

        Args:
            path: Path of the note linked to, such as `"notes/topic.md"`; case-sensitive.
            limit: Maximum backlinks to return; omit for all.
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (such as right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index; almost
                always already up to date; inspect the response's
                `_meta.index_stale` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of backlink dicts, each with:

            - source_path (str): Path of the document containing the link.
            - source_title (str): Title of the source document.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of `"markdown"`, `"wikilink"`, or `"reference"`.
            - fragment (str | None): Heading anchor (such as `"#section"`), or null.
            - raw_target (str): Literal link target as written in the source.

            Index freshness is reported out-of-band in the response's
            `_meta.index_stale` field: True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (`wait_for_pending_writes` timing out, a write completing inside the
            read window, or non-idle at response time), False when the data
            is current as of response time.

        Raises:
            ValueError: If no document exists at the given path.
            ToolError: If the index is busy or still building; retry shortly.
            IndexUnavailableError: If the index build failed or the index is broken;
                get_index_status reports the error.
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def get_outlinks(
        path: str,
        limit: int | None = None,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """List the links in a note, each with an exists flag that is false when the
        target is missing.

        Use get_context when you also need its backlinks or similar notes.

        Args:
            path: Path of the note holding the links, such as `"notes/topic.md"`;
                case-sensitive.
            limit: Maximum outlinks to return; omit for all.
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (such as right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index; almost
                always already up to date; inspect the response's
                `_meta.index_stale` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of outlink dicts, each with:

            - target_path (str): Path of the linked document.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of `"markdown"`, `"wikilink"`, or `"reference"`.
            - fragment (str | None): Heading anchor (such as `"#section"`), or null.
            - raw_target (str): Literal link target as written in the source.
            - exists (bool): True if the target document is indexed.

            A reference to an attachment is not a link, so it is not listed.

            Index freshness is reported out-of-band in the response's
            `_meta.index_stale` field: True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (`wait_for_pending_writes` timing out, a write completing inside the
            read window, or non-idle at response time), False when the data
            is current as of response time.

        Raises:
            ValueError: If no document exists at the given path.
            ToolError: If the index is busy or still building; retry shortly.
            IndexUnavailableError: If the index build failed or the index is broken;
                get_index_status reports the error.
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def get_broken_links(
        folder: str | None = None,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """List the links whose target is missing from the vault, each with the note
        that holds it.

        stats reports broken_link_count; a rename without update_links leaves such links
        behind. Links to attachments are not checked.

        Args:
            folder: Only links in notes in this folder and its sub-folders, such as
                `"Journal"`; `""` for top-level notes only; omit for the whole vault.
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (such as right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index; almost
                always already up to date; inspect the response's
                `_meta.index_stale` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of dicts, each with:

            - source_path (str): Path of the document containing the broken link.
            - source_title (str): Title of the source document.
            - target_path (str): The missing target path.
            - link_text (str): The clickable text of the link.
            - link_type (str): One of `"markdown"`, `"wikilink"`, or `"reference"`.
            - fragment (str | None): Heading anchor (such as `"#section"`), or null.
            - raw_target (str): Literal link target as written in the source.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def get_orphan_notes(
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """List every note with no links in or out, with no limit on the count.

        stats reports orphan_count; check it first on a large vault.

        Args:
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (such as right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index; almost
                always already up to date; inspect the response's
                `_meta.index_stale` field to tell whether a write was still
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
            - kind (str): Always `"note"`.
            - content_chars (int): body length in characters, frontmatter
              excluded; 0 for a note indexed before the field existed.

            A reference to an attachment is not a link, so a note whose only
            links are to attachments is an orphan.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def get_most_linked(
        limit: int = 10,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """List the notes with the most backlinks, most first.

        Use get_backlinks for the notes that link to one of them.

        Args:
            limit: Maximum notes to return (default 10).
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (such as right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index; almost
                always already up to date; inspect the response's
                `_meta.index_stale` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            List of dicts with path (str), title (str), folder (str), and
            backlink_count (int: number of distinct source documents linking to
            this note), ordered by backlink_count descending.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def get_connection_path(
        source: str,
        target: str,
        max_depth: int = 10,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Find the shortest chain of links between two notes, following links in either
        direction.

        Args:
            source: Path of the starting note, such as `"Ideas/spark.md"`.
            target: Path of the destination note.
            max_depth: Most links to follow, 1 to 10 (default 10).
            wait_for_pending_writes: When True, wait until your recent
                document mutations have been applied to the
                index before answering, so the results reflect those changes.
                Use it right after modifying notes when this read must see
                them (such as right after a document mutation whose
                effect this read should reflect). Default
                False answers immediately from the current index; almost
                always already up to date; inspect the response's
                `_meta.index_stale` field to tell whether a write was still
                in flight. Bounded by a server timeout (default 60s); on
                timeout it answers from the current index rather than waiting
                longer.

        Returns:
            Dict with the connection-path result. Fields:

            - `found` (bool): Whether a path was found within `max_depth` hops.
            - `path` (list[str]): Note paths in order from source to target,
              or an empty list if not found.
            - `hops` (int): Number of edges in the path (`len(path) - 1`), or -1 if
              not found.

            Index freshness is reported out-of-band in the response's
            `_meta.index_stale` field: True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (`wait_for_pending_writes` timing out, a write completing inside the
            read window, or non-idle at response time), False otherwise.

        Raises:
            DocumentNotFoundError: If either note does not exist.
            ToolError: If the index is busy or still building; retry shortly.
            IndexUnavailableError: If the index build failed or the index is broken;
                get_index_status reports the error.
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
