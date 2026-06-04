"""Tests for field-collapsing types and helpers (issue #469)."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_vault_mcp.managers.search import _group_by_path
from markdown_vault_mcp.types import GroupedResult, SectionHit


def test_section_hit_fields():
    s = SectionHit(heading="Risk", content="snippet", score=0.9)
    assert s.heading == "Risk"
    assert s.content == "snippet"
    assert s.score == 0.9


def test_grouped_result_fields():
    g = GroupedResult(
        path="a.md",
        title="A",
        folder="",
        score=0.9,
        search_type="semantic",
        frontmatter={},
        sections=[SectionHit(heading=None, content="x", score=0.9)],
    )
    assert g.path == "a.md"
    assert g.search_type == "semantic"
    assert len(g.sections) == 1


@dataclass
class _Row:
    path: str
    heading: str | None
    content: str
    score: float
    start_line: int = 0
    section_id: int = 0


def test_group_by_path_collapses_same_file():
    rows = [
        _Row("a.md", "X", "x1", 0.9, 5),
        _Row("a.md", "Y", "y1", 0.85, 20),
        _Row("b.md", None, "bb", 0.8, 0),
        _Row("a.md", "Z", "z1", 0.7, 50),
    ]
    groups = _group_by_path(rows, chunks_per_file=2, file_limit=10)
    assert [(g[0].path, len(g)) for g in groups] == [("a.md", 2), ("b.md", 1)]
    # a.md should keep its TWO best chunks (X 0.9 and Y 0.85), dropping Z 0.7.
    a_group_scores = sorted([r.score for r in groups[0]], reverse=True)
    assert a_group_scores == [0.9, 0.85]


def test_group_by_path_respects_file_limit():
    rows = [
        _Row("a.md", None, "", 0.9, 0),
        _Row("b.md", None, "", 0.8, 0),
        _Row("c.md", None, "", 0.7, 0),
    ]
    groups = _group_by_path(rows, chunks_per_file=2, file_limit=2)
    assert [g[0].path for g in groups] == ["a.md", "b.md"]


def test_group_by_path_section_ties_sort_by_start_line():
    """When two sections of the same file tie on score, document order wins."""
    rows = [
        _Row("a.md", "Late", "", 0.9, 100),
        _Row("a.md", "Early", "", 0.9, 5),
    ]
    groups = _group_by_path(rows, chunks_per_file=2, file_limit=10)
    headings = [r.heading for r in groups[0]]
    assert headings == ["Early", "Late"], (
        "ties on score should resolve by start_line ASC (document order)"
    )


def test_group_by_path_section_id_breaks_start_line_ties():
    """Chunks tying on both score and start_line resolve by section_id ASC.

    Models word-split fragments of one oversize source line: they share a
    score (uniform term frequency) and a start_line (same source line), so
    only section_id (the sections rowid, monotonic with document order)
    gives a deterministic order.  Input is deliberately section_id-shuffled.
    """
    rows = [
        _Row("a.md", "frag3", "", 0.9, 10, section_id=303),
        _Row("a.md", "frag1", "", 0.9, 10, section_id=101),
        _Row("a.md", "frag2", "", 0.9, 10, section_id=202),
    ]
    groups = _group_by_path(rows, chunks_per_file=5, file_limit=10)
    headings = [r.heading for r in groups[0]]
    assert headings == ["frag1", "frag2", "frag3"], (
        "score+start_line ties must resolve by section_id ASC"
    )


def test_group_by_path_rejects_zero_chunks_per_file():
    import pytest

    with pytest.raises(ValueError, match="chunks_per_file"):
        _group_by_path([], chunks_per_file=0, file_limit=10)


def test_group_by_path_preserves_score_desc_file_order():
    rows = [
        _Row("a.md", None, "", 0.6, 0),
        _Row("b.md", None, "", 0.9, 0),
        _Row("c.md", None, "", 0.7, 0),
    ]
    # Caller is responsible for pre-sorting by score DESC; helper trusts input.
    rows_sorted = sorted(rows, key=lambda r: r.score, reverse=True)
    groups = _group_by_path(rows_sorted, chunks_per_file=1, file_limit=10)
    assert [g[0].path for g in groups] == ["b.md", "c.md", "a.md"]


def test_get_similar_dedupes_multichunk_target(populated_collection):
    """A multi-chunk target document appears only ONCE in get_similar.

    Uses other.md as reference so multi.md (with 3 chunks of "foo" content)
    becomes a candidate.  Without grouping multi.md could appear 3 times in
    the top results; with grouping it appears at most once.
    """
    results = populated_collection.reader.get_similar(
        "other.md", limit=10, chunks_per_file=2
    )
    paths = [r.path for r in results]
    # multi.md should appear at most once even though it has 3 sections
    assert paths.count("multi.md") <= 1, (
        f"multi.md should be deduplicated by field collapsing; got {paths}"
    )
    assert len(paths) == len(set(paths)), (
        f"all result paths should be unique after grouping, got {paths}"
    )
    if results:
        # File score = max(section.score) — invariant on each result
        for r in results:
            assert r.sections, f"{r.path} has empty sections"
            assert r.score == max(s.score for s in r.sections)


def test_get_context_similar_returns_grouped_results(populated_collection):
    """get_context.similar is GroupedResult-shaped with one section per file."""
    from markdown_vault_mcp.types import GroupedResult

    ctx = populated_collection.reader.get_context("multi.md", similar_limit=5)
    for entry in ctx.similar:
        assert isinstance(entry, GroupedResult)
        assert len(entry.sections) == 1, (
            f"get_context.similar should default to chunks_per_file=1; got "
            f"{len(entry.sections)} sections for {entry.path}"
        )


def test_simitem_removed():
    """SimilarItem is no longer exported (#469)."""
    import pytest

    with pytest.raises(ImportError):
        from markdown_vault_mcp.types import SimilarItem  # noqa: F401


def test_sabsa_repro_one_reference_doc_dedups_target_chunks(tmp_path):
    """Regression for issue #469 user report.

    A short reference doc has its best similar match in a long doc with
    multiple H2 sections covering the same topic.  Before #469, the long
    doc occupied 4 of 5 returned slots; after, it appears once.

    Note: ``MockEmbeddingProvider`` returns hash-seeded random vectors, so the
    *exact* similarity ordering between ``ref.md`` and ``long.md``'s chunks
    isn't guaranteed by the chunk content.  What is guaranteed is the file-
    collapsing invariant: regardless of how chunks rank, ``long.md`` must
    appear at most once in the result list when its multiple chunks would
    otherwise occupy several slots.
    """
    from markdown_vault_mcp.collection import Collection
    from tests.conftest import MockEmbeddingProvider

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "ref.md").write_text(
        "# Reference\n\n" + ("SABSA enterprise security architecture.\n" * 12)
    )
    (vault / "long.md").write_text(
        "# Long\n\n## Section A\n\n"
        + ("SABSA security architecture details.\n" * 12)
        + "\n## Section B\n\n"
        + ("More SABSA enterprise material.\n" * 12)
        + "\n## Section C\n\n"
        + ("SABSA framework discussion.\n" * 12)
        + "\n## Section D\n\n"
        + ("Security architecture deep dive.\n" * 12)
    )
    (vault / "other.md").write_text(
        "# Other\n\n" + ("Something different about reading lists.\n" * 12)
    )

    col = Collection(
        source_dir=vault,
        embedding_provider=MockEmbeddingProvider(),
        embeddings_path=tmp_path / "vectors",
        max_chunk_words=20,  # force adaptive chunking to split long.md
    )
    col.index.build_index()
    col.index.build_embeddings()

    # Sanity check: long.md must actually produce multiple chunks for the
    # regression test to be meaningful.  If it doesn't, fixture sizing or
    # the adaptive chunker has drifted and the test should be updated.
    long_chunks = [m for m in col._vectors._metadata if m["path"] == "long.md"]
    assert len(long_chunks) >= 2, (
        f"long.md must produce >= 2 chunks for this regression to be "
        f"meaningful; got {len(long_chunks)} chunks "
        f"(headings={[m['heading'] for m in long_chunks]})"
    )

    results = col.reader.get_similar("ref.md", limit=5)
    paths = [r.path for r in results]
    # long.md appears at most once even though it generated multiple chunks
    assert paths.count("long.md") <= 1, (
        f"long.md should be deduplicated by file collapsing; got {paths}"
    )
    # Result paths are unique (file collapsing invariant)
    assert len(set(paths)) == len(paths), f"paths should be unique: {paths}"


