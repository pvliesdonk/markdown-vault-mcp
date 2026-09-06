#!/usr/bin/env python3
"""Every reference under ``docs/design/reference/`` is dated, sourced, and pinned.

A reference records how something outside the repository behaves (a markdown
dialect, git, a file format, a vendor API) so an agent reads it instead of
re-deriving it from parametric memory.  The ``researching-references`` skill
describes how one is written; this script enforces the part that can be
checked mechanically:

* the YAML frontmatter carries every key in ``REQUIRED_KEYS``, ``researched``
  and ``review_by`` are ISO dates, ``status`` is one of ``STATUSES``, and a
  ``superseded`` reference names an existing ``superseded_by`` file;
* ``sources`` is a non-empty list, each entry with an ``id``, a ``url``, and an
  ``accessed`` date;
* every ``[source: id]`` marker in the body names a declared source, and every
  ``[pins: tests/x.py::test_y]`` marker names a test function that exists.

A passed ``review_by`` date is *reported*, not failed, unless ``--strict`` is
given: expiry is a reason to re-research, and a build must not turn red on a
day nobody changed anything.  ``tests/test_reference_docs.py`` runs the
non-strict form in CI and surfaces expiry as a warning.

Exit status 1 with one line per finding; 0 when clean.  ``README.md`` and
``index.md`` under the reference root are indexes, not references, and are
skipped.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ROOT = Path("docs/design/reference")
SKIPPED_NAMES = frozenset({"README.md", "index.md"})
REQUIRED_KEYS: tuple[str, ...] = (
    "title",
    "subject",
    "subject_version",
    "valid_for",
    "researched",
    "review_by",
    "status",
    "sources",
)
STATUSES = frozenset({"current", "expired", "superseded"})
MARKER_KINDS = ("source", "observed", "unverified", "pins")

_FRONTMATTER_RE = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)
_MARKER_RE = re.compile(
    r"\[(?P<kind>source|observed|unverified|pins)(?::\s*(?P<arg>[^\]]*))?\]"
)
_PIN_RE = re.compile(r"^(?P<file>[^:\s]+\.py)::(?P<name>[A-Za-z_][A-Za-z0-9_:]*)$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class Reference:
    """One parsed reference page."""

    path: Path
    meta: dict[str, Any]
    body: str

    @property
    def markers(self) -> tuple[tuple[str, str], ...]:
        """``(kind, argument)`` for every marker in the body, in order.

        HTML comments are skipped first: the page template carries
        marker-shaped examples inside ``<!-- ... -->`` guidance, and those are
        instructions to the writer, not claims.
        """
        visible = _HTML_COMMENT_RE.sub("", self.body)
        return tuple(
            (m.group("kind"), (m.group("arg") or "").strip())
            for m in _MARKER_RE.finditer(visible)
        )

    def count(self, kind: str) -> int:
        """Number of markers of ``kind`` in the body."""
        return sum(1 for k, _ in self.markers if k == kind)


def parse_reference(path: Path, text: str) -> Reference:
    """Split ``text`` into frontmatter and body.

    Raises:
        ValueError: when the file has no frontmatter block or it is not a
            YAML mapping.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("no YAML frontmatter block (--- ... ---) at the top")
    try:
        meta = yaml.safe_load(m.group("yaml"))
    except yaml.YAMLError as exc:  # pragma: no cover - message shape is PyYAML's
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return Reference(path=path, meta=meta, body=m.group("body"))


def _as_date(value: object) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _check_keys(ref: Reference) -> list[str]:
    problems = [
        f"missing frontmatter key `{k}`" for k in REQUIRED_KEYS if k not in ref.meta
    ]
    for key in ("researched", "review_by"):
        if key in ref.meta and _as_date(ref.meta[key]) is None:
            problems.append(
                f"`{key}` must be an ISO date (YYYY-MM-DD), got {ref.meta[key]!r}"
            )
    for key in ("subject_version", "valid_for"):
        if key in ref.meta and not str(ref.meta[key]).strip():
            problems.append(f"`{key}` must not be empty")
    return problems


def _check_status(ref: Reference, root: Path) -> list[str]:
    status = ref.meta.get("status")
    if status is None:
        return []
    if status not in STATUSES:
        return [f"`status` must be one of {sorted(STATUSES)}, got {status!r}"]
    return _check_superseded(ref, root) if status == "superseded" else []


def _check_superseded(ref: Reference, root: Path) -> list[str]:
    target = ref.meta.get("superseded_by")
    if not target:
        return [
            "`status: superseded` requires `superseded_by: <file under the reference root>`"
        ]
    resolved = (root / str(target)).resolve()
    if not resolved.is_relative_to(root.resolve()):
        return [f"`superseded_by` names {target!r}, which is outside {root}"]
    if not resolved.is_file():
        return [f"`superseded_by` names {target!r}, which does not exist under {root}"]
    return []


