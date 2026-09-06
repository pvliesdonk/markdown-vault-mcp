"""A link does not span a paragraph boundary (#1334).

The inline-link, reference-usage and wikilink patterns used to run over the
whole code-stripped body, so a stray ``[`` paired with a ``](`` paragraphs
later and indexed pages of prose as link text. Extraction now walks the body
line by line into regions and matches inside each region; reference
*definitions* stay document-wide.

Every case below is a row of the decision table in
``docs/design/reference/commonmark-gfm.md`` ("Paragraph boundaries the
scanner should honour"): a stray ``[`` across the boundary pairs with
nothing, a link written *on* a content-carrying boundary line survives, and
a spec continuation line does not split a link. The two deliberate
departures (every ordered marker opens a region; line endings are
normalised) are pinned here as the known cost, not hidden.
"""

from __future__ import annotations

import pytest

from markdown_vault_mcp.scanner import extract_links

SRC = "notes/source.md"


def targets(content: str) -> list[str]:
    """Return the raw targets extracted from *content*, in order."""
    return [link.raw_target for link in extract_links(content, SRC)]


def texts(content: str) -> list[str]:
    """Return the link texts extracted from *content*, in order."""
    return [link.link_text for link in extract_links(content, SRC)]


# ---------------------------------------------------------------------------
# The issue's reproduction
# ---------------------------------------------------------------------------


class TestIssueReproduction:
    """The report's inputs, exactly."""

    def test_a_stray_bracket_does_not_pair_with_a_later_image(self) -> None:
        content = (
            "See item [3 below.\n\nSome other paragraph.\n\n"
            "An image: ![Image](images/pic.png)\n"
        )
        assert extract_links(content, SRC) == []

    def test_a_stray_bracket_does_not_pair_with_a_later_link(self) -> None:
        content = (
            "See item [3 below.\n\nSome other paragraph.\n\nA link: [Image](other.md)\n"
        )
        assert texts(content) == ["Image"]

    def test_a_destination_never_contains_a_line_ending(self) -> None:
        content = "the [Regulation](.\n2. The Specific Programme has:\n3. (a)\n"
        assert extract_links(content, SRC) == []

    def test_a_destination_on_its_own_line_is_the_known_cost(self) -> None:
        # CommonMark allows one line ending inside the parentheses around a
        # destination (§6.3); the scanner does not, since the same shape is
        # what the converted-PDF defect produced. Pinned, not hidden.
        assert extract_links("[a](\nnote.md\n)", SRC) == []


# ---------------------------------------------------------------------------
# Separators: a stray ``[`` before, ``](x.md)`` after, nothing pairs
# ---------------------------------------------------------------------------


SEPARATORS = {
    "blank line": "\n\n",
    "whitespace-only line": "\n \t \n",
    "bare quote marker": "\n>\n",
    "bare quote marker with trailing space": "\n> \n",
    "thematic break stars": "\n***\n",
    "thematic break dashes": "\n---\n",
    "thematic break underscores": "\n_ _ _\n",
    "setext underline equals": "\n===\n",
    "setext underline single dash": "\n-\n",
    "atx heading": "\n## Heading\n",
    "atx heading indented three spaces": "\n   # Heading\n",
    "empty atx heading": "\n#\n",
}


class TestSeparators:
    """Boundary lines that carry no link content of their own."""

    @pytest.mark.parametrize("separator", SEPARATORS.values(), ids=SEPARATORS)
    def test_a_stray_bracket_does_not_cross_it(self, separator: str) -> None:
        content = f"See [item{separator}after: [real](note.md)"
        assert texts(content) == ["real"]

    @pytest.mark.parametrize("separator", SEPARATORS.values(), ids=SEPARATORS)
    def test_a_reference_usage_does_not_cross_it(self, separator: str) -> None:
        content = f"See [item{separator}after: [real][r]\n\n[r]: note.md\n"
        assert texts(content) == ["real"]

    @pytest.mark.parametrize("separator", SEPARATORS.values(), ids=SEPARATORS)
    def test_a_wikilink_does_not_cross_it(self, separator: str) -> None:
        content = f"See [[item{separator}after: [[real]]"
        assert targets(content) == ["real"]


# ---------------------------------------------------------------------------
# Openers: the line starts a new region and keeps its own links
# ---------------------------------------------------------------------------


OPENERS = {
    "quote line with text": "> ",
    "bullet dash": "- ",
    "bullet plus": "+ ",
    "bullet star": "* ",
    "ordered dot": "1. ",
    "ordered paren": "1) ",
    "ordered not starting at one": "2. ",
}


class TestOpeners:
    """Boundary lines that carry inline content: the #1348 requirement."""

    @pytest.mark.parametrize("marker", OPENERS.values(), ids=OPENERS)
    def test_a_stray_bracket_does_not_cross_it(self, marker: str) -> None:
        content = f"See [item\n{marker}after: [real](note.md)"
        assert texts(content) == ["real"]

    @pytest.mark.parametrize("marker", OPENERS.values(), ids=OPENERS)
    def test_a_link_on_the_line_survives(self, marker: str) -> None:
        content = f"intro\n{marker}see [target](note.md)\nbody"
        assert targets(content) == ["note.md"]

    def test_a_link_on_a_heading_line_survives(self) -> None:
        # The exact input the second attempt lost (#1348).
        assert targets("intro\n# See [target](note.md)\nbody") == ["note.md"]

    def test_a_wikilink_on_a_heading_line_survives(self) -> None:
        content = "Some text.\n## Related: [[other note]]\n\nBody [link2](x.md)."
        assert targets(content) == ["x.md", "other note"]

    def test_a_heading_is_a_region_by_itself(self) -> None:
        # A stray ``[`` on the heading line pairs with nothing below it.
        assert texts("# Stray [here\nbody [real](note.md)") == ["real"]

    def test_the_ordered_marker_departure_is_the_known_cost(self) -> None:
        # §5.2 Ex. 304 makes ``2. text`` after prose continuation text; the
        # scanner opens a region on every ordered marker so a stray ``[`` in
        # one converted-PDF list item cannot pair into the next. The link
        # split here is the price, pinned so it is a decision and not a bug.
        assert extract_links("[a\n2. b](note.md)", SRC) == []


