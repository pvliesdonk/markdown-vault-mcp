"""Synthetic native write/discovery integration for explicit Decisions maintenance."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
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
from tests.fixtures.okf_decisions import accept_answers, vocabulary_text

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
def test_complete_decide_persists_once_with_etag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enforced: bool
) -> None:
    """Real writer retains raw unknown keys and body; only actual changes stamp."""
    monkeypatch.setattr(module, "_now", lambda: NOW)
    (tmp_path / "types.md").write_text(vocabulary_text())
    original_body = "Exact paragraph.\n\n  Keep internal whitespace.  \n\nhttps://example.invalid/evidence\n"
    (tmp_path / "note.md").write_text(
        "---\ncustom: {nested: keep}\nverified: [{by: 'human:fixture', at: '2026-01-01T00:00:00Z'}]\n---\n"
        + original_body
    )
    calls: list[dict[str, Any]] = []
    writes: list[tuple[str, Any]] = []

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
        on_write=lambda p, op, **_kw: writes.append((p, op)),
    )
    if enforced:
        vault.index.build_index()
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
        assert post["description"] == "Exact paragraph."
        assert post["custom"] == {"nested": "keep"}
        assert post["sources"] == [{"resource": "https://example.invalid/evidence"}]
        if not enforced:
            assert post["generated"]["at"] == "2026-11-14T10:00:00Z"
        assert post["stale_after"] == "2026-12-14T10:00:00Z"
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


@pytest.mark.parametrize("outcome", ["equal", "meaningless", "refusal", "low"])
@pytest.mark.parametrize("enforced", [False, True])
def test_second_run_noop_has_no_side_effects(
    tmp_path: Path, outcome: str, enforced: bool
) -> None:
    """Bytes, mtime, attestation, log and callbacks cannot move for abstentions."""
    (tmp_path / "types.md").write_text(vocabulary_text(durations=False))
    (tmp_path / "note.md").write_text(
        "---\ntype: Reference\nstatus: draft\ndescription: Keep.\ngenerated: {by: 'human:fixture', at: '2026-01-01T00:00:00Z'}\nverified: [{by: 'human:fixture', at: '2026-01-01T00:00:00Z'}]\n---\n\nBody.\n"
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
        on_write=lambda p, _op, **_kw: calls.append(p),
    )
    try:
        if enforced:
            vault.index.build_index()
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
    "failure", ["conflict", "malformed", "terminal", "network", "partial"]
)
def test_concurrent_edit_is_never_overwritten(tmp_path: Path, failure: str) -> None:
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
        source_dir=tmp_path, settings=VaultSettings(read_only=False, okf_mode="on")
    )
    try:
        report = OkfDecisionManager(
            vault,
            client,
            DecisionPolicy(THRESHOLDS),
            load_vocabulary(tmp_path, "types.md"),
        ).decide(DecideSelection(missing_type=True))
        assert len(calls) == (1 if failure == "terminal" else 2)
        if failure == "conflict":
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
