"""Bounded metadata-only path discovery over the keyword query boundary."""

from __future__ import annotations

import re
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING

from markdown_vault_mcp.query_types import (
    NoteQuery,
    NoteQueryItem,
    PathCoverage,
    PathQuery,
    PathReason,
    PathSearchResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.interfaces import KeywordIndex

_CANDIDATES = 10_000
_SECONDS = 0.2
_PAGE = 1000
_SPLIT = re.compile(r"[/\s_.-]+")
_TOKEN = re.compile(r"[a-zA-Z0-9]{3,64}\Z")


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.lower() for token in _SPLIT.split(value) if _TOKEN.fullmatch(token)
    )


def _within_one(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    i = j = edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        j += 1
        if len(left) == len(right):
            i += 1
    return edits + (len(right) - j) <= 1


def _matches(tokens: tuple[str, ...], path: str) -> bool:
    candidates = _tokens(path)
    return all(
        any(_within_one(token, other) for other in candidates) for token in tokens
    )


def _direct(
    index: KeywordIndex, spec: NoteQuery
) -> tuple[dict[str, tuple[int, NoteQueryItem]], bool]:
    matches: dict[str, tuple[int, NoteQueryItem]] = {}
    truncated = False
    for tier, lookup in enumerate(
        [
            replace(spec, path=spec.pattern, pattern=None),
            replace(spec, pattern_mode="prefix"),
            replace(spec, pattern_mode="substring"),
        ]
    ):
        page = index.query_documents(lookup)
        truncated |= page.truncated
        for item in page.items:
            matches.setdefault(item["path"], (tier, item))
    return matches, truncated


def _approximate(
    index: KeywordIndex,
    spec: NoteQuery,
    tokens: tuple[str, ...],
    matches: dict[str, tuple[int, NoteQueryItem]],
    clock: Callable[[], float],
) -> tuple[bool, list[PathReason]]:
    deadline = clock() + _SECONDS
    offset = 0
    while offset < _CANDIDATES:
        if clock() >= deadline:
            return False, ["time_limit"]
        page = index.query_documents(replace(spec, offset=offset))
        if clock() >= deadline:
            return False, ["time_limit"]
        for item in page.items:
            if clock() >= deadline:
                return False, ["time_limit"]
            if _matches(tokens, item["path"]):
                matches.setdefault(item["path"], (3, item))
        if clock() >= deadline:
            return False, ["time_limit"]
        offset += len(page.items)
        if offset >= page.total:
            return True, []
    return False, ["candidate_limit"]


def search_paths(
    index: KeywordIndex,
    request: PathQuery,
    clock: Callable[[], float] = monotonic,
) -> PathSearchResult:
    """Discover indexed paths with direct tiers before bounded approximation.

    Args:
        index: Keyword backend providing typed metadata queries.
        request: Validated literal query, population filters and output limit.
        clock: Monotonic seconds source; candidate retrieval counts toward 200 ms.

    Returns:
        Metadata in tier/casefolded-path/raw-path order. Exact path equality is
        case-sensitive; prefix/substring matching casefolds literal text. Fuzzy
        tokens split on slash, whitespace, underscore, hyphen and dot; eligible
        ASCII alphanumeric pieces have length 3-64, and every eligible query token
        must match some path token within one edit. Non-ASCII queries skip fuzzy.
        Approximation evaluates at most 10,000 raw-path-ordered candidates. Coverage
        describes only that pass; truncated independently records omitted matches.
    """
    query = request.query.strip()
    spec = NoteQuery(
        scope="metadata",
        folder=request.folder,
        filters=request.filters,
        pattern=query,
        order="folded_path",
        limit=request.limit,
    )
    matches, truncated = _direct(index, spec)
    tokens = _tokens(query)
    coverage: PathCoverage = "not_run"
    reasons: list[PathReason] = ["fuzzy_disabled"]
    if request.fuzzy and query.isascii() and tokens:
        scan = replace(spec, pattern=None, order="path", limit=_PAGE)
        complete, reasons = _approximate(index, scan, tokens, matches, clock)
        coverage = "complete" if complete else "limited"
    elif request.fuzzy:
        reasons = ["ineligible_query"]
    ordered = sorted(
        matches.values(),
        key=lambda hit: (hit[0], hit[1]["path"].casefold(), hit[1]["path"]),
    )
    return PathSearchResult(
        [item for _, item in ordered[: request.limit]],
        coverage,
        reasons,
        truncated or len(ordered) > request.limit,
    )
