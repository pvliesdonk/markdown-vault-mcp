"""Local Decisions command gates and lifecycle using synthetic fixtures only."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from typer.testing import CliRunner

from markdown_vault_mcp import _cli_okf
from markdown_vault_mcp import cli as cli_module
from markdown_vault_mcp._okf_decisions import DecisionsClient
from markdown_vault_mcp.cli import app
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.managers.okf_decide import DecideReport
from tests.fixtures.okf_decisions import accept_answers, vocabulary_text

if TYPE_CHECKING:
    from pathlib import Path


runner = CliRunner()


def _source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "types.md").write_text(vocabulary_text())
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_TYPES_PATH", "types.md")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_DECISIONS_API_KEY", "synthetic-only")


def test_okf_decide_cli_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI forwards selectors and nondefault policy and closes both owners."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    _source(tmp_path, monkeypatch)
    (tmp_path / "note.md").write_text("Exact paragraph.")
    captured: list[Any] = []
    closed: list[str] = []

    class FixtureClient:
        def __init__(self, *_args: Any) -> None:
            pass

        def close(self) -> None:
            closed.append("client")

    class FixtureManager:
        def __init__(
            self, _vault: Any, _client: Any, policy: Any, _vocabulary: Any, _guard: Any
        ) -> None:
            captured.append(policy)

        def decide(
            self, selection: Any, *, discovery: Any, report: DecideReport
        ) -> DecideReport:
            captured.extend([selection, discovery])
            report.add({"path": "note.md", "disposition": "written"})
            return report

    monkeypatch.setattr(_cli_okf, "DecisionsClient", FixtureClient)
    monkeypatch.setattr(_cli_okf, "OkfDecisionManager", FixtureManager)
    monkeypatch.setattr(
        cli_module,
        "_build_vault",
        lambda **_kwargs: SimpleNamespace(
            vault=object(), close=lambda: closed.append("vault")
        ),
    )
    assert runner.invoke(app, ["okf", "decide", "--help"]).exit_code == 0
    result = runner.invoke(
        app,
        [
            "okf",
            "decide",
            "--missing-type",
            "--since",
            "2026-01-01T00:00:00Z",
            "--threshold",
            "type=0.87",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["counts"]["written"] == 1
    assert data["results"][0]["path"] == "note.md"
    policy, selection, discovery = captured
    assert policy.thresholds["type"] == 0.87
    assert selection.missing_type is True
    assert selection.since == datetime(2026, 1, 1, tzinfo=UTC)
    assert discovery == (["note.md"], [])
    assert closed == ["vault", "client"]
    assert "synthetic-only" not in result.output


@pytest.mark.parametrize(
    "args,remedy",
    [
        ([], "--since|--missing-type"),
        (["--server", "https://example.invalid"], "[Nn]o such option"),
        (["--since", "2026-11-14"], "offset|aware|ISO"),
        (["--since", "not-a-date"], "offset|aware|ISO"),
        (
            ["--missing-type", "--threshold", "unknown=0.5"],
            "[Uu]nknown|[Ff]ields|type.*status",
        ),
        (
            ["--missing-type", "--threshold", "type=0.5", "--threshold", "type=0.6"],
            "repeated|duplicate",
        ),
        (["--missing-type", "--threshold", "type"], "KEY=|key=value|KEY=FLOAT"),
        (["--missing-type", "--replace-threshold", "0.8"], "[Rr]eplace|replacement"),
        (["--missing-type", "--threshold", "type=nan"], "finite|range|\\[0, ?1\\]"),
    ],
)
def test_decide_invalid_invocation_is_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], remedy: str
) -> None:
    """Invalid request syntax/configuration is never reported as abstention."""
    _source(tmp_path, monkeypatch)
    import re

    result = runner.invoke(app, ["okf", "decide", *args])
    assert result.exit_code == 2
    assert re.search(remedy, result.output), result.output
    assert "synthetic-only" not in result.output


@pytest.mark.parametrize(
    "gate",
    [
        "disabled",
        "no-path",
        "empty",
        "missing-key",
        "readonly",
        "bad-vocabulary",
        "vocabulary-loop",
        "source-loop",
    ],
)
@pytest.mark.parametrize("json_output", [False, True])
def test_disabled_or_empty_decide_never_initializes_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str, json_output: bool
) -> None:
    """Gates precede factories, persistent stores and credentials validation."""
    _source(tmp_path, monkeypatch)
    if gate not in {"readonly", "bad-vocabulary", "vocabulary-loop"}:
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OKF_DECISIONS_API_KEY")
    if gate != "empty":
        (tmp_path / "note.md").write_text("A note.")
    if gate == "disabled":
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "off")
    if gate == "no-path":
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_OKF_TYPES_PATH")
    if gate == "readonly":
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    if gate == "bad-vocabulary":
        (tmp_path / "types.md").write_text(
            vocabulary_text().replace(
                "  - {value: Runbook, description: Ordered steps.}\n", ""
            )
        )

    if gate == "vocabulary-loop":
        (tmp_path / "types.md").unlink()
        (tmp_path / "types.md").symlink_to(tmp_path / "types.md")

    if gate == "source-loop":
        (tmp_path / "loop").symlink_to(tmp_path / "loop")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path / "loop"))

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Gated command opened collaborators")

    monkeypatch.setattr(_cli_okf, "DecisionsClient", forbidden)
    monkeypatch.setattr(cli_module, "_build_vault", forbidden)
    args = ["okf", "decide", "--missing-type"] + (["--json"] if json_output else [])
    result = runner.invoke(app, args)
    _assert_decide_gate(result, gate, json_output)


def _assert_decide_gate(result: Any, gate: str, json_output: bool) -> None:
    assert result.exit_code == (
        2
        if gate
        in {
            "missing-key",
            "readonly",
            "bad-vocabulary",
            "vocabulary-loop",
            "source-loop",
        }
        else 0
    ), result.output
    if gate in {"disabled", "no-path"}:
        assert "disabled" in result.stdout.lower()
    elif gate == "source-loop":
        assert "readable local source" in result.output.lower()
    elif gate == "readonly":
        assert (
            "writable" in result.output.lower() or "read-only" in result.output.lower()
        )
    elif gate == "bad-vocabulary":
        assert "2" in result.output and any(
            word in result.output.lower() for word in ("choice", "option", "type")
        )
    elif gate == "missing-key":
        assert (
            "OKF_DECISIONS_API_KEY" in result.output
            and "OPENAI_API_KEY" in result.output
        )
    elif gate == "vocabulary-loop":
        assert (
            "vocabulary" in result.output.lower()
            and "synthetic-only" not in result.output
        )
    if json_output and gate in {"disabled", "no-path", "empty"}:
        data = json.loads(result.stdout)
        assert data["disabled"] is (gate != "empty")
        assert data["results"] == [] and data["terminal_error"] is None
        assert data["counts"] == dict.fromkeys(
            ("written", "unchanged", "skipped", "conflict", "error"), 0
        )


@pytest.mark.parametrize(
    "remote",
    [
        {"git_repo_url": "https://example.invalid/private.git"},
        {"git_token": "synthetic-only"},
    ],
)
@pytest.mark.parametrize("summary", ["key", "url"])
def test_decide_commit_only_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote: dict[str, str],
    summary: str,
) -> None:
    """Construction, first native write and close retain commits without remote sync."""
    from markdown_vault_mcp import providers, summarizer
    from markdown_vault_mcp.git import _run
    from markdown_vault_mcp.git.bootstrap import RepoBootstrap
    from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
    from tests.conftest import wait_for_writer_drain

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Fixture"], check=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"],
        check=True,
    )
    original_run = _run.subprocess.run

    def checked(command: Any, *args: Any, **kwargs: Any) -> Any:
        if any(token in {"push", "pull", "fetch", "clone", "lfs"} for token in command):
            pytest.fail(f"Remote Git command: {command}")
        return original_run(command, *args, **kwargs)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Managed bootstrap ran")

    monkeypatch.setattr(_run.subprocess, "run", checked)
    monkeypatch.setattr(RepoBootstrap, "ensure_managed_repo", forbidden)
    monkeypatch.setattr(providers, "get_embedding_provider", forbidden)
    monkeypatch.setattr(summarizer, "get_summarizer", forbidden)
    monkeypatch.setattr(PostgresVectorBackend, "__init__", forbidden)
    values = (
        {"summarize_openai_api_key": "synthetic-summary"}
        if summary == "key"
        else {"summarize_openai_base_url": "https://fixture.invalid/v1"}
    )
    sentinels = dict.fromkeys(
        ("server.db", "state.db", "vectors.db"), b"fixture persistent store"
    )
    for name, content in sentinels.items():
        (tmp_path / name).write_bytes(content)
    config = ProjectConfig(
        source_dir=tmp_path,
        read_only=False,
        state_path=tmp_path / "state.db",
        embeddings_path=tmp_path / "vectors.db",
        embedding_provider="openai",
        summarize_provider="openai",
        embedding_store="postgres",
        embedding_postgres_dsn="host=fixture.invalid",
        embedding_postgres_schema="fixture",
        **values,
        index_path=tmp_path / "server.db",
        **remote,
    )
    built = cli_module._build_vault(
        config=_cli_okf._local_config(config, None), scratch_index=True
    )
    try:
        assert (
            built.settings is not None
            and built.settings.index_path is None
            and built.settings.state_path is None
        )
        built.vault.writer.write("note.md", "Synthetic note.")
        wait_for_writer_drain(built.vault)
    finally:
        built.close()
    assert {name: (tmp_path / name).read_bytes() for name in sentinels} == sentinels
    assert subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.parametrize("failure", ["settings", "vault", "close", "none"])
def test_existing_builder_snapshot_cleanup_and_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Existing assembly owns its returned instances on every subsequent failure."""
    from unittest.mock import MagicMock

    from markdown_vault_mcp import cli
    from markdown_vault_mcp import vault as vault_module
    from markdown_vault_mcp.config_sections import _assembly

    other = tmp_path / "override"
    other.mkdir()
    config = ProjectConfig(source_dir=tmp_path)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "unchanged-fixture")

    def no_env() -> Any:
        pytest.fail("Prepared snapshot reread environment")

    monkeypatch.setattr(ProjectConfig, "from_env", no_env)
    instances = MagicMock()
    instances.embedding_provider = instances.summarizer = instances.git_strategy = (
        instances.on_write
    ) = None
    monkeypatch.setattr(_assembly, "to_vault_instances", lambda _config: instances)
    settings_owner = _assembly.to_vault_settings
    seen: list[Path] = []

    def settings(cfg: ProjectConfig, **kwargs: Any) -> Any:
        seen.append(cfg.source_dir)
        if failure == "settings":
            raise RuntimeError("settings-failure")
        return settings_owner(cfg, **kwargs)

    monkeypatch.setattr(_assembly, "to_vault_settings", settings)
    if failure == "vault":

        def fail_vault(**_kwargs: Any) -> Any:
            raise RuntimeError("vault-failure")

        monkeypatch.setattr(vault_module, "Vault", fail_vault)
    if failure in {"settings", "vault"}:
        with pytest.raises(RuntimeError):
            cli._build_vault(str(other), config=config)
        instances.close.assert_called_once()
    else:
        built = cli._build_vault(str(other), config=config)
        if failure == "close":
            built.vault.close()
            monkeypatch.setattr(
                built.vault,
                "close",
                lambda: (_ for _ in ()).throw(RuntimeError("close-failure")),
            )
            with pytest.raises(RuntimeError):
                built.close()
        else:
            built.close()
        instances.close.assert_called_once()
    assert seen == [other]
    assert (
        __import__("os").environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] == "unchanged-fixture"
    )


