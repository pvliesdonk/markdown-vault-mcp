"""Bounded indexed path discovery contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from markdown_vault_mcp._path_search import _within_one, search_paths
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.query_types import PathQuery
from tests.fixtures.structured_queries import add_note

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def index() -> Iterator[FTSIndex]:
    """Provide a metadata index with a tag predicate."""
    value = FTSIndex(":memory:", indexed_frontmatter_fields=["tags"])
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("abc", "abc", True),
        ("abc", "ab", True),
        ("abc", "abcd", True),
        ("abc", "axc", True),
        ("abc", "axy", False),
        ("abc", "acb", False),
        ("abc", "abcde", False),
        ("", "", True),
        ("", "a", True),
        ("", "ab", False),
    ],
)
def test_edit_decision(left: str, right: str, expected: bool) -> None:
    """Levenshtein distance includes no single-edit transposition."""
    assert _within_one(left, right) is expected


def test_direct_tiers_and_literal_unicode_order(index: FTSIndex) -> None:
    """Exact precedes prefix then substring, each in folded/raw path order."""
    for name in [
        "z/Straße[*]%.md",
        "STRASSE[*]%.md",
        "Straße[*]%.md",
        "strasse[*]%.mdx",
    ]:
        add_note(index, name)
    result = search_paths(index, PathQuery(query=" Straße[*]%.md ", limit=3))
    assert [x["path"] for x in result.items] == [
        "Straße[*]%.md",
        "STRASSE[*]%.md",
        "strasse[*]%.mdx",
    ]
    assert result.truncated and result.coverage == "not_run"
    assert all("sections" not in x and x["score"] is None for x in result.items)


def test_folder_and_filters(index: FTSIndex) -> None:
    """Every tier honors the same exact folder and indexed filter population."""
    for name in [
        "docs/target.md",
        "docs/nested/targat.md",
        "docs-other/targat.md",
        "Docs/targat.md",
        "target.md",
    ]:
        add_note(index, name, frontmatter={"tags": ["keep"]})
    add_note(index, "docs/targit.md", frontmatter={"tags": ["drop"]})
    result = search_paths(
        index,
        PathQuery(query="target", folder="docs/", filters={"tags": "keep"}, fuzzy=True),
        clock=lambda: 0,
    )
    assert [x["path"] for x in result.items] == [
        "docs/target.md",
        "docs/nested/targat.md",
    ]
    assert result.coverage == "complete" and not result.truncated
    assert [
        x["path"]
        for x in search_paths(index, PathQuery(query="target", folder="")).items
    ] == ["target.md"]


@pytest.mark.parametrize(
    "query,eligible",
    [
        ("ab", False),
        ("abc", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("café", False),
        ("東京", False),
    ],
)
def test_approximate_eligibility(index: FTSIndex, query: str, eligible: bool) -> None:
    """Only eligible ASCII tokens activate approximation."""
    result = search_paths(index, PathQuery(query=query, fuzzy=True), clock=lambda: 0)
    assert result.coverage == ("complete" if eligible else "not_run")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 257},
        {"query": "x\0y"},
        {"query": "ok", "limit": 0},
        {"query": "ok", "limit": 1001},
        {"query": "ok", "limit": True},
        {"query": "ok", "filters": {"tags": 1}},
    ],
)
def test_invalid_request(kwargs: dict) -> None:
    """Invalid public requests are rejected before querying."""
    with pytest.raises(ValueError):
        PathQuery(**kwargs)


def test_query_codepoint_cap(index: FTSIndex) -> None:
    """The query cap counts Unicode codepoints, including surrounding space."""
    assert search_paths(index, PathQuery(query="é" * 256)).items == []
    with pytest.raises(ValueError):
        PathQuery(query=" " * 256 + "x")


def test_candidate_cap_cannot_hide_exact_or_prefix(index: FTSIndex) -> None:
    """Direct lookup sees the whole eligible index beyond the approximate cap."""
    for i in range(10001):
        add_note(index, f"a/{i:05}.md")
    for name in ["z/target.md", "z/target.md-extra", "z/targat.md"]:
        add_note(index, name)
    result = search_paths(
        index, PathQuery(query="z/target.md", fuzzy=True), clock=lambda: 0
    )
    assert [x["path"] for x in result.items] == ["z/target.md", "z/target.md-extra"]
    assert result.coverage == "limited" and result.reasons == ["candidate_limit"]
    assert not result.truncated
    for fuzzy in [False, True]:
        direct = search_paths(
            index, PathQuery(query="z/target.md", fuzzy=fuzzy, limit=1), clock=lambda: 0
        )
        assert [x["path"] for x in direct.items] == ["z/target.md"]
    ticks = iter([0.0, 0.2])
    expired = search_paths(
        index,
        PathQuery(query="z/target.md", fuzzy=True, limit=1),
        clock=lambda: next(ticks),
    )
    assert expired.items[0]["path"] == "z/target.md" and expired.reasons == [
        "time_limit"
    ]


def test_output_cap_is_independent(index: FTSIndex) -> None:
    """Output truncation never implies incomplete approximate evaluation."""
    for name in ["abc.md", "abd.md", "abe.md"]:
        add_note(index, name)
    result = search_paths(
        index, PathQuery(query="abc", fuzzy=True, limit=1), clock=lambda: 0
    )
    assert [x["path"] for x in result.items] == ["abc.md"]
    assert result.truncated and result.coverage == "complete" and not result.reasons


def test_time_budget_includes_retrieval(index: FTSIndex) -> None:
    """A slow candidate fetch produces limited coverage before evaluation."""
    add_note(index, "abd.md")
    times = iter([0.0, 0.0, 0.201])
    result = search_paths(
        index, PathQuery(query="abc", fuzzy=True), clock=lambda: next(times)
    )
    assert (
        result.coverage == "limited"
        and result.reasons == ["time_limit"]
        and not result.items
    )


def test_faults_propagate(index: FTSIndex) -> None:
    """Database/decoding errors never turn into an empty success."""
    backend = Mock(wraps=index)
    backend.query_documents.side_effect = ValueError("corrupt JSON")
    with pytest.raises(ValueError, match="corrupt JSON"):
        search_paths(backend, PathQuery(query="abc"))


def test_deadline_after_last_candidate_never_claims_complete(index: FTSIndex) -> None:
    """Finishing the last edit comparison still counts toward the time budget."""
    add_note(index, "abd.md")
    times = iter([0.0, 0.0, 0.0, 0.0, 0.2])
    result = search_paths(
        index, PathQuery(query="abc", fuzzy=True), clock=lambda: next(times)
    )
    assert result.coverage == "limited" and result.reasons == ["time_limit"]


def test_approximate_token_splitting_and_two_edit_rejection(index: FTSIndex) -> None:
    """All eligible query tokens match path pieces; transposition costs two."""
    for path in [
        "docs/alpha_beta-gamma.delta.md",
        "docs/alpha_btea-gamma.delta.md",
        "docs/alpha_only.md",
    ]:
        add_note(index, path)
    result = search_paths(
        index, PathQuery(query="alpHa beta-gamma.delta", fuzzy=True), clock=lambda: 0
    )
    assert [x["path"] for x in result.items] == ["docs/alpha_beta-gamma.delta.md"]


def test_disabled_approximation_never_reads_candidates(index: FTSIndex) -> None:
    """Direct-only calls never start a work clock or scan a candidate population."""
    clock = Mock(side_effect=AssertionError("unexpected clock"))
    for request in [PathQuery(query="abc"), PathQuery(query="café", fuzzy=True)]:
        assert search_paths(index, request, clock=clock).coverage == "not_run"
    clock.assert_not_called()


@pytest.mark.parametrize(
    "query,candidate,matched",
    [
        ("abc", "abd", True),
        ("abc", "ac", False),
        ("ab", "axb", False),
        ("a" * 63 + "b", "a" * 63 + "c", True),
        ("a" * 63 + "b", "a" * 32 + "x" + "a" * 31 + "b", False),
        ("a" * 32 + "x" + "a" * 31 + "b", "a" * 63 + "b", False),
    ],
)
def test_public_candidate_token_boundaries(
    index: FTSIndex, query: str, candidate: str, matched: bool
) -> None:
    """Ineligible candidate pieces cannot enter through a one-edit decision."""
    add_note(index, f"{candidate}.md")
    result = search_paths(index, PathQuery(query=query, fuzzy=True), clock=lambda: 0)
    assert bool(result.items) is matched


@pytest.mark.parametrize("population", [0, 1, 999, 1000, 1001, 9999, 10000, 10001])
@pytest.mark.parametrize("limit", [1, 100, 1000])
def test_candidate_pages_and_output_limits(population: int, limit: int) -> None:
    """A contract-faithful page seam isolates candidate completion from output."""
    from markdown_vault_mcp.query_types import NoteQuery, NoteQueryItem, NoteQueryPage

    seen: list[int] = []

    def page(spec: NoteQuery) -> NoteQueryPage:
        if spec.path is not None or spec.pattern is not None:
            return NoteQueryPage([], 0, 0, spec.limit, False)
        seen.append(spec.offset)
        end = min(spec.offset + spec.limit, population)
        items: list[NoteQueryItem] = [
            {
                "path": f"a/{i:05}/candidate.md",
                "title": "Plain",
                "folder": f"a/{i:05}",
                "frontmatter": {},
                "modified_at": 0.0,
                "score": None,
            }
            for i in range(spec.offset, end)
        ]
        return NoteQueryPage(
            items, population, spec.offset, spec.limit, end < population
        )

    index = Mock()
    index.query_documents.side_effect = page
    result = search_paths(
        index, PathQuery(query="candidatf", fuzzy=True, limit=limit), clock=lambda: 0
    )
    assert result.coverage == ("complete" if population <= 10000 else "limited")
    assert result.reasons == ([] if population <= 10000 else ["candidate_limit"])
    assert [x["path"] for x in result.items] == [
        f"a/{i:05}/candidate.md" for i in range(min(population, limit))
    ]
    assert result.truncated == (min(population, 10000) > limit)
    assert max(seen, default=0) < 10000


def test_time_budget_stops_between_candidate_evaluations(index: FTSIndex) -> None:
    """Partial approximate results survive an interrupted page evaluation."""
    add_note(index, "abd.md")
    add_note(index, "abe.md")
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.2])
    result = search_paths(
        index, PathQuery(query="abc", fuzzy=True), clock=lambda: next(ticks)
    )
    assert result.coverage == "limited" and result.reasons == ["time_limit"]
    assert [x["path"] for x in result.items] == ["abd.md"]


@pytest.mark.parametrize(
    "query,path,matched",
    [
        ("alphb betb", "alpha_beta.md", True),
        ("alpha alpha", "alpha.md", True),
        ("x" * 65 + " alpha", "alpha.md", True),
        ("ab alpha", "alpha.md", True),
        ("alphb zzzzz", "alpha_beta.md", False),
        ("café project", "project.md", False),
        ("projact", "café/project.md", True),
        ("\uff50\uff52\uff4f\uff4a\uff45\uff43\uff54", "project.md", False),
        ("🚀", "docs/🚀.md", True),
    ],
)
def test_mixed_token_and_unicode_membership(
    index: FTSIndex, query: str, path: str, matched: bool
) -> None:
    """Eligible pieces compose without requiring unique witnesses or transliteration."""
    add_note(index, path)
    result = search_paths(index, PathQuery(query=query, fuzzy=True), clock=lambda: 0)
    assert [item["path"] for item in result.items] == ([path] if matched else [])
