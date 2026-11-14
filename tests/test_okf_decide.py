"""Synthetic native write/discovery integration for explicit Decisions maintenance."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import frontmatter as fm
import httpx
import pytest

from markdown_vault_mcp._okf_decisions import DecisionsClient
from markdown_vault_mcp.managers import okf_decide as module
from markdown_vault_mcp.managers.okf_decide import (
    DecideSelection,
    DecisionPolicy,
    OkfDecisionManager,
    load_vocabulary,
)
from markdown_vault_mcp.scanner import strip_frontmatter_block
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain
from tests.fixtures.okf_decisions import accept_answers, vocabulary_text, write_notes

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)
THRESHOLDS = dict.fromkeys(
    ("type", "status", "description", "sources", "stale_after"), 0.8
)


def test_discovery_uses_filesystem_and_union(tmp_path: Path) -> None:
    """Disk-only notes and filesystem mtime drive selection, once per path."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    for name, text in {
        "old.md": "---\ntype: Reference\n---\nOld.",
        "fresh.md": "---\ntype: Reference\ngenerated: {at: '2000-01-01T00:00:00Z'}\n---\nFresh.",
        "untyped.md": "Untyped.",
        "SKILL.md": "Skill.",
        "log.md": "Log.",
        "excluded.md": "Excluded.",
    }.items():
        (tmp_path / name).write_text(text)
        os.utime(tmp_path / name, (0, 0))
    os.utime(tmp_path / "fresh.md", (NOW.timestamp(), NOW.timestamp()))
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", exclude_patterns=("excluded.md",)
        ),
    )
    try:
        vocab = load_vocabulary(tmp_path, "types.md")
        paths, errors = module.discover_notes(
            tmp_path,
            DecideSelection(since=NOW, missing_type=True),
            vocab.path,
            vault.exclude_patterns,
        )
        assert paths == ["fresh.md", "untyped.md"] and errors == []
        with pytest.raises(ValueError):
            DecideSelection()
        with pytest.raises(ValueError):
            DecideSelection(since=datetime(2026, 1, 1))
    finally:
        vault.close()


