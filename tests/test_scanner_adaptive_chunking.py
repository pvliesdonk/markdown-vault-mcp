"""Tests for HeadingChunker's adaptive H-level refinement."""

from __future__ import annotations

from markdown_vault_mcp.scanner import HeadingChunker


def _doc(*sections: tuple[int, str, int]) -> str:
    """Build a markdown doc from (level, heading, body_word_count) tuples."""
    parts: list[str] = []
    for level, heading, words in sections:
        parts.append("#" * level + " " + heading)
        parts.append(" ".join(["lorem"] * words))
        parts.append("")
    return "\n".join(parts) + "\n"


def test_max_chunk_words_none_preserves_h1_h2_only_behavior():
    """max_chunk_words=None keeps today's H1/H2-only splitting."""
    # Use one word per line so the fixture clears the 30-line short-doc bypass.
    top_body = "\n".join(["lorem"] * 15)
    sub_a_body = "\n".join(["lorem"] * 15)
    deep_body = "\n".join(["lorem"] * 800)
    body = (
        f"# Top\n{top_body}\n"
        f"## Sub A\n{sub_a_body}\n"
        f"### Deep\n{deep_body}\n"  # H3, would not split today and must not split.
    )
    chunker = HeadingChunker(max_chunk_words=None)
    chunks = chunker.chunk(body, {})
    # H1 + H2 produce 2 chunks; H3 stays inside the H2 chunk.
    assert len(chunks) == 2
    headings = [c.heading for c in chunks]
    assert headings == ["Top", "Sub A"]


def test_oversize_h1_splits_at_h2():
    """An H1 chunk exceeding max_chunk_words is re-split at H2."""
    # Sub_body sized to fit within the budget once split at H2, isolating
    # the H2-split mechanism from the word-budget fallback.
    sub_body = "\n".join(["lorem"] * 50)
    body = f"# Top\n## Sub A\n{sub_body}\n## Sub B\n{sub_body}\n"
    chunker = HeadingChunker(max_chunk_words=200)
    chunks = chunker.chunk(body, {})
    assert [c.heading for c in chunks] == ["Sub A", "Sub B"]
    assert all(c.heading_level == 2 for c in chunks)


def test_recursion_descends_to_h6():
    """An H1 chunk with deeply nested oversized sub-headings descends to H6."""
    # Use a long enough body to clear the 30-line short-doc bypass.
    long_body = "\n".join(["lorem"] * 50)  # 50 lines per body
    body = (
        f"# L1\n{long_body}\n"
        f"## L2\n{long_body}\n"
        f"### L3\n{long_body}\n"
        f"#### L4\n{long_body}\n"
        f"##### L5\n{long_body}\n"
        "###### L6a\n" + " ".join(["lorem"] * 50) + "\n"
        "###### L6b\n" + " ".join(["lorem"] * 50) + "\n"
    )
    chunker = HeadingChunker(max_chunk_words=40)
    chunks = chunker.chunk(body, {})
    headings = [c.heading for c in chunks]
    assert "L6a" in headings and "L6b" in headings


def test_oversize_h6_leaf_falls_back_to_word_budget_split():
    """An oversize H6 leaf with no deeper headings splits on word budget."""
    body = "###### Solo\n" + "\n".join(["lorem"] * 100) + "\n"
    chunker = HeadingChunker(max_chunk_words=50)
    chunks = chunker.chunk(body, {})
    assert len(chunks) >= 2
    assert all(c.heading == "Solo" for c in chunks)
    assert all(c.heading_level == 6 for c in chunks)
    assert all(len(c.content.split()) <= 50 for c in chunks)


def test_oversize_preamble_falls_back_to_word_budget_split():
    """Preamble (no heading) that exceeds the budget is fragmented."""
    # One word per line so the body clears the 30-line short-doc bypass and
    # the document enters the heading-split path, putting the long preamble
    # into the preamble-leaf branch of _refine_oversize.
    preamble_words = 1000
    body = (
        "\n".join(["lorem"] * preamble_words)
        + "\n# Heading\n"
        + "\n".join(["ipsum"] * 50)
        + "\n"
    )
    chunker = HeadingChunker(max_chunk_words=200)
    chunks = chunker.chunk(body, {})
    preamble_chunks = [c for c in chunks if c.heading is None]
    assert len(preamble_chunks) >= 2
    assert all(len(c.content.split()) <= 200 for c in preamble_chunks)
    total = sum(len(c.content.split()) for c in preamble_chunks)
    assert total == preamble_words


