"""Conservative, byte-preserving union of date-grouped Markdown logs."""

from __future__ import annotations

import dataclasses
import datetime
import re
from typing import TYPE_CHECKING, Any

from markdown_it import MarkdownIt

from markdown_vault_mcp.okf import _LOG_HEADING_RE
from markdown_vault_mcp.scanner import strip_frontmatter_block

if TYPE_CHECKING:
    from markdown_it.token import Token


@dataclasses.dataclass(frozen=True)
class LogMerge:
    """Result of attempting a log union."""

    text: str | None
    added: int
    reason: str


@dataclasses.dataclass
class _Section:
    date: str
    start: int
    end: int
    entries: list[tuple[str, ...]]
    spans: list[tuple[int, int]]


@dataclasses.dataclass
class _Log:
    prefix: str
    preamble: str
    lines: list[str]
    sections: list[_Section]
    refs: dict[str, tuple[str, str]]


def _lines(text: str) -> list[str]:
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)[:-1]


def _heading_date(
    markup: str, content: str, sections: list[_Section]
) -> tuple[str, str]:
    match = _LOG_HEADING_RE.fullmatch(f"## {content}")
    if not match or markup != "##":
        return "", "non_date_heading"
    date = match.group(1)
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        return "", "invalid_date"
    if any(section.date == date for section in sections):
        return "", "duplicate_date"
    if sections and sections[-1].date < date:
        return "", "date_order"
    return date, ""


def _capture_entry(section: _Section, lines: list[str], span: list[int]) -> None:
    item = lines[span[0] : span[1]]
    while item and not item[-1].strip(" \t\r\n"):
        item.pop()
    section.entries.append(tuple(line.rstrip("\r\n") for line in item))
    section.spans.append((span[0], span[0] + len(item)))


def _read_sections(
    lines: list[str], tokens: list[Token]
) -> tuple[list[_Section], int, set[int], str]:
    sections: list[_Section] = []
    first = len(lines)
    accounted: set[int] = set()
    for index, token in enumerate(tokens):
        if (
            token.type == "heading_open"
            and token.level == 0
            and token.tag == "h2"
            and token.map
        ):
            date, reason = _heading_date(
                token.markup, tokens[index + 1].content, sections
            )
            if reason:
                return [], 0, set(), reason
            if sections:
                sections[-1].end = token.map[0]
            accounted.update(range(*token.map))
            first = min(first, token.map[0])
            sections.append(_Section(date, token.map[0], len(lines), [], []))
        elif (
            token.type == "list_item_open"
            and token.level == 1
            and token.markup in {"-", "+", "*"}
            and token.map
            and sections
        ):
            accounted.update(range(*token.map))
            _capture_entry(sections[-1], lines, token.map)
    return sections, first, accounted, ""


def _parse(text: str) -> tuple[_Log | None, str]:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None, "invalid_utf8"
    body = strip_frontmatter_block(text)
    prefix = text[: len(text) - len(body)]
    lines = _lines(body)
    env: dict[str, Any] = {}
    tokens = MarkdownIt("commonmark").parse(body, env)
    refs = {
        label: (ref["href"], ref["title"])
        for label, ref in env.get("references", {}).items()
    }
    sections, first, accounted, reason = _read_sections(lines, tokens)
    if reason:
        return None, reason
    if not sections:
        return None, "no_date_heading"
    if any(
        line.strip(" \t\r\n") and i not in accounted
        for i, line in enumerate(lines)
        if i >= first
    ):
        return None, "unaccounted_content"
    preamble = lines[:first]
    while preamble and not preamble[-1].strip(" \t\r\n"):
        preamble.pop()
    return _Log(prefix, "".join(preamble).rstrip("\r\n"), lines, sections, refs), ""


def _insertions(
    left: _Log, right: _Log, eol: str
) -> tuple[dict[int, list[str]], set[tuple[str, tuple[str, ...]]], int]:
    known = {(s.date, entry) for s in left.sections for entry in s.entries}
    insertions: dict[int, list[str]] = {}
    added = 0
    for section in right.sections:
        entries = []
        for entry in section.entries:
            key = (section.date, entry)
            if key not in known:
                known.add(key)
                entries.append(entry)
                added += 1
        if not entries:
            continue
        existing = next((s for s in left.sections if s.date == section.date), None)
        if existing:
            pos = existing.end
            while pos > existing.start + 1 and not left.lines[pos - 1].strip(" \t\r\n"):
                pos -= 1
            content = "".join(eol.join(entry) + eol for entry in entries)
        else:
            pos = next(
                (s.start for s in left.sections if s.date < section.date),
                len(left.lines),
            )
            content = (
                f"## {section.date}{eol}{eol}"
                + "".join(eol.join(entry) + eol for entry in entries)
                + eol
            )
        insertions.setdefault(pos, []).append(content)
    return insertions, known, added


def _duplicate_lines(log: _Log) -> set[int]:
    duplicates: set[int] = set()
    for section in log.sections:
        seen: set[tuple[str, ...]] = set()
        for entry, (start, end) in zip(section.entries, section.spans, strict=True):
            if entry in seen:
                duplicates.update(range(start, end))
            seen.add(entry)
    return duplicates


def merge_log_texts(upstream: str, local: str) -> LogMerge:
    """Union complete list entries while retaining upstream formatting.

    Args:
        upstream: The current upstream log.
        local: The replayed local log.

    Returns:
        Merged text and count, or a reason why the union is unsafe.
    """
    left, reason = _parse(upstream)
    if left is None:
        return LogMerge(None, 0, reason)
    right, reason = _parse(local)
    if right is None:
        return LogMerge(None, 0, reason)
    mismatch = (
        "reference_definitions"
        if left.refs != right.refs
        else "frontmatter_differs"
        if left.prefix != right.prefix
        else "preamble_differs"
        if left.preamble != right.preamble
        else ""
    )
    if mismatch:
        return LogMerge(None, 0, mismatch)
    eol_match = re.search(r"\r\n|\r|\n", upstream)
    eol = eol_match.group() if eol_match else "\n"
    insertions, known, added = _insertions(left, right, eol)
    duplicates = _duplicate_lines(left)
    if not added and not duplicates:
        return LogMerge(upstream, 0, "")
    output = [
        "" if index in duplicates else line for index, line in enumerate(left.lines)
    ]
    for pos in sorted(insertions, reverse=True):
        if pos and output[pos - 1] and not output[pos - 1].endswith(("\n", "\r")):
            output[pos - 1] += eol
        output[pos:pos] = insertions[pos]
    merged = left.prefix + "".join(output)
    check, reason = _parse(merged)
    if (
        check is None
        or check.refs != left.refs
        or not known <= {(s.date, e) for s in check.sections for e in s.entries}
    ):
        return LogMerge(None, 0, "self_check_" + reason)
    return LogMerge(merged, added, "")
