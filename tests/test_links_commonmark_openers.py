r"""A ``]`` closes the nearest unmatched ``[`` (#1526).

The scanner used to open a link at the **first** ``[`` since the last
unescaped ``]``. CommonMark opens it at the **nearest unmatched** one
(§6.3): a ``]`` matches the innermost ``[`` still open, and an opener that
closes nothing is discarded rather than swallowing what follows.

One rule, three recorded departures:

* ``[a[b](x.md)`` stored its text as ``a[b`` where it is ``b``;
* ``[a [b] c](x.md)`` stored **no row at all**, though its text merely
  contains a balanced pair — the "link text is not bracket-balanced"
  departure;
* ``![a[b](x.md)`` was read as an image, because the ``!`` sits before the
  *outer* ``[`` while the link CommonMark finds opens at the inner one —
  the defect #1526 was filed for.

None of the three needed its own repair, which is why they are fixed
together: they were never three defects.

The property here is agreement with a CommonMark reader, not with the
matcher this replaced. That is deliberate. The superseded property, in
``tests/test_links_escaped_text.py``, asserted that a change moved nothing
else; this change moves things on purpose, so the only claim worth pinning
is the one about being *right*.
"""

from __future__ import annotations

import itertools
import time

import pytest

from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.utils.links import apply_link_replacement, iter_inline_links

SRC = "source.md"


def _links(content: str) -> list[tuple[str, str]]:
    """``(link_text, raw_target)`` for every markdown link in *content*."""
    return [
        (link.link_text, link.raw_target)
        for link in extract_links(content, SRC)
        if link.link_type == "markdown"
    ]


# ---------------------------------------------------------------------------
# The three departures, each by name
# ---------------------------------------------------------------------------


class TestTheNearestOpenerWins:
    def test_an_inner_bracket_opens_the_link(self) -> None:
        # Was ``a[b``: the outer ``[`` opened the text.
        assert _links("[a[b](x.md)") == [("b", "x.md")]

    def test_an_empty_text_after_a_stray_bracket(self) -> None:
        # Was ``[``; CommonMark leaves the first bracket as literal text.
        assert _links("[[](x.md)") == [("", "x.md")]

    def test_a_doubled_pair_keeps_the_inner_one_in_the_text(self) -> None:
        assert _links("[[a]](x.md)") == [("[a]", "x.md")]


class TestBracketBalancing:
    def test_a_balanced_pair_inside_the_text_is_a_link(self) -> None:
        # The departure #1517 and #1519 both left standing.
        assert _links("[a [b] c](x.md)") == [("a [b] c", "x.md")]

    def test_the_issue_wording_case(self) -> None:
        assert _links("[Note [draft]](x.md)") == [("Note [draft]", "x.md")]

    def test_an_unbalanced_inner_bracket_still_opens_the_link(self) -> None:
        assert _links("[a [b c](x.md)") == [("b c", "x.md")]

    def test_a_closer_before_any_opener_is_literal(self) -> None:
        assert _links("[a]b](x.md)") == []


class TestImagesAndTheirDescriptions:
    def test_an_ordinary_image_is_still_not_a_link(self) -> None:
        assert _links("![alt](x.md)") == []

    def test_an_image_whose_description_opens_a_bracket_is_a_link(self) -> None:
        # #1526 as filed: the ``!`` precedes the outer ``[``, but the link
        # CommonMark finds opens at the inner one, so ``![a`` is text.
        assert _links("![a[b](x.md)") == [("b", "x.md")]

    def test_the_empty_spelling_of_the_same_shape(self) -> None:
        assert _links("![[](x.md)") == [("", "x.md")]

    def test_a_link_inside_an_image_description_is_still_found(self) -> None:
        # §6.4 Ex. 575: an image description may contain a link. The inner
        # link closes first, so it is found; the image itself is not a row.
        assert _links("![a [b](y.md)](i.png)") == [("b", "y.md")]


class TestLinksMayNotContainLinks:
    def test_only_the_inner_link_is_stored(self) -> None:
        # §6.3 Ex. 518. Once a link is found, every opener still on the
        # stack is deactivated, so the outer brackets yield nothing.
        assert _links("[a [b](y.md) c](x.md)") == [("b", "y.md")]


class TestEscapesStillHold:
    def test_an_escaped_closing_bracket_does_not_close_the_text(self) -> None:
        assert _links(r"[Bra\]cket](x.md)") == [(r"Bra\]cket", "x.md")]

    def test_an_escaped_opener_does_not_open(self) -> None:
        assert _links(r"\[a](x.md)") == []

    def test_an_escaped_bang_does_not_make_an_image(self) -> None:
        assert _links(r"\![a](x.md)") == [("a", "x.md")]


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


