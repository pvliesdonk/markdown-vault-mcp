#!/usr/bin/env python3
"""Every ``[tool.uv]`` pin carries its exit condition; CI fails once it is met.

A pin in ``[tool.uv] override-dependencies`` / ``constraint-dependencies`` is a
temporary workaround for an upstream problem.  Two things go wrong with such
pins: they outlive their reason, and Renovate cannot see the tables, so a
bound raise on the ``[project]`` side never moves the lock (#508).  This
script enforces the convention that fixes both:

* every entry carries ``# until: <GitHub issue or PR URL>`` on its line — the
  condition under which the pin goes away;
* no package is bounded both in ``[project]`` / ``[dependency-groups]`` and
  in a ``[tool.uv]`` table (keep one bound per package);
* in CI (``--offline`` not given), every referenced issue is looked up with
  ``GITHUB_TOKEN``; an issue closed as completed or a merged PR fails the run
  ("lift the pin"), and one closed without a fix fails too ("re-point the
  pin at a live issue").  A lookup that cannot be completed (private repo,
  rate limit, network) is printed as a warning and does not fail.

The ``# until:`` comment goes on the entry's own line, on a comment line
directly above it, or on the array's opening line (``= [  # until: ...``),
where it covers every entry without one of its own.  Exit status 1 with one
line per finding; 0 when clean.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

_TABLE_HEADER_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*(#.*)?$")
_ARRAY_START_RE = re.compile(
    r"^\s*(?P<table>override-dependencies|constraint-dependencies)\s*=\s*\["
)
_ENTRY_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')
_BOUND_CHARS = "<>=!~"
_UNTIL_RE = re.compile(r"#\s*until:\s*(?P<url>\S+)")
_ISSUE_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/(?:issues|pull)/(?P<number>\d+)/?$"
)
_NAME_RE = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class Pin:
    table: str
    spec: str
    line: int
    until: str | None

    @property
    def name(self) -> str:
        return _name(self.spec)


def _name(spec: str) -> str:
    m = _NAME_RE.match(spec)
    return re.sub(r"[-_.]+", "-", m.group("name")).lower() if m else spec


def _split_comment(line: str) -> tuple[str, str]:
    """Split a TOML line into code and trailing comment, honouring quotes."""
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i], line[i:]
    return line, ""


def _entries(code: str) -> list[str]:
    return [a or b for a, b in _ENTRY_RE.findall(code)]


@dataclass
class _ArrayScan:
    """Mutable state while inside one ``override-``/``constraint-dependencies`` array."""

    table: str
    pending_until: str | None = None  # from a comment-only line above an entry
    array_until: str | None = None  # from the opening line: covers every entry

    def feed(self, line: str, lineno: int, *, opening: bool) -> tuple[list[Pin], bool]:
        """Return the pins on this line and whether the array closed here."""
        code, comment = _split_comment(line)
        until = _UNTIL_RE.search(comment)
        url = until.group("url") if until else None
        entries = _entries(code)
        closing = "]" in _ENTRY_RE.sub("", code)
        pins: list[Pin] = []
        if entries:
            effective = url or self.pending_until or self.array_until
            pins = [Pin(self.table, spec, lineno, effective) for spec in entries]
            self.pending_until = None
        elif url and opening:
            self.array_until = url
        elif url and closing:
            # `]  # until: ...` belongs to nothing; surface it instead of dropping it.
            pins = [Pin(self.table, "", lineno, "MISPLACED:" + url)]
        elif url and not code.strip(", "):
            self.pending_until = url
        return pins, closing


def parse_pins(text: str) -> list[Pin]:
    """Line-level scan of ``[tool.uv]`` (tomllib drops the comments we need)."""
    pins: list[Pin] = []
    in_uv = False
    scan: _ArrayScan | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        header = _TABLE_HEADER_RE.match(line)
        if header and scan is None:
            in_uv = header.group("name").strip() == "tool.uv"
            continue
        if not in_uv:
            continue
        opening = False
        if scan is None:
            start = _ARRAY_START_RE.match(line)
            if not start:
                continue
            scan = _ArrayScan(start.group("table"))
            line = line[start.end() :]
            opening = True
        found, closed = scan.feed(line, lineno, opening=opening)
        pins.extend(found)
        if closed:
            scan = None
    return pins


def project_bounded_names(doc: dict[str, Any]) -> set[str]:
    """Packages that carry a version bound under [project] / [dependency-groups].

    A bare ``"numpy"`` is not a bound; only a spec with an operator competes
    with a [tool.uv] pin for the same package."""
    specs: list[str] = list(doc.get("project", {}).get("dependencies", []))
    for group in doc.get("project", {}).get("optional-dependencies", {}).values():
        specs.extend(group)
    for group in doc.get("dependency-groups", {}).values():
        specs.extend(s for s in group if isinstance(s, str))
    return {_name(spec) for spec in specs if any(c in spec for c in _BOUND_CHARS)}


def fetch_issue_state(url: str, token: str | None = None) -> str:
    """One of ``open``, ``fixed`` (completed issue / merged PR) or ``abandoned``
    (closed as not planned / PR closed unmerged)."""
    m = _ISSUE_URL_RE.match(url)
    if not m:
        raise ValueError(f"not a GitHub issue or pull-request URL: {url}")
    api = f"https://api.github.com/repos/{m['owner']}/{m['repo']}/issues/{m['number']}"
    req = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    if data.get("state") != "closed":
        return "open"
    if "pull_request" in data:
        return "fixed" if data["pull_request"].get("merged_at") else "abandoned"
    # `state_reason` is None on issues closed before GitHub introduced it or
    # closed via the API without one; closed-without-reason counts as fixed —
    # the pin author can re-point `until:` if that is wrong for their case.
    return "abandoned" if data.get("state_reason") == "not_planned" else "fixed"


def findings(
    text: str,
    *,
    state_of: Callable[[str], str] | None,
    pins: list[Pin] | None = None,
    warn: Callable[[str], None] = lambda msg: print(f"WARNING: {msg}"),
) -> list[str]:
    """Every violation as one human-readable line; ``state_of`` None = offline.

    Lookup failures go to ``warn`` and never become findings."""
    out: list[str] = []
    pins = parse_pins(text) if pins is None else pins
    bounded = project_bounded_names(tomllib.loads(text))
    for pin in pins:
        where = f"pyproject.toml:{pin.line} [tool.uv] {pin.table} {pin.spec!r}"
        if pin.until is not None and pin.until.startswith("MISPLACED:"):
            out.append(
                f"pyproject.toml:{pin.line} [tool.uv] {pin.table}: `# until:` on the "
                "closing `]` line covers nothing — put it on the entry's line, the "
                "comment line directly above it, or the array's opening line"
            )
            continue
        if pin.until is None:
            out.append(
                f"{where}: no exit condition — add `# until: <issue URL>` on this line, "
                "on a comment line directly above it, or on the array's opening line "
                "(covering every entry), naming what must happen before this pin can go"
            )
        elif not _ISSUE_URL_RE.match(pin.until):
            out.append(
                f"{where}: `until:` must be a GitHub issue or PR URL, got {pin.until!r}"
            )
        elif state_of is not None:
            try:
                state = state_of(pin.until)
            except (
                Exception
            ) as exc:  # unreachable issue, rate limit, network: not a verdict
                warn(
                    f"{where}: could not look up {pin.until} ({exc}); assuming still open"
                )
                state = "open"
            if state == "fixed":
                out.append(
                    f"{where}: outlived its reason — {pin.until} is resolved; lift the pin"
                )
            elif state == "abandoned":
                out.append(
                    f"{where}: {pin.until} was closed without a fix; re-point `until:` at "
                    "a live issue, or lift the pin if it is no longer needed"
                )
        if pin.name in bounded:
            out.append(
                f"{where}: {pin.name!r} also carries a version bound under "
                "[project]/[dependency-groups]; keep one bound per package (Renovate "
                "cannot see [tool.uv] tables, so a raise on the other side would never "
                "move the lock)"
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument(
        "--offline",
        action="store_true",
        help="check the convention only; do not look up issue states",
    )
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args(argv)
    text = args.pyproject.read_text(encoding="utf-8")
    token = os.environ.get("GITHUB_TOKEN") or None
    state_of = None if args.offline else (lambda url: fetch_issue_state(url, token))
    pins = parse_pins(text)
    problems = findings(text, state_of=state_of, pins=pins)
    for line in problems:
        print(line)
    if not problems:
        what = (
            "each with an exit condition"
            if args.offline
            else "all exit conditions still open"
        )
        print(f"check_pins: OK ({len(pins)} [tool.uv] pin(s), {what})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
