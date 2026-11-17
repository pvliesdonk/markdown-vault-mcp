"""Run bounded replacement proposals in a disposable stdlib process."""

from __future__ import annotations

import collections
import difflib
import io
import json
import re
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


class ReplacementLimit(ValueError):
    """A bounded replacement exceeded a request limit."""


_GAP_LINES = 32


def _same_prefix(a: list[str], b: list[str]) -> int:
    return next(
        (n for n, pair in enumerate(zip(a, b, strict=False)) if pair[0] != pair[1]),
        min(len(a), len(b)),
    )


def _gap_spans(a: list[str], b: list[str]) -> list[tuple[int, int, int]]:
    """Match a gap's common ends, and its middle only when small enough to be cheap."""
    head = _same_prefix(a, b)
    tail = _same_prefix(a[head:][::-1], b[head:][::-1])
    mid_a, mid_b = a[head : len(a) - tail], b[head : len(b) - tail]
    spans = [(0, 0, head)]
    if max(len(mid_a), len(mid_b)) <= _GAP_LINES:
        matcher = difflib.SequenceMatcher(None, mid_a, mid_b)
        spans += [
            (head + m.a, head + m.b, m.size) for m in matcher.get_matching_blocks()
        ]
    spans.append((len(a) - tail, len(b) - tail, tail))
    return [span for span in spans if span[2]]


def _anchored_blocks(a: list[str], b: list[str]) -> list[difflib.Match]:
    """Match lines between ordered unique shared anchors; time is linear in lines."""
    in_a, in_b = collections.Counter(a), collections.Counter(b)
    position = {line: j for j, line in enumerate(b) if in_b[line] == 1}
    anchors, last = [], -1
    for i, line in enumerate(a):
        if in_a[line] == 1 and position.get(line, -1) > last:
            last = position[line]
            anchors.append((i, last))
    spans: list[tuple[int, int, int]] = []
    i0 = j0 = 0
    for i, j in [*anchors, (len(a), len(b))]:
        spans += [(i0 + x, j0 + y, n) for x, y, n in _gap_spans(a[i0:i], b[j0:j])]
        if i < len(a):
            spans.append((i, j, 1))
        i0, j0 = i + 1, j + 1
    blocks: list[list[int]] = []
    for x, y, size in spans:
        if (
            blocks
            and blocks[-1][0] + blocks[-1][2] == x
            and blocks[-1][1] + blocks[-1][2] == y
        ):
            blocks[-1][2] += size
        else:
            blocks.append([x, y, size])
    return [
        *(difflib.Match(*block) for block in blocks),
        difflib.Match(len(a), len(b), 0),
    ]


def _unified(path: str, a: list[str], b: list[str]) -> Iterator[str]:
    yield f"--- {path}\n"
    yield f"+++ {path}\n"
    matcher = difflib.SequenceMatcher(None, a, b)
    if len(a) + len(b) > 1000:  # Stock matching is quadratic on scattered edits.
        matcher.matching_blocks = _anchored_blocks(a, b)  # type: ignore[attr-defined]
    for group in matcher.get_grouped_opcodes():
        old = difflib._format_range_unified(group[0][1], group[-1][2])  # type: ignore[attr-defined]
        new = difflib._format_range_unified(group[0][3], group[-1][4])  # type: ignore[attr-defined]
        yield f"@@ -{old} +{new} @@\n"
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                yield from (" " + line for line in a[i1:i2])
                continue
            if tag != "insert":
                yield from ("-" + line for line in a[i1:i2])
            if tag != "delete":
                yield from ("+" + line for line in b[j1:j2])


def _diff(path: str, text: str, new: str) -> str:
    return "".join(
        line if line[-1] in "\r\n" else line + "\n\\ No newline at end of file\n"
        for line in _unified(
            path,
            list(io.StringIO(text, newline="")),
            list(io.StringIO(new, newline="")),
        )
    )


def _fragments(pattern: re.Pattern[str], data: dict[str, Any]) -> list[str]:
    replacement: str = data["replacement"]
    if not data["regex"]:
        return [replacement]
    pattern.sub(replacement, "")  # Validate even when no note matches.
    return re.findall(r"[^\\]+|\\.[^\\]*", replacement, re.DOTALL)


def propose(data: dict[str, Any]) -> list[tuple[str, str, int, str | None]]:
    """Compile and transform snapshots, returning complete bounded proposals."""
    pattern = re.compile(
        data["pattern"] if data["regex"] else re.escape(data["pattern"])
    )
    fragments = _fragments(pattern, data)
    matches = output = added = diff_size = 0
    proposals = []
    for path, text, _etag in data["snapshots"]:

        def expand(match: re.Match[str]) -> str:
            nonlocal matches, added
            matches += 1
            if matches > data["max_matches"]:
                raise ReplacementLimit("Too many matches; narrow paths or folders.")
            parts = []
            for fragment in fragments:
                value = match.expand(fragment) if data["regex"] else fragment
                added += len(value.encode())
                if added > data["max_output"]:
                    raise ReplacementLimit(
                        "Replacement output too large; narrow the request."
                    )
                parts.append(value)
            return "".join(parts)

        new, count = pattern.subn(expand, text)
        output += len(new.encode())
        if output > data["max_output"]:
            raise ReplacementLimit("Replacement output too large; narrow the request.")
        if new == text:
            continue
        diff = None
        if data["want_diff"]:
            diff = _diff(path, text, new)
            diff_size += len(diff)
            if diff_size > data["max_diff"]:
                raise ReplacementLimit("Diff too large; narrow paths or folders.")
        proposals.append((path, new, count, diff))
    return proposals


if __name__ == "__main__":
    try:
        print(
            json.dumps({"proposals": propose(json.load(sys.stdin))}, ensure_ascii=True)
        )
    except ReplacementLimit as exc:
        print(json.dumps({"limit": str(exc)}))
    except (re.error, IndexError, ValueError, OverflowError, RecursionError) as exc:
        print(json.dumps({"error": str(exc)}))