# ---------------------------------------------------------------------------
# Continuation lines: the spec keeps the paragraph going, so does the scanner
# ---------------------------------------------------------------------------


CONTINUATIONS = {
    "soft break": "b",
    "indented four spaces": "    b",
    "html type-7 tag": "<span>b</span>",
    "empty bullet item": "*",
    "table-looking row": "| b |",
    "bullet indented four spaces": "    - b",
}


class TestContinuations:
    """Rows the table marks 'not a boundary': one link across the break."""

    @pytest.mark.parametrize("line", CONTINUATIONS.values(), ids=CONTINUATIONS)
    def test_one_link_survives_across_it(self, line: str) -> None:
        content = f"[a\n{line}](note.md)"
        assert targets(content) == ["note.md"]

    def test_consecutive_quote_lines_are_one_paragraph(self) -> None:
        assert targets("> [a\n> b](note.md)") == ["note.md"]

    def test_a_lazy_continuation_of_a_quote_is_the_same_paragraph(self) -> None:
        assert targets("> [a\nb](note.md)") == ["note.md"]

    def test_an_indented_continuation_of_an_item_is_the_same_paragraph(
        self,
    ) -> None:
        assert targets("- [a\n  b](note.md)") == ["note.md"]

    def test_a_second_item_is_a_new_region(self) -> None:
        assert extract_links("- [a\n- b](note.md)", SRC) == []

    def test_a_quote_interrupts_a_paragraph(self) -> None:
        assert extract_links("[a\n> b](note.md)", SRC) == []

    def test_table_cells_keep_their_links(self) -> None:
        content = "| a | b |\n|---|---|\n| [t](n.md) | [[w]] |\n"
        assert targets(content) == ["n.md", "w"]


# ---------------------------------------------------------------------------
# Reference definitions stay document-wide
# ---------------------------------------------------------------------------


class TestReferenceDefinitionsAreDocumentWide:
    def test_a_definition_after_a_blank_line_resolves_an_earlier_usage(
        self,
    ) -> None:
        assert targets("See [t][r].\n\n[r]: note.md\n") == ["note.md"]

    def test_a_definition_before_a_blank_line_resolves_a_later_usage(
        self,
    ) -> None:
        assert targets("[r]: note.md\n\nSee [t][r].\n") == ["note.md"]

    def test_a_definition_in_another_region_kind_still_resolves(self) -> None:
        content = "# Heading\n\n[r]: note.md\n\n- See [t][r].\n"
        assert targets(content) == ["note.md"]

    def test_a_usage_text_does_not_cross_a_blank_line(self) -> None:
        content = "[stray\n\n[t][r]\n\n[r]: note.md\n"
        assert texts(content) == ["t"]


# ---------------------------------------------------------------------------
# Line endings
# ---------------------------------------------------------------------------


class TestLineEndings:
    """CRLF and lone CR are line endings (§2.1); the scanner normalises them."""

    @pytest.mark.parametrize("nl", ["\r\n", "\r"], ids=["crlf", "cr"])
    def test_a_blank_line_in_any_spelling_is_a_boundary(self, nl: str) -> None:
        content = f"See [item{nl}{nl}after: [real](note.md){nl}"
        assert texts(content) == ["real"]

    @pytest.mark.parametrize("nl", ["\r\n", "\r"], ids=["crlf", "cr"])
    def test_a_heading_in_any_spelling_is_a_boundary(self, nl: str) -> None:
        content = f"See [item{nl}## Heading{nl}after: [real](note.md){nl}"
        assert texts(content) == ["real"]

    @pytest.mark.parametrize("nl", ["\r\n", "\r"], ids=["crlf", "cr"])
    def test_a_reference_definition_is_found_in_any_spelling(self, nl: str) -> None:
        content = f"See [t][r].{nl}{nl}[r]: note.md{nl}"
        assert targets(content) == ["note.md"]

    def test_link_text_carries_no_carriage_return(self) -> None:
        assert texts("[a\r\nb](note.md)") == ["a\nb"]

    def test_a_destination_never_contains_a_carriage_return(self) -> None:
        assert extract_links("[a](note\r.md)", SRC) == []


# ---------------------------------------------------------------------------
# What is preserved
# ---------------------------------------------------------------------------


class TestPreserved:
    def test_the_result_stays_grouped_by_kind(self) -> None:
        content = "[[w]] then [a](x.md) then [t][r]\n\n[r]: y.md\n\n[b](z.md) [[v]]"
        assert [link.link_type for link in extract_links(content, SRC)] == [
            "markdown",
            "markdown",
            "reference",
            "wikilink",
            "wikilink",
        ]

    def test_a_soft_break_inside_link_text_is_kept(self) -> None:
        assert texts("[first\nsecond](note.md)") == ["first\nsecond"]

    def test_an_image_at_the_start_of_a_region_is_still_an_image(self) -> None:
        assert extract_links("intro\n\n![alt](pic.md)", SRC) == []

    def test_a_link_whose_text_holds_a_fence_is_bounded_by_the_fence(
        self,
    ) -> None:
        # Fenced code is stripped before regions are formed, as before.
        content = "[a\n\n```\n[b](c.md)\n```\n\nd](note.md)"
        assert extract_links(content, SRC) == []