def test_no_headings_oversize_doc_falls_back_to_word_budget_split():
    """A long doc with no headings at all still respects the budget."""
    # 60 lines (clears 30-line short-doc bypass), no markdown headings.
    body = "\n".join(["lorem"] * 60) + "\n"
    chunker = HeadingChunker(max_chunk_words=20)
    chunks = chunker.chunk(body, {})
    assert len(chunks) >= 2
    assert all(c.heading is None for c in chunks)
    assert all(len(c.content.split()) <= 20 for c in chunks)
    assert sum(len(c.content.split()) for c in chunks) == 60


def test_subsplit_preamble_inherits_parent_heading():
    """A parent section's body-before-first-subheading inherits the parent's
    heading attribution rather than dropping to None when the parent's
    content recurses to a deeper heading level.

    Uses three heading levels so the recursion path actually fires: H1/H2
    are handled at the top-level `_split_at_levels(levels=(1, 2))`, but
    the "Parent" H2 chunk exceeds budget and recurses with `levels=(3,)`.
    Inside that recursion `_split_at_levels` returns a preamble with
    `heading=None`; the fix promotes it to inherit Parent's heading.
    Without the fix the test would observe `heading=None` on those
    preamble chunks.
    """
    body = (
        "# Top\n"
        + "\n".join(["top body"] * 5)
        + "\n## Parent\n"
        + "\n".join(["parent preamble"] * 15)  # 30 words → preamble of Parent
        + "\n### Child\n"
        + "\n".join(["child body"] * 20)  # 40 words under Child
        + "\n"
    )
    chunker = HeadingChunker(max_chunk_words=50)
    chunks = chunker.chunk(body, {})
    preamble_chunks = [c for c in chunks if "parent preamble" in c.content]
    assert preamble_chunks, "preamble text should appear in at least one chunk"
    for c in preamble_chunks:
        assert c.heading == "Parent", (
            f"preamble chunk got heading={c.heading!r}, expected 'Parent'"
        )


def test_budget_split_preserves_line_structure_in_oversize_paragraph():
    """An oversize paragraph keeps line boundaries — tables stay tabular."""
    # 30 consecutive table rows (no blank lines → one paragraph).  Each
    # row "| alpha | beta | gamma |" tokenises to 7 words (pipes count as
    # tokens via str.split()), so total = 210 words.  Budget=60 → roughly
    # 8 rows per chunk; word-split would strip every newline, line-bin-pack
    # keeps them so the table still renders.
    rows = ["| alpha | beta | gamma |"] * 30
    body = "\n".join(rows) + "\n"
    chunker = HeadingChunker(max_chunk_words=60)
    chunks = chunker.chunk(body, {})
    assert all("|" in c.content for c in chunks)
    assert all("\n" in c.content for c in chunks), (
        "line-structured oversize paragraph lost newlines — word-split fell back"
    )
    assert all(len(c.content.split()) <= 60 for c in chunks)


def test_budget_split_word_splits_only_individual_oversize_lines():
    """Word-split fires only for a single line that itself exceeds budget;
    surrounding normal lines line-bin-pack and keep their newlines.

    Distinguishes the new line-level-first fallback from the previous
    global word-split: a regression that reverts to "join all lines and
    word-split" would strip newlines from the surrounding short lines.
    """
    short_lines = [f"a{i} b{i} c{i}" for i in range(10)]  # 10 lines, 3 words each
    oversize = " ".join([f"x{i}" for i in range(200)])  # 1 line, 200 words
    body = "\n".join([*short_lines, oversize, *short_lines]) + "\n"
    chunker = HeadingChunker(max_chunk_words=50)
    chunks = chunker.chunk(body, {})
    assert all(len(c.content.split()) <= 50 for c in chunks)
    # Some chunks must have internal newlines (line-bin-packed short lines)
    # AND some must not (word-split fragments of the single oversize line).
    has_newline = [c for c in chunks if "\n" in c.content]
    no_newline = [c for c in chunks if "\n" not in c.content]
    assert has_newline, "short-line bin-pack output lost newlines"
    assert no_newline, "no word-split chunk emitted for the oversize single line"


def test_budget_split_preserves_blank_line_separators():
    """Bin-packed paragraphs keep their blank-line separators in chunk content.

    Four 10-word paragraphs (40 words total) with budget=25 force
    `_budget_split` to fire (40 > 25) and produce two chunks each
    bin-packing two paragraphs (10 + 10 = 20, no emit; +10 = 30 > 25,
    emit).  Each emitted chunk must preserve the inter-paragraph blank
    line — without the fix the separator gets stripped during paragraph
    collection and `pending_lines.extend(lines)` joins the line-ended
    paragraphs with no blank between them.
    """
    paragraphs = [
        "\n".join([f"{tag}{i}" for i in range(10)])
        for tag in ("alpha", "beta", "gamma", "delta")
    ]
    body = "\n\n".join(paragraphs) + "\n"

    chunker = HeadingChunker(max_chunk_words=25)
    chunks = chunker.chunk(body, {})

    assert len(chunks) == 2
    assert all("\n\n" in c.content for c in chunks), (
        "blank line between bin-packed paragraphs was lost"
    )