@pytest.mark.parametrize(
    "summary",
    [
        {"summarize_openai_api_key": "fixture"},
        {"summarize_openai_base_url": "https://fixture.invalid/v1"},
    ],
)
def test_decide_sanitizes_all_model_and_storage_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, summary: dict[str, str]
) -> None:
    """Provider autodetection and PostgreSQL cannot escape a scratch snapshot."""
    from markdown_vault_mcp import cli

    config = ProjectConfig(
        source_dir=tmp_path,
        embedding_provider="openai",
        embedding_store="postgres",
        embedding_postgres_dsn="host=fixture.invalid password=fixture",
        embedding_postgres_schema="fixture",
        index_path=tmp_path / "server.db",
        state_path=tmp_path / "state.db",
        embeddings_path=tmp_path / "vectors",
        **summary,
    )
    snapshots: list[ProjectConfig] = []

    def capture(**kwargs: Any) -> Any:
        snapshots.append(kwargs["config"])
        return "built-fixture"

    monkeypatch.setattr(cli, "_build_vault", capture)
    assert (
        cli_module._build_vault(
            config=_cli_okf._local_config(config, None), scratch_index=True
        )
        == "built-fixture"
    )
    cfg = snapshots[0]
    assert cfg.embedding_provider is None and not cfg.indexing.vector_storage_configured
    assert cfg.embedding_store == "sqlite" and cfg.embedding_search == "streamed"
    assert cfg.embedding_postgres_dsn is cfg.embedding_postgres_schema is None
    assert not cfg.summarize.has_provider() and cfg.summarize_provider is None
    assert cfg.index_path is cfg.state_path is cfg.embeddings_path is None


