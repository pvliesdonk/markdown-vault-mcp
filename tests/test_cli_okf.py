"""Local Decisions command gates and lifecycle using synthetic fixtures only."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from typer.testing import CliRunner

from markdown_vault_mcp import _cli_okf
from markdown_vault_mcp._okf_decisions import DecisionsClient
from markdown_vault_mcp.cli import app
from markdown_vault_mcp.config import ProjectConfig
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
    """The registered command produces a single report of real conditional writes."""
    _source(tmp_path, monkeypatch)
    (tmp_path / "note.md").write_text("Exact paragraph.")
    captured: list[dict[str, Any]] = []
    closed: list[bool] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        return httpx.Response(
            200, json={"answers": accept_answers(payload["questions"])}
        )

    class FixtureClient(DecisionsClient):
        def __init__(self, base_url: str, api_key: str) -> None:
            super().__init__(base_url, api_key, transport=httpx.MockTransport(respond))

        def close(self) -> None:
            closed.append(True)
            super().close()

    monkeypatch.setattr(_cli_okf, "DecisionsClient", FixtureClient)
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
            "type=0.8",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["counts"]["written"] == 1
    assert data["results"][0]["path"] == "note.md"
    assert len(captured) == 1 and closed == [True]
    assert "synthetic-only" not in result.output


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--server", "https://example.invalid"],
        ["--since", "2026-11-14"],
        ["--missing-type", "--threshold", "unknown=0.5"],
        ["--missing-type", "--threshold", "type=0.5", "--threshold", "type=0.6"],
        ["--missing-type", "--replace-threshold", "0.8"],
        ["--missing-type", "--threshold", "type=nan"],
    ],
)
def test_decide_invalid_invocation_is_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    """Invalid request syntax/configuration is never reported as abstention."""
    _source(tmp_path, monkeypatch)
    assert runner.invoke(app, ["okf", "decide", *args]).exit_code == 2


@pytest.mark.parametrize(
    "gate",
    ["disabled", "no-path", "empty", "missing-key", "readonly", "bad-vocabulary"],
)
def test_disabled_or_empty_decide_never_initializes_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    """Gates precede factories, persistent stores and credentials validation."""
    _source(tmp_path, monkeypatch)
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
        (tmp_path / "types.md").write_text("Not a vocabulary.")

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Gated command opened collaborators")

    monkeypatch.setattr(_cli_okf, "DecisionsClient", forbidden)
    monkeypatch.setattr(_cli_okf, "_build_decide_vault", forbidden)
    result = runner.invoke(app, ["okf", "decide", "--missing-type", "--json"])
    assert result.exit_code == (
        2 if gate in {"missing-key", "readonly", "bad-vocabulary"} else 0
    ), result.output


@pytest.mark.parametrize(
    "remote",
    [
        {"git_repo_url": "https://example.invalid/private.git"},
        {"git_token": "synthetic-only"},
    ],
)
def test_decide_commit_only_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote: dict[str, str]
) -> None:
    """Construction, first native write and close retain commits without remote sync."""
    from markdown_vault_mcp.git import _run
    from markdown_vault_mcp.git.bootstrap import RepoBootstrap
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
    config = ProjectConfig(
        source_dir=tmp_path,
        read_only=False,
        embedding_provider="openai",
        summarize_provider="openai",
        summarize_openai_api_key="synthetic-summary",
        index_path=tmp_path / "server.db",
        **remote,
    )
    built = _cli_okf._build_decide_vault(config, None)
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
    assert not (tmp_path / "server.db").exists()
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
    assert _cli_okf._build_decide_vault(config, None) == "built-fixture"
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

    from markdown_vault_mcp.managers.okf_decide import DecideReport

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
    manager.decide.return_value = report
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
    target.side_effect = RuntimeError("synthetic-only private body")
    monkeypatch.setattr(_cli_okf, "DecisionsClient", client_factory)
    monkeypatch.setattr(_cli_okf, "_build_decide_vault", builder)
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


@pytest.mark.parametrize("summary", ["key", "url"])
def test_native_decide_assembly_never_opens_models_or_postgres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, summary: str
) -> None:
    """Configured persistent/vector/model factories stay unopened on native writes."""
    from markdown_vault_mcp import providers, summarizer
    from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Model or persistent PostgreSQL factory opened")

    monkeypatch.setattr(providers, "get_embedding_provider", forbidden)
    monkeypatch.setattr(summarizer, "get_summarizer", forbidden)
    monkeypatch.setattr(PostgresVectorBackend, "__init__", forbidden)
    values = (
        {"summarize_openai_api_key": "fixture"}
        if summary == "key"
        else {"summarize_openai_base_url": "https://fixture.invalid/v1"}
    )
    config = ProjectConfig(
        source_dir=tmp_path,
        embedding_provider="fastembed",
        embedding_store="postgres",
        embedding_postgres_dsn="host=fixture.invalid",
        embedding_postgres_schema="fixture",
        index_path=tmp_path / "server.db",
        **values,
    )
    built = _cli_okf._build_decide_vault(config, None)
    try:
        built.vault.writer.write("note.md", "Synthetic accepted note.")
    finally:
        built.close()
    assert not (tmp_path / "server.db").exists()


def test_empty_discovery_errors_need_no_key_or_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed preflight reports operational status without requiring credentials."""
    _source(tmp_path, monkeypatch)
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_OKF_DECISIONS_API_KEY")

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Empty discovery initialized a collaborator")

    monkeypatch.setattr(_cli_okf, "DecisionsClient", forbidden)
    monkeypatch.setattr(_cli_okf, "_build_decide_vault", forbidden)
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

    from markdown_vault_mcp.managers.okf_decide import DecideReport

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
    manager.decide.return_value = report
    monkeypatch.setattr(_cli_okf, "DecisionsClient", lambda *_args: client)
    monkeypatch.setattr(_cli_okf, "_build_decide_vault", lambda *_args: built)
    monkeypatch.setattr(_cli_okf, "OkfDecisionManager", lambda *_args: manager)
    result = runner.invoke(app, ["okf", "decide", "--missing-type"])
    assert result.exit_code == 0
    assert "\x1b" not in result.stdout and "0.1" in result.stdout
    assert "meaningful" in result.stdout and "\\u001b" in result.stdout