def test_budget_split_bin_packs_small_paragraphs():
    """Sub-budget paragraphs accumulate then flush before the next overflow."""
    # Build a body with 6 distinct paragraphs of ~40 words each, separated by
    # blank lines so the paragraph collector sees them as separate units.
    # 40 lines per paragraph + blank line * 6 paragraphs clears 30-line bypass.
    paragraphs = []
    for tag in range(6):
        words = [f"p{tag}word{i}" for i in range(40)]
        paragraphs.append("\n".join(words))
    body = ("\n\n".join(paragraphs)) + "\n"

    chunker = HeadingChunker(max_chunk_words=100)
    chunks = chunker.chunk(body, {})

    # No paragraph (40 words) exceeds the budget, so word-split never fires.
    # Pairs of paragraphs (80 words) fit; adding a third (120) would overflow,
    # so chunks bin-pack two paragraphs at a time → 3 emitted chunks.
    assert len(chunks) == 3
    assert all(len(c.content.split()) <= 100 for c in chunks)
    # Every fragment is a preamble (no heading) because the body has no headings.
    assert all(c.heading is None for c in chunks)


def test_no_chunk_exceeds_budget_anywhere():
    """Short-doc bypass also honours the budget when content is oversize.

    Each token-stream is space-joined (single line), so the body lands
    under the 30-line short-doc threshold and enters the
    ``len(lines) <= short_doc_lines`` branch.  _budget_split fragments
    the single emitted chunk; assertion checks the budget invariant
    holds on every fragment.
    """
    body = (
        " ".join(["alpha"] * 6000)
        + "\n\n# Big H1\n\n"
        + " ".join(["beta"] * 5000)
        + "\n\n## Small Trailer\n\nshort\n"
    )
    chunker = HeadingChunker(max_chunk_words=300)
    chunks = chunker.chunk(body, {})
    assert all(len(c.content.split()) <= 300 for c in chunks)


def test_short_doc_bypass_still_applies():
    """Documents <= 30 lines return as one chunk."""
    body = "# A\nbody\n## B\nmore body\n"
    chunker = HeadingChunker(max_chunk_words=10)
    chunks = chunker.chunk(body, {})
    assert len(chunks) == 1


def test_heading_level_and_start_line_propagated_through_recursion():
    """Refined sub-chunks carry correct heading_level and start_line."""
    # Use one word per line so the fixture clears the 30-line short-doc bypass.
    inner_body = "\n".join(["lorem"] * 300)
    body = f"# Top\n## Inner\n{inner_body}\n"
    chunker = HeadingChunker(max_chunk_words=200)
    chunks = chunker.chunk(body, {})
    inner = next(c for c in chunks if c.heading == "Inner")
    assert inner.heading_level == 2
    # start_line points at the H2 line in the document.
    body_lines = body.splitlines()
    assert body_lines[inner.start_line].lstrip().startswith("## Inner")


def test_legacy_mode_h3_only_doc_returns_single_chunk_no_heading():
    """Legacy mode (max_chunk_words=None) preserves pre-PR behaviour:
    a doc with only H3 headings returns one chunk with heading=None
    (rather than being split at H3, which is the adaptive-mode behaviour).

    Body uses 20 lines per section (one word per line) to clear the
    30-line short-doc bypass, so the test exercises the H1/H2-only
    heading logic rather than the short-doc fast-path.
    """
    body = (
        "### A\n"
        + "\n".join(["body"] * 20)
        + "\n### B\n"
        + "\n".join(["word"] * 20)
        + "\n"
    )
    chunker = HeadingChunker(max_chunk_words=None)
    chunks = chunker.chunk(body, {})
    assert len(chunks) == 1
    assert chunks[0].heading is None


def test_adaptive_mode_h3_only_doc_splits_at_h3():
    """Adaptive mode descends to H3 when H1/H2 are absent.

    Body uses 20 lines per section (one word per line) to clear the
    30-line short-doc bypass, which would otherwise return a single chunk
    before the H3-descent code is reached.
    """
    body = (
        "### A\n"
        + "\n".join(["body"] * 20)
        + "\n### B\n"
        + "\n".join(["word"] * 20)
        + "\n"
    )
    chunker = HeadingChunker(max_chunk_words=400)
    chunks = chunker.chunk(body, {})
    assert len(chunks) == 2
    assert {c.heading for c in chunks} == {"A", "B"}
    assert all(c.heading_level == 3 for c in chunks)
