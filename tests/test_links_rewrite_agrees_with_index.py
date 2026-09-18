r"""A rename rewrites exactly the links the index holds (#1521).

#1517 taught the *read* side that a ``\]`` does not close a link's text.
The *write* side kept the negated class, so after that change the two
disagreed about which links exist: ``rename`` found the backlink row for
``[Bra\]cket](old.md)``, computed a replacement, matched nothing, wrote the
file back byte-identical — and reported ``updated_links=1``. The link went
on naming a file that no longer existed, and the operation said it had
handled it.

The fix is one primitive, not two: ``find_inline_link_open`` moved into
``utils/links.py`` and both sides call it. So the property this module
pins is not "the rewrite still behaves as it did" but the stronger and more
useful one — **for any content, the rewrite replaces exactly the
destinations the index stored**. That is the invariant whose absence made
the defect possible, and it is asserted over generated bracket soup rather
than by example.

The second half of the report is here too: a rewrite that matches nothing
is no longer counted as an updated source. It is logged instead, because a
read/write disagreement is a defect to see, not a no-op to absorb.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.scanner import HeadingChunker, extract_links, scan_directory
from markdown_vault_mcp.utils.links import apply_link_replacement

if TYPE_CHECKING:
    from pathlib import Path

SRC = "source.md"

#: Chosen so a replacement changes the content's length by exactly one
#: character, which lets a test count replacements without re-parsing.
OLD_TARGET = "x.md"
NEW_TARGET = "yy.md"
_GROWTH = len(NEW_TARGET) - len(OLD_TARGET)


def _indexed(content: str) -> int:
    """How many markdown links the index stores for ``OLD_TARGET``."""
    return sum(
        1
        for link in extract_links(content, SRC)
        if link.link_type == "markdown" and link.raw_target == OLD_TARGET
    )


def _rewritten(content: str) -> int:
    """How many destinations :func:`apply_link_replacement` replaced."""
    after = apply_link_replacement(content, "markdown", OLD_TARGET, NEW_TARGET)
    return (len(after) - len(content)) // _GROWTH


def _vault(tmp_path: Path, source: str) -> DocumentManager:
    (tmp_path / "old.md").write_text("# Old\n\nbody\n", encoding="utf-8")
    (tmp_path / SRC).write_text(source, encoding="utf-8")
    fts = FTSIndex(db_path=":memory:")
    chunker = HeadingChunker()
    for note in scan_directory(tmp_path, chunk_strategy=chunker):
        fts.upsert_note(note)
    return DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=chunker,
        read_only=False,
    )


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


class TestAnEscapedBracketInLinkText:
    def test_the_destination_is_rewritten(self) -> None:
        assert (
            apply_link_replacement(
                r"See [Bra\]cket](old.md) here.", "markdown", "old.md", "new.md"
            )
            == r"See [Bra\]cket](new.md) here."
        )

    def test_a_rename_moves_the_link_with_the_file(self, tmp_path: Path) -> None:
        # The issue's own reproduction, end to end.
        source = "See [Bra\\]cket](old.md) here.\n"
        manager = _vault(tmp_path, source)
        result = manager.rename("old.md", "new.md", update_links=True)
        assert (tmp_path / SRC).read_text(encoding="utf-8") == (
            "See [Bra\\]cket](new.md) here.\n"
        )
        assert result.updated_links == 1

    def test_the_control_still_works(self) -> None:
        assert (
            apply_link_replacement(
                "See [Bracket](old.md) here.", "markdown", "old.md", "new.md"
            )
            == "See [Bracket](new.md) here."
        )

    def test_a_title_is_still_kept(self) -> None:
        assert (
            apply_link_replacement(
                'See [a](old.md "t") here.', "markdown", "old.md", "new.md"
            )
            == 'See [a](new.md "t") here.'
        )


# ---------------------------------------------------------------------------
# The property the fix rests on
# ---------------------------------------------------------------------------


class TestTheRewriteSeesWhatTheIndexSees:
    """For any content, replaced destinations == stored destinations."""

    @pytest.mark.parametrize("length", range(8), ids=lambda n: f"len{n}")
    def test_every_short_string_agrees(self, length: int) -> None:
        # Exhaustive over the characters that decide the question: the two
        # brackets, the two parentheses, the ``!`` that makes an image, and
        # the backslash the defect turned on. Each string is also tried with
        # the destination spliced into its first ``(``, so the matching and
        # non-matching cases are both covered.
        for chars in itertools.product("[]()!\\", repeat=length):
            base = "".join(chars)
            for content in (base, base.replace("(", f"({OLD_TARGET}", 1)):
                assert _indexed(content) == _rewritten(content), content

    @pytest.mark.parametrize("length", range(6), ids=lambda n: f"len{n}")
    def test_a_wider_alphabet_agrees_too(self, length: int) -> None:
        # The same property over the characters a real destination is made
        # of, so the agreement is not an artifact of an alphabet that holds
        # only punctuation. Shorter strings, since the alphabet is wider;
        # exhaustive rather than sampled so it cannot drift between runs.
        for chars in itertools.product("[]()!\\ax.", repeat=length):
            content = "".join(chars)
            assert _indexed(content) == _rewritten(content), content


class TestTheAgreementNowCostsNothing:
    def test_an_image_whose_alt_text_opens_a_bracket_is_a_link_on_both_sides(
        self,
    ) -> None:
        # This case used to be the price of the read/write agreement. The
        # scanner's ``!`` lookbehind read ``![[](old.md)`` as an image and
        # stored no row, where CommonMark reads ``![`` as literal text
        # followed by an empty link; the superseded rewrite pattern
        # replaced it anyway, via the retry a failed lookbehind triggers.
        # Aligning the rewrite with the index meant leaving it alone.
        #
        # #1526 fixed the read side instead, so the two now agree *and*
        # agree with CommonMark: the link is indexed and it is rewritten.
        # [observed: markdown-it-py renders it ``![<a href="old.md"></a>``,
        # 2026-09-18]
        content = "![[](old.md)"
        assert [link.raw_target for link in extract_links(content, SRC)] == ["old.md"]
        assert (
            apply_link_replacement(content, "markdown", "old.md", "new.md")
            == "![[](new.md)"
        )

    def test_an_ordinary_image_is_still_left_alone(self) -> None:
        content = "![alt](old.md)"
        assert extract_links(content, SRC) == []
        assert (
            apply_link_replacement(content, "markdown", "old.md", "new.md") == content
        )


class TestTheRewriteStaysLinear:
    def test_a_run_of_unclosed_brackets_does_not_stall(self) -> None:
        # The write side carried its own instance of #1343: the pattern this
        # replaces retried from every ``[`` and cost 291 ms on 8000 of them,
        # quadrupling per doubling. A rename over a vault holding one such
        # file paid that on every source. The bound is generous on purpose;
        # the scan does this in under two milliseconds.
        started = time.perf_counter()
        content = "[" * 200000
        assert (
            apply_link_replacement(content, "markdown", "old.md", "new.md") == content
        )
        assert time.perf_counter() - started < 5.0

    def test_a_paragraph_of_escaped_brackets_does_not_stall(self) -> None:
        # And the shape an escape-aware character class could not have
        # afforded: it costs about 5 s on 39 KB of this, quadrupling.
        started = time.perf_counter()
        content = "[a\\]b" * 100000
        assert (
            apply_link_replacement(content, "markdown", "old.md", "new.md") == content
        )
        assert time.perf_counter() - started < 5.0


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------


class TestASourceTheRewriteCannotChange:
    def test_it_is_not_counted_and_is_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A row the rewrite cannot find is a disagreement between index and
        # file, which is what #1521 was. It used to be counted as an updated
        # source and pass unseen; now the source is left out of the count
        # and the mismatch is logged. Driven through the private helper with
        # a target the file does not contain, because the defect that
        # produced it for real is fixed above.
        (tmp_path / SRC).write_text("no links here\n", encoding="utf-8")
        manager = DocumentManager(
            fts=FTSIndex(db_path=":memory:"),
            source_dir=tmp_path,
            write_lock=threading.RLock(),
            chunk_strategy=HeadingChunker(),
            read_only=False,
        )
        with caplog.at_level(logging.WARNING):
            content = manager._rewrite_one_source(
                tmp_path / SRC,
                [("markdown", "absent.md", None, "absent.md", "moved.md")],
                SRC,
            )
        assert content is None
        assert "link_rewrite_matched_nothing" in caplog.text
        assert (tmp_path / SRC).read_text(encoding="utf-8") == "no links here\n"

    def test_a_rename_does_not_count_it(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The same disagreement reached the way it happens in the wild: the
        # note is indexed with its link, then edited outside the server, so
        # the row outlives the link it describes. The rename must not claim
        # to have updated a file it could not touch.
        manager = _vault(tmp_path, "See [a](old.md) here.\n")
        (tmp_path / SRC).write_text("the link is gone now\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            result = manager.rename("old.md", "new.md", update_links=True)
        assert result.updated_links == 0
        assert "link_rewrite_matched_nothing" in caplog.text
        assert (tmp_path / SRC).read_text(encoding="utf-8") == "the link is gone now\n"
