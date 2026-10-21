"""Exact storage-query regressions independent of legacy ranked search."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.query_types import NoteGrouping, NoteQuery
from tests.fixtures.structured_queries import add_note

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def index() -> Iterator[FTSIndex]:
    """Yield an isolated keyword index with controlled indexed fields."""
    value = FTSIndex(
        ":memory:",
        indexed_frontmatter_fields=["tags", "kind"],
        searchable_frontmatter_fields=["summary"],
    )
    try:
        yield value
    finally:
        value.close()


def test_scope_and_distinct_population(index: FTSIndex) -> None:
    """Scope and distinct population."""
    add_note(index, "body.md", bodies=["target one", "target two"])
    add_note(index, "title.md", title="target", bodies=["other"])
    add_note(index, "summary.md", frontmatter={"summary": "target"}, bodies=["other"])
    add_note(index, "split.md", bodies=["alpha", "beta"])
    for scope, expected in [
        ("body", {"body.md"}),
        ("metadata", {"title.md", "summary.md"}),
        ("all", {"body.md", "title.md", "summary.md"}),
    ]:
        spec = NoteQuery(query="target", scope=scope, limit=1)
        page = index.query_documents(spec)
        assert page.total == len(expected) == index.count_documents_matching(spec).total
        assert page.truncated == (len(expected) > 1)
        paths = {
            index.query_documents(replace(spec, offset=i)).items[0]["path"]
            for i in range(len(expected))
        }
        assert paths == expected
        assert ("sections" in page.items[0]) == (scope != "metadata")
    assert index.count_documents_matching(NoteQuery(query="alpha AND beta")).total == 0


@pytest.mark.parametrize(
    "query", ["title:target OR target", "absent) OR title:target OR (absent"]
)
def test_scope_cannot_be_widened(index: FTSIndex, query: str) -> None:
    """Scope cannot be widened."""
    add_note(index, "note.md", title="target", bodies=["absent"])
    assert (
        index.count_documents_matching(NoteQuery(query=query, scope="body")).total == 0
    )


def test_filters_folders_and_exact_path(index: FTSIndex) -> None:
    """Filters folders and exact path."""
    for path in [
        "docs/a.md",
        "docs/nested/b.md",
        "Docs/child/c.md",
        "docs-other/d.md",
        "root.md",
    ]:
        add_note(index, path, frontmatter={"kind": "x='ß", "tags": ["a", "a", "b"]})
    assert index.count_documents_matching(NoteQuery(folder="docs/")).total == 2
    assert index.count_documents_matching(NoteQuery(folder="")).total == 1
    spec = NoteQuery(path="docs/a.md", filters={"kind": "x='ß"})
    assert index.count_documents_matching(spec).total == 1
    assert index.count_documents_matching(replace(spec, path="DOCS/a.md")).total == 0
    for key in ["unknown", "status", "stale", "trust_tier"]:
        with pytest.raises(ValueError):
            index.count_documents_matching(NoteQuery(filters={key: "x"}))


def test_literal_path_modes_and_order(index: FTSIndex) -> None:
    """Literal path modes and order."""
    for path in ["Z.md", "a.md", "Straße[*]%.md", "STRASSE[*]%.md"]:
        add_note(index, path)
    assert [
        x["path"] for x in index.query_documents(NoteQuery(order="path")).items
    ] == ["STRASSE[*]%.md", "Straße[*]%.md", "Z.md", "a.md"]
    assert (
        index.query_documents(NoteQuery(order="folded_path", limit=1)).items[0]["path"]
        == "a.md"
    )
    for mode, pattern in [("prefix", "strasse[*]"), ("substring", "ASSE[*]%")]:
        page = index.query_documents(NoteQuery(pattern=pattern, pattern_mode=mode))
        assert page.total == 2


def test_group_population_and_missing(index: FTSIndex) -> None:
    """Group population and missing."""
    add_note(index, "a.md", frontmatter={"tags": ["one", "one", "two"]})
    add_note(index, "b.md", frontmatter={"tags": ["one"]})
    add_note(index, "c.md")
    result = index.aggregate_documents(
        NoteQuery(limit=1, offset=100), NoteGrouping(by="tag")
    )
    assert result.total == 3 and result.missing == 1
    assert [(b["key"], b["count"]) for b in result.buckets] == [
        ("one", 2),
        (None, 1),
        ("two", 1),
    ]
    limited = index.aggregate_documents(NoteQuery(), NoteGrouping(by="tag", limit=1))
    assert limited.total == 3 and limited.missing == 1 and limited.truncated


def test_time_and_fractional_month(index: FTSIndex) -> None:
    """Time and fractional month."""
    add_note(index, "a.md", modified_at=1769903999.99999)
    add_note(index, "b.md", modified_at=-0.00001)
    result = index.aggregate_documents(NoteQuery(), NoteGrouping(by="month"))
    assert {b["key"] for b in result.buckets} == {"2026-01", "1969-12"}
    spec = NoteQuery(
        modified_after="2026-01-31T23:59:59Z",
        modified_before="2026-02-01T01:00:00+01:00",
    )
    assert index.count_documents_matching(spec).total == 1
    for kwargs in [
        {"limit": 0},
        {"offset": -1},
        {"scope": "bad"},
        {"modified_after": "2026-01-01"},
        {
            "modified_after": "2026-02-01T00:00:00Z",
            "modified_before": "2026-01-01T00:00:00Z",
        },
    ]:
        with pytest.raises(ValueError):
            NoteQuery(**kwargs)


def test_sections_snapshot_and_huge_offset(index: FTSIndex) -> None:
    """Sections snapshot and huge offset."""
    add_note(index, "a.md", bodies=["target " * 500, "target " * 500])
    page = index.query_documents(NoteQuery(query="target", snippet_words=3))
    assert len(page.items[0]["sections"]) == 2
    assert all(len(s["content"].split()) <= 3 for s in page.items[0]["sections"])
    conn = index._conn()
    conn.execute("BEGIN")
    page = index.query_documents(NoteQuery(offset=2**100))
    assert page.total == 1 and page.items == [] and page.offset == 2**100
    assert conn.in_transaction
    conn.rollback()


def test_malformed_fallback_and_blank_population(index: FTSIndex) -> None:
    """Malformed fallback and blank population."""
    add_note(index, "slug.md", bodies=["vault-mcp literal"])
    add_note(index, "empty.md", bodies=[])
    for query in ["vault-mcp", '"literal']:
        assert (
            index.count_documents_matching(NoteQuery(query=query, scope="body")).total
            == 1
        )
    page = index.query_documents(NoteQuery(path="empty.md"))
    assert page.total == 1 and page.items[0]["sections"] == []
    assert index.query_documents(NoteQuery(path="missing.md")).items == []


def test_count_and_group_do_not_read_frontmatter_or_sections(
    index: FTSIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Count and group do not read frontmatter or sections."""
    from markdown_vault_mcp._fts_queries import QueryExecutor

    add_note(index, "a.md")
    statements = []
    index._conn().set_trace_callback(statements.append)

    def refuse_sections(*_args: object) -> None:
        pytest.fail("count/aggregate must not build excerpts")

    monkeypatch.setattr(QueryExecutor, "_sections", refuse_sections)
    assert index.count_documents_matching(NoteQuery()).total == 1
    assert index.aggregate_documents(NoteQuery(), NoteGrouping(by="folder")).total == 1
    assert not any("frontmatter_json" in sql or "snippet(" in sql for sql in statements)


