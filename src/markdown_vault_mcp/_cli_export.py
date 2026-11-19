"""Local OKF publication command."""

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer


def _check_destination(destination: Path, root: Path) -> None:
    """Refuse vault paths and existing hard links to vault files."""
    resolved = destination.resolve()
    if resolved.is_relative_to(root.resolve()):
        raise ValueError("Export destination must be outside the vault")
    try:
        target = resolved.stat()
    except FileNotFoundError:
        return
    if target.st_nlink < 2:
        return

    def fail(error: OSError) -> None:
        raise error

    for directory, _, files in os.walk(root, onerror=fail):
        for name in files:
            try:
                item = (Path(directory) / name).stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (item.st_dev, item.st_ino) == (target.st_dev, target.st_ino):
                raise ValueError("Export destination aliases a vault file")


def _write_destination(destination: Path, root: Path, data: bytes) -> None:
    """Atomically replace an external destination with the completed archive."""
    _check_destination(destination, root)
    fd, temporary = tempfile.mkstemp(prefix=".okf-export-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        _check_destination(destination, root)
        Path(temporary).replace(destination)
    finally:
        with suppress(FileNotFoundError):
            Path(temporary).unlink()


def export(  # noqa: PLR0913, RUF100 - CLI flags need separate arguments.
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
    """Export an OKF ZIP or print a JSON refusal without writing the destination.

    Args:
        destination: Path to write after successful publication.
        source_dir: Optional vault directory override.
        folder: Vault-relative scope, or empty for the whole vault.
        strict: Require a self-contained, consistent, conformant artifact.
        include_hidden: Include eligible hidden paths.
        include_index_excluded: Include paths excluded from the search index.
        external_base: HTTP(S) base URL for outside destinations.
    """
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
        read_only=True,
        okf_write=False,
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
            _check_destination(destination, config.source_dir)
            built.vault.index.build_index()
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
        _write_destination(destination, config.source_dir, result.data)
        if result.report.snapshot.uncommitted_paths:
            typer.echo(
                "WARNING: uncommitted paths were not included in the Git snapshot: "
                + json.dumps(
                    result.report.snapshot.uncommitted_paths, ensure_ascii=True
                ),
                err=True,
            )
    except (ValueError, OSError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        built.close()
