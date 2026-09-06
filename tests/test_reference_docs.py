"""References under ``docs/design/reference/`` honour their contract (template-owned).

A reference records how something outside this repository behaves so an agent
reads it instead of re-deriving it from memory; the ``researching-references``
skill says how one is written.  ``scripts/check_references.py`` is the
mechanical half of that contract, and this test runs it: every reference has
the required frontmatter, every ``[source: id]`` names a declared source, and
every ``[pins: tests/x.py::test_y]`` names a test that exists, and the
directory is an OKF v0.2 bundle (root ``index.md`` with ``okf_version``).  A
passed ``stale_after`` date is a warning, not a failure — staleness means
re-research, and the build must not turn red on a day nobody changed
anything.  A project with no ``docs/design/reference/`` directory passes
trivially.
"""

from __future__ import annotations

import datetime as dt
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.check_references import (  # noqa: E402
    DEFAULT_ROOT,
    bundle_findings,
    discover,
    expiry,
    findings,
    load,
)

ROOT = REPO_ROOT / DEFAULT_ROOT


def test_references_are_dated_sourced_and_pinned() -> None:
    problems: list[str] = bundle_findings(ROOT)
    for path in discover(ROOT):
        ref, problem = load(path)
        if ref is None:
            problems.append(problem or f"{path}: unreadable")
            continue
        problems += findings(ref, repo_root=REPO_ROOT, root=ROOT)
    assert not problems, "\n".join(problems)


def test_expired_references_are_surfaced_as_warnings() -> None:
    today = dt.date.today()
    for path in discover(ROOT):
        ref, _ = load(path)
        if ref is None:
            continue
        reason = expiry(ref, today)
        if reason:
            warnings.warn(
                f"{path.relative_to(REPO_ROOT)}: {reason} — re-research it with the "
                "researching-references skill rather than trusting it",
                stacklevel=1,
            )