@pytest.mark.parametrize("enforced", [False, True])
@pytest.mark.parametrize(
    "evidence,paragraph",
    [
        (
            ("https://example.invalid/evidence", ["https://example.invalid/evidence"]),
            "Exact paragraph.",
        ),
        (
            ("https://example.invalid/a`code`b", ["https://example.invalid/a"]),
            "Exact paragraph.",
        ),
        (('<img src="https://example.invalid/diagram.png">', []), "Exact paragraph."),
        (
            (
                "See 'https://example.invalid/evidence'.",
                ["https://example.invalid/evidence"],
            ),
            "Exact paragraph.",
        ),
        (
            ("https://example.invalid/O'Reilly", ["https://example.invalid/O'Reilly"]),
            "Exact paragraph.",
        ),
        (("![Image\n`code`\n](https://example.invalid/image)", []), "Exact paragraph."),
        (
            (
                "[Evidence\n`code`\n](https://example.invalid/proof)",
                ["https://example.invalid/proof"],
            ),
            "Exact paragraph.",
        ),
        (("[Evidence](fa`code`ct.md)", []), "Exact paragraph."),
        (("[Evidence\n`code`\n](fact.md)", ["fact.md"]), "Exact paragraph."),
        (
            ("HTTPS://example.invalid/proof.", ["HTTPS://example.invalid/proof"]),
            "Exact paragraph.",
        ),
        (
            (
                "HtTpS://example.invalid/Mixed%2fPath?q=A%26B.",
                ["HtTpS://example.invalid/Mixed%2fPath?q=A%26B"],
            ),
            "Exact paragraph.",
        ),
        (
            ("https://example.invalid/evidence", ["https://example.invalid/evidence"]),
            "Verbatim\u2028paragraph.",
        ),
    ],
)
def test_complete_decide_persists_once_with_etag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enforced: bool,
    evidence: tuple[str, list[str]],
    paragraph: str,
) -> None:
    """Real writer retains raw unknown keys and body; only actual changes stamp."""
    clock = [NOW]
    monkeypatch.setattr(module, "_now", lambda: clock[0])
    source_text, resources = evidence
    original_body = (
        paragraph + "\n\n  Keep internal whitespace.  \n\n" + source_text + "\n"
    )
    write_notes(
        tmp_path,
        {
            "types.md": vocabulary_text(),
            "fact.md": "---\ntype: Reference\n---\nContained fact.",
            "note.md": "---\ncustom: {nested: keep}\nverified: [{by: 'human:fixture', at: '2026-01-01T00:00:00Z'}]\n---\n"
            + original_body,
        },
    )
    calls: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["input"][0]["content"][2]["text"] == original_body
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=enforced),
    )
    builds: list[int] = []
    original_build = vault.index.build_index

    def build() -> Any:
        builds.append(1)
        result = original_build()
        clock[0] = NOW + timedelta(minutes=5)
        return result

    monkeypatch.setattr(vault.index, "build_index", build)
    native = vault.writer.write
    seen: list[dict[str, Any]] = []

    def write(*args: Any, **kwargs: Any) -> Any:
        seen.append({"args": args, **kwargs})
        return native(*args, **kwargs)

    monkeypatch.setattr(vault.writer, "write", write)
    manager = OkfDecisionManager(
        vault,
        client,
        DecisionPolicy(THRESHOLDS, okf_write=enforced),
        load_vocabulary(tmp_path, "types.md"),
    )
    try:
        report = manager.decide(DecideSelection(missing_type=True))
        wait_for_writer_drain(vault)
        assert report.exit_code() == 0 and report.counts["written"] == 1
        post = fm.load(tmp_path / "note.md")
        assert post["type"] == "Reference" and post["status"] == "draft"
        assert post["description"] == paragraph
        assert post["custom"] == {"nested": "keep"}
        assert post.get("sources", []) == [
            {"resource": resource} for resource in resources
        ]
        if not enforced:
            assert post["generated"]["at"] == "2026-11-14T10:00:00Z"
        assert post["stale_after"] == (
            "2026-12-14T10:05:00Z" if enforced else "2026-12-14T10:00:00Z"
        )
        assert len(builds) == int(enforced)
        assert post["generated"]["by"] == "process:okf-decide"
        assert "verified" not in post and "last_edited" not in post
        assert len(calls) == len(seen) == 1
        assert seen[0]["args"] == ("note.md", original_body)
        assert isinstance(seen[0]["if_match"], str)
        assert "---" not in strip_frontmatter_block((tmp_path / "note.md").read_text())
        assert "  Keep internal whitespace." in post.content
    finally:
        client.close()
        vault.close()


@pytest.mark.parametrize(
    "outcome,enforced",
    [
        *(
            (outcome, enforced)
            for outcome in ("equal", "meaningless", "refusal", "low")
            for enforced in (False, True)
        ),
        ("expiry-overflow", True),
    ],
)
def test_second_run_noop_has_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, enforced: bool
) -> None:
    """Bytes, mtime, attestation, log and callbacks cannot move for abstentions."""
    (tmp_path / "types.md").write_text(
        vocabulary_text(durations=outcome == "expiry-overflow")
    )
    clock = [datetime(9999, 11, 30, tzinfo=UTC)]
    monkeypatch.setattr(module, "_now", lambda: clock[0])
    type_line = "type: Reference\n" if outcome in {"equal", "expiry-overflow"} else ""
    (tmp_path / "note.md").write_text(
        "---\n"
        + type_line
        + "status: draft\ndescription: Keep.\ngenerated: {by: 'human:fixture', at: '2026-01-01T00:00:00Z'}\nverified: [{by: 'human:fixture', at: '2026-01-01T00:00:00Z'}]\n---\n\nBody.\n"
    )
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        questions = json.loads(request.content)["questions"]
        overrides = (
            {"meaningful": {"type": "refusal", "name": None}}
            if outcome == "refusal"
            else {"meaningful": {"probability": 0.1}}
            if outcome == "meaningless"
            else {"type": {"confidence": 0.1}}
            if outcome == "low"
            else {}
        )
        return httpx.Response(
            200, json={"answers": accept_answers(questions, **overrides)}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=enforced),
        on_write=lambda p, _content, _op, **_kw: calls.append(p),
    )
    try:
        original_build = vault.index.build_index

        def forbidden_build() -> Any:
            if outcome != "expiry-overflow":
                pytest.fail("An abstention must not scan the vault.")
            result = original_build()
            clock[0] += timedelta(days=2)
            return result

        monkeypatch.setattr(vault.index, "build_index", forbidden_build)
        before = {
            p.name: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in tmp_path.glob("*.md")
        }
        manager = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS, okf_write=enforced),
            load_vocabulary(tmp_path, "types.md"),
        )
        for _ in range(2):
            assert (
                manager.decide(
                    DecideSelection(since=datetime(1970, 1, 1, tzinfo=UTC))
                ).counts["unchanged"]
                == 1
            )
        wait_for_writer_drain(vault)
        assert {
            p.name: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in tmp_path.glob("*.md")
        } == before
        assert calls == []
    finally:
        client.close()
        vault.close()


