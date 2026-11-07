"""A line inside fenced code is never a heading (#2034).

The rules are CommonMark §4.5, as recorded in
``docs/design/reference/commonmark-gfm.md``. One scan applies them to the
chunker's split points, the section scan behind ``read(path, section=...)``
and the outline, and the first-H1 title, so each is pinned here.
"""

from __future__ import annotations

import itertools
from pathlib import Path  # noqa: TC003

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import (
    HeadingChunker,
    extract_section,
    list_section_headings,
    parse_note,
)
from markdown_vault_mcp.types import TocEntry

# (id, body, headings the body has)
_CASES = [
    ("backticks", "# A\n```\n# no\n```\n# B\n", ["A", "B"]),
    ("tildes", "# A\n~~~\n# no\n~~~\n# B\n", ["A", "B"]),
    ("info_string", "# A\n```bash\n# no\n```\n# B\n", ["A", "B"]),
    ("indented_opener", "# A\n   ```\n# no\n   ```\n# B\n", ["A", "B"]),
    ("longer_closer", "# A\n```\n# no\n`````\n# B\n", ["A", "B"]),
    (
        "shorter_closer_does_not_close",
        "# A\n````\n# no\n```\n# no2\n````\n# B\n",
        ["A", "B"],
    ),
    ("other_char_does_not_close", "# A\n```\n# no\n~~~\n# no2\n```\n# B\n", ["A", "B"]),
    (
        "closer_with_text_does_not_close",
        "# A\n```\n# no\n``` x\n# no2\n```\n# B\n",
        ["A", "B"],
    ),
    ("unclosed_runs_to_end", "# A\n```\n# no\n# no2\n", ["A"]),
    ("four_space_opener_is_indented_code", "# A\n    ```\n# B\n    ```\n", ["A", "B"]),
    ("backtick_in_info_is_not_a_fence", "# A\n``` a`b\n# B\n", ["A", "B"]),
    ("tilde_info_may_hold_backticks", "# A\n~~~ a`b\n# no\n~~~\n# B\n", ["A", "B"]),
    ("two_backticks_are_not_a_fence", "# A\n``\n# B\n``\n", ["A", "B"]),
    ("crlf", "# A\r\n```\r\n# no\r\n```\r\n# B\r\n", ["A", "B"]),
]


@pytest.mark.parametrize(
    ("body", "expected"),
    [c[1:] for c in _CASES],
    ids=[c[0] for c in _CASES],
)
def test_section_scan_skips_fenced_code(body: str, expected: list[str]) -> None:
    assert list_section_headings(body) == expected


def test_section_read_keeps_its_code_block_whole() -> None:
    body = "## Setup\n\n```bash\n# install deps\npip install x\n```\n\n## Use\ny\n"
    assert extract_section(body, "Setup") == (
        "\n```bash\n# install deps\npip install x\n```\n\n"
    )
    assert extract_section(body, "install deps") is None


def test_chunker_does_not_split_inside_fenced_code() -> None:
    body = "# A\nintro\n```python\n# comment\nx = 1\n```\n# B\nend\n"
    chunks = HeadingChunker(short_doc_lines=0).chunk(body, {})
    assert [(c.heading, c.heading_level) for c in chunks] == [("A", 1), ("B", 1)]
    assert "# comment" in chunks[0].content


def test_adaptive_resplit_does_not_split_inside_fenced_code() -> None:
    para = "word " * 40 + "\n\n"
    body = "## A\n" + para + "```\n### no\n```\n" + para + "### A1\nx\n"
    chunks = HeadingChunker(short_doc_lines=0, max_chunk_words=50).chunk(body, {})
    assert "no" not in {c.heading for c in chunks}
    assert "A1" in {c.heading for c in chunks}


def _parse(tmp_path: Path, body: str, name: str = "note.md") -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return parse_note(path, tmp_path).title


def test_title_skips_a_comment_in_fenced_code(tmp_path: Path) -> None:
    assert _parse(tmp_path, "```sh\n# comment\n```\n# Real title\n") == "Real title"


def test_title_falls_back_to_filename_when_only_code_has_hashes(
    tmp_path: Path,
) -> None:
    assert _parse(tmp_path, "```sh\n# comment\n```\ntext\n", "my-note.md") == "my-note"


def test_outline_leaves_out_fenced_code(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text(
        "## Setup\n```bash\n# install deps\n```\n## Use\ny\n", encoding="utf-8"
    )
    fts = FTSIndex(db_path=":memory:")
    fts.upsert_note(parse_note(tmp_path / "note.md", tmp_path))
    assert fts.get_toc("note.md") == [TocEntry("Setup", 2), TocEntry("Use", 2)]


def _corpus() -> list[str]:
    """Bodies mixing column-0 headings with every fence shape in _CASES.

    Headings are kept to the shapes the scan already agrees with CommonMark
    on (column 0, text after one space): the reference records indented,
    empty, setext and closing-run headings as separate departures.
    """
    parts = [
        "# h",
        "## h2",
        "x",
        "",
        "```",
        "````",
        "~~~",
        "```py",
        "``` a`b",
        "   ```",
        "    ```",
    ]
    return ["\n".join(combo) + "\n" for combo in itertools.product(parts, repeat=4)]


def test_scan_agrees_with_a_commonmark_parser() -> None:
    md = pytest.importorskip("markdown_it").MarkdownIt("commonmark")
    for body in _corpus():
        # The raw text: since #2036 the scan keeps the first line's
        # indentation, so it agrees with the parser on the note as written.
        tokens = md.parse(body)
        theirs = [
            tokens[i + 1].content
            for i, tok in enumerate(tokens)
            if tok.type == "heading_open" and tok.markup.startswith("#")
        ]
        assert list_section_headings(body) == theirs, body
