"""Fixture-derived exact query oracles, independent of SQL construction."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.query_types import NoteGrouping, NoteQuery
from tests.fixtures.structured_queries import add_note

# Bounds apply to materialized corpus work, not to character/value domains.
corpora = st.lists(st.tuples(st.text(), st.lists(st.text(), max_size=5)), max_size=8)


@settings(max_examples=60, deadline=None)
@given(corpora, st.integers(min_value=1, max_value=1000))
def test_pages_partition_fixture_population(
    corpus: list[tuple[str, list[str]]], page_size: int
) -> None:
    """Pages partition fixture population."""
    index = FTSIndex(":memory:")
    try:
        expected = []
        for i, (name, _) in enumerate(corpus):
            path = f"{i}/{name}.md"
            add_note(index, path)
            expected.append(path)
        spec = NoteQuery(scope="metadata", limit=page_size, order="path")
        actual = [
            item["path"]
            for offset in range(0, len(expected), page_size)
            for item in index.query_documents(replace(spec, offset=offset)).items
        ]
        assert actual == sorted(expected)
    finally:
        index.close()


@settings(max_examples=60, deadline=None)
@given(corpora, st.text(), st.sampled_from(["prefix", "substring"]))
def test_literal_path_count_matches_python(
    corpus: list[tuple[str, list[str]]], pattern: str, mode: str
) -> None:
    """Literal path count matches python."""
    index = FTSIndex(":memory:")
    try:
        paths = [f"{i}/{name}.md" for i, (name, _) in enumerate(corpus)]
        for path in paths:
            add_note(index, path)
        folded = pattern.casefold()
        expected = sum(
            path.casefold().startswith(folded)
            if mode == "prefix"
            else folded in path.casefold()
            for path in paths
        )
        assert (
            index.count_documents_matching(
                NoteQuery(pattern=pattern, pattern_mode=mode)
            ).total
            == expected
        )
    finally:
        index.close()


@settings(max_examples=60, deadline=None)
@given(corpora, st.sampled_from(["tags", "kind", "other"]))
def test_grouped_unique_values_match_per_note_sets(
    corpus: list[tuple[str, list[str]]], field: str
) -> None:
    """Grouped unique values match per note sets."""
    index = FTSIndex(":memory:", indexed_frontmatter_fields=[field])
    try:
        expected = Counter()
        for i, (_, values) in enumerate(corpus):
            add_note(index, f"{i}.md", frontmatter={field: values})
            expected.update(set(values) or {None})
        result = index.aggregate_documents(
            NoteQuery(), NoteGrouping(by="tag", field=field)
        )
        assert {b["key"]: b["count"] for b in result.buckets} == expected
    finally:
        index.close()


@settings(max_examples=60, deadline=None)
@given(st.text(), st.sampled_from(["tags", "kind", "other"]))
def test_indexed_filter_round_trip(value: str, field: str) -> None:
    """Indexed filter round trip."""
    index = FTSIndex(":memory:", indexed_frontmatter_fields=[field])
    try:
        add_note(index, "a.md", frontmatter={field: value})
        assert (
            index.count_documents_matching(NoteQuery(filters={field: value})).total == 1
        )
    finally:
        index.close()


@settings(max_examples=60, deadline=None)
@given(corpora, st.text(), st.integers(min_value=0))
def test_count_intersects_fixture_filters_independent_of_page(
    corpus: list[tuple[str, list[str]]], wanted: str, offset: int
) -> None:
    """Count intersects fixture filters independent of page."""
    index = FTSIndex(":memory:", indexed_frontmatter_fields=["kind"])
    try:
        expected = set()
        for i, (value, _) in enumerate(corpus):
            path = f"docs/{i}.md" if i % 2 else f"other/{i}.md"
            add_note(index, path, frontmatter={"kind": value})
            if i % 2 and value == wanted:
                expected.add(path)
        spec = NoteQuery(folder="docs", filters={"kind": wanted}, offset=offset)
        assert index.count_documents_matching(spec).total == len(expected)
    finally:
        index.close()


@settings(max_examples=40, deadline=None)
@given(st.integers(min_value=60, max_value=99), st.sampled_from(["+", "-"]))
def test_invalid_timezone_minutes_rejected(minutes: int, sign: str) -> None:
    """Invalid timezone minutes rejected."""
    import pytest

    with pytest.raises(ValueError, match="RFC3339"):
        NoteQuery(modified_before=f"2026-01-01T00:00:00{sign}01:{minutes:02}")


@settings(max_examples=80, deadline=None)
@given(
    st.one_of(
        st.text(),
        st.sampled_from(
            [
                "*",
                "**",
                "()",
                "NEAR(",
                "x:y",
                '"',
                "OR",
                "AND",
                "NOT",
                "a) OR title:b OR (c",
            ]
        ),
    ),
    st.sampled_from(["all", "metadata", "body"]),
)
def test_query_syntax_never_exposes_parser_errors(query: str, scope: str) -> None:
    """Query syntax never exposes parser errors."""
    import pytest

    if "\x00" in query:
        with pytest.raises(ValueError, match="NUL"):
            NoteQuery(query=query, scope=scope)
        return
    index = FTSIndex(":memory:")
    try:
        add_note(index, "a.md")
        result = index.count_documents_matching(NoteQuery(query=query, scope=scope))
        assert result.total in (0, 1)
    finally:
        index.close()