def test_get_similar_skips_length_downweight(monkeypatch, populated_collection):
    """#472: get_similar must call _apply_length_downweight with alpha=0.0."""
    from markdown_vault_mcp.managers import search as search_mod

    captured_alphas: list[float] = []
    original = search_mod._apply_length_downweight

    def spy(rows, *, alpha):
        captured_alphas.append(alpha)
        return original(rows, alpha=alpha)

    monkeypatch.setattr(search_mod, "_apply_length_downweight", spy)

    populated_collection.reader.get_similar("multi.md", limit=5)

    assert captured_alphas, (
        "expected get_similar to call _apply_length_downweight at least once"
    )
    assert all(a == 0.0 for a in captured_alphas), (
        f"get_similar called _apply_length_downweight with non-zero alpha; "
        f"got {captured_alphas}.  See #472."
    )


def test_get_context_similar_skips_length_downweight(monkeypatch, populated_collection):
    """#472: get_context.similar inherits alpha=0.0 via get_similar delegation."""
    from markdown_vault_mcp.managers import search as search_mod

    captured_alphas: list[float] = []
    original = search_mod._apply_length_downweight

    def spy(rows, *, alpha):
        captured_alphas.append(alpha)
        return original(rows, alpha=alpha)

    monkeypatch.setattr(search_mod, "_apply_length_downweight", spy)

    populated_collection.reader.get_context("multi.md", similar_limit=3)

    assert captured_alphas, (
        "expected get_context to trigger _apply_length_downweight via get_similar"
    )
    assert all(a == 0.0 for a in captured_alphas), (
        f"get_context.similar called _apply_length_downweight with non-zero "
        f"alpha; got {captured_alphas}.  See #472."
    )
