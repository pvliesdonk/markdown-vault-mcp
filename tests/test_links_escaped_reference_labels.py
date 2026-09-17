r"""A backslash in a reference label does not close it (#1519).

``[Bra\]cket][ref]`` is a valid CommonMark reference link — a link label
ends at the first ``]`` that is *not* backslash-escaped — and the scanner
recorded no row for it: no outlink on the note, no backlink on the target.
#1517 fixed the same defect for inline link text and deliberately left the
reference family, whose match shape is two adjacent bracket spans rather
than one followed by ``(``. This module covers what that left.

Both the usage (``[text][ref]``) and the definition (``[ref]: x.md``) sides
are here, because a label spelled with an escape has to survive on both to
match: CommonMark normalises a label by case-folding and collapsing
whitespace, not by resolving escapes, so ``[a\]b]`` matches ``[a\]b]`` and
the two sides must agree on the spelling.

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

from markdown_vault_mcp.scanner import (
    _find_reference_usage,
    _iter_reference_definitions,
    extract_links,
)

SRC = "source.md"

#: The two patterns the scans replaced. Kept here, not imported, precisely
#: because they no longer exist in the scanner: the differential properties
#: below are only meaningful against the spellings that actually shipped.
_SUPERSEDED_USAGE = re.compile(r"\[([^\]]*)\]\[([^\]]*)\]")
_SUPERSEDED_DEF = re.compile(r"^\s*\[([^\]]+)\]:\s*(.+)$", re.MULTILINE)


def _links(content: str) -> list[tuple[str, str]]:
    """``(link_text, target_path)`` for every link in *content*."""
    return [(link.link_text, link.target_path) for link in extract_links(content, SRC)]


def _scan_usages(text: str) -> list[tuple[int, str, str]]:
    """Every usage the scan finds, resuming as the caller does."""
    found: list[tuple[int, str, str]] = []
    pos = 0
    while (usage := _find_reference_usage(text, pos)) is not None:
        pos, link_text, ref = usage
        found.append((pos, link_text, ref))
    return found


def _regex_usages(text: str) -> list[tuple[int, str, str]]:
    """The same, as ``finditer`` over the superseded pattern gave it."""
    return [(m.end(), m.group(1), m.group(2)) for m in _SUPERSEDED_USAGE.finditer(text)]


def _regex_definitions(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _SUPERSEDED_DEF.finditer(text)]


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


class TestWhatIsNotClaimed:
    def test_a_label_ending_in_an_escaped_bracket_closes_no_link(self) -> None:
        # The rows this drops, and the mirror of what #1517 dropped
        # inline. In ``[a\][ref]`` the first ``]`` is escaped, so the label
        # runs on to the one after ``ref`` and nothing follows it: not a
        # full reference link. A CommonMark reader does find a link here,
        # but by the *shortcut* form ``[ref]`` left over once ``[a\]`` is
        # literal text — a form this scanner has never extracted.
        # [observed: markdown-it-py 'commonmark', 2026-09-17]
        assert _links("See [a\\][ref].\n\n[ref]: notes/x.md\n") == []

    def test_the_shortcut_form_is_still_not_extracted(self) -> None:
        # Stated so the case above is read as one instance of a standing
        # gap rather than as something #1519 introduced.
        assert _links("See [ref] here.\n\n[ref]: notes/x.md\n") == []

    def test_a_label_keeps_the_backslash_as_written(self) -> None:
        # As for inline text and ``raw_target``: what the file holds is
        # what a rewrite has to search for, so nothing is decoded here.
        assert _links("[a\\]b][r]\n\n[r]: x.md\n") == [("a\\]b", "x.md")]


# ---------------------------------------------------------------------------
# The property the semantics note rests on
# ---------------------------------------------------------------------------


class TestTheScansAgreeWithThePatternsTheyReplaced:
    """Backslash-free input indexes exactly as it did before."""

    @pytest.mark.parametrize("length", range(10), ids=lambda n: f"len{n}")
    def test_no_backslash_free_usage_changed_meaning(self, length: int) -> None:
        # Exhaustive rather than sampled, over the characters the pattern
        # can distinguish: a bracket that opens, one that closes, and a
        # filler standing for everything else. The whole sequence is
        # compared, not the first match, so the scan's resume points are
        # pinned against ``finditer``'s non-overlapping ones.
        for chars in itertools.product("[]a", repeat=length):
            text = "".join(chars)
            assert _scan_usages(text) == _regex_usages(text), text

    @pytest.mark.parametrize("length", range(7), ids=lambda n: f"len{n}")
    def test_no_backslash_free_definition_changed_meaning(self, length: int) -> None:
        # The definition alphabet needs four more characters than the
        # usage one: the ``:`` that makes a definition, the newline the
        # multi-line anchors turn on, and a space and a filler for the
        # indent and the target.
        for chars in itertools.product("[]:\na ", repeat=length):
            text = "".join(chars)
            assert list(_iter_reference_definitions(text)) == _regex_definitions(
                text
            ), text

    @pytest.mark.parametrize(
        "text",
        [
            "[a][r]",
            "[a]x[b][r]",
            "[a][b[c][d]",
            "[a][]",
            "[][]",
            "[unclosed][",
            "][a][r]",
            "no brackets at all",
        ],
        ids=[
            "plain",
            "failed-then-found",
            "inner-bracket-in-ref",
            "collapsed",
            "both-empty",
            "unclosed-ref",
            "closer-first",
            "none",
        ],
    )
    def test_the_named_usage_shapes_agree_too(self, text: str) -> None:
        assert _scan_usages(text) == _regex_usages(text)

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

    def test_a_backslash_free_note_indexes_identically(self) -> None:
        note = (
            "# Title\n\n"
            "See [one][a] and [two][b].\n\n"
            "A [broken] label][c] and a [^fn][^gn].\n\n"
            "[a]: one.md\n[b]: two.md\n[c]: three.md\n"
        )
        assert _links(note) == [("one", "one.md"), ("two", "two.md")]


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
