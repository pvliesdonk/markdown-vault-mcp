"""Unit tests for FTSIndex (fts_index.py)."""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.fts_index import FTSIndex, _json_default
from markdown_vault_mcp.types import Chunk, FTSResult, ParsedNote

# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


def make_note(
    path: str = "test.md",
    title: str = "Test",
    frontmatter: dict | None = None,
    chunks: list[Chunk] | None = None,
    content_hash: str = "abc123",
    modified_at: float = 1000.0,
) -> ParsedNote:
    """Create a ParsedNote for testing.

    Args:
        path: Relative document path including ``.md`` extension.
        title: Document title.
        frontmatter: Frontmatter metadata dict. Defaults to ``{}``.
        chunks: List of chunks. Defaults to a single generic chunk.
        content_hash: Hash string stored in the note.
        modified_at: Modification timestamp.

    Returns:
        A fully-populated :class:`ParsedNote` suitable for indexing.
    """
    if chunks is None:
        chunks = [
            Chunk(heading="Test", heading_level=1, content="Test content", start_line=0)
        ]
    return ParsedNote(
        path=path,
        frontmatter=frontmatter or {},
        title=title,
        chunks=chunks,
        content_hash=content_hash,
        modified_at=modified_at,
    )


# ---------------------------------------------------------------------------
# Helpers for building a tagged index in multiple tests
# ---------------------------------------------------------------------------

_INDEXED_FIELDS = ["cluster", "topics", "genre"]


def _tagged_index() -> FTSIndex:
    """Return a fresh in-memory index with the standard indexed fields."""
    return FTSIndex(":memory:", indexed_frontmatter_fields=_INDEXED_FIELDS)


# ===========================================================================
# Tests
# ===========================================================================


