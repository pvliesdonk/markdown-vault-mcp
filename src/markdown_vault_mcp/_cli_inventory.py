"""Local-only current-file discovery, independent of index admission."""

from __future__ import annotations

import json
import math
from pathlib import Path

import typer

from markdown_vault_mcp._cli_client import ClientError, ClientOptions, resolve_settings
from markdown_vault_mcp._cli_options import Local, SourceDir  # noqa: TC001 -- Typer resolves aliases at runtime
from markdown_vault_mcp._file_inventory import ScanLimits, Window, scan_current_files
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
from markdown_vault_mcp.exceptions import ConfigurationError


def changed_files(  # noqa: PLR0913, RUF100 -- flat CLI options for one operation
    start: str = typer.Option(
        ..., help="Inclusive RFC3339 instant with Z or UTC offset."
    ),
    end: str = typer.Option(
        ..., help="Exclusive RFC3339 instant with Z or UTC offset."
    ),
    source_dir: SourceDir = None,
    local: Local = False,
    max_entries: int = typer.Option(
        100_000, min=1, max=1_000_000, help="Maximum filesystem entries visited."
    ),
    max_seconds: float = typer.Option(
        10.0,
        min=0.001,
        max=300.0,
        help="Cooperative scan deadline; does not interrupt filesystem calls.",
    ),
) -> None:
    """List eligible current files in a UTC modification window as JSON (local only).

    No vault file contents, index or history are read. Dot paths and configured
    exclusions are omitted; symlinks are skipped with explicit incompleteness.
    Requires no-follow descriptor traversal; unsupported platforms
    return an incomplete result rather than falling back to unsafe traversal.
    Exit 0 means the eligible scan completed; exit 1 retains partial JSON;
    exit 2 rejects the request. This does not identify deletions or old names.
    """
    try:
        window = Window(start, end)
        if not math.isfinite(max_seconds):
            raise ValueError("--max-seconds must be finite.")
        if resolve_settings(ClientOptions(local=local)).url is not None:
            raise ClientError(
                "changed-files is local only; use --local and select the local vault."
            )
        config = ProjectConfig.from_env()
        selected = Path(source_dir) if source_dir is not None else config.source_dir
        try:
            root = selected.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ClientError(
                "The selected local vault directory is unavailable.", 1
            ) from None
        # Pure configuration mapping; unlike to_vault_settings, this does not
        # instantiate a provider, store, git strategy or any other collaborator.
        settings = VaultSettings.from_project_config(config)
        result = scan_current_files(
            root, window, settings, ScanLimits(max_entries, max_seconds)
        )
    except ClientError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.code) from None
    except (ValueError, ConfigurationError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(json.dumps(result, ensure_ascii=True))
    if not result["complete"]:
        raise typer.Exit(1)
