"""Conservative, byte-preserving union of date-grouped Markdown logs."""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

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


@dataclasses.dataclass
class _Log:
    prefix: str
    preamble: str
    lines: list[str]
    sections: list[_Section]


def _lines(text: str) -> list[str]:
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)[:-1]


def _fences_closed(tokens: list[Token], lines: list[str]) -> bool:
    for token in tokens:
        if token.type == "fence" and token.map:
            closing = lines[token.map[1] - 1].strip()
            pattern = (
                re.escape(token.markup[0]) + "{" + str(len(token.markup)) + r",}\s*"
            )
            if not re.fullmatch(pattern, closing):
                return False
    return True


def _read_sections(
    lines: list[str], body: str
) -> tuple[list[_Section], int, set[int], str]:
    tokens = MarkdownIt("commonmark").parse(body)
    if not _fences_closed(tokens, lines):
        return [], 0, set(), "unclosed_fence"
    sections: list[_Section] = []
    first = len(lines)
    accounted: set[int] = set()
    for token in tokens:
        if (
            token.type == "heading_open"
            and token.level == 0
            and token.tag == "h2"
            and token.map
        ):
            line = lines[token.map[0]].rstrip("\r\n")
            match = _LOG_HEADING_RE.fullmatch(line)
            if not match or token.markup != "##":
                return [], 0, set(), "non_date_heading"
            if any(section.date == match.group(1) for section in sections):
                return [], 0, set(), "duplicate_date"
            if sections:
                sections[-1].end = token.map[0]
            accounted.update(range(*token.map))
            first = min(first, token.map[0])
            sections.append(_Section(match.group(1), token.map[0], len(lines), []))
        elif (
            token.level == 0
            and token.map
            and token.map[0] >= first
            and token.type != "bullet_list_open"
        ):
            return [], 0, set(), "unsupported_content"
        elif (
            token.type == "list_item_open"
            and token.level == 1
            and token.map
            and sections
        ):
            accounted.update(range(*token.map))
            item = lines[token.map[0] : token.map[1]]
            while item and not item[-1].strip(" \t\r\n"):
                item.pop()
            sections[-1].entries.append(tuple(line.rstrip("\r\n") for line in item))
    return sections, first, accounted, ""


def _parse(text: str) -> tuple[_Log | None, str]:
    # Each refusal accounts for a distinct unsafe Markdown structure.
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None, "invalid_utf8"
    body = strip_frontmatter_block(text)
    prefix = text[: len(text) - len(body)]
    lines = _lines(body)
    sections, first, accounted, reason = _read_sections(lines, body)
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
    if any(not section.entries for section in sections):
        return None, "empty_section"
    return _Log(prefix, "".join(lines[:first]).rstrip("\r\n"), lines, sections), ""


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


def merge_log_texts(upstream: str, local: str) -> LogMerge:
    """Union complete list entries while retaining upstream formatting.

    Args:
        upstream: The current upstream log.
        local: The replayed local log.

    Returns:
        Merged text and count, or a reason why the union is unsafe.
    """
    # Keep insertion decisions and the final safety check together.
    left, reason = _parse(upstream)
    if left is None:
        return LogMerge(None, 0, reason)
    right, reason = _parse(local)
    if right is None:
        return LogMerge(None, 0, reason)
    mismatch = (
        "frontmatter_differs" if left.prefix != right.prefix else "preamble_differs"
    )
    if left.prefix != right.prefix or left.preamble != right.preamble:
        return LogMerge(None, 0, mismatch)
    eol_match = re.search(r"\r\n|\r|\n", upstream)
    eol = eol_match.group() if eol_match else "\n"
    insertions, known, added = _insertions(left, right, eol)
    if not added:
        return LogMerge(upstream, 0, "")
    output = list(left.lines)
    for pos in sorted(insertions, reverse=True):
        if pos and output[pos - 1] and not output[pos - 1].endswith(("\n", "\r")):
            output[pos - 1] += eol
        output[pos:pos] = insertions[pos]
    merged = left.prefix + "".join(output)
    check, reason = _parse(merged)
    if check is None or not known <= {
        (s.date, e) for s in check.sections for e in s.entries
    }:
        return LogMerge(None, 0, "self_check_" + reason)
    return LogMerge(merged, added, "")
