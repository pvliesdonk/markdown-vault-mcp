r"""A reference link opens at the nearest unmatched ``[`` too (#1528).

#1526 gave the *inline* family CommonMark's opener rule and deliberately
left the reference family on the old one: its shape is two adjacent bracket
spans rather than one span followed by ``(``, so the inline iterator did not
drop in. This module covers the carry-across.

What moved is not the whole scan but the *walk*. Both families now step
through ``iter_bracket_links`` and supply only the test for what has to
follow a closed span, so the rule lives in one function and cannot drift
between them — which is the failure #1521 was, one abstraction lower.

One rule, three recorded departures, mirroring the inline three:

* ``[a[b][r]`` stored its text as ``a[b`` where it is ``b`` — the defect
  #1528 was filed for;
* ``[a [b] c][r]`` stored **no row at all**, though its text merely
  contains a balanced pair;
* ``![alt][r]`` stored a *link* row, though it is an image — the reference
  scan had no image test at all, where the inline one always had.

The third **removes** rows, so it is worth being plain about what it
removes: only an image reference naming a note. One naming an actual image
already stored nothing, dropped by the attachment filter, which is why this
went unnoticed.

As in ``tests/test_links_commonmark_openers.py``, the property is agreement
with a CommonMark reader rather than with the matcher this replaced. The
"nothing else moved" claim belongs to a change that moves nothing; this one
moves rows on purpose.

**What this claimed, and what closed it.** When this module was written
the shortcut form (``[label]`` with no second span) was not extracted, and
it was the whole of the remaining disagreement with a CommonMark reader —
774 inputs of the ``WIDE_DEFS`` corpus below, and not a shape test away,
since a reader falls back from the full form to the shortcut one when the
label lookup *fails* and this walk could not see the definition table.
#1531 gave the walk that table, so the properties below now assert
**equality** with a reader rather than containment plus a recorded
shortfall.

The gap was never merely subtractive, which is worth keeping because it is
easy to assume otherwise. A shortcut link *deactivates the openers
enclosing it*, exactly as any other link does, so not making one left an
opener active that a reader had retired: in ``[[a]b][r]`` a reader reads
``[a]`` as a shortcut and then ``[r]`` as another, where this scanner read
the whole thing as one full reference with the text ``[a]b``. That is why
closing the gap moved rows the shortcut form does not itself appear in.

**What this module still does not reach.** Its alphabet is ``[]!ar`` — no
``(``, no space, no ``^``. Those characters are where the shortcut rung
meets the *other* families, and the collisions there are covered by
``tests/test_links_shortcut_references.py`` instead.
"""

from __future__ import annotations

import itertools
import time

import pytest

from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.utils.links import find_bracket_span, iter_bracket_links

SRC = "source.md"

#: Appended to every generated body. One label, and a two-character one,
#: so that the generator's single-letter fillers cannot spell it by
#: accident: a bare ``[a]`` is then not a link on either side. That was
#: chosen to keep the shortcut form rare while it was unimplemented; since
#: #1531 nothing is excluded for it, and the narrow set simply keeps this
#: corpus focused on the opener rule rather than on the ladder.
LABEL = "ar"
DEFS = f"\n\n[{LABEL}]: x.md\n"

#: A second definition set, where **both filler letters are themselves
#: defined labels**. It matters because the corpus above barely reaches
#: this change's own shapes: with a two-character label, ``[a[b][ar]``
#: needs nine characters and the generator stops at seven, so only four
#: inputs in 97,655 actually exercise the fix. Single-letter labels make
#: those shapes fit, at the cost of firing the shortcut form constantly —
#: which is why, while the form was unimplemented, the narrow set carried
#: the containment property and this one was pinned by count. Since #1531
#: both assert equality and the distinction is historical.
#:
#: The figures in ``docs/design/design.md`` and the
#: ``INDEX_SEMANTICS_VERSION`` note come from *this* corpus. Only the
#: *after* column is what CI computes: the before column needs the
#: pre-change code and is a recorded measurement, not an assertion.
WIDE_DEFS = "\n\n[r]: x.md\n[a]: x.md\n"


