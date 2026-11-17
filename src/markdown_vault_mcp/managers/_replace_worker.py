"""Run bounded replacement proposals in a disposable stdlib process."""

from __future__ import annotations

import difflib
import json
import re
import sys
from typing import Any


class ReplacementLimit(ValueError):
    """A bounded replacement exceeded a request limit."""


def _diff(path: str, text: str, new: str) -> str:
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in difflib.unified_diff(
            text.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
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
