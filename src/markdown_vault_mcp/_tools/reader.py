from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp_pvl_core import tool_boundary

from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.exceptions import DocumentNotFoundError, InvalidRequestError
from markdown_vault_mcp.utils import is_note
from markdown_vault_mcp.utils.serialization import toc_payload
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_queryable import needs_queryable
from ..domain import get_vault
from ._common import (
    _maybe_wait_for_drain,
    _staleness_result,
    _WaitForPendingWrites,
    attach_conventions,
    attach_okf,
    attach_okf_to_results,
)


def register(mcp: FastMCP) -> None:
    """Register read/query tools on *mcp*."""

    @mcp.tool(
        icons=_TOOL_ICONS["search"],
        annotations={
            "title": "Search Vault",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def search(
        query: str,
        limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] | None = None,
        folder: str | None = None,
        filters: dict[str, str] | None = None,
        chunks_per_file: int | None = None,
        snippet_words: int | None = None,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Search notes by keyword and meaning; returns notes ranked by score, each with
        its best-matching sections as snippets.

        Use list_documents for a complete listing and read for a path you already know.

        Args:
            query: Words to match or a natural-language question.
            limit: Maximum notes to return (default 10).
            mode: Omit for the best mode this vault serves. `"keyword"` suits exact terms,
                operators and filenames; `"semantic"` matches meaning only; `"hybrid"`
                combines both. `"semantic"` and `"hybrid"` need semantic_search_available
                from stats.
            folder: Only notes under this folder, a value from list_folders; `""` for
                top-level notes only.
            filters: Frontmatter values to match, all of them, such as `{"tags": "pacing"}`;
                keys come from indexed_frontmatter_fields in stats, and a list field
                matches when it holds the value. On an OKF bundle, status (draft, stable
                or deprecated; stable includes notes with none), stale (`"true"` or
                `"false"`) and trust_tier (unverified, machine-confirmed or
                human-reviewed) filter too.
            chunks_per_file: Maximum sections per note, at least 1; omit for the server
                default.
            snippet_words: Snippet width in words; omit for the server default, 0 for
                whole sections. read with section set to a result's heading returns that
                whole section.
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
            List of result dicts ranked by file relevance. Each contains:

            - path (str): Relative path of the document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - score (float): File-level score = max(section.score).
              Higher is a better match. BM25 (keyword) or cosine (semantic/
              hybrid); not comparable across modes.
            - search_type (str): `"keyword"`, `"semantic"`, or `"hybrid"`.
            - frontmatter (dict): Parsed YAML frontmatter of the document.
            - okf (dict, optional): OKF read annotation: present only when
              the vault is an active OKF bundle. Carries `type` (when
              declared), `status` (defaults to `"stable"`), `stale`
              (bool, `stale_after` reached), `trust_tier` (`"unverified"` /
              `"machine-confirmed"` / `"human-reviewed"`), and
              `sources_count` (when the note cites sources).
            - sections (list[dict]): Up to `chunks_per_file` best-matching
              sections, each with:

              - heading (str | None): Section heading or null for intro.
              - content (str): Matched snippet (or full chunk if
                snippet_words=0).  Call read(path, section=heading) for
                the full section text.
              - score (float): Chunk-level score for this section.

            On an active OKF bundle, ranking demotes deprecated notes, stale notes
            less, and the reserved index.md and log.md below real notes.

            An empty query returns an empty list in semantic and hybrid mode.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
            otherwise.

        Raises:
            EmbeddingsNotConfiguredError: If mode is `"semantic"` or `"hybrid"` and
                no embedding provider is configured (a `ValueError` subclass).
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
        hits = await attach_okf_to_results(vault, [asdict(r) for r in results])
        return _staleness_result(
            vault,
            hits,
            drained_on_request=drained,
            gen_before=gen_before,
        )

    @mcp.tool(
        icons=_TOOL_ICONS["read"],
        annotations={
            "title": "Read Note",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def read(
        path: str,
        section: str | None = None,
        revision: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return a note's text, frontmatter and etag, or an attachment's bytes as
        base64.

        Look paths up with search or list_documents first rather than guessing them.

        Args:
            path: Path in the vault, such as `"Journal/note.md"` or `"assets/diagram.pdf"`;
                case-sensitive.
            section: A heading, such as the heading field of a search result; returns that
                section and its sub-sections instead of the whole note. Notes only.
            revision: A commit SHA from get_history or from a write result's
                previous_revision; returns the note as it stood then, without an etag.
                Pass path as the note is named today. To restore that text, write it
                with the etag of a read without revision.

        Returns:
            For .md: dict with path, title, folder, content (the full raw
            file including frontmatter, or the section text when section= is
            given), frontmatter (dict:
            empty {} when section= is provided; call read(path) without
            section= to get the full document's frontmatter),
            modified_at (Unix timestamp), etag (SHA-256 hex str or null).
            On an active OKF bundle, whole-document reads also carry
            an 'okf' dict (type, status, stale, trust_tier, and the note's
            'sources' list when present); section reads omit it because they
            carry no frontmatter to derive it from.
            For attachments: dict with path, mime_type (str or null),
            size_bytes (int), content_base64 (str), modified_at (Unix timestamp),
            etag (SHA-256 hex str or null).
            The 'etag' value can be passed as 'if_match' to write, edit,
            delete, or rename to guard against concurrent modifications.
            With revision=: dict with path, historical_path (the name the note
            carried at that revision), revision, and content: the whole raw
            file, or just one section's body when section= is also given. No
            etag and no modified_at (both describe the note as it is now), and
            no 'okf' block for the same reason.

        Raises:
            ValueError: If no file exists at the given path, the extension is
                not in the attachment allowlist, the file exceeds
                `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB`, or the requested
                section heading is not found. A whole note over the size limit
                this server returns in one read
                (`MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES`) is refused; read it by
                `section`. With `revision`: if the vault
                is not git-backed, the revision is unusable, the path is an
                attachment, or git's records do not show the note existing at
                that revision (a name later reused by a different note is
                refused rather than answered with the other note's content).
            DocumentUnreadableError: If the file cannot be read or decoded, its
                frontmatter does not parse, or git stores it in LFS at that
                revision.
        """
        if revision is not None:
            return asdict(
                await asyncio.to_thread(
                    vault.reader.read_revision, path, revision, section=section
                )
            )
        if not is_note(path):
            cap_mb = vault.max_attachment_size_mb
            if cap_mb > 0:
                size = await asyncio.to_thread(vault.reader.attachment_size, path)
                limit = int(cap_mb * 1024 * 1024)
                if size > limit:
                    raise InvalidRequestError(
                        f"Attachment {path!r} is {size:,} bytes, over the "
                        f"{limit:,}-byte limit this server returns in a read. Fetch "
                        "it with create_download_link if that tool is available."
                    )
            attachment = await asyncio.to_thread(vault.reader.read_attachment, path)
            return asdict(attachment)
        note = await asyncio.to_thread(vault.reader.read, path, section=section)
        if note is None:
            raise DocumentNotFoundError.note(path)
        data = asdict(note)
        if section is None:
            # Section reads carry no frontmatter (see the docstring caveat),
            # so an annotation would be derived from defaults and mislead.
            data = await attach_okf(vault, data, note.frontmatter, include_sources=True)
        return data

    @mcp.tool(
        icons=_TOOL_ICONS["list_documents"],
        annotations={
            "title": "List Documents",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def list_documents(
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
        filters: dict[str, str] | None = None,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """List notes, and optionally attachments, with path, title and frontmatter but
        no body text.

        Use search to find notes by what they say.

        Args:
            folder: Only notes in this folder and its sub-folders, such as `"Journal"`;
                `""` for top-level notes only; omit for the whole vault.
            pattern: Glob matched against paths, such as `"Journal/*.md"` or
                `"**/*meeting*.md"`.
            include_attachments: Also list non-note files, marked kind=`"attachment"` with
                their mime_type. Default false.
            filters: Frontmatter values to match, all of them, such as `{"tags": "craft"}`;
                any key works, a list field matches when it holds the value, and
                attachments never match. On an OKF bundle, status (draft, stable or
                deprecated; stable includes notes with none), stale (`"true"` or `"false"`)
                and trust_tier (unverified, machine-confirmed or human-reviewed) filter
                too.
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
            List of info dicts. Every entry has a 'kind' field.
            Notes: path, title, folder, frontmatter, modified_at, kind=`"note"`,
            and content_chars (int): body length in characters, frontmatter
            excluded; 0 for a note indexed before the field existed.
            Attachments (when include_attachments=True): path, folder,
            mime_type, size_bytes, modified_at, kind=`"attachment"`.
            Body content is not included in either case.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
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
            filters=filters,
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def list_folders(
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[str]:
        """List every folder that holds notes; `""` stands for the top level.

        Pass one as the folder argument of search, list_documents and the other
        folder-scoped tools.

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
            Sorted list of folder paths, such as `["", "Journal", "Projects"]`.
            Pass any of these as the 'folder' argument to 'search' or
            'list_documents'.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def list_tags(
        field: str = "tags",
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[str]:
        """List the distinct values one indexed frontmatter field takes across the
        vault.

        Use the values in the filters argument of search.

        Args:
            field: A field from indexed_frontmatter_fields in stats (default `"tags"`).
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
            Sorted list of distinct string values, such as
            `["craft", "pacing", "worldbuilding"]`. Use these as values in the
            'filters' dict when calling 'search'.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
            otherwise.

        Raises:
            InvalidRequestError: If field is not an indexed frontmatter field; the
                message names the fields that are indexed.
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def stats(
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Report the vault's size, link health and capabilities: the search modes it
        serves and the frontmatter fields search can filter on.

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
            Dict with the following fields:

            - document_count (int): Total number of indexed documents.
            - chunk_count (int): Total number of indexed text chunks.
            - folder_count (int): Total number of folders containing documents.
            - semantic_search_available (bool): True if mode=`"semantic"` or
              mode=`"hybrid"` can be used in 'search'.
            - indexed_frontmatter_fields (list[str]): Field names usable as
              'filters' in 'search' and as 'field' in 'list_tags'.
            - attachment_extensions (list[str]): Allowed non-.md extensions, or
              `["*"]` when any extension is allowed.
            - link_count (int): Total number of indexed links. 0 may mean no
              links exist or link tracking not yet built (call 'reindex').
            - broken_link_count (int): Links pointing to missing documents.
              Call 'get_broken_links' if non-zero.
            - orphan_count (int): Notes with no inbound or outbound links.
              Call 'get_orphan_notes' if non-zero.
            - okf (dict, optional): Present only when the vault is an active
              OKF (Open Knowledge Format) bundle. Carries mode,
              declared_version, a per-`type` histogram plus untyped_count,
              status and trust-tier breakdowns, and stale_count. Those cover
              the note population 'okf_validate' audits; the reserved
              `index.md` / `log.md` files are counted apart as
              reserved_count.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
            otherwise.
        """
        drained = await _maybe_wait_for_drain(vault, wait_for_pending_writes, "stats")
        gen_before = vault.index.write_generation()
        result = await asyncio.to_thread(vault.reader.stats)
        payload = asdict(result)
        okf_section = await asyncio.to_thread(vault.reader.okf_stats)
        if okf_section is not None:
            payload["okf"] = okf_section
        return _staleness_result(
            vault, payload, drained_on_request=drained, gen_before=gen_before
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_similar"],
        annotations={
            "title": "Similar Notes",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def get_similar(
        path: str,
        limit: int = 10,
        chunks_per_file: int | None = None,
        folder: str | None = None,
        filters: dict[str, str] | None = None,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """Find the notes closest in meaning to a given note; returns them ranked by
        similarity with their closest sections, the note itself excluded.

        Needs semantic_search_available from stats. A note whose embeddings are not
        built yet has no similar notes.

        Args:
            path: Path of the note to compare against, such as `"notes/topic.md"`;
                case-sensitive.
            limit: Maximum notes to return (default 10).
            chunks_per_file: Maximum sections per note, at least 1; omit for the server
                default.
            folder: Only notes in this folder or below, such as `"3-Resources"`; `""` for
                top-level notes only.
            filters: Frontmatter values to match, all of them, such as
                `{"type": "resource"}`; any key works and a list field matches when it holds the
                value. On an OKF bundle, status (stable includes notes with none), stale
                (`"true"` or `"false"`) and trust_tier filter too.
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
            List of result dicts ranked by file similarity. Each contains:

            - path (str): Relative path of the similar document.
            - title (str): Document title.
            - folder (str): Parent folder path.
            - score (float): File-level cosine similarity (max of section
              scores), 0.0-1.0; higher = more similar.
            - search_type (str): Always `"semantic"`.
            - frontmatter (dict): Parsed YAML frontmatter.
            - sections (list[dict]): Up to chunks_per_file best-matching
              sections, each with:

              - heading (str | None): Section heading or null for intro.
              - content (str): Matched chunk text.
              - score (float): Chunk-level score for this section.

            Index freshness is reported out-of-band in the response's
            `_meta.index_stale` field: True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (`wait_for_pending_writes` timing out, a write completing inside the
            read window, or non-idle at response time), False otherwise.

        Raises:
            DocumentNotFoundError: If no document exists at the given path.
            EmbeddingsNotConfiguredError: If the vault has no embeddings.
            InvalidRequestError: If chunks_per_file is below 1.
            ToolError: If the index is busy or still building; retry shortly.
            IndexUnavailableError: If the index build failed or the index is broken;
                get_index_status reports the error.
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
            folder=folder,
            filters=filters,
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def get_toc(
        path: str,
        max_level: int | None = None,
        max_notes: int = 200,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Return the heading outline of a note, or of every note under a folder.

        For a note: a list of {heading, level}, the title first as level 1. For a
        folder: {path, notes, truncated}, each note with its path, title and headings.

        Args:
            path: A note path ending in .md, such as `"a/b.md"`, or a folder, such as `"a/b"`.
            max_level: Deepest heading level to keep, such as 2 for H1 and H2; omit for
                all. The title always stays.
            max_notes: Folders only: notes to include, first by path (default 200);
                truncated is true when more exist.
            wait_for_pending_writes: When True, wait until recent
                document mutations are applied to the index
                before answering. Default False answers from the current
                index; inspect '_meta.index_stale' to tell whether a write was
                still in flight. Bounded by a server timeout (default 60s).

        Returns:
            Note mode: list of {heading (str), level (int)}.
            Folder mode: {path (str), notes (list[{path, title, headings,
            content_chars}]), truncated (bool)}. Empty/nonexistent folder → empty
            'notes'. Each note's content_chars (int) is its body length in
            characters, frontmatter excluded; 0 for a note indexed before the
            field existed.

            Index freshness is reported out-of-band in '_meta.index_stale'.

        Raises:
            ValueError: Note path with no document; invalid folder path.
            ToolError: If the index is busy or still building; retry shortly.
            IndexUnavailableError: If the index build failed or the index is broken;
                get_index_status reports the error.
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def get_recent(
        limit: int = 20,
        folder: str | None = None,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> list[dict[str, Any]]:
        """List the most recently modified notes, newest first.

        Use it to pick up recent activity without a search query.

        Args:
            limit: Maximum notes to return (default 20).
            folder: Only notes in this folder and its sub-folders, such as `"Journal"`;
                `""` for top-level notes only; omit for the whole vault.
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
            One dict per note, each with: path, title, folder,
            frontmatter, modified_at (Unix timestamp), kind (`"note"`), and
            content_chars (int): body length in characters, frontmatter excluded;
            0 for a note indexed before the field existed.

            Index freshness rides in the response's `_meta.index_stale`
            field: True when the IndexWriter was non-idle, a write completed
            inside the read window, or `wait_for_pending_writes` timed out; False
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
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def get_context(
        path: str,
        similar_limit: int = 5,
        link_limit: int = 10,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return a note's metadata, backlinks, outlinks, similar notes, folder
        neighbours, indexed tags and applicable conventions in one call.

        Use it instead of get_backlinks, get_outlinks and get_similar when you need more
        than one of them.

        Args:
            path: Path of the note, such as `"notes/topic.md"`; case-sensitive.
            similar_limit: Maximum similar notes (default 5); 0 skips them, which suits
                a vault where stats shows semantic_search_available false.
            link_limit: Maximum backlinks, and separately outlinks (default 10).
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
              - link_type (str): One of `"markdown"`, `"wikilink"`, or `"reference"`.
              - fragment (str | None): Heading anchor (such as `"#section"`), or null.
              - raw_target (str): Literal link target as written in the source.

            - outlinks (list): Links from this note. List of dicts, each with:

              - target_path (str): Path of the linked document.
              - link_text (str): The clickable text of the link.
              - link_type (str): One of `"markdown"`, `"wikilink"`, or `"reference"`.
              - fragment (str | None): Heading anchor (such as `"#section"`), or null.
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
              - search_type (str): Always `"semantic"`.
              - frontmatter (dict): Parsed YAML frontmatter.
              - sections (list): Single best-matching section, each with
                heading (str|null), content (str), score (float).
                Call get_similar(path, chunks_per_file=N) for more sections.

            - folder_notes (list[str]): Paths of other notes in the same
              folder (up to 20). Plain strings, not dicts.
            - tags (dict[str, list[str]]): Indexed frontmatter field →
              distinct values for this note.
            - conventions (list, optional): the user's authoring conventions
              for the note's folder (root-first list of {folder, path,
              content}). Present only when convention files apply. Honor
              them when writing to or proposing links involving this note;
              some folders are self-contained by design.
            - okf (dict, optional): OKF read annotation for this note
              (type, status, stale, trust_tier, sources_count). Present
              only when the vault is an active OKF bundle.

            Index freshness is reported out-of-band in the response's
            `_meta.index_stale` field: True when the IndexWriter had
            pending or in-flight work at any of three observation points
            (`wait_for_pending_writes` timing out, a write completing inside the
            read window, or non-idle at response time), False otherwise.

        Raises:
            ValueError: If no document exists at the given path.
            ToolError: If the index is busy or still building; retry shortly.
            IndexUnavailableError: If the index build failed or the index is broken;
                get_index_status reports the error.
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
        data = await attach_conventions(vault, asdict(result), path)
        data = await attach_okf(vault, data, result.frontmatter)
        return _staleness_result(
            vault, data, drained_on_request=drained, gen_before=gen_before
        )

    @mcp.tool(
        icons=_TOOL_ICONS["get_conventions"],
        annotations={
            "title": "Folder Conventions",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def get_conventions(
        path: str = "",
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return the vault owner's authoring conventions for a note or folder, vault
        root first and the most specific last.

        Args:
            path: A note or folder path, such as `"3-Resources/topic.md"` or `"3-Resources"`; a
                note takes its folder's conventions. `""` (the default) returns the root
                conventions and lists every folder that has its own.

        Returns:
            Dict with:

            - path (str): The path that was queried.
            - conventions (list): Applicable convention entries, root-first.
              Each has folder (str, `""` for vault root), path (str, the
              convention file's own path), and content (str, its markdown
              body).
            - convention_folders (list[str], discovery mode only): all
              folders carrying a convention file (`""` = vault root),
              included only when path is `""`; it requires a vault-wide
              folder walk, so a call with a path skips it.

            Convention files are left out of search and listings but can be read
            and edited like any note. This tool reads them from disk, so it
            answers while the index is still building.

        Raises:
            ValueError: If the path escapes the vault root.
        """

        def _lookup() -> dict[str, Any]:
            entries = [asdict(e) for e in vault.conventions.for_path(path)]
            data: dict[str, Any] = {"path": path, "conventions": entries}
            if not path:
                data["convention_folders"] = vault.conventions.list_folders()
            return data

        return await asyncio.to_thread(_lookup)

    @mcp.tool(
        description=(
            "Audit how far the vault conforms to the Open Knowledge Format, with "
            "example paths per rule; works on a vault not yet declared as a bundle."
        ),
        icons=_TOOL_ICONS["okf_validate"],
        tags={"okf"},
        annotations={
            "title": "Validate OKF Bundle",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def okf_validate(vault: Vault = Depends(get_vault)) -> dict[str, Any]:
        """Audit the vault's Open Knowledge Format conformance from disk.

        Returns:
            Report dict. The audit reads the vault from disk, so it works before
            the index is built, and it skips the vault's excluded paths. Every
            rule except root_index_missing is a finding with `count` (int) and
            up to 20 `examples` (list[str] of paths).

            - mode (str), declared_version (str | None), active (bool): the
              detection state.
            - total_notes (int), conformant_notes (int): the progress ratio;
              the reserved index.md and log.md are not counted as notes.

            Conformance rules, which the format requires:

            - missing_type: notes without a non-empty `type`; the reserved
              index.md and log.md are exempt.
            - unparseable_frontmatter: notes whose frontmatter does not parse.
            - misplaced_okf_version: `okf_version` declared anywhere but the
              root index.md.
            - index_frontmatter: index.md files carrying frontmatter, any on a
              folder index and anything but `okf_version` on the root,
              server-generated indexes included.

            Advisory rules, tolerated but worth fixing:

            - unknown_status: a `status` outside draft, stable and deprecated.
            - log_heading_shape: log.md files whose `##` headings are not
              YYYY-MM-DD dates.
            - root_index_missing (bool): true when the root index.md is absent.

            Informational rules, not deviations:

            - wikilink_files: notes containing wikilinks, which matter only when
              exporting.
            - missing_recommended: notes without the recommended `title` or
              `description`.
        """
        report = await asyncio.to_thread(vault.reader.okf_validate)
        return asdict(report)
