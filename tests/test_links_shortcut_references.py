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
blanks out precisely the ones its own definition scan collects, which is
why a label *inside* a line (``See [ar]: here``) still links. Testing each
span in place was tried three times and was wrong three times, so the
answer is reused rather than re-derived.
"""

from __future__ import annotations

import itertools
import re
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
        # Why the definitions are blanked from the scan's own answer
        # rather than tested for in place with a check for ``:``.
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
        # The count and the bound are both chosen from measurements, and
        # the bound is loose on purpose. The surviving constant is §6.3's
        # own — a failed label parse reads up to 999 characters before
        # giving up, once per span — so this shape is linear but not
        # cheap, and CI runners vary by about 8x: 20000 repeats cost 1.7 s
        # on a local 3.11 and 6.5 s on the runner that first ran it at
        # half that size.
        #
        # What the bound has to separate is growth, not absolute time.
        # Either quadratic this caught multiplies the cost by 4 per
        # doubling, putting 20000 repeats near 8.5 s locally and far past
        # 30 s on a slow runner, while the linear version has room to
        # spare on both. A tighter bound would police the constant and
        # flake; this one fails only on the defect.
        body = unit * 20000
        started = time.perf_counter()
        extract_links(body + DEFS, SRC)
        assert time.perf_counter() - started < 30.0, label


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


# ---------------------------------------------------------------------------
# What the shortcut rung must not claim
# ---------------------------------------------------------------------------


class TestTheOtherFormsKeepTheirSpans:
    """Found by self-review, not by the corpora — see the class below.

    The shortcut rung resolves on a span's *text alone*. Every earlier
    reference form needed a second bracket span, which meant a span some
    other family had already claimed could never also be a reference: a
    ``(`` is not a bracket span. That safety was structural, and the
    shortcut rung removed it without anything failing, because no
    generated alphabet in this suite contained a ``(``.
    """

    def test_an_inline_link_is_not_also_a_shortcut(self) -> None:
        # The one most likely to be hit: any inline link whose text
        # happens to match a definition stored a second row, to a
        # different target.
        # [observed: markdown-it-py 'commonmark' renders ``[ar](y.md)``
        # with ``[ar]`` defined as ``<p><a href="y.md">ar</a></p>`` — one
        # link, 2026-09-18]
        rows = [
            (link.link_type, link.raw_target)
            for link in extract_links("[ar](y.md)" + DEFS, SRC)
        ]
        assert rows == [("markdown", "y.md")]

    def test_a_title_does_not_change_that(self) -> None:
        rows = [
            (link.link_type, link.raw_target)
            for link in extract_links("[ar](y.md 'T')" + DEFS, SRC)
        ]
        assert rows == [("markdown", "y.md")]

    def test_an_empty_destination_still_claims_the_span(self) -> None:
        # ``[ar]()`` is a link to the empty string for a reader, so the
        # span is spoken for even though there is nothing to index. The
        # precedence test therefore asks whether the inline form *closed*,
        # not whether it yielded a target — the two differ here and only
        # here.
        assert _links("[ar]()") == []

    def test_a_wikilink_is_not_also_a_shortcut(self) -> None:
        # ``[[a]]`` is a wikilink here, and its inner ``[a]`` is a bracket
        # span like any other, so the rung read it as a reference too.
        # [observed: markdown-it-py 'commonmark' renders ``[[a]]`` with
        # ``[a]`` defined as ``<p>[<a href="y.md">a</a>]</p>`` — this
        # project follows Obsidian here instead, a departure that predates
        # the rung, 2026-09-18]
        rows = [
            (link.link_type, link.target_path)
            for link in extract_links("[[note]]\n\n[note]: y.md\n", SRC)
        ]
        assert rows == [("wikilink", "note.md")]

    def test_an_embed_is_not_either(self) -> None:
        rows = [
            (link.link_type, link.target_path)
            for link in extract_links("![[note]]\n\n[note]: y.md\n", SRC)
        ]
        assert rows == [("wikilink", "note.md")]

    def test_a_shortcut_beside_a_wikilink_still_resolves(self) -> None:
        # The blanking must take the wikilink and nothing else.
        rows = [
            (link.link_type, link.target_path)
            for link in extract_links("[[note]] and [ar]." + DEFS, SRC)
        ]
        assert sorted(rows) == [("reference", "x.md"), ("wikilink", "note.md")]

    def test_a_bracket_run_that_is_no_wikilink_keeps_its_reference(self) -> None:
        # ``[[a]b][ar]`` opens with ``[[`` but never closes ``]]``, so the
        # wikilink scan claims nothing and the full form stands. This is
        # the input a shape test for "inside a ``[[…]]``" gets wrong, and
        # the reason the blanking reuses the scan's own answer.
        # [observed: markdown-it-py 'commonmark' renders ``[[a]b][ar]`` as
        # ``<p><a href="x.md">[a]b</a></p>``, 2026-09-18]
        assert _links("[[a]b][ar]") == [("[a]b", "x.md")]

    def test_an_unclosed_paren_is_still_a_shortcut(self) -> None:
        # And the mirror, which is why the test cannot be a check for a
        # literal ``(``: nothing closes, so the inline form never claimed
        # the span.
        # [observed: markdown-it-py 'commonmark' renders ``[ar](unclosed``
        # as ``<p><a href="x.md">ar</a>(unclosed</p>``, 2026-09-18]
        assert _links("[ar](unclosed") == [("ar", "x.md")]


class TestADefinitionOnlyHidesItsOwnDestination:
    """The greedy definition tail over-matches, so the cut has to be short.

    ``_iter_definition_matches`` reads the destination greedily to the end
    of the line, which was harmless while nothing consumed the span
    bounds. Blanking consumes them, so a line the scan *thinks* is a
    definition took its links with it.
    """

    def test_prose_after_a_false_definition_survives(self) -> None:
        # ``[TODO]: revisit ... later`` is not a definition for a reader:
        # ``revisit`` is the destination and what follows is not a valid
        # title. The row here is one ``main`` stores too.
        # [observed: markdown-it-py 'commonmark' renders
        # ``[TODO]: revisit [a][ar] later`` as
        # ``<p>[TODO]: revisit <a href="x.md">a</a> later</p>``, 2026-09-18]
        assert _links("[TODO]: revisit [a][ar] later") == [("a", "x.md")]

    def test_prose_on_a_definitions_second_line_survives(self) -> None:
        # Same defect by the other route: the destination may sit on the
        # following line, so the greedy tail swallowed that line instead.
        assert _links("[zz]:\nSee [ar] here") == [("ar", "x.md")]

    def test_a_real_definition_is_still_hidden_whole(self) -> None:
        assert extract_links("[ar]: x.md\n", SRC) == []

    def test_a_title_is_hidden_with_its_destination(self) -> None:
        # Otherwise a bracket *inside* a title leaks out as a usage. The
        # cut runs past the destination only when what follows it is
        # exactly a title, never when it is prose.
        assert extract_links('[zz]: y.md "See [ar]"' + DEFS, SRC) == []

    def test_a_definition_with_no_destination_hides_nothing(self) -> None:
        # ``[ar]:`` followed by only whitespace defines nothing, so a
        # reader reads the line as a paragraph and ``[ar]`` in it is a
        # shortcut like any other. Cutting the line would take that row —
        # the same mistake the greedy tail makes one case over, and the
        # one the destination-bounded cut does not by itself avoid.
        # [observed: markdown-it-py 'commonmark' renders ``[ar]:   `` with
        # ``[ar]`` defined as ``<p><a href="x.md">ar</a>:</p>``, 2026-09-18]
        assert _links("[ar]:   ") == [("ar", "x.md")]

    def test_an_unclosed_pointy_destination_hides_nothing_either(self) -> None:
        # The other route to "no destination": ``<`` with no ``>`` before
        # the line ends is not a destination, so the line is not a
        # definition.
        assert _links("[zz]: <unclosed\nSee [ar] here") == [("ar", "x.md")]

    def test_a_pointy_destination_is_hidden_whole(self) -> None:
        # ``<a b.md>`` holds a space, so "the destination ends at the
        # first whitespace" is wrong for the pointy form.
        assert _links("[zz]: <a b.md>\n\nSee [ar].") == [("ar", "x.md")]


class TestLabelsThatDefineNothing:
    def test_a_whitespace_only_label_defines_nothing(self) -> None:
        # §4.7 wants a non-whitespace character in the label. The old
        # pattern's ``[^\]]+`` admitted ``[ ]`` and keyed it ``""``, a
        # third latent table entry of exactly the kind this PR set out to
        # close — and the one that would have made ``[]`` a link.
        assert extract_links("[ ]: x.md\n\nA [] here and [ ] too.\n", SRC) == []

    def test_an_unchecked_task_box_is_not_a_link(self) -> None:
        # Why that entry mattered rather than being a curiosity: ``[ ]``
        # is every unchecked box in the vault.
        assert extract_links("- [ ] a task\n- [x] done\n\n[ ]: x.md\n", SRC) == []

    def test_a_blank_line_holding_spaces_still_bounds_a_label(self) -> None:
        # A blank line is one holding only spaces or tabs, so testing for
        # a literal ``\n\n`` let the label-swallowing defect through on
        # the whitespace-dirty spelling — the likelier one in a
        # hand-edited note.
        assert _links("[ar]\n[\n   \n", "\n[ar]: x.md\n") == [("ar", "x.md")]


class TestFootnotesStayProse:
    def test_a_footnote_reference_is_not_a_links_text(self) -> None:
        # #1104's *text*-side guard. The ladder subsumes the label side —
        # a footnote definition is prose, so it never enters the table —
        # but ``[^a][r]`` has a real label in its second span, and the
        # ladder makes a link whose text is a footnote reference. Plain
        # CommonMark agrees with the ladder; GFM reads ``[^a]`` as a
        # footnote, and following GFM here is this project's deliberate
        # departure.
        assert extract_links("See [^a][ar] here." + DEFS, SRC) == []


class TestAgreementWhereTheOtherFormsMeet:
    r"""The corpus that would have caught the class above.

    Every generated alphabet in this suite was built from the characters
    the form under test needed. None contained a ``(``, so no property
    here could see the shortcut rung colliding with the inline family —
    the defect was found by reading, which is the weaker instrument. This
    corpus adds the parenthesis and the space, and compares **both**
    families' rows against a CommonMark reader at once.

    Rows are compared as a sorted multiset rather than in order, because
    ``extract_links`` returns them grouped by kind (``[*inline,
    *reference, *wiki]``) and a reader returns them in document order. The
    reference-only corpora in ``tests/test_links_reference_openers.py``
    still check order, so nothing is lost.

    Two classes are excluded, both places where **markdown-it departs
    from the spec** rather than places we are unsure. Each is pinned by a
    named test below, so excluding it from the sweep hides nothing:

    * a body ending in ``(`` — markdown-it abandons the whole link when
      only whitespace follows the ``(`` to the end of the inline block,
      where §6.3 says a failed inline attempt falls back to a reference;
    * a whitespace-only second span ``[ ]`` — §4.7 wants a non-whitespace
      character in a label, so it is no label and the ladder falls
      through; markdown-it normalises it to ``""``, misses, and stops.

    Empty destinations are dropped from the reader's side: ``[a]()`` is a
    link to the empty string, which is not a vault path and is nothing to
    index. Destinations are compared percent-decoded, since ``raw_target``
    is the destination *as written* by contract.
    """

    ALPHABET = "[]()a "
    DEFS = "\n\n[a]: x.md\n"

    @staticmethod
    def _ours(body: str) -> list[tuple[str, str]]:
        return sorted(
            (link.link_text, link.raw_target)
            for link in extract_links(
                body + TestAgreementWhereTheOtherFormsMeet.DEFS, SRC
            )
            if link.link_type in ("markdown", "reference")
        )

    @staticmethod
    def _theirs(markdown_it: object, body: str) -> list[tuple[str, str]]:
        from urllib.parse import unquote

        out: list[tuple[str, str]] = []

        def walk(children: list) -> None:  # type: ignore[type-arg]
            href: str | None = None
            text = ""
            for child in children:
                if child.type == "link_open":
                    href, text = child.attrGet("href"), ""
                elif child.type == "link_close" and href is not None:
                    out.append((text, unquote(href)))
                    href = None
                elif child.type == "image":
                    # An image description may contain a link (Ex. 575).
                    walk(child.children or [])
                elif href is not None:
                    text += "\n" if child.type == "softbreak" else child.content

        for token in markdown_it.parse(  # type: ignore[attr-defined]
            body + TestAgreementWhereTheOtherFormsMeet.DEFS
        ):
            if token.children:
                walk(token.children)
        return sorted(pair for pair in out if pair[1])

    @pytest.mark.parametrize("length", range(1, 7), ids=lambda n: f"len{n}")
    def test_both_families_agree_over_a_parenthesis_alphabet(self, length: int) -> None:
        markdown_it = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
        trailing_open = re.compile(r"\([ \t]*$")
        empty_label = re.compile(r"\[[ \t]+\]")
        for chars in itertools.product(self.ALPHABET, repeat=length):
            body = "".join(chars)
            if trailing_open.search(body) or empty_label.search(body):
                continue
            rows = extract_links(body + self.DEFS, SRC)
            if any(link.link_type == "wikilink" for link in rows):
                # ``[[a]]`` is a wikilink here and two CommonMark
                # shortcuts to a reader — a long-standing deliberate
                # departure, and the same skip the other two corpora
                # carry. Worth spelling out why it is *needed* now: until
                # the wikilink spans were blanked out of the reference
                # scan, such an input produced a reference row too, and
                # that spurious row happened to match the reader. The
                # comparison passed because two wrongs lined up.
                continue
            assert self._ours(body) == self._theirs(markdown_it, body), body

    def test_a_body_ending_in_an_open_paren_is_a_shortcut(self) -> None:
        # The first excluded class, pinned. §6.3 falls back when the
        # inline attempt fails, and nothing closes this one.
        # [observed: markdown-it-py 'commonmark' renders ``[a](`` with
        # ``[a]`` defined as ``<p>[a](</p>`` — no link, and no fallback,
        # which is the departure, 2026-09-18]
        assert _links("[ar](") == [("ar", "x.md")]

    def test_a_whitespace_only_second_span_falls_through(self) -> None:
        # The second. ``[ ]`` holds no non-whitespace character, so §4.7
        # says it is not a label at all and the ladder reaches the text.
        # [observed: markdown-it-py 'commonmark' renders ``[ar][ ]`` as
        # ``<p>[ar][ ]</p>``, 2026-09-18]
        assert _links("[ar][ ]") == [("ar", "x.md")]
