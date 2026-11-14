"""Explicit local Decisions command, with lazy preflight and native ownership."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

import typer

from markdown_vault_mcp._cli_output import terminal_text
from markdown_vault_mcp._okf_decisions import DecisionsClient
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.config_sections._assembly import source_dir_problem
from markdown_vault_mcp.git.submodules import SubmoduleGuard
from markdown_vault_mcp.managers.okf_decide import (
    DecideReport,
    DecideSelection,
    DecisionPolicy,
    OkfDecisionManager,
    discover_notes,
    load_vocabulary,
)
from markdown_vault_mcp.okf import OkfDetector

if TYPE_CHECKING:
    from markdown_vault_mcp.cli import _BuiltVault


def _local_config(config: ProjectConfig, source_dir: str | None) -> ProjectConfig:
    return replace(
        config,
        source_dir=Path(source_dir or config.source_dir).resolve(),
        git_repo_url=None,
        git_token=None,
        embedding_provider=None,
        summarize_provider=None,
        summarize_openai_api_key=None,
        summarize_openai_base_url=None,
        index_path=None,
        state_path=None,
        embeddings_path=None,
        embedding_store="sqlite",
        embedding_search="streamed",
        embedding_postgres_dsn=None,
        embedding_postgres_schema=None,
    )


def _build_decide_vault(config: ProjectConfig, source_dir: str | None) -> _BuiltVault:
    from markdown_vault_mcp.cli import _build_vault

    return _build_vault(config=_local_config(config, source_dir), scratch_index=True)


def _policy(
    config: ProjectConfig,
    overrides: list[str],
    replacement: float | None,
    meaningful: float | None,
) -> DecisionPolicy:
    thresholds = {
        key: getattr(config, f"okf_decisions_{key}_threshold")
        for key in ("type", "status", "description", "sources", "stale_after")
    }
    seen = set()
    for override in overrides:
        key, value = override.split("=", 1)
        if key not in thresholds or key in seen:
            raise ValueError("Unknown or repeated threshold.")
        seen.add(key)
        thresholds[key] = float(value)
    return DecisionPolicy(
        thresholds,
        config.okf_decisions_replace_threshold if replacement is None else replacement,
        config.okf_decisions_meaningful_threshold if meaningful is None else meaningful,
        config.okf_write,
    )


def _render(report: DecideReport, json_output: bool) -> NoReturn:
    if json_output:
        typer.echo(json.dumps(asdict(report), ensure_ascii=False))
    else:
        for result in report.results:
            typer.echo(terminal_text(json.dumps(result, ensure_ascii=False)))
        typer.echo(f"{report.counts} {report.terminal_error or ''}")
    raise typer.Exit(report.exit_code())


def register_okf_commands(okf: typer.Typer) -> None:
    """Register local OKF maintenance commands on the caller's single group.

    Args:
        okf: Existing local command group.
    """

    @okf.command()
    def decide(  # noqa: PLR0913, RUF100 -- Typer requires one parameter per public option.
        source_dir: str | None = typer.Option(None, help="Local vault override."),
        since: str | None = typer.Option(
            None, help="Select mtime since an aware ISO datetime."
        ),
        missing_type: bool = typer.Option(
            False, help="Select notes without a raw type."
        ),
        threshold: Annotated[
            list[str] | None,
            typer.Option(help="Repeat KEY=FLOAT for field acceptance."),
        ] = None,
        replace_threshold: float | None = typer.Option(
            None, help="Strict replacement threshold."
        ),
        meaningful_threshold: float | None = typer.Option(
            None, help="Whole-change acceptance threshold."
        ),
        json_output: bool = typer.Option(
            False, "--json", help="Emit one complete JSON report."
        ),
    ) -> None:
        """Apply closed metadata decisions to explicitly selected local notes."""
        report = DecideReport()
        try:
            config = _local_config(ProjectConfig.from_env(), source_dir)
            selection = DecideSelection(
                datetime.fromisoformat(since) if since else None, missing_type
            )
            policy = _policy(
                config, threshold or [], replace_threshold, meaningful_threshold
            )
            if (
                not config.okf_types_path
                or not OkfDetector(config.source_dir, config.okf_mode).state().active
            ):
                _render(DecideReport(), json_output)
            if config.read_only or source_dir_problem(config):
                raise ValueError("Requires a writable local source directory.")
            vocabulary = load_vocabulary(config.source_dir, config.okf_types_path)
            excludes = VaultSettings(
                exclude_patterns=config.exclude,
                conventions_file=config.conventions_file,
            ).effective_exclude_patterns()
            guard = SubmoduleGuard(config.source_dir)
            discovery = discover_notes(
                config.source_dir, selection, vocabulary.path, excludes, guard
            )
            if not discovery[0]:
                for error in discovery[1]:
                    report.add(error)
                _render(report, json_output)
            if not config.okf_decisions_api_key:
                raise ValueError("Set OKF_DECISIONS_API_KEY or OPENAI_API_KEY.")
        except (OSError, UnicodeError, ValueError) as error:
            raise typer.BadParameter(
                "Invalid Decisions selection, configuration or vocabulary; check the local source, key and thresholds."
            ) from error
        try:
            with ExitStack() as stack:
                client = DecisionsClient(
                    config.okf_decisions_base_url, config.okf_decisions_api_key
                )
                stack.callback(client.close)
                built = _build_decide_vault(config, None)
                stack.callback(built.close)
                if config.okf_write:
                    built.vault.index.build_index()
                report = OkfDecisionManager(
                    built.vault, client, policy, vocabulary, guard
                ).decide(selection, discovery=discovery)
        except Exception:
            report.terminal_error = "Cannot complete local Decisions pass safely."
            report.counts["error"] += 1
        _render(report, json_output)
