r"""The wikilink matcher is linear on a bracket run (#1343).

``\[\[([^\]|\n]+)(?:\|([^\]]+))?\]\]`` rescans forward from every ``[[``,
so a body of ``[`` that never closes costs O(n²): the issue measured
``extract_links`` at about 29 s on 40000 of them, and one such file stalls
a whole vault, because indexing runs single-threaded over one write queue.
The shape is not hypothetical — a bracketed-citation export gone wrong, or
pasted log or JSON that was never fenced.

``_find_wikilink`` replaces the pattern with a scan. What makes it linear
is that all those retries ask the same question: a target ends at the first
``]``, ``|`` or line ending after the ``[[``, and that stop is the same
character whichever ``[[`` of a run opened it, so one failure at the stop
is a failure for every opener before it and the search resumes past the
stop.

Two properties, as in the two escape-aware scans this follows. The first is
the bound. The second is that nothing else moved: the scan agrees with the
pattern it replaced on every short string over the characters that pattern
can distinguish. This change alters no row for any input, which is why it
needs no ``INDEX_SEMANTICS_VERSION`` note — a claim only the second
property can carry.
"""

from __future__ import annotations

import itertools
import re
import time

import pytest

from markdown_vault_mcp.scanner import _find_wikilink, extract_links

SRC = "source.md"

#: The pattern the scan replaced. Kept here, not imported, precisely
#: because it no longer exists in the scanner.
_SUPERSEDED_WIKILINK = re.compile(r"\[\[([^\]|\n]+)(?:\|([^\]]+))?\]\]")


def _scan(text: str) -> list[tuple[int, str, str | None]]:
    """Every wikilink the scan finds, resuming as the caller does."""
    found: list[tuple[int, str, str | None]] = []
    pos = 0
    while (link := _find_wikilink(text, pos)) is not None:
        pos = link[0]
        found.append(link)
    return found


def _regex(text: str) -> list[tuple[int, str, str | None]]:
    """The same, as ``finditer`` over the superseded pattern gave it."""
    return [
        (m.end(), m.group(1), m.group(2)) for m in _SUPERSEDED_WIKILINK.finditer(text)
    ]


# ---------------------------------------------------------------------------
# The bound
# ---------------------------------------------------------------------------


class TestTheScanIsLinear:
    def test_the_issue_input_no_longer_stalls(self) -> None:
        # 40000 unmatched ``[`` — the issue's own headline case, measured
        # there at about 29 s for the whole extraction. A generous wall
        # clock rather than a tight one, so a loaded runner does not make
        # this flaky: the quadratic version exceeded it by orders of
        # magnitude and no linear one comes close to it.
        started = time.perf_counter()
        assert extract_links("[" * 40000, SRC) == []
        assert time.perf_counter() - started < 5.0

    # There is deliberately no growth-ratio test here, and the reason is
    # worth recording: one was written, and it went flaky on CI within a
    # day. Distinguishing linear (2x per doubling) from quadratic (4x)
    # needs roughly ±40% accuracy, and the linear scan over 20000 openers
    # takes about 50 microseconds — far too small a quantity to measure
    # that closely on a shared runner. It read 3.6x on a green commit
    # whose growth is provably 2x, which makes it an instrument that
    # reports on the runner rather than on the code.
    #
    # The wall clock below is the instrument that fits: the defect's
    # signature is four orders of magnitude (0.03 ms against 515 ms at
    # 32000), so a bound placed between them is crossed by the defect and
    # nowhere near the truth on a slow day. Its weakness is the honest
    # trade: a future regression milder than the bound would slip through
    # where a working ratio test would have caught it.

    @pytest.mark.parametrize(
        "region",
        ["[[a|" * 100000, "[[a|b" * 100000 + "]"],
        ids=["unterminated-alias", "alias-then-lone-bracket"],
    )
    def test_the_alias_shapes_do_not_stall_either(self, region: str) -> None:
        # The two shapes the scan's first version got wrong: a pipe ends a
        # target too, and the alias search that follows it resumed at the
        # pipe rather than past its own reading, so every opener re-ran it
        # (#1524 review). The second shape reaches the same blowup by a
        # different route, where the alias search succeeds and the ``]]``
        # test fails.
        #
        # 100000 and one second, rather than the 40000 and five seconds
        # the bare-opener case above uses, because a bound has to be one
        # the defect would actually cross: the quadratic version of this
        # shape cost about 0.8 s at 40000, which 5 s would have waved
        # through. At 100000 it costs several seconds and the linear one
        # costs under a millisecond.
        started = time.perf_counter()
        assert extract_links(region, SRC) == []
        assert time.perf_counter() - started < 1.0

    def test_an_unclosed_run_inside_real_prose_is_still_scanned(self) -> None:
        # The run must not swallow the links around it.
        note = "See [[real note]].\n\n" + "[" * 5000 + "\n\nAnd [[other]].\n"
        assert [link.target_path for link in extract_links(note, SRC)] == [
            "real note.md",
            "other.md",
        ]