@pytest.mark.parametrize(
    "failure", ["conflict", "admission", "malformed", "terminal", "network", "partial"]
)
def test_concurrent_edit_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Conflicts/malformed replies never replace bytes; terminal failures stop work."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    (tmp_path / "a.md").write_text("First.\n")
    (tmp_path / "b.md").write_text("Second.\n")
    calls: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if failure == "conflict":
            (tmp_path / "a.md").write_text("Concurrent newer bytes.\n")
        if failure == "terminal":
            return httpx.Response(429, text="private")
        if failure == "network":
            raise httpx.ConnectError("private", request=request)
        questions = json.loads(request.content)["questions"]
        answers = (
            []
            if failure == "malformed"
            else accept_answers(
                questions,
                **(
                    {"type": {"type": "refusal", "name": None}}
                    if failure == "partial"
                    else {}
                ),
            )
        )
        return httpx.Response(200, json={"answers": answers})

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False, okf_mode="on", okf_write=failure == "admission"
        ),
    )
    original_build = vault.index.build_index

    def build() -> Any:
        result = original_build()
        (tmp_path / "a.md").write_text("Concurrent newer bytes.\n")
        return result

    if failure == "admission":
        monkeypatch.setattr(vault.index, "build_index", build)
    try:
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS, okf_write=failure == "admission"),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(DecideSelection(missing_type=True))
        assert len(calls) == (1 if failure == "terminal" else 2)
        if failure in {"conflict", "admission"}:
            assert (tmp_path / "a.md").read_text() == "Concurrent newer bytes.\n"
            assert report.counts["conflict"] == 1
        elif failure == "partial":
            assert report.exit_code() == 0 and report.counts["written"] == 2
            assert "type" not in fm.load(tmp_path / "a.md")
            assert fm.load(tmp_path / "a.md")["status"] == "draft"
        else:
            assert (tmp_path / "a.md").read_text() == "First.\n"
            assert report.exit_code() == 1
    finally:
        client.close()
        vault.close()


