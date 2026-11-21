"""Synthetic read-side evidence contracts for #1444."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import frontmatter
import pytest
from pydantic_core import to_json

from markdown_vault_mcp._okf_write import (
    OkfWriteIntent,
    build_okf_write_enrich,
    okf_write_intent,
    package_version,
)
from markdown_vault_mcp.okf import (
    _MAX_READ_CHARS,
    OkfAuditReport,
    OkfDetector,
    OkfFinding,
    append_okf_verification,
    audit_bundle,
    derive_annotation,
    derive_stale,
    derive_trust_tier,
)
from markdown_vault_mcp.scanner import parse_frontmatter

if TYPE_CHECKING:
    from pathlib import Path

NOW = dt.datetime(2026, 11, 15, 12, tzinfo=dt.UTC)
AT = "2026-11-14T09:00:00Z"


@pytest.mark.parametrize(
    "value",
    [
        0,
        True,
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
    # Never fresh (OKF 0.3 §5.5, #2410); null, "" and [] are absent (§11.2).
    assert annotation["stale"] is (value not in (None, "", []))
    assert annotation["recorded"] == {"stale_after": value}


@pytest.mark.parametrize(
    ("value", "state", "basis"),
    [
        ("2026-11-15", "expired", "utc_day_start"),
        (dt.date(2026, 11, 16), "unexpired", "utc_day_start"),
        # NOW is 12:00Z: 2026-11-15T12:00Z exactly, as a YAML-native datetime.
        (dt.datetime(2026, 11, 15, 12, tzinfo=dt.UTC), "expired", "offset_instant"),
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
        {"by": "", "at": AT},
        {"by": " ", "at": AT},
        {"by": "codex", "at": AT},
        {"by": "Jane Doe", "at": AT},
        {"by": "human:alice", "at": "2026-11-14T10:00:00"},
        {"by": "human:alice", "at": "2026-11-31"},
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
        ("human:user alice", "human-reviewed"),
        ("process:nightly build", "machine-confirmed"),
        ("agent/1.0", "machine-confirmed"),
        # OKF 0.3 §7: the id is opaque, and consumers MUST accept other
        # schemes and treat them as non-human (#2407).
        ("team:finance-fpa", "machine-confirmed"),
        ("svc:indexer", "machine-confirmed"),
        ("Human:alice", "machine-confirmed"),
        (" human:alice", "machine-confirmed"),
        (":anonymous", "machine-confirmed"),
        ("process:", "machine-confirmed"),
        ("process:build\tci", "machine-confirmed"),
        ("reference_agent/gemini-2.5-pro", "machine-confirmed"),
        ("agent/llama3:8b", "machine-confirmed"),
        ("agent/human:alice", "machine-confirmed"),
        ("agent/", "machine-confirmed"),
        ("/1.0", "machine-confirmed"),
        ("agent/1.0/extra", "machine-confirmed"),
        ("agent/one two", "machine-confirmed"),
        ("human:", "human-reviewed"),
        ("human: alice", "human-reviewed"),
        ("human:alice\nbob", "human-reviewed"),
        ("human:alice\tsmith", "human-reviewed"),
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


def test_absent_null_empty_and_scalar_review() -> None:
    # OKF 0.3 §11.2 rule 1: null, "" and [] are the same as an absent key;
    # rule 2: one item is a one-item list, so a scalar is one malformed entry.
    for metadata, state, malformed in [
        ({}, "missing", 0),
        ({"verified": []}, "missing", 0),
        ({"verified": None}, "missing", 0),
        ({"verified": ""}, "missing", 0),
        ({"verified": "yes"}, "malformed", 1),
    ]:
        result = derive_annotation(metadata, now=NOW, include_sources=True)
        assert result["freshness"]["state"] == "missing"
        assert result["review"]["state"] == state
        assert result["review"]["malformed_entries"] == malformed
        assert result["recorded"] == ({"verified": "yes"} if malformed else {})
        assert "recorded" not in derive_annotation(metadata, now=NOW)


@pytest.mark.parametrize("value", [None, "", []])
def test_null_or_empty_fields_are_absent(value: Any) -> None:
    # OKF 0.3 §11.2 rule 1; §5.5: "An empty or null `stale_after` is the
    # same as an absent key."
    metadata = {"stale_after": value, "generated": value, "sources": value}
    result = derive_annotation(metadata, now=NOW, include_sources=True)
    assert result["freshness"] == {"state": "missing", "basis": "no_expiry_supplied"}
    assert result["generation"] == _generation("missing")
    assert result["attribution"] == _attribution("missing")
    assert result["stale"] is False
    assert result["recorded"] == {}
    assert "sources" not in result


@pytest.mark.parametrize("value", ["null", "''", "[]"])
def test_audit_reads_null_or_empty_fields_as_absent(tmp_path: Path, value: str) -> None:
    (tmp_path / "a.md").write_text(
        f"---\ntype: Concept\nstale_after: {value}\ngenerated: {value}\n"
        f"verified: {value}\nsources: {value}\n---\nbody"
    )
    report = audit_bundle(tmp_path)
    assert report.missing_freshness == OkfFinding(1, ("a.md",))
    assert report.malformed_freshness.count == 0
    assert report.malformed_generated.count == 0
    assert report.malformed_review.count == 0
    assert report.malformed_sources.count == 0


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
    assert derive_stale(metadata, now=NOW) is True
    assert result["stale"] is True
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
            "---\ntype: Concept\nstale_after: nonsense\nverified: nope\n---"
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


@pytest.mark.parametrize(
    ("metadata", "applicability", "review_state"),
    [
        ({}, {"state": "none", "basis": "no_review_supplied"}, "missing"),
        ({"verified": []}, {"state": "none", "basis": "no_review_supplied"}, "missing"),
        (
            {"verified": None},
            {"state": "none", "basis": "no_review_supplied"},
            "missing",
        ),
        (
            {"verified": True},
            {"state": "unknown", "basis": "no_snapshot_binding"},
            "malformed",
        ),
        (
            {"verified": {}},
            {"state": "unknown", "basis": "no_snapshot_binding"},
            "malformed",
        ),
        (
            {"verified": [None]},
            {"state": "unknown", "basis": "no_snapshot_binding"},
            "malformed",
        ),
        (
            {"verified": [None, {"by": "human:alice", "at": AT}]},
            {"state": "unknown", "basis": "no_snapshot_binding"},
            "recorded",
        ),
        (
            {"verified": {"by": "human:alice", "at": AT}},
            {"state": "unknown", "basis": "no_snapshot_binding"},
            "recorded",
        ),
        (
            {"verified": [{"by": "human:alice", "at": AT, "snapshot": "claimed"}]},
            {"state": "unknown", "basis": "no_snapshot_binding"},
            "recorded",
        ),
    ],
)
def test_review_applicability(
    metadata: dict[str, Any], applicability: dict[str, str], review_state: str
) -> None:
    result = derive_annotation(metadata, now=NOW)
    assert result["review_applicability"] == applicability
    assert result["review"]["state"] == review_state


def test_review_written_for_a_spaced_subject_reads_back_recorded() -> None:
    text = append_okf_verification(
        "---\ntype: concept\n---\nBody\n", subject="Jane Doe", now=NOW
    )
    metadata = frontmatter.loads(text).metadata
    assert metadata["verified"][0]["by"] == "human:Jane Doe"
    assert derive_trust_tier(metadata) == "human-reviewed"


def _attribution(state: str, recorded: int = 0, malformed: int = 0) -> dict[str, Any]:
    return {
        "state": state,
        "recorded_entries": recorded,
        "malformed_entries": malformed,
        "basis": "supplied_metadata_only",
    }


def _generation(state: str) -> dict[str, str]:
    return {"state": state, "basis": "supplied_metadata_only"}


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ([], _attribution("missing")),
        ({"resource": "https://a"}, _attribution("recorded", recorded=1)),
        ({"resource": None}, _attribution("malformed", malformed=1)),
        ([{"resource": ""}], _attribution("malformed", malformed=1)),
        ([{"id": "a", "resource": "https://a"}], _attribution("recorded", recorded=1)),
        ([{"id": "a"}], _attribution("malformed", malformed=1)),
        (
            [{"id": "a", "resource": "https://a"}, "https://x"],
            _attribution("recorded", 1, 1),
        ),
        (["https://x"], _attribution("malformed", malformed=1)),
        ("https://x", _attribution("malformed", malformed=1)),
        ({"id": "a"}, _attribution("malformed", malformed=1)),
        (None, _attribution("missing")),
        ("", _attribution("missing")),
    ],
)
def test_sources_reported_like_review(sources: Any, expected: dict[str, Any]) -> None:
    for include_sources in (True, False):
        result = derive_annotation(
            {"sources": sources}, now=NOW, include_sources=include_sources
        )
        assert result["attribution"] == expected
        assert "malformed_sources" not in result


def test_one_source_mapping_is_a_one_item_list() -> None:
    # OKF 0.3 §11.2 rule 2: "Where a field is a list (`tags`, `sources`,
    # `verified`, `parameters`), consumers MUST accept one item as a list
    # with one item."
    source = {"id": "a", "resource": "https://a"}
    full = derive_annotation({"sources": source}, now=NOW, include_sources=True)
    assert full["sources"] == [source]
    compact = derive_annotation({"sources": source}, now=NOW)
    assert compact["sources_count"] == 1


def test_absent_provenance_is_missing() -> None:
    result = derive_annotation({}, now=NOW)
    assert result["attribution"] == _attribution("missing")
    assert result["generation"] == _generation("missing")


@pytest.mark.parametrize(
    "generated",
    [
        {"by": 7, "at": AT},
        {"by": None, "at": AT},
        {"by": "", "at": AT},
        {"by": "codex"},
        {"at": AT},
        {"by": "codex/1.0", "at": "2026-11-14T09:00:00"},
        {"by": "codex/1.0", "at": "2026-02-30"},
        {"by": "Jane Doe", "at": AT},
        [{"by": "codex/1.0", "at": AT}],
        "codex at a date",
    ],
)
def test_generated_malformed_under_okf_5_2(generated: Any) -> None:
    result = derive_annotation({"generated": generated}, now=NOW)
    assert result["generation"] == _generation("malformed")
    assert "malformed_generated" not in result
    if isinstance(generated, dict):
        review = derive_annotation({"verified": generated}, now=NOW)["review"]
        assert review["state"] == "malformed"


@pytest.mark.parametrize(
    "actor",
    [
        "markdown-vault-mcp/1.4.0",
        "human:alice",
        "human:Jane Doe",
        "process:ci",
        "team:finance-fpa",
        "svc:indexer",
        "human:alice\tsmith",
    ],
)
def test_generated_recorded_for_a_usable_actor(actor: str) -> None:
    result = derive_annotation({"generated": {"by": actor, "at": AT}}, now=NOW)
    assert result["generation"] == _generation("recorded")


@pytest.mark.parametrize("at", ["2026-06-25", "'2026-06-25'"])
def test_date_only_at_is_recorded(at: str) -> None:
    # OKF 0.3 §5: a date only, quoted or YAML-native, is a valid timestamp
    # for every key (#2400).
    raw = (
        "---\n"
        f"generated: {{by: 'human:a', at: {at}}}\n"
        f"verified: {{by: 'human:a', at: {at}}}\n"
        "---\nbody\n"
    )
    metadata = dict(parse_frontmatter(raw).metadata)
    result = derive_annotation(metadata, now=NOW)
    assert result["generation"] == _generation("recorded")
    assert result["review"]["state"] == "recorded"
    assert result["trust_tier"] == "human-reviewed"


@pytest.mark.parametrize("at", [None, ""])
def test_generated_null_or_empty_at_is_absent(at: str | None) -> None:
    # OKF 0.3 §11.2 rule 1 applies inside a family too.
    result = derive_annotation({"generated": {"by": "codex/1.0", "at": at}}, now=NOW)
    assert result["generation"] == _generation("recorded")
    review = derive_annotation({"verified": {"by": "codex/1.0", "at": at}}, now=NOW)
    assert review["review"]["state"] == "malformed"


def test_generated_at_is_optional_but_verified_at_is_not() -> None:
    # OKF §5.2 requires `by` within `generated` and gives every verification
    # event both `by` and `at`.
    record = {"by": "codex/1.0"}
    assert derive_annotation({"generated": record}, now=NOW)["generation"] == (
        _generation("recorded")
    )
    assert (
        derive_annotation({"verified": record}, now=NOW)["review"]["state"]
        == "malformed"
    )


@pytest.mark.parametrize(
    ("intent_actor", "expected_by"),
    [
        (None, f"markdown-vault-mcp/{package_version()}"),
        ("human:Jane Doe", "human:Jane Doe"),
        ("human:alice@example.com", "human:alice@example.com"),
    ],
)
def test_server_generated_stamp_reads_back_recorded(
    tmp_path: Path, intent_actor: str | None, expected_by: str
) -> None:
    enrich = build_okf_write_enrich(
        okf_write=True,
        detector=OkfDetector(tmp_path, mode="on"),
        version=package_version(),
    )
    assert enrich is not None
    note = "---\ntype: Concept\nsources: [{id: a, resource: https://a}]\n---\nBody\n"
    if intent_actor is None:
        text = enrich(note, "write")
    else:
        with okf_write_intent(OkfWriteIntent(actor=intent_actor)):
            text = enrich(note, "write")
    metadata = frontmatter.loads(text).metadata
    assert metadata["generated"]["by"] == expected_by
    annotation = derive_annotation(metadata, now=NOW)
    assert annotation["generation"] == _generation("recorded")
    assert annotation["attribution"]["state"] == "recorded"
    (tmp_path / "note.md").write_text(text)
    report = audit_bundle(tmp_path)
    assert report.malformed_generated == report.malformed_sources == OkfFinding(0, ())


def test_audit_provenance_shares_the_annotation_rules(tmp_path: Path) -> None:
    files = {
        "by-number.md": f"---\ntype: Concept\ngenerated: {{by: 7, at: '{AT}'}}\n---\n",
        "date-at.md": "---\ntype: Concept\ngenerated: {by: codex/1.0, at: 2026-11-14}\n---\n",
        "naive-at.md": (
            "---\ntype: Concept\n"
            "generated: {by: codex/1.0, at: '2026-11-14T09:00:00'}\n---\n"
        ),
        "no-at.md": "---\ntype: Concept\ngenerated: {by: codex/1.0}\n---\n",
        "mixed.md": "---\ntype: Concept\nsources: [{resource: https://a}, https://x]\n---\n",
        "no-resource.md": "---\ntype: Concept\nsources: [{id: a}]\n---\n",
        "good.md": (
            "---\ntype: Concept\nsources: [{id: a, resource: https://a}]\n"
            f"generated: {{by: markdown-vault-mcp/1.4.0, at: '{AT}'}}\n---\n"
        ),
    }
    for name, raw in files.items():
        (tmp_path / name).write_text(raw)
    report = audit_bundle(tmp_path)
    assert report.malformed_generated == OkfFinding(2, ("by-number.md", "naive-at.md"))
    assert report.malformed_sources == OkfFinding(2, ("mixed.md", "no-resource.md"))


def test_over_cap_provenance_is_withheld_like_evidence(tmp_path: Path) -> None:
    prefix = "---\ntype: Concept\nsources: nope\ngenerated: {by: 7}\n---\n"
    (tmp_path / "exact.md").write_text(prefix + "a" * (_MAX_READ_CHARS - len(prefix)))
    (tmp_path / "large.md").write_text(
        prefix + "a" * (_MAX_READ_CHARS + 1 - len(prefix))
    )
    (tmp_path / "broken.md").write_text("---\nsources: [\ngenerated: {by: 7}\n---\n")
    report = audit_bundle(tmp_path)
    assert report.malformed_sources == OkfFinding(1, ("exact.md",))
    assert report.malformed_generated == OkfFinding(1, ("exact.md",))
    assert report.evidence_unavailable == OkfFinding(2, ("broken.md", "large.md"))