# ---------------------------------------------------------------------------
# The property: nothing else moved
# ---------------------------------------------------------------------------


class TestTheScanAgreesWithThePatternItReplaced:
    @pytest.mark.parametrize("length", range(9), ids=lambda n: f"len{n}")
    def test_no_input_changed_meaning(self, length: int) -> None:
        # Exhaustive over the characters the pattern can distinguish: the
        # bracket that opens, the one that closes, the pipe that starts an
        # alias, the line ending the target may not hold, and a filler
        # standing for everything else. The whole sequence is compared, so
        # the scan's resume points are pinned against ``finditer``'s
        # non-overlapping ones.
        for chars in itertools.product("[]|\na", repeat=length):
            text = "".join(chars)
            assert _scan(text) == _regex(text), text

    @pytest.mark.parametrize(
        "text",
        [
            "[[a]]",
            "[[a|b]]",
            "[[[a]]",
            "[[a]]]",
            "[[a|b|c]]",
            "[[a|b\nc]]",
            "[[a\nb]]",
            "[[]]",
            "[[a|]]",
            "[[a|b]",
            "[[a[[b]]",
            "[[a]] and [[b|c]]",
            "![[a]]",
            "no brackets at all",
        ],
        ids=[
            "plain",
            "alias",
            "three-openers",
            "three-closers",
            "pipe-in-alias",
            "line-ending-in-alias",
            "line-ending-in-target",
            "empty-target",
            "empty-alias",
            "unterminated",
            "nested-opener",
            "two-links",
            "embed",
            "none",
        ],
    )
    def test_the_named_shapes_agree_too(self, text: str) -> None:
        assert _scan(text) == _regex(text)

    def test_the_leftmost_opener_still_wins(self) -> None:
        # ``[[[a]]`` links ``[a``, not ``a``: the pattern's leftmost match
        # starts at the first ``[[``, and the target class admits ``[``.
        # Spelled out because it is the one case where "leftmost" is
        # visible in the stored row.
        assert _scan("[[[a]]") == [(6, "[a", None)]

    def test_a_note_of_real_wikilinks_indexes_identically(self) -> None:
        note = (
            "# Title\n\n"
            "See [[one]] and [[two|the second]].\n\n"
            "An embed ![[three]], a fragment [[four#Heading]], and a table\n"
            "cell [[five\\|aliased]].\n"
        )
        assert [
            (link.target_path, link.link_text, link.fragment)
            for link in extract_links(note, SRC)
        ] == [
            ("one.md", "one", None),
            ("two.md", "the second", None),
            ("three.md", "three", None),
            # ``link_text`` is the target as written, fragment included:
            # it is read before the ``#`` split, which is pre-existing and
            # unchanged here.
            ("four.md", "four#Heading", "Heading"),
            ("five.md", "aliased", None),
        ]