def test_terminal_error_retains_earlier_write(tmp_path: Path) -> None:
    """Stopping later paid work retains the first note's applied outcome."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text("Fixture paragraph.")
    submitted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        submitted.append(payload["input"][0]["content"][0]["text"])
        if len(submitted) == 2:
            return httpx.Response(401, text="private")
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=False, okf_mode="on")
    )
    try:
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(DecideSelection(missing_type=True))
        assert report.counts["written"] == report.counts["error"] == 1
        assert len(submitted) == 2 and report.exit_code() == 1
        assert fm.load(tmp_path / "a.md")["type"] == "Reference"
        assert (tmp_path / "c.md").read_text() == "Fixture paragraph."
    finally:
        client.close()
        vault.close()


@pytest.mark.parametrize("outcome", ["suppressed", "low", "conflict", "write-error"])
def test_reporting_retains_proposals_and_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Policy acceptance and persistence stay distinct even on suppression/failure."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    (tmp_path / "note.md").write_text(
        "Exact paragraph.\n\nhttps://example.invalid/evidence"
    )

    def respond(request: httpx.Request) -> httpx.Response:
        questions = json.loads(request.content)["questions"]
        overrides = (
            {"meaningful": {"probability": 0.1}}
            if outcome == "suppressed"
            else {"description": {"probability": 0.1}}
            if outcome == "low"
            else {}
        )
        if outcome == "conflict":
            (tmp_path / "note.md").write_text("Concurrent bytes.")
        return httpx.Response(
            200, json={"answers": accept_answers(questions, **overrides)}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=False, okf_mode="on")
    )
    if outcome == "write-error":

        def fail(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("private fixture body or key")

        monkeypatch.setattr(vault.writer, "write", fail)
    try:
        result = (
            OkfDecisionManager(
                vault,
                client,
                DecisionPolicy(THRESHOLDS),
                load_vocabulary(tmp_path, "types.md"),
            )
            .decide(DecideSelection(missing_type=True))
            .results[0]
        )
        assert result["answers"][0]["name"] == "meaningful"
        field = result["fields"]["description"]
        assert field["proposed"] == "Exact paragraph."
        assert field["accepted"] == (outcome in {"conflict", "write-error"})
        assert field["persisted"] is False
        assert result["fields"]["sources"]["candidates"] == {
            "sources:0": "https://example.invalid/evidence"
        }
        assert "private fixture" not in json.dumps(result)
    finally:
        client.close()
        vault.close()


def test_empty_injected_discovery_does_not_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even an empty preflight snapshot is authoritative, without another traversal."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    (tmp_path / "note.md").write_text("A note.")

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Discovery was repeated")

    monkeypatch.setattr(module, "discover_notes", forbidden)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    client = DecisionsClient(
        "https://fixture.invalid/v1",
        "fixture",
        transport=httpx.MockTransport(forbidden),
    )
    try:
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(DecideSelection(missing_type=True), discovery=([], []))
        assert report.results == [] and report.exit_code() == 0
    finally:
        client.close()
        vault.close()


def test_discovery_reports_traversal_and_read_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Traversal failures, submodules and unreadable notes have distinct outcomes."""
    from unittest.mock import MagicMock

    def scan(_root: Any, _excludes: Any, *, on_error: Any) -> Any:
        on_error(OSError(13, "denied", str(tmp_path / "directory")))
        yield tmp_path / "submodule.md"
        yield tmp_path / "missing.md"

    monkeypatch.setattr(module, "iter_markdown_files", scan)
    guard = MagicMock()
    guard.submodule_of.side_effect = lambda path: path.name == "submodule.md"
    paths, errors = module.discover_notes(
        tmp_path, DecideSelection(missing_type=True), "types.md", guard=guard
    )
    assert paths == []
    assert [error["error"] for error in errors] == [
        "Cannot enumerate notes.",
        "Cannot select note safely.",
    ]


@pytest.mark.parametrize(
    "failure", ["missing", "cold-missing", "unreadable", "conflict"]
)
def test_prerequest_read_failures_keep_discovery_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Native reread errors retain preflight failures and never contact Decisions."""
    from markdown_vault_mcp.exceptions import EditConflictError

    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))

    reads: list[str] = []

    def read(_path: str) -> Any:
        if failure == "cold-missing" and not reads:
            reads.append(_path)
            return native_read(_path)
        if failure == "unreadable":
            raise OSError("private fixture body")
        if failure == "conflict":
            raise EditConflictError("private fixture body")
        return None

    def forbidden(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Failed reread reached inference")

    (tmp_path / "note.md").write_text("[[missing]]")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    native_read = vault.reader.read
    client = DecisionsClient(
        "https://fixture.invalid/v1",
        "fixture",
        transport=httpx.MockTransport(forbidden),
    )
    monkeypatch.setattr(vault.reader, "read", read)
    try:
        errors = [
            {
                "path": "directory",
                "disposition": "error",
                "error": "Cannot enumerate notes.",
            }
        ]
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(DecideSelection(missing_type=True), discovery=(["note.md"], errors))
        assert len(report.results) == 2 and report.exit_code() == 1
        assert report.counts["conflict"] == (1 if failure == "conflict" else 0)
        assert "private fixture" not in json.dumps(report.results)
    finally:
        client.close()
        vault.close()


@pytest.mark.parametrize(
    "bad",
    [
        "---\ncustom: [\n---\nBroken.",
        "---\n1: bad key\n---\nNumeric.",
        "---\n- operator\n- metadata\n---\nSequence.",
        *(
            f"---\n{root}\n---\nInvalid root."
            for root in ("[]", "null", "false", "42", "scalar")
        ),
        "symlink-loop",
    ],
)
def test_review_discovery_bad_yaml_retains_other_notes(
    tmp_path: Path, bad: str
) -> None:
    """Malformed YAML is an observable per-note discovery failure."""
    if bad == "symlink-loop":
        (tmp_path / "a.md").symlink_to(tmp_path / "a.md")
    else:
        (tmp_path / "a.md").write_text(bad)
    (tmp_path / "b.md").write_text("Good.")
    paths, errors = module.discover_notes(
        tmp_path, DecideSelection(missing_type=True), "types.md"
    )
    assert paths == ["b.md"]
    assert len(errors) == 1 and errors[0]["path"] == "a.md"


@pytest.mark.parametrize("target", ["types.md", "log.md", "SKILL.md"])
def test_review_discovery_excludes_canonical_aliases(
    tmp_path: Path, target: str
) -> None:
    """A lexical alias cannot reintroduce vocabulary or reserved content."""
    (tmp_path / "types.md").write_text(vocabulary_text())
    (tmp_path / target).write_text(vocabulary_text())
    (tmp_path / "alias.md").symlink_to(tmp_path / target)
    paths, errors = module.discover_notes(
        tmp_path, DecideSelection(missing_type=True), "types.md"
    )
    assert paths == [] and errors == []


@pytest.mark.parametrize(
    "bad",
    [
        "---\ncustom: [\n---\nBroken.",
        "---\ncustom: &cycle [*cycle]\n---\nCyclic.",
        "---\n1: bad key\n---\nNumeric.",
        "---\n- operator\n- metadata\n---\nSequence.",
        *(
            f"---\n{root}\n---\nInvalid root."
            for root in ("[]", "null", "false", "42", "scalar")
        ),
    ],
)
def test_review_bad_metadata_keeps_prior_and_later_reports(
    tmp_path: Path, bad: str
) -> None:
    """YAML and cyclic metadata errors stay bounded to their own note."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    for name, text in (("a.md", "First."), ("b.md", bad), ("c.md", "Last.")):
        (tmp_path / name).write_text(text)
    submitted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        submitted.append(payload["input"][0]["content"][0]["text"])
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(
            DecideSelection(missing_type=True), discovery=(["a.md", "b.md", "c.md"], [])
        )
        assert [r["disposition"] for r in report.results] == [
            "written",
            "error",
            "written",
        ]
        assert submitted == ["Path: a.md", "Path: c.md"]
        assert (tmp_path / "b.md").read_text() == bad
        assert report.exit_code() == 1
    finally:
        client.close()
        vault.close()