class TestBuildFromNotes:
    def test_build_from_notes_returns_total_chunk_count(self) -> None:
        """build_from_notes returns the total number of chunks indexed."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                "a.md",
                chunks=[
                    Chunk(heading="H1", heading_level=1, content="alpha", start_line=0),
                    Chunk(heading="H2", heading_level=2, content="beta", start_line=5),
                ],
            ),
            make_note(
                "b.md",
                chunks=[
                    Chunk(heading="B1", heading_level=1, content="gamma", start_line=0),
                ],
            ),
            make_note(
                "c.md",
                chunks=[
                    Chunk(heading="C1", heading_level=1, content="delta", start_line=0),
                    Chunk(
                        heading="C2", heading_level=2, content="epsilon", start_line=3
                    ),
                    Chunk(heading="C3", heading_level=2, content="zeta", start_line=6),
                ],
            ),
        ]
        total = idx.build_from_notes(notes)
        assert total == 6


class TestSearch:
    def test_search_returns_fts_results(self) -> None:
        """search() returns FTSResult objects for matching terms."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "dragons.md",
                title="Dragons",
                chunks=[
                    Chunk(
                        heading="Overview",
                        heading_level=1,
                        content="Dragons breathe fire and hoard treasure.",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("dragons")
        assert len(results) >= 1
        assert all(isinstance(r, FTSResult) for r in results)
        paths = {r.path for r in results}
        assert "dragons.md" in paths

    def test_search_bm25_ranking_orders_by_relevance(self) -> None:
        """More-relevant documents score higher than less-relevant ones."""
        idx = FTSIndex(":memory:")
        # "python" appears many times in high.md, once in low.md
        idx.upsert_note(
            make_note(
                "high.md",
                title="High relevance",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="python python python python python programming",
                        start_line=0,
                    )
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "low.md",
                title="Low relevance",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="python is mentioned once here among other words",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("python", limit=10)
        assert len(results) == 2
        high_result = next(r for r in results if r.path == "high.md")
        low_result = next(r for r in results if r.path == "low.md")
        assert high_result.score > low_result.score

    def test_search_with_folder_filter(self) -> None:
        """folder= filter returns only documents under that folder."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "Journal/2024-01.md",
                title="January",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="today I went for a walk",
                        start_line=0,
                    )
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "Projects/alpha.md",
                title="Alpha",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="today the project started",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("today", folder="Journal")
        assert len(results) == 1
        assert results[0].path == "Journal/2024-01.md"
        assert results[0].folder == "Journal"

    def test_search_with_tag_filters(self) -> None:
        """filters= restricts results to documents matching the tag pair."""
        idx = _tagged_index()
        idx.upsert_note(
            make_note(
                "fiction/story.md",
                title="Story",
                frontmatter={"cluster": "fiction"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="once upon a time",
                        start_line=0,
                    )
                ],
            )
        )
        idx.upsert_note(
            make_note(
                "nonfiction/essay.md",
                title="Essay",
                frontmatter={"cluster": "nonfiction"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="once upon a time there were facts",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("once", filters={"cluster": "fiction"})
        assert len(results) == 1
        assert results[0].path == "fiction/story.md"

    def test_search_multiple_filters_anded(self) -> None:
        """Multiple filter entries are ANDed — only docs matching ALL pass."""
        idx = _tagged_index()
        # Matches cluster=fiction but not genre=horror
        idx.upsert_note(
            make_note(
                "a.md",
                frontmatter={"cluster": "fiction", "genre": "romance"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="love story",
                        start_line=0,
                    )
                ],
            )
        )
        # Matches both cluster=fiction AND genre=horror
        idx.upsert_note(
            make_note(
                "b.md",
                frontmatter={"cluster": "fiction", "genre": "horror"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="scary story",
                        start_line=0,
                    )
                ],
            )
        )
        # Matches genre=horror but not cluster=fiction
        idx.upsert_note(
            make_note(
                "c.md",
                frontmatter={"cluster": "nonfiction", "genre": "horror"},
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="true horror story",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("story", filters={"cluster": "fiction", "genre": "horror"})
        assert len(results) == 1
        assert results[0].path == "b.md"

    def test_search_empty_query_returns_empty_results(self) -> None:
        """search() returns an empty list for an empty query string (no exception)."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "a.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="hello world",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("")
        assert results == []

    def test_search_malformed_fts5_syntax_degrades_to_term_search(self) -> None:
        """A query FTS5 rejects (unclosed quote) degrades to a quoted term
        search rather than silently returning [] — the terms are still found
        when present (#866)."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "a.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="hello unclosed world",
                        start_line=0,
                    )
                ],
            )
        )
        # Unclosed quote is invalid FTS5 syntax; the fallback quotes each token
        # so the real word "unclosed" is still matched.
        results = idx.search('"unclosed world')
        assert [r.path for r in results] == ["a.md"]

    def test_search_invalid_fts5_column_degrades_to_term_search(self) -> None:
        """A `foo:bar` query where `foo` is not an FTS column degrades to a
        term search instead of returning [] (#866)."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "a.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="the value lives here",
                        start_line=0,
                    )
                ],
            )
        )
        # `nonexistent_column:value` raises "no such column"; the fallback
        # quotes it into one phrase literal ("nonexistent column value", a
        # single adjacency phrase) — those tokens are not adjacent in the doc,
        # so no match.
        assert idx.search("nonexistent_column:value") == []
        # ...but the bare word does match once the column syntax is gone.
        assert [r.path for r in idx.search("value")] == ["a.md"]

    def test_search_non_fts5_operational_error_propagates(self) -> None:
        """Non-FTS5 OperationalError (e.g. DB lock) must propagate, not return []."""
        from unittest.mock import MagicMock

        idx = FTSIndex(":memory:")
        # Replace the per-thread conn provider with one that returns a mock
        # raising a non-FTS5 DB error.
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")
        idx._conn = lambda: mock_conn  # type: ignore[method-assign]

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            idx.search("hello")

    def _hyphen_idx(self) -> FTSIndex:
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "doc.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="Ingest File-back Lint pipeline",
                        start_line=0,
                    )
                ],
            )
        )
        return idx

    def test_hyphenated_query_finds_document(self) -> None:
        """#866 repro: a hyphenated natural-language query returned [] because
        FTS5 rejected it and the error was swallowed. It must now find the doc."""
        idx = self._hyphen_idx()
        results = idx.search("File-back Ingest Lint")
        assert [r.path for r in results] == ["doc.md"]
        idx.close()

    def test_single_hyphenated_slug_finds_document(self) -> None:
        """A lone slug like `vault-mcp` (raises 'no such column') must match."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "s.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="the vault-mcp server rocks",
                        start_line=0,
                    )
                ],
            )
        )
        assert [r.path for r in idx.search("vault-mcp")] == ["s.md"]
        idx.close()

    def test_wellformed_boolean_query_bypasses_fallback(self) -> None:
        """A valid FTS5 OR query must keep operator semantics (not be quoted).

        `Ingest OR zzzmissing` parses cleanly and matches on `Ingest`; if the
        quote-fallback fired it would AND three literals (incl. `or`) and match
        nothing. Returning the doc proves well-formed queries bypass the
        fallback — the documented FTS5-query contract is preserved.
        """
        idx = self._hyphen_idx()
        assert [r.path for r in idx.search("Ingest OR zzzmissing")] == ["doc.md"]
        idx.close()

    def test_wellformed_phrase_and_prefix_queries_still_work(self) -> None:
        idx = self._hyphen_idx()
        assert [r.path for r in idx.search('"Ingest File"')] == ["doc.md"]  # phrase
        assert [r.path for r in idx.search(" Inge*")] == ["doc.md"]  # prefix
        idx.close()

    def test_hyphenated_query_with_absent_terms_returns_empty(self) -> None:
        """The fallback still returns [] when the quoted terms aren't present —
        graceful degradation, not a false positive."""
        idx = self._hyphen_idx()
        assert idx.search("absent-slug notpresent") == []
        idx.close()

    def test_search_retry_non_fts5_error_propagates(self) -> None:
        """A non-FTS5 error on the *quoted retry* must propagate, not be
        swallowed to [] — the same guarantee as the first execute (#866)."""
        from unittest.mock import MagicMock

        idx = FTSIndex(":memory:")
        mock_conn = MagicMock()
        # First execute: FTS5 syntax error (triggers the retry). Retry: a
        # genuine environmental error that must not be masked. Use a non-locked
        # error so the @_retry_on_locked decorator doesn't re-drive search().
        mock_conn.execute.side_effect = [
            sqlite3.OperationalError('fts5: syntax error near "-"'),
            sqlite3.OperationalError("disk I/O error"),
        ]
        idx._conn = lambda: mock_conn  # type: ignore[method-assign]
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            idx.search("vault-mcp")

    def test_search_retry_still_malformed_returns_empty_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If the quoted retry is *also* rejected as an FTS5 query error, the
        search degrades to [] — and logs at WARNING (degraded but continuing),
        not silently."""
        import logging
        from unittest.mock import MagicMock

        idx = FTSIndex(":memory:")
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            sqlite3.OperationalError('fts5: syntax error near "-"'),
            sqlite3.OperationalError("fts5: unterminated string"),
        ]
        idx._conn = lambda: mock_conn  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.fts_index"):
            assert idx.search("vault-mcp") == []
        assert any("fts_search_unexecutable" in r.getMessage() for r in caplog.records)

    def test_search_whitespace_only_query_with_fts_error_returns_empty(self) -> None:
        """A truthy query with no quotable tokens that FTS5 rejects degrades to
        [] (the unrecoverable-fallback guard)."""
        from unittest.mock import MagicMock

        idx = FTSIndex(":memory:")
        mock_conn = MagicMock()
        # A one-element side_effect: only the FIRST execute may run. If the
        # `if not fallback: return []` guard were removed, the code would call
        # execute a second time (with the empty match) and StopIteration would
        # fail the test — so this genuinely pins the guard branch.
        mock_conn.execute.side_effect = [
            sqlite3.OperationalError("fts5: syntax error"),
        ]
        idx._conn = lambda: mock_conn  # type: ignore[method-assign]
        # "   " is truthy (bypasses the empty-query guard) but tokenizes to no
        # quotable tokens, so the fallback is empty and search returns [].
        assert idx.search("   ") == []

    def test_fallback_honours_folder_filter(self) -> None:
        """A hyphenated query that triggers the quoted retry must still respect
        the folder= filter — the retry reuses the same folder/tag params."""
        idx = FTSIndex(":memory:")
        for path in ("Journal/a.md", "Notes/b.md"):
            idx.upsert_note(
                make_note(
                    path,
                    chunks=[
                        Chunk(
                            heading=None,
                            heading_level=0,
                            content="the vault-mcp pipeline",
                            start_line=0,
                        )
                    ],
                )
            )
        results = idx.search("vault-mcp", folder="Journal")
        assert [r.path for r in results] == ["Journal/a.md"]
        idx.close()

    def test_fallback_escapes_embedded_quote(self) -> None:
        """A token containing a double-quote is matched via the quote-doubling
        escape, not re-broken into malformed FTS5 syntax that masks as []."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "q.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content='he said "hi" today',
                        start_line=0,
                    )
                ],
            )
        )
        # `said"hi` is an unterminated FTS5 string; the fallback must produce
        # "said""hi" (doubled quote) and still find the doc.
        results = idx.search('said"hi')
        assert [r.path for r in results] == ["q.md"]
        idx.close()

    def test_search_returns_start_line(self) -> None:
        """search() propagates start_line from the sections row.

        Regression for the keyword/hybrid (score DESC, start_line ASC)
        within-group tie-break (PR #471 review): FTSResult must carry the
        chunk's start_line so :func:`_group_by_path` can break ties in
        document order instead of relying on a hardcoded 0.
        """
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "multi.md",
                title="Multi-section",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="alpha keyword intro",
                        start_line=0,
                    ),
                    Chunk(
                        heading="Section A",
                        heading_level=1,
                        content="alpha keyword middle",
                        start_line=10,
                    ),
                    Chunk(
                        heading="Section B",
                        heading_level=1,
                        content="alpha keyword tail",
                        start_line=42,
                    ),
                ],
            )
        )
        results = idx.search("keyword", limit=10)
        # All three chunks should match.
        assert len(results) == 3
        # Every result must expose start_line as an int.
        for r in results:
            assert isinstance(r.start_line, int)
        # And the set must match the source chunks' start_lines — proving the
        # column was actually plumbed through (vs. the prior hardcoded 0).
        assert {r.start_line for r in results} == {0, 10, 42}

    def test_search_returns_section_id(self) -> None:
        """search() propagates the sections rowid as section_id.

        section_id is the final deterministic tie-break in
        :func:`_group_by_path` for chunks sharing (score, start_line) — e.g.
        word-split fragments of one oversize source line.  Each FTSResult
        must carry the rowid of its matching sections row.
        """
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "doc.md",
                title="Doc",
                chunks=[
                    Chunk(
                        heading="One",
                        heading_level=1,
                        content="alpha keyword first",
                        start_line=0,
                    ),
                    Chunk(
                        heading="Two",
                        heading_level=1,
                        content="alpha keyword second",
                        start_line=8,
                    ),
                ],
            )
        )
        results = idx.search("keyword", limit=10)
        assert len(results) == 2
        # Every result carries a positive section_id (SQLite rowid >= 1).
        for r in results:
            assert isinstance(r.section_id, int)
            assert r.section_id >= 1
        # The two chunks resolve to distinct sections rows.
        assert len({r.section_id for r in results}) == 2
        # section_id order matches start_line order (sections inserted in
        # document order), so it is a valid document-order tie-break.
        by_start = sorted(results, key=lambda r: r.start_line)
        assert [r.section_id for r in by_start] == sorted(r.section_id for r in results)

    def test_sections_has_document_id_index(self) -> None:
        """sections.document_id is indexed for the correlated subquery in search().

        The keyword channel issues a correlated subquery over ``sections`` to
        propagate ``start_line`` to each FTS row. Without an index on
        ``sections.document_id`` that subquery degrades to a full-table scan per
        result row — sibling tables (``links``, ``document_tags``,
        ``document_aliases``) all have explicit ``document_id`` indexes, so the
        gap on ``sections`` was a perf regression flagged by local review.
        """
        idx = FTSIndex(":memory:")
        cur = idx._conn().execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sections'"
        )
        index_names = {row[0] for row in cur.fetchall()}
        assert any(
            "docid" in n.lower() or "document_id" in n.lower() for n in index_names
        ), f"expected an index on sections(document_id); got {index_names}"