def _check_sources(ref: Reference) -> tuple[list[str], set[str]]:
    sources = ref.meta.get("sources")
    if sources is None:
        return [], set()
    if not isinstance(sources, list) or not sources:
        return ["`sources` must be a non-empty list"], set()
    problems: list[str] = []
    ids: set[str] = set()
    for i, entry in enumerate(sources):
        if not isinstance(entry, dict):
            problems.append(
                f"sources[{i}] must be a mapping with id, title, url, accessed"
            )
            continue
        sid = str(entry.get("id") or "").strip()
        if not sid:
            problems.append(f"sources[{i}] has no `id`")
        elif sid in ids:
            problems.append(f"sources[{i}] duplicates id {sid!r}")
        else:
            ids.add(sid)
        if not str(entry.get("url") or "").strip():
            problems.append(f"sources[{i}] ({sid or '?'}) has no `url`")
        if _as_date(entry.get("accessed")) is None:
            problems.append(f"sources[{i}] ({sid or '?'}) needs an ISO `accessed` date")
    return problems, ids


def _check_markers(ref: Reference, source_ids: set[str], repo_root: Path) -> list[str]:
    problems: list[str] = []
    for kind, arg in ref.markers:
        if kind == "source":
            if not arg:
                problems.append("`[source]` marker without a source id")
            elif arg not in source_ids:
                problems.append(f"`[source: {arg}]` names no declared source")
        elif kind == "pins":
            problems.extend(
                _check_pin(pin.strip(), repo_root)
                for pin in arg.split(",")
                if pin.strip()
            )
            if not arg.strip():
                problems.append("`[pins]` marker without a test id")
    if ref.count("source") == 0 and ref.count("observed") == 0:
        problems.append(
            "no `[source: id]` or `[observed: how]` claim at all — this is memory, not a reference"
        )
    return [p for p in problems if p]


def _check_pin(pin: str, repo_root: Path) -> str:
    m = _PIN_RE.match(pin)
    if not m:
        return f"`[pins: {pin}]` is not of the form tests/file.py::test_name"
    test_file = repo_root / m.group("file")
    if not test_file.is_file():
        return f"`[pins: {pin}]` names {m.group('file')}, which does not exist"
    qualname = m.group("name")
    if not _defined(test_file.read_text(encoding="utf-8"), qualname.split("::")):
        return f"`[pins: {pin}]` names {qualname!r}, which is not defined in {m.group('file')}"
    return ""


def _defined(source: str, parts: list[str]) -> bool:
    """Whether ``Class::...::function`` exists in ``source`` with that nesting."""
    try:
        body: list[ast.stmt] = ast.parse(source).body
    except SyntaxError:
        return False
    for part in parts[:-1]:
        classes = [n for n in body if isinstance(n, ast.ClassDef) and n.name == part]
        if not classes:
            return False
        body = classes[0].body
    return any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == parts[-1]
        for n in body
    )


def findings(ref: Reference, *, repo_root: Path, root: Path) -> list[str]:
    """Contract violations for one reference, each prefixed with its path."""
    problems = _check_keys(ref)
    problems += _check_status(ref, root)
    source_problems, ids = _check_sources(ref)
    problems += source_problems
    problems += _check_markers(ref, ids, repo_root)
    return [f"{ref.path}: {p}" for p in problems]


def expiry(ref: Reference, today: dt.date) -> str | None:
    """Why the reference should be re-researched, or ``None`` if it is current."""
    if ref.meta.get("status") == "expired":
        return "marked `status: expired`"
    if ref.meta.get("status") == "superseded":
        return None
    review_by = _as_date(ref.meta.get("review_by"))
    if review_by is not None and review_by < today:
        return f"`review_by` {review_by.isoformat()} has passed"
    return None


def discover(root: Path) -> list[Path]:
    """Reference pages under ``root`` (absent root → empty), indexes skipped."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.md") if p.name not in SKIPPED_NAMES)


def load(path: Path) -> tuple[Reference | None, str | None]:
    """Parse ``path``; the second item is the finding when parsing fails."""
    try:
        return parse_reference(path, path.read_text(encoding="utf-8")), None
    except ValueError as exc:
        return None, f"{path}: {exc}"


def summary_line(ref: Reference, today: dt.date) -> str:
    """One report line: status, dates, marker counts, expiry reason."""
    meta = ref.meta
    counts = ", ".join(f"{ref.count(k)} {k}" for k in MARKER_KINDS)
    line = (
        f"{ref.path}: status={meta.get('status')} subject_version={meta.get('subject_version')} "
        f"valid_for={meta.get('valid_for')!s} researched={meta.get('researched')} "
        f"review_by={meta.get('review_by')} [{counts}]"
    )
    reason = expiry(ref, today)
    return f"{line}  <- RE-RESEARCH: {reason}" if reason else line


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  See the module docstring for the contract."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="reference directory (default: docs/design/reference)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="repository root that `[pins: ...]` paths are relative to",
    )
    parser.add_argument(
        "--strict", action="store_true", help="also fail on a passed review_by date"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print findings only, no per-reference summary",
    )
    args = parser.parse_args(argv)
    today = dt.date.today()

    problems: list[str] = []
    for path in discover(args.root):
        ref, problem = load(path)
        if ref is None:
            problems.append(problem or f"{path}: unreadable")
            continue
        problems += findings(ref, repo_root=args.repo_root, root=args.root)
        reason = expiry(ref, today)
        if args.strict and reason:
            problems.append(f"{path}: {reason}")
        if not args.quiet:
            print(summary_line(ref, today))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
