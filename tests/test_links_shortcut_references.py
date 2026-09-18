r"""``[label]`` alone is a link when the label is defined (#1531).

The shortcut reference form. No version of this scanner had ever stored
one: a reference usage had to be *two* adjacent bracket spans, so a label
standing on its own produced no row — no outlink on the note, no backlink
on the target — however plainly a reader links it. After #1528 closed the
opener question, this was the whole of the remaining disagreement with a
CommonMark reader.

**It is a ladder, not a shape.** §6.3 does not test one pattern; it tries
rungs in order, and the rungs are not independent:

* **full** — ``[text][label]``, and only when *label is defined*;
* **collapsed** — ``[text][]``, resolving on the text;
* **shortcut** — ``[text]``, resolving on the text.

Two consequences make this more than an extra branch, and both were found
by measuring rather than by reading:

1. **The test cannot be purely syntactic.** Whether ``[a][b]`` is a link
   depends on the definition table, so the shape test is a closure over it
   rather than the plain function the inline family uses. That is the
   layering change #1531 was filed to warn about.
2. **Failing to resolve is not the same as failing to parse.** A label
   that parses but is undefined *ends* the ladder — the reader has spent
   the second span on it. A second span that is not a valid label at all
   (``[[]``, whose brackets do not balance) was never a candidate, so the
   ladder falls through and reads the first span as a shortcut. Getting
   this backwards is the difference between ``[ar][a]`` and ``[a][[]``,
   and both spellings are pinned below.

And one thing the form breaks that nothing else did: **a definition line
is itself a label standing alone**. ``[r]: x.md`` matched no previous
usage shape, so nothing had to exclude it; under the shortcut rung it
matches immediately, and every definition in a vault would become a link
to itself. A reader removes definitions before parsing inlines; this scan
does the same test line-wise, which is why a label *inside* a line
(``See [ar]: here``) still links.
"""

from __future__ import annotations

import itertools
import time

import pytest

from markdown_vault_mcp.scanner import extract_links

SRC = "source.md"
DEFS = "\n\n[ar]: x.md\n"


def _links(body: str, defs: str = DEFS) -> list[tuple[str, str]]:
    """``(link_text, raw_target)`` for every reference link in *body*."""
    return [
        (link.link_text, link.raw_target)
        for link in extract_links(body + defs, SRC)
        if link.link_type == "reference"
    ]


# ---------------------------------------------------------------------------
# The form
# ---------------------------------------------------------------------------


class TestTheShortcutForm:
    def test_a_defined_label_alone_is_a_link(self) -> None:
        assert _links("See [ar] here.") == [("ar", "x.md")]

    def test_an_undefined_label_alone_is_not(self) -> None:
        # What keeps the form from turning every bracketed aside into a
        # link: the definition has to exist.
        assert _links("See [nope] here.") == []

    def test_the_label_match_ignores_case(self) -> None:
        # Definitions are keyed lower-cased, as the other forms already
        # resolve them.
        assert _links("See [AR] here.") == [("AR", "x.md")]

    def test_a_label_carrying_an_escaped_bracket_resolves(self) -> None:
        # The #1519 spelling, one form further on.
        assert _links(r"See [a\]b] here.", "\n\n[a\\]b]: x.md\n") == [(r"a\]b", "x.md")]

    def test_an_image_shortcut_stores_no_row(self) -> None:
        # ``![ar]`` is an image, and the reference family skips those since
        # #1528.
        assert _links("![ar]") == []

    def test_two_in_one_line_both_resolve(self) -> None:
        assert _links("[ar] and [ar]") == [("ar", "x.md"), ("ar", "x.md")]


class TestTheLadderIsOrdered:
    def test_a_full_reference_still_wins_over_the_shortcut(self) -> None:
        # ``[a][ar]`` is the full form: the text is ``a``, not ``ar``.
        assert _links("[a][ar]") == [("a", "x.md")]

    def test_the_collapsed_form_still_resolves_on_its_text(self) -> None:
        assert _links("[ar][]") == [("ar", "x.md")]

    def test_a_defined_label_with_an_undefined_second_span_is_no_link(
        self,
    ) -> None:
        # The rung that is easy to get wrong. ``[a]`` is a *valid* label
        # that happens to be undefined, so the reader spends the second
        # span on it and stops — it does not reread ``[ar]`` as a shortcut.
        # [observed: markdown-it-py 'commonmark' renders
        # ``[ar][a]`` as ``<p>[ar][a]</p>``, 2026-09-18]
        assert _links("[ar][a]") == []

    def test_an_unparseable_second_span_falls_through_to_the_shortcut(
        self,
    ) -> None:
        # And its mirror. ``[[]`` has unbalanced brackets, so it is not a
        # label at all; the second span was never a candidate and the
        # ladder falls through, linking ``[ar]`` as a shortcut.
        # [observed: markdown-it-py 'commonmark' renders
        # ``[ar][[]`` as ``<p><a href="x.md">ar</a>[[]</p>``, 2026-09-18]
        assert _links("[ar][[]") == [("ar", "x.md")]


