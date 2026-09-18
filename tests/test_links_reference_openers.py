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

**What this does not claim.** The shortcut form (``[label]`` with no second
span) is still not extracted, and it is the whole of the remaining
disagreement with a CommonMark reader. It is out of scope on evidence, not
by preference: CommonMark falls back from the full form to the shortcut one
when the label lookup *fails*, so a scan that supports it has to consult the
definition table, which this walk does not see. Bolting a shortcut shape
test onto the walk leaves 774 inputs still disagreeing, where the proper
ladder would leave none.

That gap is not merely subtractive, which is worth stating because it is
easy to assume otherwise. A shortcut link *deactivates the openers enclosing
it*, exactly as any other link does, so not making one leaves an opener
active that a reader has retired: in ``[[a]b][r]`` a reader reads ``[a]`` as
a shortcut and then ``[r]`` as another, where we read the whole thing as one
full reference with the text ``[a]b``. So the residue is not "rows we miss"
but "inputs a shortcut would have changed", and the property below is scoped
by what the *input* could do, never by what we happened to output.
"""

from __future__ import annotations

import itertools
import time

import pytest

from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.utils.links import iter_bracket_links

SRC = "source.md"

#: Appended to every generated body. One label, and a two-character one,
#: so that the generator's single-letter fillers cannot spell it by
#: accident: a bare ``[a]`` is then not a link on either side, and the
#: shortcut form — the one gap this module does not close — stays rare
#: enough that excluding it leaves most of the corpus intact.
LABEL = "ar"
DEFS = f"\n\n[{LABEL}]: x.md\n"


def _links(body: str) -> list[tuple[str, str]]:
    """``(link_text, raw_target)`` for every reference link in *body*."""
    return [
        (link.link_text, link.raw_target)
        for link in extract_links(body + DEFS, SRC)
        if link.link_type == "reference"
    ]


def _has_wikilink(body: str) -> bool:
    """``[[a]]`` is a wikilink here and a reference for CommonMark.

    A deliberate, long-standing departure of its own, so inputs that reach
    it are outside this module's comparison rather than failures of it.
    """
    return any(link.link_type == "wikilink" for link in extract_links(body + DEFS, SRC))


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
            text += child.content


def _oracle(markdown_it, body: str) -> list[tuple[str, str]]:
    """``(text, href)`` for every link — not image — a CommonMark reader finds."""
    found: list[tuple[str, str]] = []
    for token in markdown_it.parse(body + DEFS):
        if token.children:
            _collect(token.children, found)
    return [(text, href) for text, href in found if href == "x.md"]


def _is_subsequence(ours: list[tuple[str, str]], theirs: list[tuple[str, str]]) -> bool:
    """Every row of *ours* is one of *theirs*, in order."""
    remaining = iter(theirs)
    return all(row in remaining for row in ours)


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

    def test_the_shortcut_form_is_still_not_extracted(self) -> None:
        # Stated here so the generated property's exclusion below is read
        # as a standing gap rather than as something this change made.
        assert _links("[ar]") == []


# ---------------------------------------------------------------------------
# The property that matters once "unchanged" is not one
# ---------------------------------------------------------------------------


class TestAgreementWithACommonMarkReader:
    @pytest.mark.parametrize("length", range(1, 8), ids=lambda n: f"len{n}")
    def test_we_never_find_a_link_a_reader_does_not(self, length: int) -> None:
        # Exhaustive over the characters that decide the opener question: the
        # two brackets, the ``!`` that makes an image, and the two letters
        # that spell the one defined label.
        #
        # The claim is containment, and it is the exact shape of what #1528
        # was: every row we store is a row a CommonMark reader stores, with
        # the same text, the same target and in the same order. Inventing a
        # row (``![alt][ar]``), mistexting one (``[a[b][ar]``) or reordering
        # them all break it. It needs no exclusion for the shortcut form,
        # because *missing* a link cannot break containment — which is why
        # this is the claim worth asserting unconditionally, with the
        # shortfall pinned separately below.
        markdown_it = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
        for chars in itertools.product("[]!ar", repeat=length):
            body = "".join(chars)
            if _has_wikilink(body):
                continue
            assert _is_subsequence(_links(body), _oracle(markdown_it, body)), body

    @pytest.mark.parametrize("length", range(1, 8), ids=lambda n: f"len{n}")
    def test_the_shortfall_is_the_shortcut_gap_and_no_wider(self, length: int) -> None:
        # Containment alone would be satisfied by storing nothing at all, so
        # the inputs where we fall short of the reader are counted, not
        # waved at. Every one of them is the shortcut form.
        #
        # A number that *rises* means rows were lost and the change that
        # lost them owes an explanation; one that *falls* means the gap
        # narrowed and these figures want rewriting rather than relaxing.
        # Before this change the same corpus disagreed on 5682 inputs; after
        # it, 5574, with none newly disagreeing.
        expected = {1: 0, 2: 0, 3: 0, 4: 1, 5: 9, 6: 62, 7: 390}
        markdown_it = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
        short = sum(
            1
            for chars in itertools.product("[]!ar", repeat=length)
            if not _has_wikilink(body := "".join(chars))
            and _links(body) != _oracle(markdown_it, body)
        )
        assert short == expected[length]

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
        # inner link closes, so ``[a [b][ar] c]`` yields only ``b`` — and the
        # trailing ``[r]`` that a CommonMark reader then reads as a shortcut
        # is the gap this module does not close.
        assert _links("[a [b][ar] c][ar]") == [("b", "x.md")]


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
        list(iter_bracket_links(region, lambda _region, _at: None))
        assert time.perf_counter() - started < 5.0, label

    def test_a_body_of_reference_usages_does_not_stall(self) -> None:
        started = time.perf_counter()
        assert extract_links("[a][ar] " * 50000 + DEFS, SRC) != []
        assert time.perf_counter() - started < 10.0