@pytest.mark.parametrize("enforced", [False, True])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        ("Evidence", "note.md", "facts/Evidence.md", []),
        ("Proof", "note.md", "facts/Evidence.md", []),
        ("Proof", "facts/note.md", "facts/Evidence.md", ["Evidence.md"]),
        ("Evidence", "notes/note.md", "x/Evidence.md", ["long/Evidence.md"]),
        ("Evidence", "facts/note.md", "Evidence.md", ["facts/Evidence.md"]),
    ],
)
def test_native_wikilink_resolution_supplies_decision_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enforced: bool,
    required: bool,
    case: tuple[str, str, str, list[str]],
) -> None:
    """Scratch admission and native tie-breaks preserve writer requirement policy."""
    stem, source, target, others = case
    write_notes(
        tmp_path,
        {
            **dict.fromkeys([target, *others], "---\naliases: [Proof]\n---\nEvidence."),
            "types.md": vocabulary_text(durations=False),
            source: "Initial body with [[old-unresolved]].",
        },
    )
    events: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert events[-2:] == ["drained", "parsed"]
        assert (
            payload["input"][0]["content"][2]["text"]
            == f"First paragraph.\n\n[[{stem}]]"
        )
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    settings = VaultSettings(
        read_only=False,
        okf_mode="on",
        okf_write=enforced,
        required_frontmatter=("operator",) if required else None,
        attachment_extensions=(".PDF",),
        title_field="name",
    )
    vault = Vault(source_dir=tmp_path, settings=settings)
    original_build = vault.index.build_index
    original_drain = vault._coordinator.prepare_index_read
    original_parse = module.parse_note

    builds: list[int] = []

    def build() -> Any:
        builds.append(1)
        result = original_build()
        assert [
            row["raw_target"] for row in vault._fts.get_outlinks(source, limit=None)
        ] == ["old-unresolved"]
        (tmp_path / source).write_text(f"First paragraph.\n\n[[{stem}]]")
        return result

    def drain() -> None:
        original_drain()
        events.append("drained")

    def parse(*args: Any, **kwargs: Any) -> Any:
        assert events[-1] == "drained"
        assert args[2] is vault._chunk_strategy
        assert kwargs == {
            "title_field": "name",
            "attachment_extensions": frozenset({"pdf"}),
        }
        events.append("parsed")
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(vault.index, "build_index", build)
    monkeypatch.setattr(vault._coordinator, "prepare_index_read", drain)
    monkeypatch.setattr(module, "parse_note", parse)
    try:
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS, okf_write=enforced),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(DecideSelection(missing_type=True), discovery=([source], []))
        assert report.counts["written"] == 1, report.results
        assert fm.load(tmp_path / source)["sources"] == [{"resource": target}]
        assert len(builds) == 1
        assert vault._index_mgr._required_frontmatter is None
        assert settings.required_frontmatter == (("operator",) if required else None)
        assert vault._required_frontmatter == (["operator"] if required else None)
        assert vault._okf_migrate._reserved_frontmatter.required_fields == (
            ("operator",) if required else ()
        )
        if enforced:
            assert vault._okf_convention._reserved_frontmatter.required_fields == (
                ("operator",) if required else ()
            )
            assert (
                "operator" in fm.load((tmp_path / source).parent / "index.md")
                if required
                else True
            )
    finally:
        client.close()
        vault.close()


