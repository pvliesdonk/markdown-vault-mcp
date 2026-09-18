r"""A backslash in a reference label does not close it (#1519).

``[Bra\]cket][ref]`` is a valid CommonMark reference link — a link label
ends at the first ``]`` that is *not* backslash-escaped — and the scanner
recorded no row for it: no outlink on the note, no backlink on the target.
#1517 fixed the same defect for inline link text and deliberately left the
reference family, whose match shape is two adjacent bracket spans rather
than one followed by ``(``. This module covers what that left.

Both the usage (``[text][ref]``) and the definition (``[ref]: x.md``) sides
are here, because a label spelled with an escape has to survive on both to
match — and the two sides can only ever spell it the same way, since a
label ends at its first unescaped ``]`` and so a label holding one can be
written no other way.

Two properties carry the module, as in ``test_links_escaped_text.py``. The
first is the defect. The second is what makes the change safe against
existing vaults: each scan agrees with the pattern it replaced on every
input carrying no backslash, asserted exhaustively over short strings
rather than by example, because "nothing else moved" is the claim an
``INDEX_SEMANTICS_VERSION`` note rests on.
"""

from __future__ import annotations

import itertools
import re

import pytest

from markdown_vault_mcp.scanner import _iter_reference_definitions, extract_links

SRC = "source.md"


def _links(content: str) -> list[tuple[str, str]]:
    """``(link_text, target_path)`` for every link in *content*."""
    return [(link.link_text, link.target_path) for link in extract_links(content, SRC)]


#: The definition pattern the scan replaced. Kept here, not imported,
#: precisely because it no longer exists in the scanner: the differential
#: below is only meaningful against the spelling that actually shipped.
#:
#: Its usage-side twin is gone. That property — "the usage scan agrees
#: with the pattern it replaced on backslash-free input" — stopped being
#: true, and stopped being the right claim, when #1531 taught the scan the
#: shortcut form: the pattern matched two adjacent spans and nothing else,
#: while the scan now consults the definition table and finds links no
#: regex over the text alone could. Agreement is asserted against a
#: CommonMark reader instead, in
#: ``tests/test_links_reference_openers.py``, where it is exact.
_SUPERSEDED_DEF = re.compile(r"^\s*\[([^\]]+)\]:\s*(.+)$", re.MULTILINE)


def _regex_definitions(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _SUPERSEDED_DEF.finditer(text)]


def _crosses_a_blank_line(text: str) -> bool:
    """True where the superseded pattern read a definition across a blank line.

    The one shape on which the definition scan and the pattern are allowed
    to differ, and the scan is the one that is right. ``\\s*`` let the
    pattern run a label or a destination straight through a blank line, so
    ``[\\n\\n]: a`` was a definition whose label was a paragraph break, and
    ``[:]:`` followed by a blank line took an unrelated later line as its
    target. §6.3 allows neither; #1531 corrected both, because the
    shortcut form was the first thing to ever resolve against a wrong
    entry in that table and turn it into a row.
    """
    return any("\n\n" in match.group(0) for match in _SUPERSEDED_DEF.finditer(text))


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


class TestEscapedBracketsInReferenceLabels:
    def test_an_escaped_bracket_in_the_link_text_no_longer_ends_it(self) -> None:
        assert _links("See [Bra\\]cket][ref].\n\n[ref]: notes/x.md\n") == [
            ("Bra\\]cket", "notes/x.md")
        ]

    def test_an_escaped_bracket_in_the_reference_label_is_read_through(self) -> None:
        assert _links("See [Bracket][r\\]ef].\n\n[r\\]ef]: notes/x.md\n") == [
            ("Bracket", "notes/x.md")
        ]

    def test_both_labels_may_carry_one(self) -> None:
        assert _links("See [a\\]b][r\\]ef].\n\n[r\\]ef]: notes/x.md\n") == [
            ("a\\]b", "notes/x.md")
        ]

    def test_the_collapsed_form_falls_back_to_the_escaped_text(self) -> None:
        # ``[text][]`` looks its own text up as the label, so the text and
        # the definition have to agree on the spelling — which they now do.
        assert _links("See [Bra\\]cket][].\n\n[Bra\\]cket]: notes/x.md\n") == [
            ("Bra\\]cket", "notes/x.md")
        ]

    def test_the_definition_side_alone_was_also_losing_the_row(self) -> None:
        # The usage here needs no escape awareness; the definition does.
        # Without the definition-side fix the label is unknown and the
        # usage resolves to nothing, so this is its own case.
        defs = _iter_reference_definitions("[r\\]ef]: notes/x.md\n")
        assert list(defs) == [("r\\]ef", "notes/x.md")]

    def test_the_target_resolves_like_any_other_link(self) -> None:
        (link,) = extract_links(
            "See [Bra\\]cket][ref].\n\n[ref]: notes/x.md\n", "sub/src.md"
        )
        assert (link.target_path, link.link_type) == ("sub/notes/x.md", "reference")

    def test_a_second_usage_after_an_escaped_one_is_still_found(self) -> None:
        content = "[a][r] and [b\\]c][r]\n\n[r]: x.md\n"
        assert _links(content) == [("a", "x.md"), ("b\\]c", "x.md")]

    def test_two_footnote_references_are_still_not_a_link(self) -> None:
        assert _links("a [^1][^2] b\n\n[^1]: one\n[^2]: two\n") == []