class TestUpsert:
    def test_upsert_note_replaces_existing(self) -> None:
        """upsert_note removes old content and makes new content searchable."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "replace.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="the old unique word xylophone",
                        start_line=0,
                    )
                ],
                content_hash="old",
            )
        )
        # Sanity: old content is searchable before upsert.
        assert len(idx.search("xylophone")) == 1

        idx.upsert_note(
            make_note(
                "replace.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="entirely new content kazoo",
                        start_line=0,
                    )
                ],
                content_hash="new",
            )
        )
        assert idx.search("xylophone") == []
        results = idx.search("kazoo")
        assert len(results) == 1
        assert results[0].path == "replace.md"


class TestFrontmatterSerialization:
    def test_date_in_frontmatter_stored_as_iso_string(self) -> None:
        """datetime.date in frontmatter is stored as ISO string."""
        idx = FTSIndex(":memory:")
        note = make_note(
            "dated.md",
            frontmatter={"created": datetime.date(2024, 1, 15), "title": "Dated"},
        )
        idx.upsert_note(note)
        row = idx.get_note("dated.md")
        assert row is not None
        fm = json.loads(row["frontmatter_json"])
        assert fm["created"] == "2024-01-15"

    def test_datetime_in_frontmatter_stored_as_iso_string(self) -> None:
        """datetime.datetime in frontmatter is stored as ISO string."""
        idx = FTSIndex(":memory:")
        note = make_note(
            "timestamped.md",
            frontmatter={"updated": datetime.datetime(2024, 6, 15, 12, 30, 0)},
        )
        idx.upsert_note(note)
        row = idx.get_note("timestamped.md")
        assert row is not None
        fm = json.loads(row["frontmatter_json"])
        assert fm["updated"] == "2024-06-15T12:30:00"

    def test_time_in_frontmatter_stored_as_iso_string(self) -> None:
        """datetime.time in frontmatter is stored as ISO string."""
        idx = FTSIndex(":memory:")
        note = make_note(
            "timed.md",
            frontmatter={"starts_at": datetime.time(15, 30, 0)},
        )
        idx.upsert_note(note)
        row = idx.get_note("timed.md")
        assert row is not None
        fm = json.loads(row["frontmatter_json"])
        assert fm["starts_at"] == "15:30:00"

    def test_json_default_raises_for_unsupported_types(self) -> None:
        """_json_default raises TypeError for non-date types."""
        with pytest.raises(TypeError, match="set"):
            _json_default({1, 2, 3})


class TestDelete:
    def test_delete_by_path_removes_search_results(self) -> None:
        """delete_by_path makes the note unsearchable."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "gone.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="the unique word fjord",
                        start_line=0,
                    )
                ],
            )
        )
        assert len(idx.search("fjord")) == 1

        deleted = idx.delete_by_path("gone.md")
        assert deleted == 1
        assert idx.search("fjord") == []

    def test_delete_cascades_to_sections_and_tags(self) -> None:
        """Deleting a document removes its sections and tags from the DB."""
        idx = _tagged_index()
        idx.upsert_note(
            make_note(
                "cascade.md",
                frontmatter={"cluster": "fiction"},
                chunks=[
                    Chunk(
                        heading="Ch1", heading_level=1, content="content", start_line=0
                    ),
                    Chunk(heading="Ch2", heading_level=2, content="more", start_line=5),
                ],
            )
        )

        # Verify sections and tags exist before deletion.
        conn = idx._conn()
        sec_count = conn.execute(
            "SELECT COUNT(*) FROM sections WHERE document_id IN "
            "(SELECT id FROM documents WHERE path = ?)",
            ("cascade.md",),
        ).fetchone()[0]
        assert sec_count == 2

        tag_count = conn.execute(
            "SELECT COUNT(*) FROM document_tags WHERE document_id IN "
            "(SELECT id FROM documents WHERE path = ?)",
            ("cascade.md",),
        ).fetchone()[0]
        assert tag_count == 1

        idx.delete_by_path("cascade.md")

        # Documents row is gone — CASCADE should have cleared child rows.
        doc_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE path = ?", ("cascade.md",)
        ).fetchone()[0]
        assert doc_count == 0

        # Sections and tags for the deleted document must also be gone.
        orphan_secs = conn.execute(
            "SELECT COUNT(*) FROM sections WHERE document_id NOT IN "
            "(SELECT id FROM documents)"
        ).fetchone()[0]
        assert orphan_secs == 0

        orphan_tags = conn.execute(
            "SELECT COUNT(*) FROM document_tags WHERE document_id NOT IN "
            "(SELECT id FROM documents)"
        ).fetchone()[0]
        assert orphan_tags == 0


