#!/usr/bin/env python3
"""Every reference under ``docs/design/reference/`` is an OKF concept: dated, sourced, pinned.

A reference records how something outside the repository behaves (a markdown
dialect, git, a file format, a vendor API) so an agent reads it instead of
re-deriving it from parametric memory.  The ``researching-references`` skill
describes how one is written; the directory is an Open Knowledge Format
(OKF v0.2) bundle, and this script enforces the part of that contract which
can be checked mechanically:

* the YAML frontmatter carries every key in ``REQUIRED_KEYS``; ``type`` is
  ``Reference``; ``generated`` is ``{by, at}`` with an actor and an ISO date
  or datetime; ``stale_after`` is a calendar date (``YYYY-MM-DD``); ``status``,
  when present, is one of OKF's ``draft`` / ``stable`` / ``deprecated``;
  ``verified`` is a list of ``{by, at}`` entries; a ``superseded_by`` names a
  file under the reference root;
* ``sources`` is a non-empty list, each entry with an ``id``, a ``resource``
  and an ``accessed`` calendar date;
* every ``[source: id]`` marker in the body names a declared source, and every
  ``[pins: tests/x.py::test_y]`` marker names a pytest node that exists;
* the bundle root carries an ``index.md`` declaring ``okf_version``, and a
  ``log.md``, if present, uses ``## YYYY-MM-DD`` headings.

A passed ``stale_after`` date is *reported*, not failed, unless ``--strict`` is
given: staleness is a reason to re-research, and a build must not turn red on
a day nobody changed anything.  ``tests/test_reference_docs.py`` runs the
non-strict form in CI and surfaces staleness as a warning.

Exit status 1 with one line per finding; 0 when clean.  ``README.md``,
``index.md`` and ``log.md`` under the reference root are OKF's reserved and
navigation files, not references, and are skipped by the per-page checks.
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
SKIPPED_NAMES = frozenset({"README.md", "index.md", "log.md"})
REFERENCE_TYPE = "Reference"
OKF_VERSION = "0.2"
REQUIRED_KEYS: tuple[str, ...] = (
    "type",
    "title",
    "description",
    "subject_version",
    "valid_for",
    "generated",
    "stale_after",
    "sources",
)
STATUSES = frozenset({"draft", "stable", "deprecated"})
MARKER_KINDS = ("source", "observed", "unverified", "pins")

_FRONTMATTER_RE = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)
_MARKER_RE = re.compile(
    r"\[(?P<kind>source|observed|unverified|pins)(?::\s*(?P<arg>[^\]]*))?\]"
)
# A pin names a pytest node: a file under tests/, optional Test* classes, and a
# test_* function.  Anything else (a helper, production code) would let CI
# certify a claim that no test covers.
_PIN_RE = re.compile(
    r"^(?P<file>tests/[^:\s]+\.py)::(?P<name>(?:Test[A-Za-z0-9_]*::)*test[A-Za-z0-9_]*)$"
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# OKF actor convention: `human:<id>`, `process:<id>`, or `<producer>/<version>`.
_ACTOR_RE = re.compile(r"^(?:human:\S+|process:\S+|[^\s/]+/[^\s/]+)$")
_LOG_HEADING_RE = re.compile(r"^## (?P<date>.+?)\s*$", re.MULTILINE)


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

    @property
    def status(self) -> str:
        """OKF lifecycle status; absent means ``stable``."""
        return str(self.meta.get("status") or "stable")

    @property
    def trust_tier(self) -> str:
        """OKF trust tier derived from ``verified``: unverified, machine-confirmed, human-reviewed."""
        entries = _verified_entries(self.meta)
        if any(str(e.get("by", "")).startswith("human:") for e in entries):
            return "human-reviewed"
        return "machine-confirmed" if entries else "unverified"


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


# The contract spells dates ``YYYY-MM-DD``. ``date.fromisoformat`` also accepts
# the basic (``20270306``) and week (``2027-W10-6``) ISO spellings from Python
# 3.11 on, which a date-only consumer may not, so the text is checked first.
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAY_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])")


def _as_day(value: object) -> dt.date | None:
    """A calendar date only (``YYYY-MM-DD``); a datetime is rejected."""
    if isinstance(value, dt.datetime):
        return None
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and _DAY_RE.match(value):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_date(value: object) -> dt.date | None:
    """A calendar date or a datetime whose date part is spelt ``YYYY-MM-DD``."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and _DAY_PREFIX_RE.match(value):
        for parse in (dt.date.fromisoformat, dt.datetime.fromisoformat):
            try:
                parsed = parse(value)
            except ValueError:
                continue
            return parsed.date() if isinstance(parsed, dt.datetime) else parsed
    return None