def test_read_only_page_total_share_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read only page total share view."""
    from markdown_vault_mcp._fts_queries import QueryExecutor

    database = tmp_path / "index.db"
    writer = FTSIndex(database)
    add_note(writer, "a.md")
    reader = FTSIndex(database, read_only=True)
    original = QueryExecutor._total

    def total_then_write(
        executor: QueryExecutor, sql: str, params: list[object]
    ) -> int:
        total = original(executor, sql, params)
        add_note(writer, "b.md")
        return total

    monkeypatch.setattr(QueryExecutor, "_total", total_then_write)
    try:
        page = reader.query_documents(NoteQuery(scope="metadata"))
        assert page.total == len(page.items) == 1
        monkeypatch.setattr(QueryExecutor, "_total", original)
        assert reader.count_documents_matching(NoteQuery()).total == 2
        cursor = reader._conn().execute("SELECT path FROM documents_live")
        assert reader.query_documents(NoteQuery(scope="metadata")).total == 2
        cursor.close()
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("query", ["*", "**"])
def test_malformed_special_query_uses_literal_fallback(
    index: FTSIndex, query: str
) -> None:
    """Malformed special query uses literal fallback."""
    add_note(index, "a.md")
    assert index.count_documents_matching(NoteQuery(query=query)).total == 0


@pytest.mark.parametrize("offset", ["+00:60", "+01:99", "+24:00", "-00:60"])
def test_invalid_rfc3339_offsets_rejected(offset: str) -> None:
    """Invalid rfc3339 offsets rejected."""
    with pytest.raises(ValueError, match="RFC3339"):
        NoteQuery(modified_after="2026-01-01T00:00:00" + offset)


def test_nul_query_is_explicitly_invalid() -> None:
    """Nul query is explicitly invalid."""
    with pytest.raises(ValueError, match="NUL"):
        NoteQuery(query="x\x00y")


@pytest.mark.parametrize("field", ["tags", ""])
def test_persisted_empty_fields_override_constructor(
    index: FTSIndex, field: str
) -> None:
    """Persisted empty fields override constructor."""
    index._conn().execute(
        "INSERT OR REPLACE INTO meta VALUES ('indexed_frontmatter_fields', '')"
    )
    index._conn().commit()
    with pytest.raises(ValueError, match="not configured"):
        index.count_documents_matching(NoteQuery(filters={field: "x"}))


def test_failure_preserves_outer_transaction_and_backend_errors(
    index: FTSIndex,
) -> None:
    """Failure preserves outer transaction and backend errors."""
    import sqlite3

    add_note(index, "a.md")
    conn = index._conn()
    conn.execute("BEGIN")
    conn.execute("UPDATE documents SET title='pending' WHERE path='a.md'")
    with pytest.raises(ValueError):
        index.query_documents(NoteQuery(filters={"unknown": "x"}))
    assert conn.in_transaction
    assert conn.execute("SELECT title FROM documents").fetchone()[0] == "pending"
    conn.execute("DROP TABLE notes_fts")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        index.query_documents(NoteQuery(query="target"))
    assert conn.in_transaction
    conn.rollback()
    assert index.count_documents_matching(NoteQuery(query="target")).total == 1


def test_tombstones_excluded_and_full_sections(index: FTSIndex) -> None:
    """Tombstones excluded and full sections."""
    from markdown_vault_mcp.types import SkippedFile

    add_note(index, "a.md", bodies=["target " * 300])
    index.upsert_tombstone(
        SkippedFile(path="skip.md", category="parse_error", detail="bad"),
        content_hash="skip",
        modified_at=1000.0,
    )
    spec = NoteQuery(snippet_words=0, chunks_per_file=2**100)
    page = index.query_documents(spec)
    assert (
        page.total == 1 and len(page.items[0]["sections"][0]["content"].split()) == 300
    )
    assert index.aggregate_documents(spec, NoteGrouping(by="folder")).total == 1
    assert index.query_documents(replace(spec, offset=1)).items == []


def test_null_empty_and_casefold_equal_tags_remain_distinct(index: FTSIndex) -> None:
    """Null empty and casefold equal tags remain distinct."""
    add_note(index, "a.md", frontmatter={"tags": ["", "Straße", "STRASSE"]})
    add_note(index, "b.md")
    result = index.aggregate_documents(NoteQuery(), NoteGrouping(by="tag"))
    assert [(b["key"], b["count"]) for b in result.buckets] == [
        (None, 1),
        ("", 1),
        ("STRASSE", 1),
        ("Straße", 1),
    ]
    assert result.missing == 1


def test_filter_request_copies_input_and_validates_numeric_controls() -> None:
    """Filter request copies input and validates numeric controls."""
    filters = {"kind": "one"}
    spec = NoteQuery(filters=filters)
    filters["kind"] = "two"
    assert spec.filters["kind"] == "one"
    for field in ["offset", "limit", "chunks_per_file", "snippet_words"]:
        for value in [True, 1.5, "2", -1]:
            with pytest.raises(ValueError):
                NoteQuery(**{field: value})
    with pytest.raises(ValueError):
        NoteGrouping(by="tag", limit=1001)


def test_equivalent_offsets_and_inclusive_exclusive_bounds(index: FTSIndex) -> None:
    """Equivalent offsets and inclusive exclusive bounds."""
    add_note(index, "a.md", modified_at=1767225600.5)
    for lower in ["2026-01-01T00:00:00.5Z", "2026-01-01T01:00:00.5+01:00"]:
        assert (
            index.count_documents_matching(NoteQuery(modified_after=lower)).total == 1
        )
        assert (
            index.count_documents_matching(NoteQuery(modified_before=lower)).total == 0
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"modified_after": "2026-02-30T00:00:00Z"},
        {"order": "bad"},
        {"pattern_mode": "bad"},
        {"filters": {"kind": 3}},
        {"filters": {3: "kind"}},
    ],
)
def test_remaining_request_validation_rejects_invalid_values(
    kwargs: dict[str, Any],
) -> None:
    """Remaining request validation rejects invalid values."""
    with pytest.raises(ValueError):
        NoteQuery(**kwargs)


def test_invalid_grouping_axis() -> None:
    """Invalid grouping axis."""
    with pytest.raises(ValueError, match="by must"):
        NoteGrouping(by="bad")


def test_population_exceeds_maximum_output_page(index: FTSIndex) -> None:
    """Population exceeds maximum output page."""
    for i in range(1003):
        add_note(index, f"{i}.md", frontmatter={"tags": ["same"]})
    spec = NoteQuery(scope="metadata", limit=1000)
    page = index.query_documents(spec)
    assert page.total == 1003 and len(page.items) == 1000 and page.truncated
    assert index.count_documents_matching(spec).total == 1003
    grouped = index.aggregate_documents(spec, NoteGrouping(by="tag", limit=1))
    assert grouped.buckets == [{"key": "same", "count": 1003}]


@pytest.mark.parametrize("projection, budget", [("count", 100_000), ("page", 500_000)])
def test_broad_query_work_stays_bounded(
    index: FTSIndex, projection: str, budget: int
) -> None:
    """Guard broad populations against quadratic joins and repeated global ranking."""
    for i in range(300):
        add_note(index, f"{i}.md")
    steps = 0

    def within_budget() -> int:
        nonlocal steps
        steps += 1000
        return int(steps > budget)

    conn = index._conn()
    conn.set_progress_handler(within_budget, 1000)
    try:
        spec = NoteQuery(query="target", scope="body", limit=300)
        result = (
            index.count_documents_matching(spec)
            if projection == "count"
            else index.query_documents(spec)
        )
        assert result.total == 300
    finally:
        conn.set_progress_handler(None, 0)


@pytest.mark.parametrize("field", ["unknown", "status", "stale", "trust_tier"])
def test_field_refusals_have_a_deliberate_request_type(
    index: FTSIndex, field: str
) -> None:
    """Only explicit field validation is a deliberate caller error."""
    request = NoteQuery(filters={field: "value"})
    for operation in (
        lambda: index.query_documents(request),
        lambda: index.count_documents_matching(request),
        lambda: index.aggregate_documents(request, NoteGrouping(by="folder")),
        lambda: index.aggregate_documents(
            NoteQuery(), NoteGrouping(by="tag", field=field)
        ),
    ):
        with pytest.raises(InvalidRequestError):
            operation()