class TestListFolders:
    def test_list_folders_returns_sorted_distinct_values(self) -> None:
        """list_folders() returns all distinct folder values in sorted order."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note("Journal/jan.md"))
        idx.upsert_note(make_note("Journal/feb.md"))
        idx.upsert_note(make_note("Projects/alpha.md"))
        idx.upsert_note(make_note("root.md"))

        folders = idx.list_folders()
        assert folders == sorted(set(folders))
        assert "Journal" in folders
        assert "Projects" in folders
        assert "" in folders  # root document
        # No duplicates.
        assert len(folders) == len(set(folders))


class TestListFieldValues:
    def test_list_field_values_returns_distinct_values(self) -> None:
        """list_field_values() returns distinct tag values for a field."""
        idx = _tagged_index()
        idx.upsert_note(make_note("a.md", frontmatter={"cluster": "fiction"}))
        idx.upsert_note(make_note("b.md", frontmatter={"cluster": "nonfiction"}))
        idx.upsert_note(make_note("c.md", frontmatter={"cluster": "fiction"}))

        values = idx.list_field_values("cluster")
        assert sorted(values) == ["fiction", "nonfiction"]
        # No duplicates.
        assert len(values) == len(set(values))


class TestTagIndexing:
    def test_tag_indexing_scalar_creates_one_row(self) -> None:
        """A scalar frontmatter value produces exactly one document_tags row."""
        idx = _tagged_index()
        idx.upsert_note(make_note("scalar.md", frontmatter={"cluster": "fiction"}))
        rows = (
            idx._conn()
            .execute("SELECT tag_value FROM document_tags WHERE tag_key = 'cluster'")
            .fetchall()
        )
        assert len(rows) == 1
        assert rows[0][0] == "fiction"

    def test_tag_indexing_list_deduplicates(self) -> None:
        """A list frontmatter value creates one row per distinct item."""
        idx = _tagged_index()
        idx.upsert_note(make_note("list.md", frontmatter={"topics": ["a", "b", "a"]}))
        rows = (
            idx._conn()
            .execute(
                "SELECT tag_value FROM document_tags WHERE tag_key = 'topics' "
                "ORDER BY tag_value"
            )
            .fetchall()
        )
        assert len(rows) == 2
        assert rows[0][0] == "a"
        assert rows[1][0] == "b"

    def test_tag_indexing_complex_value_skipped(self) -> None:
        """A nested dict frontmatter value is NOT promoted to document_tags."""
        idx = _tagged_index()
        idx.upsert_note(
            make_note("complex.md", frontmatter={"cluster": {"key": "val"}})
        )
        rows = (
            idx._conn()
            .execute("SELECT COUNT(*) FROM document_tags WHERE tag_key = 'cluster'")
            .fetchone()
        )
        assert rows[0] == 0


class TestGetNote:
    def test_get_note_returns_correct_dict(self) -> None:
        """get_note() returns a dict with the expected keys and values."""
        idx = FTSIndex(":memory:")
        note = make_note(
            "Journal/entry.md",
            title="My Entry",
            frontmatter={"date": "2024-01-01"},
            content_hash="deadbeef",
            modified_at=9999.0,
        )
        idx.upsert_note(note)

        result = idx.get_note("Journal/entry.md")
        assert result is not None
        assert result["path"] == "Journal/entry.md"
        assert result["title"] == "My Entry"
        assert result["folder"] == "Journal"
        assert result["content_hash"] == "deadbeef"
        assert result["modified_at"] == pytest.approx(9999.0)

    def test_get_note_not_found_returns_none(self) -> None:
        """get_note() returns None for a path that was never indexed."""
        idx = FTSIndex(":memory:")
        assert idx.get_note("nonexistent.md") is None


class TestInMemoryMode:
    def test_in_memory_mode_works(self) -> None:
        """FTSIndex with ':memory:' is functional end-to-end."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(
            make_note(
                "mem.md",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="in-memory test passage",
                        start_line=0,
                    )
                ],
            )
        )
        results = idx.search("memory")
        assert len(results) >= 1
        assert results[0].path == "mem.md"


