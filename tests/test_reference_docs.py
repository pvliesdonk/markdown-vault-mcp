"""References under ``docs/design/reference/`` honour their contract (template-owned).

A reference records how something outside this repository behaves so an agent
reads it instead of re-deriving it from memory; the ``researching-references``
skill says how one is written.  ``scripts/check_references.py`` is the
mechanical half of that contract, and this test runs it: every reference has
the required frontmatter, every ``[source: id]`` names a declared source, and
every ``[pins: tests/x.py::test_y]`` names a test that exists, and the
directory is an OKF v0.3 bundle (root ``index.md`` with ``okf_version``).  A
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

import pytest  # noqa: E402
from scripts.check_references import (  # noqa: E402
    DEFAULT_ROOT,
    Reference,
    _check_actor_entry,
    bundle_findings,
    discover,
    expiry,
    findings,
    load,
)

from markdown_vault_mcp.okf import audit_bundle, derive_trust_tier  # noqa: E402
from markdown_vault_mcp.scanner import parse_frontmatter  # noqa: E402

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
    # UTC, matching how the checker reads ``stale_after`` (see its ``main``).
    today = dt.datetime.now(dt.UTC).date()
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


@pytest.mark.parametrize(
    ("at", "valid"),
    [
        (dt.date(2026, 9, 12), True),
        ("2026-09-12", True),
        ("2026-09-12T10:00:00+02:00", True),
        (dt.datetime(2026, 9, 12, 10, tzinfo=dt.UTC), True),
        # OKF 0.3 §13.2: "A date-time without an offset stays invalid."
        ("2026-09-12T10:00:00", False),
        (dt.datetime(2026, 9, 12, 10), False),
        ("12 September 2026", False),
    ],
)
def test_checker_reads_at_as_an_okf_timestamp(at: object, valid: bool) -> None:
    problems = _check_actor_entry("verified[0]", {"by": "human:a", "at": at})
    assert (not problems) is valid, problems


def test_server_reads_every_reference_page_as_recorded() -> None:
    # The checker and the server agree on a valid record (#2400): a page the
    # checker passes reads as recorded, not malformed, through the server.
    if not ROOT.is_dir():
        pytest.skip("no reference bundle")
    report = audit_bundle(ROOT)
    assert report.malformed_generated.count == 0, report.malformed_generated
    assert report.malformed_review.count == 0, report.malformed_review
    assert report.malformed_freshness.count == 0, report.malformed_freshness


@pytest.mark.parametrize(
    "by", ["team:docs", "svc:bot", "human:a b", "process:x\ty", "agent/1.0"]
)
def test_checker_accepts_any_okf_actor(by: str) -> None:
    # OKF 0.3 §7: "Consumers MUST accept other schemes" (#2430).
    assert _check_actor_entry("verified[0]", {"by": by, "at": "2026-11-21"}) == []


def test_checker_rejects_a_string_with_neither_actor_form() -> None:
    assert _check_actor_entry("verified[0]", {"by": "Jane Doe", "at": "2026-11-21"})


@pytest.mark.parametrize(
    ("generated_at", "verified_at", "tier"),
    [
        ("2026-11-21T10:00:00Z", "2026-11-21T11:00:00Z", "human-reviewed"),
        ("2026-11-21T10:00:00Z", "2026-11-21T10:00:00Z", "human-reviewed"),
        ("2026-11-21T10:00:00Z", "2026-11-21T09:00:00Z", "unverified"),
        (None, "2026-11-21T09:00:00Z", "human-reviewed"),
    ],
)
def test_checker_counts_only_current_reviews(
    generated_at: str | None, verified_at: str, tier: str
) -> None:
    # OKF 0.3 §5.3: "Consumers MUST calculate the trust tier only from
    # current events" (#2430).
    meta: dict[str, object] = {"verified": [{"by": "human:a", "at": verified_at}]}
    if generated_at is not None:
        meta["generated"] = {"by": "process:x", "at": generated_at}
    assert Reference(path=Path("x.md"), meta=meta, body="").trust_tier == tier


def test_checker_and_server_agree_on_every_page_trust_tier() -> None:
    # One rule set: the bundle's own checker reads trust as the server does.
    if not ROOT.is_dir():
        pytest.skip("no reference bundle")
    for path in discover(ROOT):
        ref, problem = load(path)
        assert ref is not None, problem
        text = path.read_text(encoding="utf-8")
        server = derive_trust_tier(dict(parse_frontmatter(text).metadata))
        assert ref.trust_tier == server, path