def _links(body: str, defs: str = DEFS) -> list[tuple[str, str]]:
    """``(link_text, raw_target)`` for every reference link in *body*."""
    return [
        (link.link_text, link.raw_target)
        for link in extract_links(body + defs, SRC)
        if link.link_type == "reference"
    ]


def _has_wikilink(body: str, defs: str = DEFS) -> bool:
    """``[[a]]`` is a wikilink here and a reference for CommonMark.

    A deliberate, long-standing departure of its own, so inputs that reach
    it are outside this module's comparison rather than failures of it.
    """
    return any(link.link_type == "wikilink" for link in extract_links(body + defs, SRC))


def _collect(children, found: list[tuple[str, str]]) -> None:
    """Collect links from *children*, descending into image descriptions.

    The descent is not a detail: §6.4 Ex. 575 says an image description may
    contain a link, markdown-it nests that link's tokens under the image
    token, and a walker that only reads the top level reports no link at
    all. An oracle blind to a construct silently turns a real disagreement
    into a pass, so it is written out rather than assumed.
    """
    href: str | None = None
    text = ""
    for child in children:
        if child.type == "link_open":
            href, text = child.attrGet("href"), ""
        elif child.type == "link_close" and href is not None:
            found.append((text, href))
            href = None
        elif child.type == "image":
            _collect(child.children or [], found)
            if href is not None:
                text += child.attrGet("alt") or ""
        elif href is not None:
            # A softbreak's ``content`` is empty, so summing it silently
            # drops the line ending. This alphabet holds no newline, but
            # an oracle that is wrong only for inputs nobody generates
            # yet is a trap for whoever widens it.
            text += "\n" if child.type == "softbreak" else child.content


def _oracle(markdown_it, body: str, defs: str = DEFS) -> list[tuple[str, str]]:
    """``(text, href)`` for every link — not image — a CommonMark reader finds."""
    found: list[tuple[str, str]] = []
    for token in markdown_it.parse(body + defs):
        if token.children:
            _collect(token.children, found)
    return [(text, href) for text, href in found if href == "x.md"]


# ---------------------------------------------------------------------------
# The three departures, each by name
# ---------------------------------------------------------------------------


class TestTheNearestOpenerWins:
    def test_an_inner_bracket_opens_the_link(self) -> None:
        # The issue's own reproduction. Was ``a[b``.
        assert _links("See [a[b][ar] here.") == [("b", "x.md")]

    def test_a_balanced_pair_inside_the_text_is_a_link_now(self) -> None:
        # Stored no row at all: the old scan's text could not hold a ``[``,
        # so the span ended at the inner ``]`` and the shape fell apart.
        assert _links("[a [b] c][ar]") == [("a [b] c", "x.md")]

    def test_an_image_reference_stores_no_row(self) -> None:
        # The reference scan had no ``!`` test, so this was a link row.
        # [observed: markdown-it-py 'commonmark' renders it
        # ``<img src="x.md" alt="alt" />``, 2026-09-18]
        assert _links("![alt][ar]") == []

    def test_an_image_whose_alt_text_opens_a_bracket_is_a_link(self) -> None:
        # The inline family's ``![a[b](x)`` case, one family to the right:
        # the ``!`` sits before the *outer* ``[`` while the link opens at
        # the inner one, so this is a link and not an image.
        assert _links("![a[b][ar]") == [("b", "x.md")]

    def test_a_link_inside_an_image_description_still_counts(self) -> None:
        # §6.4 Ex. 575: an image description may contain a link, and the
        # inner one closes first.
        assert _links("![an [a][ar] alt][ar]") == [("a", "x.md")]