@pytest.mark.parametrize(
    "failure", ["client", "builder", "index", "manager", "vault-close", "client-close"]
)
def test_decide_lifecycle_failures_close_all_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Every failure closes acquired owners and emits one safe structured report."""
    from unittest.mock import MagicMock

    _source(tmp_path, monkeypatch)
    (tmp_path / "note.md").write_text("Fixture paragraph.")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    client, built, manager = MagicMock(), MagicMock(), MagicMock()
    report = DecideReport()
    report.add(
        {
            "path": "note.md",
            "disposition": "written",
            "answers": [{"name": "type", "confidence": 0.99}],
        }
    )
    manager.decide.side_effect = lambda *_args, **kwargs: (
        kwargs["report"].results.extend(report.results),
        kwargs["report"].counts.update(report.counts),
    )
    client_factory = MagicMock(return_value=client)
    builder = MagicMock(return_value=built)
    manager_factory = MagicMock(return_value=manager)
    target = {
        "client": client_factory,
        "builder": builder,
        "index": built.vault.index.build_index,
        "manager": manager.decide,
        "vault-close": built.close,
        "client-close": client.close,
    }[failure]
    if failure == "index":
        manager.decide.side_effect = lambda *_args, **_kwargs: (
            built.vault.index.build_index()
        )
    target.side_effect = RuntimeError("synthetic-only private body")
    monkeypatch.setattr(_cli_okf, "DecisionsClient", client_factory)
    monkeypatch.setattr(cli_module, "_build_vault", builder)
    monkeypatch.setattr(_cli_okf, "OkfDecisionManager", manager_factory)
    result = runner.invoke(app, ["okf", "decide", "--missing-type", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["counts"]["error"] == 1
    assert "private body" not in result.output
    if failure != "client":
        client.close.assert_called_once()
    if failure not in {"client", "builder"}:
        built.close.assert_called_once()
    if failure in {"vault-close", "client-close"}:
        assert data["counts"]["written"] == 1
        assert data["results"][0]["answers"][0]["confidence"] == 0.99


def test_empty_discovery_errors_need_no_key_or_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed preflight reports operational status without requiring credentials."""
    _source(tmp_path, monkeypatch)
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_OKF_DECISIONS_API_KEY")

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Empty discovery initialized a collaborator")

    monkeypatch.setattr(_cli_okf, "DecisionsClient", forbidden)
    monkeypatch.setattr(cli_module, "_build_vault", forbidden)
    monkeypatch.setattr(
        _cli_okf,
        "discover_notes",
        lambda *_args: (
            [],
            [
                {
                    "path": "note.md",
                    "disposition": "error",
                    "error": "Cannot enumerate notes.",
                }
            ],
        ),
    )
    result = runner.invoke(app, ["okf", "decide", "--missing-type", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["counts"]["error"] == 1


def test_text_decision_report_escapes_terminal_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Text includes actual answers while untrusted paths cannot control a terminal."""
    from unittest.mock import MagicMock

    _source(tmp_path, monkeypatch)
    (tmp_path / "note.md").write_text("Fixture.")
    report = DecideReport()
    report.add(
        {
            "path": "note\x1b[31m.md",
            "disposition": "unchanged",
            "answers": [{"name": "meaningful", "probability": 0.1}],
        }
    )
    client, built, manager = MagicMock(), MagicMock(), MagicMock()
    manager.decide.side_effect = lambda *_args, **kwargs: (
        kwargs["report"].results.extend(report.results),
        kwargs["report"].counts.update(report.counts),
    )
    monkeypatch.setattr(_cli_okf, "DecisionsClient", lambda *_args: client)
    monkeypatch.setattr(cli_module, "_build_vault", lambda **_kwargs: built)
    monkeypatch.setattr(_cli_okf, "OkfDecisionManager", lambda *_args: manager)
    result = runner.invoke(app, ["okf", "decide", "--missing-type"])
    assert result.exit_code == 0
    assert "\x1b" not in result.stdout and "0.1" in result.stdout
    assert "meaningful" in result.stdout and "\\u001b" in result.stdout


def test_review_malformed_vocabulary_cli_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid operator YAML exits two without exposing its source content."""
    _source(tmp_path, monkeypatch)
    (tmp_path / "types.md").write_text("---\ntypes: [private-fixture-marker\n---\n")
    result = runner.invoke(app, ["okf", "decide", "--missing-type", "--json"])
    assert result.exit_code == 2
    assert "private-fixture-marker" not in result.output


@pytest.mark.parametrize("json_output", [False, True])
def test_review_preserved_source_date_renders_after_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, json_output: bool
) -> None:
    """Reports retain native YAML dates without failing after an applied write."""
    _source(tmp_path, monkeypatch)
    (tmp_path / "note.md").write_text(
        "---\nsources:\n  - resource: https://example.invalid/evidence\n    accessed: 2026-11-01\n---\nExact paragraph."
    )

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    class FixtureClient(DecisionsClient):
        def __init__(self, base_url: str, api_key: str) -> None:
            super().__init__(base_url, api_key, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(_cli_okf, "DecisionsClient", FixtureClient)
    args = ["okf", "decide", "--missing-type"] + (["--json"] if json_output else [])
    result = runner.invoke(app, args)
    assert "type: Reference" in (tmp_path / "note.md").read_text()
    assert result.exit_code == 0
    if json_output:
        report = json.loads(result.stdout)
        assert report["counts"]["written"] == 1
        assert (
            report["results"][0]["fields"]["sources"]["proposed"][0]["accessed"]
            == "2026-11-01"
        )
    else:
        assert '"disposition": "written"' in result.stdout
        assert "2026-11-01" in result.stdout


def test_review_native_invalid_configuration_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The native configuration error class uses the safe invocation boundary."""
    _source(tmp_path, monkeypatch)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE", "0")
    result = runner.invoke(app, ["okf", "decide", "--missing-type", "--json"])
    assert result.exit_code == 2
    assert "Invalid Decisions" in result.output


def test_invalid_note_retains_cli_batch_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-note parser failure cannot erase completed writes or abandon later notes."""
    _source(tmp_path, monkeypatch)
    bad = "---\n1: bad key\n---\nSecond."
    for name, text in (("a.md", "First."), ("b.md", bad), ("c.md", "Last.")):
        (tmp_path / name).write_text(text)

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    class FixtureClient(DecisionsClient):
        def __init__(self, base_url: str, api_key: str) -> None:
            super().__init__(base_url, api_key, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(_cli_okf, "DecisionsClient", FixtureClient)
    result = runner.invoke(
        app, ["okf", "decide", "--since", "1970-01-01T00:00:00Z", "--json"]
    )
    assert result.exit_code == 1
    assert "type: Reference" in (tmp_path / "a.md").read_text()
    report = json.loads(result.stdout)
    assert report["counts"]["written"] == 2 and report["counts"]["error"] == 1
    assert [row["path"] for row in report["results"]] == ["a.md", "b.md", "c.md"]
    assert (tmp_path / "b.md").read_text() == bad