def _oracle(markdown_it, src: str) -> list[tuple[str, str]]:
    """``(text, href)`` for every link a CommonMark reader finds."""
    found: list[tuple[str, str]] = []
    for token in markdown_it.parse(src):
        if not token.children:
            continue
        href: str | None = None
        text = ""
        for child in token.children:
            if child.type == "link_open":
                href, text = child.attrGet("href"), ""
            elif child.type == "link_close" and href is not None:
                found.append((text, href))
                href = None
            elif href is not None:
                text += child.content
    return found


class TestAgreementWithACommonMarkReader:
    """Every bracket arrangement is read the way a reader reads it."""

    @pytest.mark.parametrize("length", range(8), ids=lambda n: f"len{n}")
    def test_every_short_bracket_arrangement_agrees(self, length: int) -> None:
        # Exhaustive over the characters that decide the question: both
        # brackets, both parentheses, and the ``!`` that would make an
        # image. Each arrangement is tried with a destination spliced in,
        # and again with letters between the punctuation so link *text* is
        # compared and not only link presence.
        #
        # Inputs whose destination is not a plain name are skipped: the
        # reader percent-encodes an href and accepts an empty destination
        # where this scanner does not, and neither is the opener question.
        markdown_it = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
        dests = {"x.md", "y.md"}
        for chars in itertools.product("[]!()", repeat=length):
            base = "".join(chars)
            for content in (
                base.replace("(", "(x.md"),
                "a".join(base).replace("(", "(y.md"),
            ):
                expected = _oracle(markdown_it, content)
                if any(href not in dests for _, href in expected):
                    continue
                assert _links(content) == expected, content

    def test_the_rewrite_agrees_with_the_index_on_these_too(self) -> None:
        # #1521's property, restated over the shapes this change moved: the
        # rewrite must replace exactly the destinations the index stored,
        # and both sides moved together because they share the iterator.
        for content in (
            "[a[b](x.md)",
            "[a [b] c](x.md)",
            "![a[b](x.md)",
            "![[](x.md)",
            "[a [b](x.md) c](x.md)",
            "![alt](x.md)",
            "[[a]](x.md)",
        ):
            stored = [target for _, target in _links(content)]
            after = apply_link_replacement(content, "markdown", "x.md", "yy.md")
            assert (len(after) - len(content)) == stored.count("x.md"), content
            # Not only how much moved but which: see
            # ``test_links_rewrite_agrees_with_index.py`` for why a length
            # delta alone is too weak a check.
            assert [target for _, target in _links(after)] == [
                "yy.md" if target == "x.md" else target for target in stored
            ], content


class TestTheOffsetsItReports:
    """Each link is reported at the offsets it actually occupies.

    The rewrite splices at ``target_start`` and resumes at ``end``, so an
    offset that is merely plausible corrupts a file quietly. An earlier
    draft of #1526 dropped the offset and had the rewrite search the link
    for the destination instead; it found the *title*'s copy whenever one
    repeated the destination, and the file came out with a mangled title
    and a stale link. Asserted exhaustively, because the shape that broke
    it was not among anybody's examples.
    """

    @pytest.mark.parametrize("length", range(7), ids=lambda n: f"len{n}")
    def test_every_short_string_reports_honest_offsets(self, length: int) -> None:
        for chars in itertools.product("[]!()\\", repeat=length):
            base = "".join(chars)
            for region in (base, base.replace("(", "(x.md", 1)):
                for link in iter_inline_links(region):
                    assert region[link.open_index] == "[", region
                    assert (
                        region[
                            link.target_start : link.target_start + len(link.raw_target)
                        ]
                        == link.raw_target
                    ), region
                    assert region[link.end - 1] == ")", region

    @pytest.mark.parametrize(
        "region",
        [
            '[a](x.md "x.md")',
            '[a](x.md "the x.md note")',
            "[x.md](x.md)",
            "[a](<x.md>)",
            '[a](<x.md> "x.md")',
            "[a](  x.md  )",
            "[a[b](x.md)",
            "![alt](img.png) and [real](x.md)",
        ],
        ids=[
            "title-repeats-destination",
            "title-contains-destination",
            "text-repeats-destination",
            "pointy",
            "pointy-with-title",
            "padded",
            "nested-opener",
            "image-then-link",
        ],
    )
    def test_the_named_shapes_report_honest_offsets(self, region: str) -> None:
        links = list(iter_inline_links(region))
        assert links, region
        for link in links:
            assert (
                region[link.target_start : link.target_start + len(link.raw_target)]
                == link.raw_target
            ), region


class TestTheWalkStaysLinear:
    @pytest.mark.parametrize(
        ("unit", "label"),
        [("[", "openers"), ("[a\\]b", "escaped"), ("[a](x.md) ", "real-links")],
        ids=["openers", "escaped", "real-links"],
    )
    def test_a_long_run_does_not_stall(self, unit: str, label: str) -> None:
        # Each opener is pushed and popped at most once, so a run of ``[``
        # costs one push apiece and no rescan. The bound is generous; the
        # walk does 200000 of these in well under a second.
        region = unit * 200000
        started = time.perf_counter()
        list(iter_inline_links(region))
        assert time.perf_counter() - started < 5.0, label
