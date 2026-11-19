"""Local OKF publication command."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer


def _check_destination(destination: Path, root: Path) -> None:
    """Refuse destinations inside the vault, including symlink aliases."""
    resolved = destination.resolve()
    if resolved.is_relative_to(root.resolve()):
        raise ValueError("Export destination must be outside the vault")


def export(  # noqa: PLR0913, RUF100 - CLI flags need separate arguments.
    destination: Annotated[Path, typer.Argument(help="Destination ZIP file.")],
    source_dir: str | None = typer.Option(None, help="Vault directory."),
    folder: str = typer.Option("", help="Vault-relative scope root."),
    strict: bool = typer.Option(False, help="Require a self-contained artifact."),
    require_review: bool = typer.Option(
        False, help="Require reviews of exported concepts."
    ),
    require_check: str | None = typer.Option(
        None, help="Check kind; requires `--require-review` (default: human-review)."
    ),
    review_evidence: str | None = typer.Option(
        None,
        help="Evidence: binding (default) or local_receipt; requires `--require-review`.",
    ),
    include_hidden: bool = typer.Option(False, help="Include hidden paths."),
    include_index_excluded: bool = typer.Option(
        False, help="Include index exclusions."
    ),
    external_base: str | None = typer.Option(None, help="URL for outside references."),
) -> None:
    """Export an OKF ZIP or print a JSON refusal without writing the destination.

    Args:
        destination: Path to write after successful publication.
        source_dir: Optional vault directory override.
        folder: Vault-relative scope, or empty for the whole vault.
        strict: Require a self-contained, consistent, conformant artifact.
        require_review: Require a matching review for every concept.
        require_check: Required check kind.
        review_evidence: Accept bindings or require local receipts.
        include_hidden: Include eligible hidden paths.
        include_index_excluded: Include paths excluded from the search index.
        external_base: HTTP(S) base URL for outside destinations.
    """
    from markdown_vault_mcp._cli_read import _build_reader_vault
    from markdown_vault_mcp.bundle_inventory import InspectionPolicy
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.managers._write_kernel import atomic_write
    from markdown_vault_mcp.okf_bundle import build_okf_bundle
    from markdown_vault_mcp.okf_export_report import (
        ExportCollisionError,
        PublicationRefused,
        refusal_document,
    )

    built = None
    try:
        config = ProjectConfig.from_env()
        original_settings = VaultSettings.from_project_config(config)
        state_path = original_settings.effective_state_path(config.source_dir)
        inspection_policy = InspectionPolicy(
            original_settings.conventions_file,
            (state_path,) if state_path is not None else (),
            original_settings.effective_embeddings_path(),
            original_settings.index_path,
        )
        config = replace(
            config,
            embedding_provider=None,
            embeddings_path=None,
            index_path=None,
            state_path=None,
            summarize_provider=None,
            read_only=True,
            okf_write=False,
        )
        built = _build_reader_vault(
            config, source_dir, True, inspection_policy=inspection_policy
        )
        config = replace(config, source_dir=built.vault.source_dir)
        try:
            _check_destination(destination, config.source_dir)
            built.vault.index.build_index()
            result = build_okf_bundle(
                built.vault,
                config,
                folder=folder,
                strict=strict,
                require_review=require_review,
                require_check=require_check,
                review_evidence=review_evidence,
                include_hidden=include_hidden,
                include_index_excluded=include_index_excluded,
                external_base=external_base,
            )
        except (ExportCollisionError, PublicationRefused) as exc:
            typer.echo(refusal_document(exc).decode("utf-8"))
            raise typer.Exit(1) from exc
        atomic_write(destination, result.data)
        if result.report.snapshot.uncommitted_paths:
            typer.echo(
                "WARNING: uncommitted paths were not included in the Git snapshot: "
                + json.dumps(
                    result.report.snapshot.uncommitted_paths, ensure_ascii=True
                ),
                err=True,
            )
    except (ConfigurationError, ValueError, OSError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if built is not None:
            built.close()