class TestWhatStaysAsItWas:
    def test_a_plain_reference_is_untouched(self) -> None:
        assert _links("[a][ar]") == [("a", "x.md")]

    def test_the_collapsed_form_is_untouched(self) -> None:
        # The text is the label when the second span is empty, so the
        # fixture spells the defined label rather than a filler.
        assert _links("[ar][]") == [("ar", "x.md")]

    def test_an_escaped_bracket_still_does_not_close_a_label(self) -> None:
        # #1519, which this must not undo.
        assert _links(r"[Bra\]cket][ar]") == [(r"Bra\]cket", "x.md")]

    def test_two_footnote_references_are_still_not_a_link(self) -> None:
        # #1104's deliberate departure: CommonMark has no footnotes and
        # reads ``[^1][^2]`` as a reference link, so this is one of the
        # places the scanner is knowingly not a CommonMark reader.
        assert extract_links("a [^1][^2] b\n\n[^1]: one\n[^2]: two\n", SRC) == []

    def test_an_anchor_target_is_not_a_vault_link(self) -> None:
        # A definition naming a fragment points inside the same document,
        # so it is no more a vault link than an inline ``[a](#x)`` is.
        # Covered here because this change moved which shapes reach the
        # resolution branches at all, and an unexercised branch is one
        # nobody notices breaking.
        assert extract_links("[a][r]\n\n[r]: #section\n", SRC) == []

    def test_a_definition_with_an_empty_target_is_ignored(self) -> None:
        # ``[r]: <>`` names nothing, so the usage resolves to nothing —
        # the reference mirror of ``[a]()`` storing no row.
        assert extract_links("[a][r]\n\n[r]: <>\n", SRC) == []

    def test_the_shortcut_form_is_extracted_since_1531(self) -> None:
        # This module shipped asserting the opposite, and said so as a
        # standing gap. #1531 closed it; the form itself is covered by
        # ``tests/test_links_shortcut_references.py``, and the line stays
        # here because the properties below changed shape when it landed.
        assert _links("[ar]") == [("ar", "x.md")]


# ---------------------------------------------------------------------------
# The property that matters once "unchanged" is not one
# ---------------------------------------------------------------------------


class TestAgreementWithACommonMarkReader:
    @pytest.mark.parametrize("length", range(1, 8), ids=lambda n: f"len{n}")
    def test_every_short_bracket_arrangement_agrees(self, length: int) -> None:
        # Exhaustive over the characters that decide the opener question: the
        # two brackets, the ``!`` that makes an image, and the two letters
        # that spell the one defined label.
        #
        # **Equality**, and with no exclusion but the wikilink one. This
        # module shipped asserting mere containment — every row we store is
        # one a reader stores — because the shortcut form was missing and a
        # missing form can only ever lose rows. #1531 supplied it, so the
        # weaker claim is no longer the strongest true one: on this corpus
        # the scanner now reads references exactly as a CommonMark reader
        # does. The count that used to be pinned beside this (390 shortfalls
        # at length 7) is gone because it is zero.
        markdown_it = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
        for chars in itertools.product("[]!ar", repeat=length):
            body = "".join(chars)
            if _has_wikilink(body):
                continue
            assert _links(body) == _oracle(markdown_it, body), body

    @pytest.mark.parametrize("length", range(1, 8), ids=lambda n: f"len{n}")
    def test_the_wider_corpus_agrees_too(self, length: int) -> None:
        # The corpus where both filler letters are defined labels, so the
        # shapes this family's fixes are about actually fit inside seven
        # characters. It used to be pinned by a count (5574 disagreements)
        # because containment failed here: a shortcut link deactivates the
        # openers around it, so without the form we read one full reference
        # where a reader reads two shortcuts. With the form, that whole
        # class resolves and the count is zero, so it is asserted as
        # equality like the narrow one rather than pinned as a number.
        markdown_it = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
        for chars in itertools.product("[]!ar", repeat=length):
            body = "".join(chars)
            if _has_wikilink(body, WIDE_DEFS):
                continue
            assert _links(body, WIDE_DEFS) == _oracle(markdown_it, body, WIDE_DEFS), (
                body
            )

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("[a[b][ar]", [("b", "x.md")]),
            ("[a [b] c][ar]", [("a [b] c", "x.md")]),
            ("![alt][ar]", []),
            ("![a[b][ar]", [("b", "x.md")]),
            ("![an [a][ar] alt][ar]", [("a", "x.md")]),
            ("[[a]b][ar]", [("[a]b", "x.md")]),
            ("[a][ar] and [b[c][ar]", [("a", "x.md"), ("c", "x.md")]),
            ("[ar][]", [("ar", "x.md")]),
        ],
        ids=[
            "nested-opener",
            "balanced-pair",
            "image",
            "image-with-nested-opener",
            "link-inside-image-description",
            "leading-pair",
            "two-usages",
            "collapsed",
        ],
    )
    def test_the_named_shapes_agree_exactly(
        self, body: str, expected: list[tuple[str, str]]
    ) -> None:
        # The shapes worth reading in a diff, with their answers written
        # out, so a regression names itself instead of arriving as one
        # generated string. Each expectation is also checked against the
        # reader, so a wrong literal here cannot quietly become the spec.
        markdown_it = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
        assert _links(body) == expected
        assert _oracle(markdown_it, body) == expected


