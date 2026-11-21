"""A trivial edit keeps ``generated.at`` (OKF 0.3 §5.2, #2418).

Trivial means a reader provably cannot see the change: the frontmatter is
equal apart from ``generated`` and ``verified``, and the body parses to the
same CommonMark tokens.
"""

from __future__ import annotations

import datetime as dt

import pytest

from markdown_vault_mcp._trivial_edit import is_trivial_edit
from markdown_vault_mcp.okf import apply_okf_write_stamp
from markdown_vault_mcp.scanner import parse_frontmatter

HEAD = "---\ntitle: T\ntags: [a]\ngenerated: {by: old/1, at: '2026-01-01T00:00:00Z'}\n---\n"
NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Some *text*.\n", "Some *text*.\n"),  # unchanged
        ("Some text.\n", "Some text.   \n\n\n"),  # trailing whitespace
        ("Some *text*.\n", "Some _text_.\n"),  # emphasis marker
        ("- a\n- b\n", "* a\n* b\n"),  # bullet marker
        ("```py\nx = 1\n```\n", "~~~py\nx = 1\n~~~\n"),  # fence marker
        ("# H\n\nPara.\n", "# H #\n\n\n\nPara.\n"),  # closing hashes, blank lines
        ("H\n=\n\nPara.\n", "# H\n\nPara.\n"),  # setext and ATX h1
    ],
)
def test_a_format_or_whitespace_change_is_trivial(before: str, after: str) -> None:
    assert is_trivial_edit(HEAD + before, HEAD + after)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Some text.\n", "Some test.\n"),  # a word
        ("Line one\nline two\n", "Line one  \nline two\n"),  # hard break
        ("- a\n- b\n", "- a\n  - b\n"),  # nesting
        ("See [x](a.md).\n", "See [x](b.md).\n"),  # link destination
        ("```py\nx = 1\n```\n", "```py\nx = 2\n```\n"),  # code
        ("```py\nx = 1\n```\n", "```js\nx = 1\n```\n"),  # info string
        ("Text.\n<!-- a -->\n", "Text.\n<!-- b -->\n"),  # HTML comment
        ("One line\nwrapped.\n", "One line wrapped.\n"),  # reflow
    ],
)
def test_a_visible_change_is_meaningful(before: str, after: str) -> None:
    assert not is_trivial_edit(HEAD + before, HEAD + after)


def test_a_frontmatter_change_is_meaningful() -> None:
    assert not is_trivial_edit(HEAD + "x\n", HEAD.replace("[a]", "[a, b]") + "x\n")


def test_a_change_to_only_the_server_keys_is_trivial() -> None:
    other = HEAD.replace("old/1", "other/2") + "x\n"
    head_without_closing = HEAD.removesuffix("---\n")
    with_review = (
        head_without_closing + "verified: {by: human:a, at: '2026-02-01'}\n---\n"
    )
    assert is_trivial_edit(HEAD + "x\n", other)
    assert is_trivial_edit(HEAD + "x\n", with_review + "x\n")


def test_unreadable_frontmatter_is_never_trivial() -> None:
    assert not is_trivial_edit("---\n: [bad\n---\nx\n", "---\n: [bad\n---\nx\n")


def test_a_trivial_edit_keeps_the_stored_generated() -> None:
    stored = HEAD + "Some *text*.\n"
    out = apply_okf_write_stamp(
        HEAD + "Some _text_.\n", actor="x/9", now=NOW, stored=stored
    )
    assert parse_frontmatter(out).metadata["generated"] == {
        "by": "old/1",
        "at": "2026-01-01T00:00:00Z",
    }
    assert out.endswith("Some _text_.\n")


def test_a_trivial_edit_adds_no_generated_where_the_note_had_none() -> None:
    stored = "---\ntitle: T\n---\nSome text.\n"
    out = apply_okf_write_stamp(
        "---\ntitle: T\n---\nSome text.  \n", actor="x/9", now=NOW, stored=stored
    )
    assert "generated" not in parse_frontmatter(out).metadata


def test_a_meaningful_edit_still_moves_generated() -> None:
    stored = HEAD + "Some text.\n"
    out = apply_okf_write_stamp(
        HEAD + "Other text.\n", actor="x/9", now=NOW, stored=stored
    )
    assert parse_frontmatter(out).metadata["generated"] == {
        "by": "x/9",
        "at": "2026-11-21T12:00:00Z",
    }