class TestDefinitionsAreNotUsages:
    def test_a_definition_line_is_not_a_link_to_itself(self) -> None:
        # The trap the form springs: every definition is a label standing
        # alone. Nothing had to exclude them before, because no previous
        # usage shape could match one.
        assert extract_links("[ar]: x.md\n", SRC) == []

    def test_an_indented_definition_is_not_one_either(self) -> None:
        assert extract_links("   [ar]: x.md\n", SRC) == []

    def test_a_label_inside_a_line_still_links(self) -> None:
        # Why the test is line-aware rather than a bare check for ``:``.
        # Here the colon is prose, and a reader links the label.
        # [observed: markdown-it-py 'commonmark' renders
        # ``See [ar]: here`` as ``<p>See <a href="x.md">ar</a>: here</p>``,
        # 2026-09-18]
        assert _links("See [ar]: here") == [("ar", "x.md")]

    def test_a_definition_and_a_usage_of_it_coexist(self) -> None:
        assert _links("See [ar] here.") == [("ar", "x.md")]


class TestWhatStaysAsItWas:
    def test_two_footnote_references_are_still_not_a_link(self) -> None:
        # #1104's deliberate departure, which the shortcut rung could have
        # undone: ``[^1]`` is a label standing alone, and its definition
        # line looks like a reference definition. The footnote prefix is
        # still filtered, so GFM footnotes do not become vault links.
        assert extract_links("a [^1][^2] b\n\n[^1]: one\n[^2]: two\n", SRC) == []

    def test_a_lone_footnote_reference_is_not_a_link_either(self) -> None:
        assert extract_links("a [^1] b\n\n[^1]: one.md\n", SRC) == []

    def test_an_external_shortcut_target_is_skipped(self) -> None:
        assert extract_links("[ex]\n\n[ex]: https://example.com\n", SRC) == []

    def test_an_attachment_shortcut_target_is_skipped(self) -> None:
        assert extract_links("[img]\n\n[img]: picture.png\n", SRC) == []


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


class TestTheLadderStaysLinear:
    @pytest.mark.parametrize(
        ("unit", "label"),
        [
            ("[", "openers"),
            ("[ar]", "shortcuts"),
            ("[a\\]b", "escaped"),
            ("[ar][", "failed-second-spans"),
        ],
        ids=["openers", "shortcuts", "escaped", "failed-second-spans"],
    )
    def test_a_long_run_does_not_stall(self, unit: str, label: str) -> None:
        # The ladder adds a label parse per closed span, and that parse
        # walks forward from the ``]``, so ``[ar][`` — a run of spans whose
        # second span never closes — is the shape to watch. It caught two
        # real costs while this was written: an ``rfind`` to the region
        # start in the definition-line test, and a stack walk per link to
        # retire enclosing openers. Both were quadratic; the run cost 8.5 s
        # at 20000 repeats and now costs about a tenth of that.
        #
        # 10000 rather than the 200000 the other link modules use, because
        # the surviving constant is §6.3's own: a failed label parse reads
        # up to 999 characters before giving up, once per span. Linear, but
        # not cheap, so the count is chosen to leave the bound real
        # headroom on a slow runner rather than to sit just under it.
        body = unit * 10000
        started = time.perf_counter()
        extract_links(body + DEFS, SRC)
        assert time.perf_counter() - started < 5.0, label


class TestAgreementWithACommonMarkReader:
    @pytest.mark.parametrize("length", range(1, 7), ids=lambda n: f"len{n}")
    def test_the_ladder_agrees_over_a_label_shaped_alphabet(self, length: int) -> None:
        # ``tests/test_links_reference_openers.py`` carries the exhaustive
        # comparison over bracket arrangements. This one narrows the
        # alphabet onto the ladder's own decisions — a colon, so definition
        # lines are generated, and a newline, so line-awareness is
        # exercised — which that corpus does not reach.
        markdown_it = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
        for chars in itertools.product("[]:\nar", repeat=length):
            body = "".join(chars)
            got = extract_links(body + DEFS, SRC)
            if any(link.link_type == "wikilink" for link in got):
                continue
            ours = [
                (link.link_text, link.raw_target)
                for link in got
                if link.link_type == "reference"
            ]
            expected: list[tuple[str, str]] = []
            for token in markdown_it.parse(body + DEFS):
                if not token.children:
                    continue
                href: str | None = None
                text = ""
                for child in token.children:
                    if child.type == "link_open":
                        href, text = child.attrGet("href"), ""
                    elif child.type == "link_close" and href is not None:
                        if href == "x.md":
                            expected.append((text, href))
                        href = None
                    elif href is not None:
                        # A line ending inside link text is a ``softbreak``
                        # token whose ``content`` is empty, so summing
                        # ``content`` alone silently drops it and the
                        # comparison reports a difference that is the
                        # oracle's, not the scanner's.
                        text += "\n" if child.type == "softbreak" else child.content
            assert ours == expected, body