class TestWhatThisModuleOnceDidNotClaim:
    """Two gaps recorded here as standing, and closed by #1531."""

    def test_a_label_ending_in_an_escaped_bracket_links_by_the_shortcut(
        self,
    ) -> None:
        # This module shipped asserting ``== []`` and explaining why: in
        # ``[a\][ref]`` the first ``]`` is escaped, so the label runs on and
        # nothing closes a full reference. A reader still finds a link, by
        # the *shortcut* form ``[ref]`` left over once ``[a\]`` is literal
        # text — and it recorded the reader's exact output while noting the
        # scanner could not produce it.
        #
        # It can now, and it produces precisely what was recorded:
        # [observed: markdown-it-py 'commonmark' renders this input as
        # ``<p>See [a]<a href="notes/x.md">ref</a> here.</p>``, so its link
        # text is ``ref`` and not the label, 2026-09-17]
        assert _links("See [a\\][ref].\n\n[ref]: notes/x.md\n") == [
            ("ref", "notes/x.md")
        ]

    def test_the_shortcut_form_is_extracted_now(self) -> None:
        # The standing gap the case above was an instance of (#1531).
        assert _links("See [ref] here.\n\n[ref]: notes/x.md\n") == [
            ("ref", "notes/x.md")
        ]

    def test_a_label_keeps_the_backslash_as_written(self) -> None:
        # As for inline text and ``raw_target``: what the file holds is
        # what a rewrite has to search for, so nothing is decoded here.
        assert _links("[a\\]b][r]\n\n[r]: x.md\n") == [("a\\]b", "x.md")]


# ---------------------------------------------------------------------------
# The property the semantics note rests on
# ---------------------------------------------------------------------------


class TestTheScansAgreeWithThePatternsTheyReplaced:
    """Backslash-free input indexes exactly as it did before."""

    @pytest.mark.parametrize("length", range(7), ids=lambda n: f"len{n}")
    def test_no_backslash_free_definition_changed_meaning(self, length: int) -> None:
        # The definition alphabet needs four more characters than the
        # usage one: the ``:`` that makes a definition, the newline the
        # multi-line anchors turn on, and a space and a filler for the
        # indent and the target.
        for chars in itertools.product("[]:\na ", repeat=length):
            text = "".join(chars)
            if _crosses_a_blank_line(text):
                continue
            assert list(_iter_reference_definitions(text)) == _regex_definitions(
                text
            ), text

    @pytest.mark.parametrize(
        "text",
        [
            "[r]: x.md\n",
            "   [r]: x.md\n",
            "[r]:\n   x.md\n",
            "[r]: x.md [s]: y.md\n",
            "[a\n[b]: y.md\n",
            "[a] [b]: y.md\n",
            "[]: x.md\n",
            "[r] x.md\n",
            "\u00a0[r]: x.md\n",
        ],
        ids=[
            "plain",
            "indented",
            "target-on-next-line",
            "two-on-one-line",
            "label-spans-a-line",
            "not-first-on-its-line",
            "empty-label",
            "no-colon",
            "unicode-indent",
        ],
    )
    def test_the_named_definition_shapes_agree_too(self, text: str) -> None:
        assert list(_iter_reference_definitions(text)) == _regex_definitions(text)

    def test_a_backslash_free_note_indexes_as_a_reader_reads_it(self) -> None:
        note = (
            "# Title\n\n"
            "See [one][a] and [two][b].\n\n"
            "A [broken] label][c] and a [^fn][^gn].\n\n"
            "[a]: one.md\n[b]: two.md\n[c]: three.md\n"
        )
        # ``[c]`` is a shortcut reference — a defined label standing on
        # its own — so it stores a row since #1531. ``[broken]`` is not
        # defined and stores none, which is what keeps the form from
        # turning every bracketed aside into a link.
        assert _links(note) == [
            ("one", "one.md"),
            ("two", "two.md"),
            ("c", "three.md"),
        ]


class TestTheScansStayLinear:
    def test_a_paragraph_of_escaped_labels_does_not_blow_up(self) -> None:
        # The reason both are scans and not wider character classes: an
        # escape-aware class has to read past every escaped ``]``, so the
        # engine's retry from each ``[`` turns quadratic. A bound rather
        # than a timing assertion, so the test does not go flaky on a slow
        # runner — the quadratic version exceeds it by orders of magnitude.
        assert extract_links("[a\\]b][r" * 8000, SRC) == []

    def test_a_body_of_bracket_openers_does_not_blow_up(self) -> None:
        # The definition scan runs over the whole body rather than one
        # paragraph, so its pathological shape is a run of line-opening
        # ``[`` that never closes.
        assert extract_links("[a\n" * 8000, SRC) == []
