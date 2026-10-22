"""Exhaustive indexed note queries with bounded wire projections."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated, Any, Literal, NotRequired, TypeVar, cast

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp_pvl_core import tool_boundary
from pydantic import Field
from typing_extensions import TypedDict

from markdown_vault_mcp._icons import _TOOL_ICONS
from markdown_vault_mcp._server_queryable import needs_queryable
from markdown_vault_mcp._tools._common import (
    _maybe_wait_for_drain,
    _staleness_result,
    _WaitForPendingWrites,
    attach_okf_to_results,
)
from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.domain import get_vault
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.query_types import (
    NoteAggregate,
    NoteCount,
    NoteGrouping,
    NoteQuery,
    NoteQueryPage,
    PathCoverage,
    PathQuery,
    PathReason,
    PathSearchResult,
)
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Callable

_PageSize = Annotated[int, Field(strict=True, ge=1, le=1000)]
_Offset = Annotated[int, Field(strict=True, ge=0)]
_Chunks = Annotated[int, Field(strict=True, ge=1)]
_Words = Annotated[int, Field(strict=True, ge=0)]
_Scope = Literal["all", "metadata", "body"]
_By = Literal["folder", "tag", "month"]


class _Section(TypedDict):
    heading: str | None
    content: str
    score: float


class _Item(TypedDict):
    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    score: float | None
    sections: NotRequired[list[_Section]]
    okf: NotRequired[dict[str, Any]]


class _Paths(TypedDict):
    items: list[_Item]
    coverage: PathCoverage
    reasons: list[PathReason]
    truncated: bool
    index_stale: bool


class _Count(TypedDict):
    total: int
    index_stale: bool


class _Page(_Count):
    items: list[_Item]
    offset: int
    limit: int
    truncated: bool


class _Bucket(TypedDict):
    key: str | None
    count: int


class _Aggregate(_Count):
    buckets: list[_Bucket]
    missing: int
    truncated: bool


_RequestT = TypeVar("_RequestT")


def _request(factory: Callable[[], _RequestT]) -> _RequestT:
    try:
        return factory()
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc


async def _run(
    vault: Vault,
    operation: Callable[
        [], NoteQueryPage | NoteCount | NoteAggregate | PathSearchResult
    ],
    wait: bool,
    tool_name: str,
) -> Any:
    drained = await _maybe_wait_for_drain(vault, wait, tool_name)
    generation = vault.index.write_generation()
    try:
        result = await asyncio.to_thread(operation)
    except InvalidRequestError as exc:
        raise InvalidRequestError(
            f"{tool_name}: {exc}. Call stats for indexed_frontmatter_fields; "
            "use search for computed OKF filters."
        ) from exc
    data = asdict(result)
    if "items" in data:
        await attach_okf_to_results(vault, data["items"])
    data["index_stale"] = False
    return _staleness_result(
        vault, data, drained_on_request=drained, gen_before=generation
    )


def register(mcp: FastMCP) -> None:
    """Register exact note queries and bounded path discovery.

    Args:
        mcp: Server receiving read-only query tools.
    """

    @mcp.tool(
        icons=_TOOL_ICONS["search"],
        tags={"group:reader"},
        annotations={
            "title": "Query Notes",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def query_documents(
        query: str = "",
        scope: _Scope = "all",
        path: str | None = None,
        folder: str | None = None,
        pattern: str | None = None,
        filters: dict[str, str] | None = None,
        modified_after: str | None = None,
        modified_before: str | None = None,
        offset: _Offset = 0,
        limit: _PageSize = 20,
        chunks_per_file: _Chunks = 2,
        snippet_words: _Words = 200,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> _Page:
        """Page through all matching indexed notes with an exact total.

        Queries with text rank by keyword score then path; blank queries rank by
        modification time descending then path. Pages are live views, so concurrent
        writes can move notes between pages. Use read to retrieve full note content.

        Args:
            query: FTS5 expression; blank selects all indexed notes. Malformed syntax
                matches its literal words.
            scope: Indexed columns to match: metadata (path, title, headings, summary),
                body, or all. Terms must match within one indexed section.
            path: Case-sensitive exact vault-relative path, including .md.
            folder: Restrict to this folder and descendants; empty selects root notes.
            pattern: Case-sensitive path glob; *, ? and bracket classes are wildcards.
            filters: AND equality over fields in indexed_frontmatter_fields from stats.
                List fields match a member; computed OKF fields use search instead.
            modified_after: Inclusive RFC3339 modification instant with timezone.
            modified_before: Exclusive RFC3339 modification instant with timezone.
            offset: Notes to skip in this live view, at least 0.
            limit: Maximum returned notes, 1-1000; total is independent of this cap.
            chunks_per_file: Maximum matching sections per note, at least 1.
            snippet_words: Excerpt width in words, at least 0; 0 returns whole sections.
            wait_for_pending_writes: Wait for recent index writes before answering.
                On timeout, answer from the current index with index_stale true.

        Returns:
            Exact total, items, offset, limit, truncation and index_stale. Metadata
            scope omits sections; body/all include bounded snippets. Active OKF
            bundles annotate each item. index_stale mirrors response _meta.
        """
        spec = _request(
            lambda: NoteQuery(
                query=query,
                scope=scope,
                path=path,
                folder=folder,
                pattern=pattern,
                filters=filters or {},
                modified_after=modified_after,
                modified_before=modified_before,
                offset=offset,
                limit=limit,
                chunks_per_file=chunks_per_file,
                snippet_words=snippet_words,
            )
        )
        return cast(
            "_Page",
            await _run(
                vault,
                lambda: vault.reader.query_documents(spec),
                wait_for_pending_writes,
                "query_documents",
            ),
        )

    @mcp.tool(
        icons=_TOOL_ICONS["search"],
        tags={"group:reader"},
        annotations={
            "title": "Count Notes",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def count_documents(
        query: str = "",
        scope: _Scope = "all",
        path: str | None = None,
        folder: str | None = None,
        pattern: str | None = None,
        filters: dict[str, str] | None = None,
        modified_after: str | None = None,
        modified_before: str | None = None,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> _Count:
        """Count every matching indexed note without fetching content.

        The exact total is independent of search result caps and counts each note once.

        Args:
            query: FTS5 expression; blank selects all indexed notes. Malformed syntax
                matches its literal words.
            scope: Indexed columns to match: metadata (path, title, headings, summary),
                body, or all. Terms must match within one indexed section.
            path: Case-sensitive exact vault-relative path, including .md.
            folder: Restrict to this folder and descendants; empty selects root notes.
            pattern: Case-sensitive path glob; *, ? and bracket classes are wildcards.
            filters: AND equality over fields in indexed_frontmatter_fields from stats.
                List fields match a member; computed OKF fields use search instead.
            modified_after: Inclusive RFC3339 modification instant with timezone.
            modified_before: Exclusive RFC3339 modification instant with timezone.
            wait_for_pending_writes: Wait for recent index writes before answering.
                On timeout, answer from the current index with index_stale true.

        Returns:
            Exact total and index_stale, also present in response _meta.
        """
        spec = _request(
            lambda: NoteQuery(
                query=query,
                scope=scope,
                path=path,
                folder=folder,
                pattern=pattern,
                filters=filters or {},
                modified_after=modified_after,
                modified_before=modified_before,
            )
        )
        return cast(
            "_Count",
            await _run(
                vault,
                lambda: vault.reader.count_documents_matching(spec),
                wait_for_pending_writes,
                "count_documents",
            ),
        )

    @mcp.tool(
        icons=_TOOL_ICONS["search"],
        tags={"group:reader"},
        annotations={
            "title": "Aggregate Notes",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def aggregate_documents(
        by: _By,
        query: str = "",
        scope: _Scope = "all",
        path: str | None = None,
        folder: str | None = None,
        pattern: str | None = None,
        filters: dict[str, str] | None = None,
        modified_after: str | None = None,
        modified_before: str | None = None,
        field: str = "tags",
        limit: _PageSize = 50,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> _Aggregate:
        """Group every matching indexed note into bounded frequency buckets.

        Counts cover the whole population. Multi-value tag buckets can sum above
        total; missing counts notes without a tag value. UTC months use YYYY-MM.
        Buckets rank by count descending then `key.casefold()` and raw key.

        Args:
            query: FTS5 expression; blank selects all indexed notes. Malformed syntax
                matches its literal words.
            scope: Indexed columns to match: metadata (path, title, headings, summary),
                body, or all. Terms must match within one indexed section.
            path: Case-sensitive exact vault-relative path, including .md.
            folder: Restrict to this folder and descendants; empty selects root notes.
            pattern: Case-sensitive path glob; *, ? and bracket classes are wildcards.
            filters: AND equality over fields in indexed_frontmatter_fields from stats.
                List fields match a member; computed OKF fields use search instead.
            modified_after: Inclusive RFC3339 modification instant with timezone.
            modified_before: Exclusive RFC3339 modification instant with timezone.
            by: Group by folder, configured tag field or UTC modification month.
            field: Indexed field for tag grouping; ignored for folder/month.
            limit: Maximum returned buckets, 1-1000; never caps the input population.
            wait_for_pending_writes: Wait for recent index writes before answering.
                On timeout, answer from the current index with index_stale true.

        Returns:
            Exact total, buckets, missing, truncation and index_stale, which also
            appears in response _meta. Null tag buckets represent missing values.
        """
        spec = _request(
            lambda: NoteQuery(
                query=query,
                scope=scope,
                path=path,
                folder=folder,
                pattern=pattern,
                filters=filters or {},
                modified_after=modified_after,
                modified_before=modified_before,
            )
        )
        grouping = _request(lambda: NoteGrouping(by=by, field=field, limit=limit))
        return cast(
            "_Aggregate",
            await _run(
                vault,
                lambda: vault.reader.aggregate_documents(spec, grouping),
                wait_for_pending_writes,
                "aggregate_documents",
            ),
        )

    @mcp.tool(
        icons=_TOOL_ICONS["search"],
        tags={"group:reader"},
        annotations={
            "title": "Discover Note Paths",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    @needs_queryable()
    async def search_paths(
        query: Annotated[str, Field(min_length=1, max_length=256)],
        folder: str | None = None,
        filters: dict[str, str] | None = None,
        fuzzy: bool = False,
        limit: _PageSize = 100,
        wait_for_pending_writes: _WaitForPendingWrites = False,
        vault: Vault = Depends(get_vault),
    ) -> _Paths:
        """Discover note paths with direct matches before optional typo matches.

        Returns metadata without bodies. Exact paths rank first, then case-insensitive
        literal prefix matches, followed by other literal matches and approximate matches. Coverage describes
        the approximate scan; truncated independently describes omitted results.
        Use read with a returned path to retrieve its content.

        Args:
            query: Literal path text, 1-256 Unicode code points; whitespace alone is empty.
                Surrounding whitespace is ignored; wildcard characters are literal.
            folder: Restrict to this folder and descendants; empty selects root notes.
            filters: AND equality over indexed_frontmatter_fields from stats;
                list fields match a member. Use search for computed OKF filters.
            fuzzy: Try ASCII alphanumeric tokens of length 3-64 within one edit;
                transposition costs two edits. Non-ASCII queries use direct matching.
                The approximate pass evaluates at most 10,000 candidates in 200 ms.
            limit: Maximum returned metadata items, 1-1000.
            wait_for_pending_writes: Wait for recent index writes before answering.
                On timeout, answer from the current index with index_stale true.

        Returns:
            Items, coverage (complete, limited or not_run), reasons, truncated and
            index_stale, also present in response metadata.
        """
        request = _request(
            lambda: PathQuery(
                query=query,
                folder=folder,
                filters=filters or {},
                fuzzy=fuzzy,
                limit=limit,
            )
        )
        return cast(
            "_Paths",
            await _run(
                vault,
                lambda: vault.reader.search_paths(request),
                wait_for_pending_writes,
                "search_paths",
            ),
        )
