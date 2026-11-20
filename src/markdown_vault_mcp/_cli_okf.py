"""Explicit local Decisions command, with lazy preflight and native ownership."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml
from pydantic_core import to_jsonable_python

from markdown_vault_mcp._cli_output import terminal_text
from markdown_vault_mcp._okf_decisions import DecisionsClient
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.config_sections._assembly import source_dir_problem
from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git.submodules import SubmoduleGuard
from markdown_vault_mcp.managers.okf_decide import (
    DecideReport,
    DecideSelection,
    DecisionInputError,
    DecisionPolicy,
    OkfDecisionManager,
    Vocabulary,
    discover_notes,
    load_vocabulary,
)
from markdown_vault_mcp.okf import OkfDetector


def _local_config(config: ProjectConfig, source_dir: str | None) -> ProjectConfig:
    try:
        root = Path(source_dir or config.source_dir).resolve()
    except RuntimeError:
        raise DecisionInputError("Use a readable local source directory.") from None
    return replace(
        config,
        source_dir=root,
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
        try:
            key, value = override.split("=", 1)
            number = float(value)
        except ValueError:
            raise DecisionInputError(
                "Use --threshold KEY=FLOAT with a finite number."
            ) from None
        if key not in thresholds or key in seen:
            raise DecisionInputError(
                "Unknown or repeated threshold field; use each field once."
            )
        seen.add(key)
        thresholds[key] = number
    return DecisionPolicy(
        thresholds,
        config.okf_decisions_replace_threshold if replacement is None else replacement,
        config.okf_decisions_meaningful_threshold if meaningful is None else meaningful,
        config.okf_write,
    )


def _selection(since: str | None, missing_type: bool) -> DecideSelection:
    try:
        parsed_since = datetime.fromisoformat(since) if since else None
    except ValueError:
        raise DecisionInputError(
            "Use an offset-aware ISO datetime for --since."
        ) from None
    return DecideSelection(parsed_since, missing_type)


def _vocabulary(root: Path, path: str) -> Vocabulary:
    try:
        return load_vocabulary(root, path)
    except DecisionInputError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError, yaml.YAMLError):
        raise DecisionInputError(
            "Use a contained readable ordinary vocabulary with valid YAML."
        ) from None


def _render(report: DecideReport, json_output: bool) -> NoReturn:
    data = to_jsonable_python(asdict(report))
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        for result in data["results"]:
            typer.echo(terminal_text(json.dumps(result, ensure_ascii=False)))
        typer.echo(f"{report.counts} {report.terminal_error or ''}")
        if report.disabled:
            typer.echo("Decisions disabled: enable OKF and configure a types path.")
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
            None, help="Select modification time since an aware ISO datetime."
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
        from markdown_vault_mcp.cli import _build_vault

        report = DecideReport()
        try:
            config = _local_config(ProjectConfig.from_env(), source_dir)
            selection = _selection(since, missing_type)
            policy = _policy(
                config, threshold or [], replace_threshold, meaningful_threshold
            )
            if (
                not config.okf_types_path
                or not OkfDetector(config.source_dir, config.okf_mode).state().active
            ):
                _render(DecideReport(disabled=True), json_output)
            if config.read_only or source_dir_problem(config):
                raise DecisionInputError("Requires a writable local source directory.")
            vocabulary = _vocabulary(config.source_dir, config.okf_types_path)
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
                raise DecisionInputError("Set OKF_DECISIONS_API_KEY or OPENAI_API_KEY.")
        except DecisionInputError as error:
            raise typer.BadParameter(str(error)) from error
        except (
            OSError,
            ValueError,
            TypeError,
            ConfigurationError,
        ) as error:
            raise typer.BadParameter(
                "Invalid Decisions configuration: use finite [0,1] thresholds, replacement above type/status, a valid HTTP(S) endpoint and a readable local source."
            ) from error
        try:
            with ExitStack() as stack:
                client = DecisionsClient(
                    config.okf_decisions_base_url, config.okf_decisions_api_key
                )
                stack.callback(client.close)
                built = _build_vault(config=config, scratch_index=True)
                stack.callback(built.close)
                OkfDecisionManager(
                    built.vault, client, policy, vocabulary, guard
                ).decide(selection, discovery=discovery, report=report)
        except Exception:
            report.terminal_error = "Cannot complete local Decisions pass safely."
            report.counts["error"] += 1
        _render(report, json_output)
