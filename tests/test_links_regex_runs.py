"""Note-parsing regexes do not retry from inside long runs (#1749)."""

from __future__ import annotations

import itertools
import re
import time

import pytest

from markdown_vault_mcp.scanner import (
    _RE_INLINE_CODE,
    _strip_inline_code,
    extract_links,
)
from markdown_vault_mcp.utils.links import trailing_title_pattern

_OLD_CODE = re.compile(r"`+[^`\n]+`+")
_OLD_TITLE = re.compile(
    r"""[ \t]+(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))[ \t]*$"""
)


@pytest.mark.parametrize("kind", ["backticks", "spaces", "tabs"])
def test_a_failed_run_does_not_stall(kind: str) -> None:
    # Measure only the owner, not unrelated parsing work. The old patterns
    # take seconds at this size; the guarded versions take milliseconds.
    if kind == "backticks":
        text = "`" * 40000 + "a\n"
        started = time.perf_counter()
        assert _strip_inline_code(text) == text
    else:
        text = "a" + (" " if kind == "spaces" else "\t") * 40000 + "b"
        started = time.perf_counter()
        assert trailing_title_pattern.search(text) is None
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("length", range(9))
def test_inline_code_keeps_the_same_spans(length: int) -> None:
    for chars in itertools.product("`\na", repeat=length):
        text = "".join(chars)
        assert [m.span() for m in _RE_INLINE_CODE.finditer(text)] == [
            m.span() for m in _OLD_CODE.finditer(text)
        ], text


def test_titles_keep_the_same_spans_and_fullmatches() -> None:
    # Each title delimiter, escapes, both separators and a non-separator
    # whitespace character are included. Longer inputs cover escaped
    # delimiters and mixed runs beyond the exhaustive short-input bound.
    alphabet = " \t\n\u00a0\"'()\\a"
    texts = itertools.chain(
        (
            "".join(chars)
            for length in range(6)
            for chars in itertools.product(alphabet, repeat=length)
        ),
        (
            f"a{gap}{opening}a\\{opening}b{closing}{tail}"
            for gap in (" ", "\t", " \t" * 100)
            for opening, closing in (('"', '"'), ("'", "'"), ("(", ")"))
            for tail in ("", "\n", "  ", " bad", " \\")
        ),
    )
    for text in texts:
        assert [m.span() for m in trailing_title_pattern.finditer(text)] == [
            m.span() for m in _OLD_TITLE.finditer(text)
        ], text
        assert bool(trailing_title_pattern.fullmatch(text)) == bool(
            _OLD_TITLE.fullmatch(text)
        )


@pytest.mark.parametrize(
    ("body", "targets"),
    [
        ("`" * 4000 + "a\n", []),
        ("[x](a" + " " * 4000 + "b)\n", ["a" + " " * 4000 + "b"]),
    ],
)
def test_issue_inputs_leave_surrounding_links_visible(
    body: str, targets: list[str]
) -> None:
    note = "See [[before]].\n\n" + body + '\nSee [after](after.md "title").'
    assert [link.target_path for link in extract_links(note, "source.md")] == [
        *targets,
        "after.md",
        "before.md",
    ]
