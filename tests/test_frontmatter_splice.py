"""The server's own writes rewrite only the keys they change (#2419).

OKF 0.3 §4: "A producer that modifies an existing file SHOULD NOT
re-serialize values that it did not change."
"""

from __future__ import annotations

import datetime as dt

import pytest

from markdown_vault_mcp._frontmatter_splice import REMOVE, splice_keys
from markdown_vault_mcp.okf import append_okf_verification, apply_okf_write_stamp
from markdown_vault_mcp.scanner import parse_frontmatter

NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)
STAMP = {"by": "x/1", "at": "2026-11-21T12:00:00Z"}

NOTE = (
    "---\n"
    "# authored comment\n"
    "title: 'Quoted Title'   # trailing comment\n"
    "tags: [a, b]\n"
    "generated: {by: old/1, at: '2026-01-01T00:00:00Z'}\n"
    "stale_after: 2027-01-01\n"
    "---\n"
    "# Body\n\n  indented line kept  \n"
)


def _lines_other_than(text: str, key: str) -> list[str]:
    """*text*'s lines without *key*'s entry: its line and its indented lines."""
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith(f"{key}:"):
            inside = True
            continue
        if inside and line[:1] in (" ", "\t", "-") and line != "---":
            continue
        inside = False
        kept.append(line)
    return kept


def test_a_stamp_keeps_every_other_byte() -> None:
    stored = NOTE.replace("# Body", "# Older body")
    out = apply_okf_write_stamp(NOTE, actor="x/1", now=NOW, stored=stored)
    assert _lines_other_than(out, "generated") == _lines_other_than(NOTE, "generated")
    assert parse_frontmatter(out).metadata["generated"] == STAMP
    # The key keeps its slot between tags and stale_after.
    block = out.split("---\n")[1].splitlines()
    keys = [line.split(":")[0] for line in block if line[:1] not in (" ", "#")]
    assert keys == ["title", "tags", "generated", "stale_after"]


def test_an_absent_key_is_appended_at_the_end_of_the_block() -> None:
    text = "---\ntitle: T  # keep\n---\nbody\n"
    out = apply_okf_write_stamp(text, actor="x/1", now=NOW, stored=None)
    assert out.startswith("---\ntitle: T  # keep\ngenerated:\n")
    assert out.endswith("---\nbody\n")


def test_verified_from_the_stored_note_replaces_only_its_lines() -> None:
    stored = "---\ntitle: T\nverified:\n- by: human:a\n  at: '2026-01-02'\n---\nb\n"
    written = "---\ntitle: T  # edited\nverified:\n- by: human:forged\n  at: '2099-01-01'\n---\nb\n"
    out = apply_okf_write_stamp(written, actor="x/1", now=NOW, stored=stored)
    meta = parse_frontmatter(out).metadata
    assert meta["verified"] == [{"by": "human:a", "at": "2026-01-02"}]
    assert "title: T  # edited\n" in out


def test_verified_is_removed_when_the_stored_note_has_none() -> None:
    written = "---\ntitle: T\nverified: {by: human:forged, at: '2099-01-01'}\n---\nb\n"
    out = apply_okf_write_stamp(written, actor="x/1", now=NOW, stored=None)
    assert "verified" not in parse_frontmatter(out).metadata
    assert out.startswith("---\ntitle: T\ngenerated:")


def test_okf_verify_appends_without_touching_other_lines() -> None:
    out = append_okf_verification(NOTE, subject="alice", now=NOW)
    assert _lines_other_than(out, "verified") == NOTE.splitlines()
    assert parse_frontmatter(out).metadata["verified"] == [
        {"by": "human:alice", "at": "2026-11-21T12:00:00Z"}
    ]


@pytest.mark.parametrize(
    "text",
    [
        "---\r\ntitle: T\r\n---\r\nbody\r\n",  # CRLF
        "---\ntitle: T\ngenerated: a\ngenerated: b\n---\nbody\n",  # duplicate key
        "---\n{title: T}\n---\nbody\n",  # flow-style top-level mapping
        "no frontmatter at all\n",
    ],
)
def test_a_block_the_scan_cannot_read_falls_back_to_a_full_write(text: str) -> None:
    out = apply_okf_write_stamp(text, actor="x/1", now=NOW, stored=None)
    assert parse_frontmatter(out).metadata["generated"] == STAMP


def test_splice_refuses_a_result_that_reads_back_differently() -> None:
    text = "---\ntitle: T\n---\nbody\n"
    assert splice_keys(text, {"generated": STAMP}, expected={"title": "other"}) is None


def test_splice_removes_a_key_and_its_continuation_lines() -> None:
    text = "---\na: 1\nverified:\n  - by: human:a\n    at: x\n\nb: 2\n---\nbody\n"
    out = splice_keys(text, {"verified": REMOVE}, expected={"a": 1, "b": 2})
    assert out == "---\na: 1\n\nb: 2\n---\nbody\n"


def test_a_column_zero_sequence_belongs_to_its_key() -> None:
    text = "---\nverified:\n- by: human:a\n  at: x\ntitle: T\n---\nbody\n"
    out = splice_keys(
        text,
        {"verified": [{"by": "human:b", "at": "y"}]},
        expected={"verified": [{"by": "human:b", "at": "y"}], "title": "T"},
    )
    assert out is not None
    assert out.endswith("title: T\n---\nbody\n")
