"""Local OKF publication command."""

from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer


def export(
    destination: Annotated[Path, typer.Argument(help="Destination ZIP file.")],
    source_dir: str | None = typer.Option(None, help="Vault directory."),
    folder: str = typer.Option("", help="Vault-relative scope root."),
    strict: bool = typer.Option(False, help="Require a self-contained artifact."),
    include_hidden: bool = typer.Option(False, help="Include hidden paths."),
    include_index_excluded: bool = typer.Option(
        False, help="Include index exclusions."
    ),
    external_base: str | None = typer.Option(None, help="URL for outside references."),
) -> None:
    """Export an OKF ZIP or print a JSON refusal without writing the destination."""
    from markdown_vault_mcp._cli_read import _build_reader_vault
    from markdown_vault_mcp.bundle_inventory import InspectionPolicy
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
    from markdown_vault_mcp.okf_bundle import build_okf_bundle
    from markdown_vault_mcp.okf_export_report import (
        ExportCollisionError,
        PublicationRefused,
        refusal_document,
    )

    config = ProjectConfig.from_env()
    original_settings = VaultSettings.from_project_config(config)
    config = replace(
        config,
        embedding_provider=None,
        embeddings_path=None,
        index_path=None,
        state_path=None,
        summarize_provider=None,
    )
    built = _build_reader_vault(config, source_dir, True)
    try:
        config = replace(config, source_dir=built.vault.source_dir)
        state_path = original_settings.effective_state_path(config.source_dir)
        # Keep the configured inventory boundary even though the reader opens no stores.
        built.vault._doc_mgr._inspection_policy = InspectionPolicy(
            original_settings.conventions_file,
            (state_path,) if state_path is not None else (),
            original_settings.effective_embeddings_path(),
            original_settings.index_path,
        )
        try:
            result = build_okf_bundle(
                built.vault,
                config,
                folder=folder,
                strict=strict,
                include_hidden=include_hidden,
                include_index_excluded=include_index_excluded,
                external_base=external_base,
            )
        except (ExportCollisionError, PublicationRefused) as exc:
            typer.echo(refusal_document(exc).decode("utf-8"))
            raise typer.Exit(1) from exc
        destination.write_bytes(result.data)
    finally:
        built.close()