class TestWALMode:
    def test_file_based_index_uses_wal_journal_mode(self, tmp_path: Path) -> None:
        """File-based FTSIndex uses WAL journal mode for concurrent reads."""
        db_path = tmp_path / "test.db"
        idx = FTSIndex(str(db_path))
        mode = idx._conn().execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_in_memory_index_uses_memory_journal_mode(self) -> None:
        """In-memory FTSIndex skips WAL and retains SQLite default 'memory' mode."""
        idx = FTSIndex(":memory:")
        mode = idx._conn().execute("PRAGMA journal_mode").fetchone()[0]
        # WAL pragma is skipped for :memory: databases; SQLite uses 'memory' mode.
        assert mode.lower() == "memory"

    def test_wal_warning_logged_when_pragma_returns_non_wal(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A warning is logged when WAL mode cannot be enabled."""
        import logging
        from unittest.mock import MagicMock

        db_path = tmp_path / "nowarn.db"

        mock_conn = MagicMock()
        # WAL pragma returns "delete" (simulating a filesystem without WAL support).
        mock_conn.execute.return_value.fetchone.return_value = ["delete"]

        with (
            patch(
                "markdown_vault_mcp.fts_index.sqlite3.connect", return_value=mock_conn
            ),
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.fts_index"),
        ):
            FTSIndex(db_path=db_path)

        assert any(
            "Could not enable WAL journal mode" in r.message for r in caplog.records
        )

    def test_wal_allows_concurrent_reader_during_write(self, tmp_path: Path) -> None:
        """A reader on a second connection succeeds while the first connection writes."""
        import sqlite3

        db_path = tmp_path / "concurrent.db"
        idx = FTSIndex(str(db_path))
        # Seed one document so there is something to read.
        idx.upsert_note(make_note(path="seed.md"))

        writer_conn = sqlite3.connect(str(db_path), check_same_thread=False)
        reader_conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            # Begin an exclusive write transaction on writer_conn.
            writer_conn.execute("BEGIN EXCLUSIVE")
            writer_conn.execute(
                "INSERT OR REPLACE INTO documents(path, title, folder, "
                "frontmatter_json, content_hash, modified_at) "
                "VALUES ('concurrent.md', 'Concurrent', '', '{}', 'abc', 0.0)"
            )
            # WAL allows the reader to see the previously committed state
            # without waiting for the writer to commit.
            rows = reader_conn.execute(
                "SELECT path FROM documents WHERE path = 'seed.md'"
            ).fetchall()
            assert len(rows) == 1, (
                "Reader should see committed data while writer holds EXCLUSIVE"
            )
            writer_conn.rollback()
        finally:
            writer_conn.close()
            reader_conn.close()


class TestGetRecent:
    """Tests for FTSIndex.get_recent()."""

    def test_returns_notes_ordered_by_mtime_desc(self) -> None:
        """get_recent returns notes most-recent first."""
        idx = FTSIndex(":memory:")
        notes = [
            ParsedNote(
                path=f"note{i}.md",
                frontmatter={},
                title=f"Note {i}",
                chunks=[
                    Chunk(heading=None, heading_level=0, content="c", start_line=0)
                ],
                content_hash=f"h{i}",
                modified_at=float(i * 100),
            )
            for i in range(5)
        ]
        idx.build_from_notes(notes)
        rows = idx.get_recent(limit=5)
        mtimes = [r["modified_at"] for r in rows]
        assert mtimes == sorted(mtimes, reverse=True)

    def test_respects_limit(self) -> None:
        """get_recent returns at most `limit` rows."""
        idx = FTSIndex(":memory:")
        notes = [
            ParsedNote(
                path=f"note{i}.md",
                frontmatter={},
                title=f"Note {i}",
                chunks=[
                    Chunk(heading=None, heading_level=0, content="c", start_line=0)
                ],
                content_hash=f"h{i}",
                modified_at=float(i),
            )
            for i in range(10)
        ]
        idx.build_from_notes(notes)
        rows = idx.get_recent(limit=3)
        assert len(rows) == 3

    def test_folder_filter(self) -> None:
        """get_recent with folder returns only matching documents."""
        idx = FTSIndex(":memory:")
        notes = [
            ParsedNote(
                path="root.md",
                frontmatter={},
                title="Root",
                chunks=[
                    Chunk(heading=None, heading_level=0, content="c", start_line=0)
                ],
                content_hash="h1",
                modified_at=100.0,
            ),
            ParsedNote(
                path="Journal/day1.md",
                frontmatter={},
                title="Day 1",
                chunks=[
                    Chunk(heading=None, heading_level=0, content="c", start_line=0)
                ],
                content_hash="h2",
                modified_at=200.0,
            ),
            ParsedNote(
                path="Journal/day2.md",
                frontmatter={},
                title="Day 2",
                chunks=[
                    Chunk(heading=None, heading_level=0, content="c", start_line=0)
                ],
                content_hash="h3",
                modified_at=300.0,
            ),
        ]
        idx.build_from_notes(notes)
        rows = idx.get_recent(folder="Journal")
        paths = {r["path"] for r in rows}
        assert paths == {"Journal/day1.md", "Journal/day2.md"}

    def test_folder_filter_nested_subfolder(self) -> None:
        """get_recent with folder includes nested sub-folder documents."""
        idx = FTSIndex(":memory:")
        notes = [
            ParsedNote(
                path="Journal/day1.md",
                frontmatter={},
                title="Day 1",
                chunks=[
                    Chunk(heading=None, heading_level=0, content="c", start_line=0)
                ],
                content_hash="h1",
                modified_at=100.0,
            ),
            ParsedNote(
                path="Journal/sub/nested.md",
                frontmatter={},
                title="Nested",
                chunks=[
                    Chunk(heading=None, heading_level=0, content="c", start_line=0)
                ],
                content_hash="h2",
                modified_at=200.0,
            ),
            ParsedNote(
                path="Other/note.md",
                frontmatter={},
                title="Other",
                chunks=[
                    Chunk(heading=None, heading_level=0, content="c", start_line=0)
                ],
                content_hash="h3",
                modified_at=300.0,
            ),
        ]
        idx.build_from_notes(notes)
        rows = idx.get_recent(folder="Journal")
        paths = {r["path"] for r in rows}
        assert paths == {"Journal/day1.md", "Journal/sub/nested.md"}

    def test_empty_index_returns_empty(self) -> None:
        """get_recent on empty index returns []."""
        idx = FTSIndex(":memory:")
        assert idx.get_recent() == []


def test_chunking_meta_roundtrip(tmp_path):
    from markdown_vault_mcp.fts_index import ChunkingMeta, FTSIndex

    idx = FTSIndex(db_path=str(tmp_path / "i.db"))
    idx.set_chunking_meta(model="bge-m3:latest", max_chunk_chars_override=5000)
    assert idx.get_chunking_meta() == ChunkingMeta(
        model="bge-m3:latest", max_chunk_chars_override=5000
    )
    idx2 = FTSIndex(db_path=str(tmp_path / "j.db"))
    assert idx2.get_chunking_meta() == ChunkingMeta(
        model=None, max_chunk_chars_override=None
    )


def test_chunking_meta_stores_none_as_empty(tmp_path):
    """``None`` model + override round-trip back as ``None`` (stored empty)."""
    from markdown_vault_mcp.fts_index import ChunkingMeta, FTSIndex

    idx = FTSIndex(db_path=str(tmp_path / "k.db"))
    idx.set_chunking_meta(model=None, max_chunk_chars_override=None)
    assert idx.get_chunking_meta() == ChunkingMeta(
        model=None, max_chunk_chars_override=None
    )


def test_chunking_meta_indexed_frontmatter_fields_roundtrip(tmp_path):
    """indexed_frontmatter_fields persists and defaults to ``""``."""
    from markdown_vault_mcp.fts_index import ChunkingMeta, FTSIndex

    idx = FTSIndex(db_path=str(tmp_path / "i.db"))
    idx.set_chunking_meta(
        model=None,
        max_chunk_chars_override=None,
        indexed_frontmatter_fields="tags,category",
    )
    assert idx.get_chunking_meta() == ChunkingMeta(
        model=None,
        max_chunk_chars_override=None,
        indexed_frontmatter_fields="tags,category",
    )
    idx2 = FTSIndex(db_path=str(tmp_path / "j.db"))
    assert idx2.get_chunking_meta().indexed_frontmatter_fields == ""


# ---------------------------------------------------------------------------
# Curated ranking: summary column, legacy migration, persisted rank config
# ---------------------------------------------------------------------------


def _two_chunk_note(frontmatter: dict | None = None) -> ParsedNote:
    return make_note(
        path="doc.md",
        title="Doc",
        frontmatter=frontmatter or {},
        chunks=[
            Chunk(heading="One", heading_level=1, content="first body", start_line=0),
            Chunk(heading="Two", heading_level=1, content="second body", start_line=5),
        ],
    )


class TestSummaryColumn:
    def test_summary_indexed_on_chunk0_row_only(self) -> None:
        idx = FTSIndex(searchable_frontmatter_fields=["summary"])
        idx.upsert_note(_two_chunk_note({"summary": "quantum entanglement overview"}))
        rows = (
            idx._conn()
            .execute("SELECT heading, summary FROM notes_fts ORDER BY rowid")
            .fetchall()
        )
        assert [tuple(r) for r in rows] == [
            ("One", "quantum entanglement overview"),
            ("Two", ""),
        ]
        results = idx.search("entanglement")
        assert len(results) == 1
        assert results[0].heading == "One"
        idx.close()

    def test_summary_column_filter_works(self) -> None:
        idx = FTSIndex(searchable_frontmatter_fields=["summary"])
        idx.upsert_note(_two_chunk_note({"summary": "quantum entanglement"}))
        assert len(idx.search("summary:entanglement")) == 1
        assert idx.search("content:entanglement") == []
        idx.close()

    def test_without_configured_fields_summary_is_empty(self) -> None:
        idx = FTSIndex()
        idx.upsert_note(_two_chunk_note({"summary": "quantum entanglement"}))
        assert idx.search("entanglement") == []
        idx.close()

    def test_snippet_stays_content_based(self) -> None:
        """A summary-only match still snippets from the content column."""
        idx = FTSIndex(searchable_frontmatter_fields=["summary"])
        idx.upsert_note(_two_chunk_note({"summary": "zebra sighting notes"}))
        results = idx.search("zebra", snippet_words=3)
        assert len(results) == 1
        # The chunk content carries no match, so snippet() falls back to the
        # leading tokens of the CONTENT column — never the summary text.
        assert "first" in results[0].content
        assert "zebra" not in results[0].content
        idx.close()

    def test_list_chunks_includes_frontmatter_json_and_first_flag(self) -> None:
        idx = FTSIndex(searchable_frontmatter_fields=["summary"])
        idx.upsert_note(_two_chunk_note({"summary": "s"}))
        rows = idx.list_chunks()
        assert [r["is_first_chunk"] for r in rows] == [True, False]
        assert all(json.loads(r["frontmatter_json"]) == {"summary": "s"} for r in rows)
        idx.close()


_LEGACY_SCHEMA = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
    frontmatter_json TEXT,
    content_hash TEXT NOT NULL,
    modified_at REAL NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE sections (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    heading TEXT,
    heading_level INTEGER NOT NULL,
    content TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE VIRTUAL TABLE notes_fts USING fts5(
    path, title, folder, heading, content,
    tokenize='porter unicode61'
);
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class TestNotesFtsSummaryMigration:
    def test_legacy_five_column_table_migrates_without_rescan(
        self, tmp_path: Path
    ) -> None:
        """A pre-summary index gains the column and keeps rows + sentinel."""
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(db)
        conn.executescript(_LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO documents (id, path, title, folder, frontmatter_json,"
            " content_hash, modified_at, chunk_count)"
            " VALUES (1, 'a.md', 'Alpha', '', '{}', 'h1', 1000.0, 2)"
        )
        conn.execute(
            "INSERT INTO sections (document_id, heading, heading_level, content,"
            " start_line) VALUES (1, 'Intro', 1, 'hello world body', 0)"
        )
        conn.execute(
            "INSERT INTO sections (document_id, heading, heading_level, content,"
            " start_line) VALUES (1, NULL, 0, 'preamble text', 5)"
        )
        conn.execute(
            "INSERT INTO notes_fts (path, title, folder, heading, content)"
            " VALUES ('a.md', 'Alpha', '', 'Intro', 'hello world body')"
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('build_completed_at', 'ts')"
        )
        conn.commit()
        conn.close()

        idx = FTSIndex(db_path=db)
        cols = [
            r[1] for r in idx._conn().execute("PRAGMA table_info(notes_fts)").fetchall()
        ]
        assert cols == ["path", "title", "folder", "heading", "content", "summary"]
        # Rows were repopulated in pure SQL from sections/documents — both
        # section rows are searchable, with empty summaries and NULL headings
        # coalesced to ''.
        fts_rows = (
            idx._conn()
            .execute(
                "SELECT path, title, heading, content, summary FROM notes_fts"
                " ORDER BY rowid"
            )
            .fetchall()
        )
        assert [tuple(r) for r in fts_rows] == [
            ("a.md", "Alpha", "Intro", "hello world body", ""),
            ("a.md", "Alpha", "", "preamble text", ""),
        ]
        assert len(idx.search("preamble")) == 1
        # The meta table — and with it the warm-restart sentinel — survives.
        assert idx.is_build_completed() is True
        idx.close()

    def test_six_column_table_is_left_alone(self, tmp_path: Path) -> None:
        """Reopening a current-schema index does not drop/repopulate."""
        db = tmp_path / "current.db"
        idx = FTSIndex(db_path=db, searchable_frontmatter_fields=["summary"])
        idx.upsert_note(_two_chunk_note({"summary": "quantum entanglement"}))
        idx.close()

        idx2 = FTSIndex(db_path=db, searchable_frontmatter_fields=["summary"])
        # Summary text survives a reopen (a migration would have wiped it).
        assert len(idx2.search("summary:entanglement")) == 1
        idx2.close()

    def test_failed_repopulation_rolls_back_and_preserves_legacy_table(
        self, tmp_path: Path
    ) -> None:
        """A crash mid-migration must not strand an empty ``notes_fts``.

        The DROP/CREATE/INSERT run inside one explicit transaction, so a
        failure during the repopulating ``INSERT ... SELECT`` rolls back to
        the intact legacy 5-column table. The migration then retries cleanly
        on the next open, rather than early-returning on a present-but-empty
        6-column table that the warm-restart sentinel would report as
        populated (silently killing keyword search forever).
        """
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(db)
        conn.executescript(_LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO documents (id, path, title, folder, frontmatter_json,"
            " content_hash, modified_at, chunk_count)"
            " VALUES (1, 'a.md', 'Alpha', '', '{}', 'h1', 1000.0, 1)"
        )
        conn.execute(
            "INSERT INTO sections (document_id, heading, heading_level, content,"
            " start_line) VALUES (1, 'Intro', 1, 'hello world body', 0)"
        )
        conn.execute(
            "INSERT INTO notes_fts (path, title, folder, heading, content)"
            " VALUES ('a.md', 'Alpha', '', 'Intro', 'hello world body')"
        )
        conn.commit()
        conn.close()

        class _FailRepopulateConnection(sqlite3.Connection):
            """Raise only on the migration's repopulating ``INSERT ... SELECT``."""

            def execute(self, sql, *args):  # type: ignore[override]
                upper = sql.upper()
                if "INSERT INTO NOTES_FTS" in upper and "SELECT" in upper:
                    raise sqlite3.OperationalError("simulated disk-full mid-migration")
                return super().execute(sql, *args)

        real_connect = sqlite3.connect

        def _failing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            kwargs["factory"] = _FailRepopulateConnection
            return real_connect(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch("markdown_vault_mcp.fts_index.sqlite3.connect", _failing_connect),
            pytest.raises(sqlite3.OperationalError, match="simulated disk-full"),
        ):
            FTSIndex(db_path=db)

        # Rollback restored the legacy 5-column table and its row intact.
        verify = sqlite3.connect(db)
        cols = [r[1] for r in verify.execute("PRAGMA table_info(notes_fts)").fetchall()]
        assert cols == ["path", "title", "folder", "heading", "content"]
        assert verify.execute("SELECT count(*) FROM notes_fts").fetchone()[0] == 1
        verify.close()

        # A healthy retry completes the migration and keeps the row searchable.
        idx = FTSIndex(db_path=db)
        cols2 = [
            r[1] for r in idx._conn().execute("PRAGMA table_info(notes_fts)").fetchall()
        ]
        assert cols2 == ["path", "title", "folder", "heading", "content", "summary"]
        assert len(idx.search("hello")) == 1
        idx.close()


def test_unknown_fts_weights_column_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A typo'd fts_weights column (bypassing SearchConfig validation) is
    ignored by the rank config; warn so it isn't a silent ranking degrade."""
    import logging

    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.fts_index"):
        idx = FTSIndex(db_path=":memory:", fts_weights={"titel": 3.0, "title": 2.0})
    idx.close()
    assert any(
        "unknown fts_weights" in r.getMessage() and "titel" in r.getMessage()
        for r in caplog.records
    )


def test_fts_column_constants_stay_in_lockstep() -> None:
    """The two ``_FTS_COLUMNS`` copies and the live ``notes_fts`` must agree.

    ``fts_index._persist_rank_config`` builds ``bm25()`` arguments positionally
    from ``FTSIndex._FTS_COLUMNS``, and ``SearchConfig`` validates
    ``fts_weights`` keys against its own copy in ``config_sections.search``. If
    the two drift or fall out of the table's declared column order, weights map
    to the wrong columns or a valid key is rejected — with nothing else
    catching it. This pins the three-way agreement.
    """
    from markdown_vault_mcp.config_sections.search import _FTS_COLUMNS as _CFG_COLS

    idx = FTSIndex(db_path=":memory:")
    live = tuple(
        r[1] for r in idx._conn().execute("PRAGMA table_info(notes_fts)").fetchall()
    )
    idx.close()
    assert FTSIndex._FTS_COLUMNS == _CFG_COLS == live


class TestPersistedRankConfig:
    @staticmethod
    def _populate(idx: FTSIndex) -> None:
        idx.upsert_note(
            make_note(
                path="title_doc.md",
                title="alpha reference",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="body without the term",
                        start_line=0,
                    )
                ],
            )
        )
        idx.upsert_note(
            make_note(
                path="content_doc.md",
                title="other",
                chunks=[
                    Chunk(
                        heading=None,
                        heading_level=0,
                        content="alpha appears in this body",
                        start_line=0,
                    )
                ],
            )
        )

    def test_persisted_weights_flip_ordering_vs_default(self, tmp_path: Path) -> None:
        """f.rank picks up the persisted rank config: zeroing the column the
        default-first document matched on demotes it below the other."""
        default_idx = FTSIndex(db_path=tmp_path / "default.db")
        self._populate(default_idx)
        default_results = default_idx.search("alpha")
        assert [r.score > 0 for r in default_results] == [True, True]
        default_first = default_results[0].path
        default_idx.close()

        # Zero out whichever column the default winner matched on.
        zeroed_column = "title" if default_first == "title_doc.md" else "content"
        weighted_idx = FTSIndex(
            db_path=tmp_path / "weighted.db",
            fts_weights={zeroed_column: 0.0},
        )
        self._populate(weighted_idx)
        weighted_results = weighted_idx.search("alpha")
        assert weighted_results[0].path != default_first
        assert weighted_results[-1].path == default_first
        assert weighted_results[-1].score == 0.0
        weighted_idx.close()

    def test_all_ones_weights_match_default_scores(self, tmp_path: Path) -> None:
        default_idx = FTSIndex(db_path=tmp_path / "default.db")
        self._populate(default_idx)
        default_scores = {r.path: r.score for r in default_idx.search("alpha")}
        default_idx.close()

        ones_idx = FTSIndex(
            db_path=tmp_path / "ones.db",
            fts_weights=dict.fromkeys(FTSIndex._FTS_COLUMNS, 1.0),
        )
        self._populate(ones_idx)
        ones_scores = {r.path: r.score for r in ones_idx.search("alpha")}
        ones_idx.close()

        assert ones_scores == default_scores

    def test_reopen_without_weights_resets_to_default(self, tmp_path: Path) -> None:
        """Removing FTS_WEIGHTS restores default bm25() (idempotent reset)."""
        db = tmp_path / "shared.db"
        weighted_idx = FTSIndex(db_path=db, fts_weights={"content": 0.0})
        self._populate(weighted_idx)
        assert any(r.score == 0.0 for r in weighted_idx.search("alpha"))
        weighted_idx.close()

        reset_idx = FTSIndex(db_path=db)
        reset_scores = {r.path: r.score for r in reset_idx.search("alpha")}
        reset_idx.close()

        default_idx = FTSIndex(db_path=tmp_path / "default.db")
        self._populate(default_idx)
        default_scores = {r.path: r.score for r in default_idx.search("alpha")}
        default_idx.close()

        assert reset_scores == default_scores