def _verified_entries(meta: dict[str, Any]) -> list[dict[str, Any]]:
    value = meta.get("verified")
    if isinstance(value, list):
        return [e for e in value if isinstance(e, dict)]
    return []


def _check_keys(ref: Reference) -> list[str]:
    # A key written as `sources:` with nothing after it parses to None, which
    # is as absent as a missing key; report it the same way.
    problems = [
        f"missing frontmatter key `{k}`"
        for k in REQUIRED_KEYS
        if ref.meta.get(k) is None
    ]
    if ref.meta.get("type") is not None and ref.meta["type"] != REFERENCE_TYPE:
        problems.append(f"`type` must be {REFERENCE_TYPE!r}, got {ref.meta['type']!r}")
    if "stale_after" in ref.meta and _as_day(ref.meta["stale_after"]) is None:
        problems.append(
            "`stale_after` must be a calendar date (YYYY-MM-DD, no time part), "
            f"got {ref.meta['stale_after']!r}"
        )
    for key in ("title", "description", "subject_version", "valid_for"):
        if key in ref.meta and not str(ref.meta[key]).strip():
            problems.append(f"`{key}` must not be empty")
    return problems


def _check_actor_entry(label: str, entry: object) -> list[str]:
    """``{by, at}`` shape shared by ``generated`` and each ``verified`` entry."""
    if not isinstance(entry, dict):
        return [f"`{label}` must be a mapping with `by` and `at`"]
    problems: list[str] = []
    by = str(entry.get("by") or "").strip()
    if not _ACTOR_RE.match(by):
        problems.append(
            f"`{label}.by` must follow the OKF actor convention "
            f"(human:<id>, process:<id> or <producer>/<version>), got {by!r}"
        )
    if _as_date(entry.get("at")) is None:
        problems.append(f"`{label}.at` must be an ISO date or datetime")
    return problems


def _check_trust(ref: Reference) -> list[str]:
    problems: list[str] = []
    if ref.meta.get("generated") is not None:
        problems += _check_actor_entry("generated", ref.meta["generated"])
    value = ref.meta.get("verified")
    if value is None:
        return problems
    if not isinstance(value, list):
        # OKF lets a single verifier be written as a bare mapping, but not
        # every consumer honours that shorthand; the list form is read by all.
        return [*problems, "`verified` must be a list of `{by, at}` mappings"]
    for i, entry in enumerate(value):
        problems += _check_actor_entry(f"verified[{i}]", entry)
    return problems


def _check_status(ref: Reference, root: Path) -> list[str]:
    status = ref.meta.get("status")
    if status is not None and status not in STATUSES:
        return [f"`status` must be one of {sorted(STATUSES)}, got {status!r}"]
    target = ref.meta.get("superseded_by")
    if target is None:
        if status == "deprecated":
            return [
                "`status: deprecated` requires `superseded_by: <file under the reference root>`; "
                "a deprecated page with no successor is re-researched, not retired"
            ]
        return []
    return _check_successor(str(target), status, root)


def _check_successor(target: str, status: object, root: Path) -> list[str]:
    resolved = (root / target).resolve()
    if not resolved.is_relative_to(root.resolve()):
        return [f"`superseded_by` names {target!r}, which is outside {root}"]
    if not resolved.is_file():
        return [f"`superseded_by` names {target!r}, which does not exist under {root}"]
    if status != "deprecated":
        return ["`superseded_by` is only meaningful with `status: deprecated`"]
    return []


def _check_sources(ref: Reference) -> tuple[list[str], set[str]]:
    sources = ref.meta.get("sources")
    if not isinstance(sources, list) or not sources:
        return ["`sources` must be a non-empty list"], set()
    problems: list[str] = []
    ids: set[str] = set()
    for i, entry in enumerate(sources):
        if not isinstance(entry, dict):
            problems.append(
                f"sources[{i}] must be a mapping with id, resource, accessed (title recommended)"
            )
            continue
        sid = str(entry.get("id") or "").strip()
        if not sid:
            problems.append(f"sources[{i}] has no `id`")
        elif sid in ids:
            problems.append(f"sources[{i}] duplicates id {sid!r}")
        else:
            ids.add(sid)
        if not str(entry.get("resource") or "").strip():
            problems.append(
                f"sources[{i}] ({sid or '?'}) has no `resource` (OKF's URI field)"
            )
        if _as_day(entry.get("accessed")) is None:
            problems.append(
                f"sources[{i}] ({sid or '?'}) needs a calendar `accessed` date (YYYY-MM-DD)"
            )
    return problems, ids