class TestLinksMayNotContainLinks:
    def test_only_the_inner_reference_links(self) -> None:
        # §6.3 Ex. 518. The outer opener is deactivated the moment the
        # inner link closes, so the outer span yields nothing — and the
        # trailing ``[ar]`` is then read as a shortcut, which is what a
        # CommonMark reader does and what this module could not match
        # until #1531.
        assert _links("[a [b][ar] c][ar]") == [("b", "x.md"), ("ar", "x.md")]


class TestTheBracketSpanPrimitive:
    """``find_bracket_span`` is now only ever entered at a ``[``.

    Its remaining caller, ``_iter_reference_definitions``, hands it the
    index of an opener, so its "a ``]`` before any ``[`` closes nothing"
    path stopped being reachable through it when the reference scan moved
    onto the shared walk. #1531 took the other caller away entirely: a
    reference *label* must have balanced brackets, which this primitive
    does not require, so the ladder reads it with ``_parse_link_label``
    instead. The behaviour is still part of a public helper's
    contract, and the docstring still promises it, so it is tested
    directly rather than deleted or left to rot untested.
    """

    def test_a_closer_before_any_opener_is_skipped(self) -> None:
        assert find_bracket_span("]] [a] b", 0) == (3, 5, "a")

    def test_a_region_of_closers_alone_finds_nothing(self) -> None:
        assert find_bracket_span("]]]", 0) is None

    def test_an_escaped_closer_does_not_close_the_span(self) -> None:
        assert find_bracket_span(r"[a\]b] c", 0) == (0, 5, r"a\]b")

    def test_the_first_opener_since_the_last_closer_wins(self) -> None:
        # The primitive keeps the *outermost* rule; the nearest-unmatched
        # rule lives in ``iter_bracket_links``, one level up. Pinned so the
        # two are not confused when reading either in isolation.
        assert find_bracket_span("[a[b] c", 0) == (0, 4, "a[b")


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


class TestTheWalkStaysLinear:
    @pytest.mark.parametrize(
        ("unit", "label"),
        [("[", "openers"), ("[a\\]b", "escaped"), ("[a][ar] ", "real-links")],
        ids=["openers", "escaped", "real-links"],
    )
    def test_a_long_run_does_not_stall(self, unit: str, label: str) -> None:
        # The reference scan resumed from ``close + 1`` on a failed
        # candidate, which is linear already; the shared walk keeps it so,
        # pushing and popping each opener at most once. The bound is
        # generous on purpose.
        region = unit * 200000
        started = time.perf_counter()
        list(iter_bracket_links(region, lambda _region, _open, _close: None))
        assert time.perf_counter() - started < 5.0, label

    def test_a_body_of_reference_usages_does_not_stall(self) -> None:
        started = time.perf_counter()
        assert extract_links("[a][ar] " * 50000 + DEFS, SRC) != []
        assert time.perf_counter() - started < 10.0
