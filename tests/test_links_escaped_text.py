"""A backslash in link text does not close it (#1517).

``[a\\]b](x.md)`` is a valid CommonMark link — §6.3 reads link text with
escapes honoured, so the ``\\]`` is a literal ``]`` and the text runs on.
The opener used to be matched by ``\\[([^\\]]*)\\]\\(``, a class that cannot
see a backslash, so that link produced no row at all: no outlink on the
note, no backlink on the target. The generated output of #1513 landed in
exactly that hole, rendering everywhere and reaching the graph nowhere.

This module carries the defect itself: such a link is found, with its
target resolved.

It used to carry a second property — that the scan agreed with the class
it replaced on every backslash-free input, the "nothing else moved" claim
the #1517 bump rested on. That property is gone on purpose. #1526 adopted
CommonMark's rule that a ``]`` closes the *nearest* unmatched ``[``, which
deliberately disagrees with the old class on nested brackets, so agreeing
with it is no longer a property worth having. The successor lives in
``tests/test_links_commonmark_openers.py`` and compares against a
CommonMark reader rather than against a superseded regex, which is the
stronger claim and the one a semantics note should rest on.
"""

from __future__ import annotations

from markdown_vault_mcp.scanner import extract_links

SRC = "source.md"


def _links(content: str) -> list[tuple[str, str]]:
    """``(link_text, raw_target)`` for every link in *content*."""
    return [(link.link_text, link.raw_target) for link in extract_links(content, SRC)]


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


class TestEscapedBracketsInLinkText:
    def test_an_escaped_closing_bracket_no_longer_ends_the_text(self) -> None:
        assert _links(r"[Bra\]cket](x.md)") == [(r"Bra\]cket", "x.md")]

    def test_an_escaped_pair_is_read_through(self) -> None:
        assert _links(r"[a \[b\] c](x.md)") == [(r"a \[b\] c", "x.md")]

    def test_the_target_resolves_like_any_other_link(self) -> None:
        (link,) = extract_links(r"[Bra\]cket](notes/x.md)", "sub/src.md")
        assert (link.target_path, link.link_type) == ("sub/notes/x.md", "markdown")

    def test_a_second_link_after_an_escaped_one_is_still_found(self) -> None:
        # The scan resumes after the destination it just parsed, so the
        # first link does not swallow the second.
        assert _links(r"[a](x.md) and [b\]c](y.md)") == [
            ("a", "x.md"),
            (r"b\]c", "y.md"),
        ]

    def test_text_ending_in_an_escaped_bracket_closes_no_link(self) -> None:
        # The opposite spelling, and the rows this bump drops: in
        # ``[a\](x.md)`` the ``]`` is escaped, so CommonMark never closes
        # the text and there is no link. The old class read it as one.
        assert _links(r"[a\](x.md)") == []

    def test_an_escaped_bracket_does_not_open_a_link_either(self) -> None:
        assert _links(r"\[a](x.md)") == []

    def test_an_image_is_still_skipped(self) -> None:
        assert _links(r"![Bra\]cket](x.png)") == []


class TestWhatIsNotClaimed:
    def test_link_text_keeps_the_backslash_as_written(self) -> None:
        # ``raw_target`` keeps the destination's spelling, and link_text
        # keeps the text's, for the same reason: what the file holds is
        # what a rewrite has to search for. Decoding it is a separate
        # question from whether the link is found.
        assert _links(r"[a\]b](x.md)") == [(r"a\]b", "x.md")]

    def test_balanced_brackets_are_a_link_now(self) -> None:
        # This was the departure #1517 left standing, on the ground that
        # balancing needs counting rather than escape-awareness. True, and
        # #1526 did the counting: a ``]`` closes the nearest unmatched
        # ``[``, so the inner pair is part of the text.
        assert _links("[Note [draft]](x.md)") == [("Note [draft]", "x.md")]


# ---------------------------------------------------------------------------
# The property the bump rests on
# ---------------------------------------------------------------------------


class TestTheScanStaysLinear:
    def test_a_paragraph_of_escaped_brackets_does_not_blow_up(self) -> None:
        # The reason the opener is a scan and not a wider character class:
        # an escape-aware class has to read past every escaped ``]``, so the
        # engine's retry from each ``[`` turns quadratic and this input cost
        # seconds. A bound rather than a timing assertion, so the test does
        # not go flaky on a slow runner — the quadratic version exceeded it
        # by three orders of magnitude.
        region = "[a\\]b" * 8000
        assert extract_links(region, SRC) == []