def _check_markers(ref: Reference, source_ids: set[str], repo_root: Path) -> list[str]:
    problems: list[str] = []
    for kind, arg in ref.markers:
        if kind == "source":
            if not arg:
                problems.append("`[source]` marker without a source id")
            elif arg not in source_ids:
                problems.append(f"`[source: {arg}]` names no declared source")
        elif kind == "observed" and not arg:
            problems.append(
                "`[observed]` marker without its evidence: say what was run or which fixture"
            )
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
        return (
            f"`[pins: {pin}]` is not of the form tests/file.py::test_name "
            "(optionally tests/file.py::TestClass::test_name)"
        )
    test_file = repo_root / m.group("file")
    if not test_file.resolve().is_relative_to((repo_root / "tests").resolve()):
        return f"`[pins: {pin}]` names a path that resolves outside tests/"
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
    problems += _check_trust(ref)
    problems += _check_status(ref, root)
    source_problems, ids = _check_sources(ref)
    problems += source_problems
    problems += _check_markers(ref, ids, repo_root)
    return [f"{ref.path}: {p}" for p in problems]


def _declared_okf_version(index: Path) -> object | None:
    """The root ``index.md``'s ``okf_version``; ``None`` when unreadable."""
    m = _FRONTMATTER_RE.match(index.read_text(encoding="utf-8"))
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group("yaml"))
    except yaml.YAMLError:
        return None
    return meta.get("okf_version", "") if isinstance(meta, dict) else None


def bundle_findings(root: Path) -> list[str]:
    """OKF bundle-level violations: the root ``index.md`` marker and ``log.md`` headings."""
    if not discover(root):
        return []
    problems: list[str] = []
    index = root / "index.md"
    if not index.is_file():
        problems.append(
            f"{index}: missing; an OKF bundle root declares `okf_version` there"
        )
    else:
        declared = _declared_okf_version(index)
        if declared is None:
            problems.append(f"{index}: frontmatter is missing or not valid YAML")
        elif str(declared) != OKF_VERSION:
            problems.append(
                f'{index}: must declare `okf_version: "{OKF_VERSION}"` in its frontmatter'
            )
    log = root / "log.md"
    if log.is_file():
        for hm in _LOG_HEADING_RE.finditer(log.read_text(encoding="utf-8")):
            if _as_date(hm.group("date")) is None:
                problems.append(
                    f"{log}: heading `## {hm.group('date')}` is not a `## YYYY-MM-DD` date"
                )
    return problems


def expiry(ref: Reference, today: dt.date) -> str | None:
    """Why the reference should be re-researched, or ``None`` if it is current."""
    if ref.status == "deprecated":
        return None
    stale_after = _as_day(ref.meta.get("stale_after"))
    if stale_after is not None and today >= stale_after:
        return f"stale since {stale_after.isoformat()} (`stale_after`)"
    return None


def discover(root: Path) -> list[Path]:
    """Reference pages under ``root`` (absent root → empty), reserved files skipped."""
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
    """One report line: status, trust, dates, marker counts, expiry reason."""
    meta = ref.meta
    generated: dict[str, Any] = (
        meta["generated"] if isinstance(meta.get("generated"), dict) else {}
    )
    counts = ", ".join(f"{ref.count(k)} {k}" for k in MARKER_KINDS)
    line = (
        f"{ref.path}: status={ref.status} trust={ref.trust_tier} "
        f"subject_version={meta.get('subject_version')} valid_for={meta.get('valid_for')!s} "
        f"generated={generated.get('at')} stale_after={meta.get('stale_after')} [{counts}]"
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
        "--strict", action="store_true", help="also fail on a passed stale_after date"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print findings only, no per-reference summary",
    )
    args = parser.parse_args(argv)
    today = dt.date.today()

    problems: list[str] = bundle_findings(args.root)
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
