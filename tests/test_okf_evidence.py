"""Synthetic read-side evidence contracts for #1444."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import pytest
from pydantic_core import to_json

from markdown_vault_mcp.okf import (
    _MAX_READ_CHARS,
    OkfAuditReport,
    OkfFinding,
    audit_bundle,
    derive_annotation,
    derive_stale,
    derive_trust_tier,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = dt.datetime(2026, 11, 15, 12, tzinfo=dt.UTC)
AT = "2026-11-14T09:00:00Z"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        0,
        True,
        [],
        {},
        "2026-13-01",
        "2026-11-15T12:00:00",
        "20261115T120000Z",
        "2026-W46-7T12:00Z",
        "2026-11-15X12:00Z",
        "2026-11-15T12:00+00:60",
    ],
)
def test_malformed_expiry_never_means_unexpired(value: Any) -> None:
    annotation = derive_annotation(
        {"stale_after": value}, now=NOW, include_sources=True
    )
    assert annotation["freshness"] == {
        "state": "malformed",
        "basis": "unsupported_expiry",
    }
    assert annotation["stale"] is False
    assert annotation["recorded"] == {"stale_after": value}


@pytest.mark.parametrize(
    ("value", "state", "basis"),
    [
        ("2026-11-15", "expired", "server_local_date"),
        (dt.date(2026, 11, 16), "unexpired", "server_local_date"),
        ("2026-11-15T12:00:00Z", "expired", "offset_instant"),
        ("2026-11-15T14:00:01+02:00", "unexpired", "offset_instant"),
        ("2026-11-15 14:00:00.123+02:00", "unexpired", "offset_instant"),
    ],
)
def test_freshness_basis(value: Any, state: str, basis: str) -> None:
    result = derive_annotation({"stale_after": value}, now=NOW)
    assert result["freshness"] == {"state": state, "basis": basis}
    assert result["stale"] == (state == "expired")


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"at": AT},
        {"by": "human:alice"},
        {"by": None, "at": AT},
        {"by": 42, "at": AT},
        {"by": "human:", "at": AT},
        {"by": "process:", "at": AT},
        {"by": "human:user alice", "at": AT},
        {"by": "process:build\tci", "at": AT},
        {"by": "agent/", "at": AT},
        {"by": "/1.0", "at": AT},
        {"by": "agent/1.0/extra", "at": AT},
        {"by": "agent/one two", "at": AT},
        {"by": " ", "at": AT},
        {"by": "human:alice", "at": "2026-11-14"},
        {"by": "human:alice", "at": "2026-11-14T10:00:00"},
        "human:alice",
        None,
    ],
)
def test_malformed_review_never_promotes(entry: Any) -> None:
    result = derive_annotation({"verified": [entry]}, now=NOW, include_sources=True)
    assert result["trust_tier"] == "unverified"
    assert result["review"] == {
        "state": "malformed",
        "recorded_entries": 0,
        "malformed_entries": 1,
        "basis": "supplied_metadata_only",
    }
    assert result["recorded"]["verified"] == [entry]


@pytest.mark.parametrize(
    ("actor", "tier"),
    [
        ("human:alice", "human-reviewed"),
        ("process:ci", "machine-confirmed"),
        ("process:urn:uuid:123", "machine-confirmed"),
        ("process:build/ci", "machine-confirmed"),
        ("human:user:alice", "human-reviewed"),
        ("human:urn:uuid:123", "human-reviewed"),
        ("human:team/alice", "human-reviewed"),
        ("agent/1.0", "machine-confirmed"),
    ],
)
def test_recorded_claim_survives_malformed_sibling(actor: str, tier: str) -> None:
    good = {"by": actor, "at": AT, "extension": {"claim": "retained"}}
    for value in (good, [good], [None, {}, good]):
        result = derive_annotation({"verified": value}, now=NOW, include_sources=True)
        assert result["trust_tier"] == tier
        assert result["review"]["state"] == "recorded"
        assert result["review"]["recorded_entries"] == 1
        assert result["review"]["malformed_entries"] == (
            2 if isinstance(value, list) and len(value) == 3 else 0
        )
        assert result["recorded"]["verified"] == value


def test_absent_empty_null_and_malformed_outer_review() -> None:
    for metadata, state, malformed in [
        ({}, "missing", 0),
        ({"verified": []}, "empty", 0),
        ({"verified": None}, "malformed", 1),
        ({"verified": "yes"}, "malformed", 1),
    ]:
        result = derive_annotation(metadata, now=NOW, include_sources=True)
        assert result["freshness"]["state"] == "missing"
        assert result["review"]["state"] == state
        assert result["review"]["malformed_entries"] == malformed
        assert result["recorded"] == metadata
        assert "recorded" not in derive_annotation(metadata, now=NOW)


class _NaiveZone(dt.tzinfo):
    def utcoffset(self, _value: dt.datetime | None) -> None:
        return None


def test_timezone_object_without_offset_is_naive() -> None:
    value = dt.datetime(2026, 11, 14, tzinfo=_NaiveZone())
    metadata = {"verified": {"by": "human:alice", "at": value}, "stale_after": value}
    result = derive_annotation(metadata, now=NOW, include_sources=True)
    assert result["review"]["state"] == "malformed"
    assert result["freshness"]["state"] == "malformed"
    assert derive_trust_tier(metadata) == "unverified"
    assert derive_stale(metadata, now=NOW) is False
    assert result["recorded"] == metadata


def test_parsed_dates_and_extensions_serialize_without_mutation() -> None:
    metadata = {
        "verified": {"by": "human:alice", "at": NOW, "extra": ["value"]},
        "stale_after": NOW.date(),
    }
    result = derive_annotation(metadata, now=NOW, include_sources=True)
    assert b'"at":"2026-11-15T12:00:00Z"' in to_json(result)
    assert result["recorded"] == metadata


def test_audit_evidence_is_separate_from_conformance(tmp_path: Path) -> None:
    files = {
        "a.md": "---\ntype: Concept\nverified: {}\nstale_after: nonsense\n---\nbody",
        "b.md": "---\ntype: Concept\n---\nbody",
        "c.md": "---\n: [invalid\n---\n",
        "index.md": "---\nokf_version: '0.2'\n---\n",
        "ignored.md": "---\nverified: {}\n---\n",
    }
    for name, raw in files.items():
        (tmp_path / name).write_text(raw)
    report = audit_bundle(tmp_path, exclude_patterns=["ignored.md"])
    assert report.conformant_notes == 2
    assert report.missing_freshness == OkfFinding(1, ("b.md",))
    assert report.malformed_freshness == OkfFinding(1, ("a.md",))
    assert report.malformed_review == OkfFinding(1, ("a.md",))
    assert report.evidence_unavailable == OkfFinding(1, ("c.md",))
    assert {name: (tmp_path / name).read_text() for name in files} == files
    assert asdict(report)["malformed_review"]["count"] == 1


def test_audit_limit_withholds_only_new_evidence_findings(tmp_path: Path) -> None:
    prefix = "---\ntype: Concept\nverified: {}\n---\n"
    (tmp_path / "exact.md").write_text(prefix + "a" * (_MAX_READ_CHARS - len(prefix)))
    (tmp_path / "large.md").write_text(
        prefix + "a" * (_MAX_READ_CHARS + 1 - len(prefix))
    )
    (tmp_path / "unreadable.md").write_bytes(b"\xff")
    report = audit_bundle(tmp_path)
    assert report.total_notes == 2  # existing read-failure counting is unchanged
    assert report.conformant_notes == 2
    assert report.missing_freshness.examples == ("exact.md",)
    assert report.malformed_review.examples == ("exact.md",)
    assert report.evidence_unavailable == OkfFinding(2, ("large.md", "unreadable.md"))


def test_audit_evidence_examples_capped_sorted(tmp_path: Path) -> None:
    for name in ("z.md", "b.md", "a.md"):
        (tmp_path / name).write_text(
            "---\ntype: Concept\nstale_after: null\nverified: nope\n---"
        )
    report = audit_bundle(tmp_path, example_cap=1)
    assert report.malformed_freshness == OkfFinding(3, ("a.md",))
    assert report.malformed_review == OkfFinding(3, ("a.md",))


def test_report_old_positional_constructor() -> None:
    empty = OkfFinding(0, ())
    report = OkfAuditReport(
        "off", None, False, 0, 0, empty, empty, empty, empty, empty, True, empty, empty
    )
    assert report.evidence_unavailable == empty
    assert report.malformed_review == empty
