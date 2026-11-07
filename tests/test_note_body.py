"""The body the index reads keeps its first line's indentation (#2036).

python-frontmatter strips the body at both ends, so a note opening with an
indented code block lost the indentation and its ``#`` line became a
heading and the title. ``note_body`` restores only that indentation, so
every other note must read exactly as before.
"""

from __future__ import annotations

import itertools
import time
from pathlib import Path  # noqa: TC003

import pytest

from markdown_vault_mcp.scanner import (
    extract_section,
    list_section_headings,
    note_body,
    parse_frontmatter,
    parse_note,
)

_CASES = [
    ("indented_code", "    # not a heading\n\ntext\n", "    # not a heading\n\ntext"),
    (
        "after_frontmatter",
        "---\ntitle: T\n---\n    # not a heading\n\ntext\n",
        "    # not a heading\n\ntext",
    ),
    ("blank_lines_still_dropped", "\n\n    code\n# H\n", "    code\n# H"),
    ("tab_indent", "\t- tabbed\n", "\t- tabbed"),
    ("plain", "# Plain\n\nbody\n", "# Plain\n\nbody"),
    ("whitespace_only_stays_empty", "---\nx: 1\n---\n   \n  \n", ""),
    ("crlf", "\r\n   x\r\ny\r\n", "   x\ny"),
]


@pytest.mark.parametrize(
    ("text", "expected"), [c[1:] for c in _CASES], ids=[c[0] for c in _CASES]
)
def test_note_body(text: str, expected: str) -> None:
    assert note_body(text) == expected


def test_only_spaces_and_tabs_are_restored() -> None:
    # A non-breaking space is content to CommonMark, not indentation; the
    # stripped body is kept as python-frontmatter returns it.
    assert note_body("\u00a0x\n") == parse_frontmatter("\u00a0x\n").content


def test_long_leading_whitespace_is_linear() -> None:
    # 200,000 spaces then a line break: a regex search for the trailing
    # indentation backtracks quadratically on this (seconds); slicing does not.
    text = " " * 200_000 + "\n  x\n"
    start = time.perf_counter()
    assert note_body(text) == "  x"
    assert time.perf_counter() - start < 0.5


def _unindented_corpus() -> list[str]:
    starts = ["", "\n", "\n\n", "---\na: 1\n---\n", "---\na: 1\n---\n\n"]
    firsts = ["# H", "text", "- item", "```", "[x](y.md)", "\u00a0nbsp"]
    rests = ["", "\n", "\nbody\n", "\n    code\n", "\n\n## H2\n  \n"]
    return [s + f + r for s, f, r in itertools.product(starts, firsts, rests)]


@pytest.mark.parametrize("text", _unindented_corpus())
def test_unindented_note_reads_exactly_as_before(text: str) -> None:
    assert note_body(text) == parse_frontmatter(text).content


def test_indexed_note_keeps_indented_code(tmp_path: Path) -> None:
    (tmp_path / "n.md").write_text("    # not a heading\n\ntext\n", encoding="utf-8")
    note = parse_note(tmp_path / "n.md", tmp_path)
    assert note.title == "n"
    assert note.headings == []
    assert note.chunks[0].content.startswith("    # not a heading")
    assert note.content_chars == len("    # not a heading\n\ntext")


def test_section_scan_and_read_see_the_same_body() -> None:
    text = "    # code\n\n# Real\nbody\n"
    assert list_section_headings(text) == ["Real"]
    assert extract_section(text, "code") is None
    assert extract_section(text, "Real") == "body"


def test_headings_agree_with_a_commonmark_parser_on_indented_openings() -> None:
    md = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
    indents = ["", " ", "   ", "    ", "\t", "      "]
    firsts = ["# a", "```", "text"]
    rests = ["\n# b\n", "\n\n# b\n", "\n```\n# c\n"]
    for lead, indent, first, rest in itertools.product(
        ["", "\n\n"], indents, firsts, rests
    ):
        text = lead + indent + first + rest
        tokens = md.parse(text)
        theirs = [
            tokens[i + 1].content
            for i, tok in enumerate(tokens)
            if tok.type == "heading_open" and tok.markup.startswith("#")
        ]
        # Indented ATX headings (1-3 spaces) are a separate recorded departure
        # of _HEADING_RE (docs/design/reference/commonmark-gfm.md); skip them.
        if indent in (" ", "   ") and first == "# a":
            continue
        assert list_section_headings(text) == theirs, repr(text)
