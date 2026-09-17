"""A backslash in link text does not close it (#1517).

``[a\\]b](x.md)`` is a valid CommonMark link — §6.3 reads link text with
escapes honoured, so the ``\\]`` is a literal ``]`` and the text runs on.
The opener used to be matched by ``\\[([^\\]]*)\\]\\(``, a class that cannot
see a backslash, so that link produced no row at all: no outlink on the
note, no backlink on the target. The generated output of #1513 landed in
exactly that hole, rendering everywhere and reaching the graph nowhere.

Two properties carry this module. The first is the defect itself: such a
link is now found, with its target resolved. The second is what makes the
change safe to ship against existing vaults — the scan that replaced the
class agrees with it on every input that carries no backslash, so a note
without one indexes exactly as before. The second is asserted as a
differential property over *every* short bracket string rather than a
handful of examples, because "nothing else moved" is the claim an
``INDEX_SEMANTICS_VERSION`` bump rests on.

What this module does *not* claim: that link text is bracket-balanced.
``[a [b] c](x.md)`` is a link for CommonMark and still is not one here —
a departure recorded in ``docs/design/design.md`` and tracked separately,
because it needs counting rather than escape-awareness.
"""

from __future__ import annotations

import itertools
import re

import pytest

from markdown_vault_mcp.scanner import _find_inline_link_open, extract_links

SRC = "source.md"

#: The class the scan replaced. Kept here, not imported, precisely because
#: it no longer exists in the scanner: the differential property below is
#: only meaningful against the spelling that actually shipped.
_SUPERSEDED_OPENER = re.compile(r"\[([^\]]*)\]\(")


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

    def test_balanced_brackets_are_still_not_a_link_here(self) -> None:
        # A departure, not an oversight: CommonMark links this and the
        # scanner does not, because balancing needs counting rather than
        # escape-awareness. Stated so a reader is not misled into thinking
        # #1517 covered it.
        assert _links("[Note [draft]](x.md)") == []


# ---------------------------------------------------------------------------
# The property the bump rests on
# ---------------------------------------------------------------------------


class TestTheScanAgreesWithTheClassItReplaced:
    """Backslash-free input indexes exactly as it did before the bump."""

    @pytest.mark.parametrize("length", range(8), ids=lambda n: f"len{n}")
    def test_no_backslash_free_input_changed_meaning(self, length: int) -> None:
        # Exhaustive rather than sampled: every string of this length over
        # the characters the opener can distinguish — a bracket that opens,
        # one that closes, the paren that must follow, its partner, and a
        # filler standing for everything else. No backslash appears, so the
        # two must agree on all of them.
        for chars in itertools.product("[]()a", repeat=length):
            text = "".join(chars)
            match = _SUPERSEDED_OPENER.search(text)
            expected = (match.start(), match.end(), match.group(1)) if match else None
            assert _find_inline_link_open(text, 0) == expected, text

    @pytest.mark.parametrize(
        "text",
        [
            "[a](x.md)",
            "[[a](x.md)",
            "[a]b[c](x.md)",
            "[a]b](x.md)",
            "[](x.md)",
            "[a [b] c](x.md)",
            "no brackets at all",
            "[unclosed",
            "](x.md)",
        ],
        ids=[
            "plain",
            "double-open",
            "failed-then-found",
            "closer-without-opener",
            "empty-text",
            "inner-brackets",
            "none",
            "unclosed",
            "closer-first",
        ],
    )
    def test_the_named_shapes_agree_too(self, text: str) -> None:
        match = _SUPERSEDED_OPENER.search(text)
        expected = (match.start(), match.end(), match.group(1)) if match else None
        assert _find_inline_link_open(text, 0) == expected

    def test_a_backslash_free_note_indexes_identically(self) -> None:
        note = (
            "# Title\n\n"
            "See [one](a.md) and [two](b.md).\n\n"
            "An ![image](c.png) and a [broken] bracket](d.md).\n"
        )
        assert _links(note) == [("one", "a.md"), ("two", "b.md")]


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