@pytest.mark.parametrize("failure", ["refresh", "publication"])
def test_native_refresh_failure_keeps_prior_and_later_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A failed native refresh cannot supply graph evidence or erase batch outcomes."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    for name, body in (("a.md", "First."), ("b.md", "[[a]]"), ("c.md", "Last.")):
        (tmp_path / name).write_text(body)
    submitted: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        submitted.append(payload["input"][0]["content"][0]["text"])
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))

    def failed_refresh(*, force: bool = False) -> None:
        assert not force
        raise RuntimeError("synthetic private refresh error")

    report = module.DecideReport()
    try:
        if failure == "publication":
            original_build = vault._index_mgr.build_index
            monkeypatch.setattr(vault._index_mgr, "build_index", failed_refresh)
            with pytest.raises(RuntimeError):
                vault.index.build_index()
            monkeypatch.setattr(vault._index_mgr, "build_index", original_build)
        else:
            monkeypatch.setattr(
                vault._coordinator, "prepare_index_read", failed_refresh
            )
        returned = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(
            DecideSelection(missing_type=True),
            discovery=(["a.md", "b.md", "c.md"], []),
            report=report,
        )
        assert returned is report
        assert [r["disposition"] for r in report.results] == [
            "written",
            "error",
            "written",
        ]
        assert submitted == ["Path: a.md", "Path: c.md"]
        assert (tmp_path / "b.md").read_text() == "[[a]]"
        assert "synthetic private" not in json.dumps(report.results)
    finally:
        client.close()
        vault.close()


@pytest.mark.parametrize("header", ["", "---\n \n---\n", "---\nUnclosed header.\n"])
def test_empty_or_unclosed_frontmatter_retains_body_on_write(
    tmp_path: Path, header: str
) -> None:
    """Absent/empty/unclosed headers admit prose without discarding original bytes."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    original = header + "Literal body."
    (tmp_path / "note.md").write_text(original)

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["input"][0]["content"][2]["text"] == strip_frontmatter_block(
            original
        )
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    client = DecisionsClient(
        "https://fixture.invalid/v1", "fixture", transport=httpx.MockTransport(respond)
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(DecideSelection(missing_type=True))
        assert report.counts["written"] == 1, report.results
        assert strip_frontmatter_block(
            (tmp_path / "note.md").read_text()
        ) == strip_frontmatter_block(original)
    finally:
        client.close()
        vault.close()


@pytest.mark.parametrize("enforced", [False, True])
def test_native_empty_publication_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enforced: bool
) -> None:
    """Published empty readiness survives queued graph/convention consumers."""
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=enforced),
    )
    vault.index.build_index()
    assert vault.index.is_queryable()
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text("First paragraph.\n\n[[missing]]")

    def forbidden_build() -> Any:
        pytest.fail("Published readiness must prevent another build.")

    monkeypatch.setattr(vault.index, "build_index", forbidden_build)
    client = DecisionsClient(
        "https://fixture.invalid/v1",
        "fixture",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "answers": accept_answers(json.loads(request.content)["questions"])
                },
            )
        ),
    )
    try:
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS, okf_write=enforced),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(DecideSelection(missing_type=True), discovery=(["a.md", "b.md"], []))
        assert report.counts["written"] == 2, report.results
        assert all(
            fm.load(tmp_path / name)["type"] == "Reference" for name in ("a.md", "b.md")
        )
    finally:
        client.close()
        vault.close()
