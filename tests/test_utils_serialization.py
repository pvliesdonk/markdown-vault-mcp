"""Tests for the TOC payload serialization helper (#779)."""

from __future__ import annotations

from markdown_vault_mcp.types import SubtreeNote, SubtreeToc, TocEntry
from markdown_vault_mcp.utils.serialization import toc_payload


def test_toc_payload_note_list() -> None:
    data = [TocEntry("Title", 1), TocEntry("Intro", 2)]
    assert toc_payload(data) == [
        {"heading": "Title", "level": 1},
        {"heading": "Intro", "level": 2},
    ]


def test_toc_payload_subtree_nested() -> None:
    data = SubtreeToc(
        path="Projects",
        notes=[
            SubtreeNote(
                path="Projects/a.md",
                title="A",
                headings=[TocEntry("A", 1), TocEntry("Goals", 2)],
            )
        ],
        truncated=False,
    )
    assert toc_payload(data) == {
        "path": "Projects",
        "notes": [
            {
                "path": "Projects/a.md",
                "title": "A",
                "headings": [
                    {"heading": "A", "level": 1},
                    {"heading": "Goals", "level": 2},
                ],
            }
        ],
        "truncated": False,
    }


def test_toc_payload_empty_subtree() -> None:
    data = SubtreeToc(path="empty", notes=[], truncated=False)
    assert toc_payload(data) == {"path": "empty", "notes": [], "truncated": False}


def test_toc_payload_empty_note_list() -> None:
    assert toc_payload([]) == []
