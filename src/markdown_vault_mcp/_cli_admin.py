"""Local index administration and compatibility aliases alongside hosted writers.

Parameter-source behavior: docs/design/reference/typer-parameter-sources.md.

``index``, ``reindex`` and ``embeddings import`` change the shared index or the
stored vectors, and ``repair-log`` changes one ``log.md`` (#1403), so they live
under ``admin``. Reader registration remains independently read-only
(:mod:`._cli_reader`). The names the first three shipped under still run the
same local command and name the new form on stderr; ``index`` can also host
subcommands when composed with the hosted writers.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import typer

from markdown_vault_mcp.config import _ENV_PREFIX

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp._okf_log_repair import LogRepairPreview


def register_admin_commands(
    app: typer.Typer,
    index: Callable[..., None],
    reindex: Callable[..., None],
    embeddings_import: Callable[..., None],
    *,
    index_group: typer.Typer | None = None,
) -> None:
    """Register local administration with aliases and optional hosted index composition.

    Args:
        app: The Typer app receiving ``admin`` and the aliases.
        index: The full-build command.
        reindex: The incremental reindex command.
        embeddings_import: The command that copies vectors from another store.
        index_group: Optional hosted index group preserving the local invocation.
    """
    admin = typer.Typer(
        help="Commands that write the index, the stored vectors or the vault.",
        no_args_is_help=True,
    )
    admin.command("index")(index)
    admin.command("reindex")(reindex)
    admin.command("repair-log")(repair_log)
    embeddings = typer.Typer(
        help="Move embeddings between vector stores.", no_args_is_help=True
    )
    embeddings.command("import")(embeddings_import)
    admin.add_typer(embeddings, name="embeddings")
    app.add_typer(admin, name="admin")

    if index_group is None:
        app.command("index", hidden=True)(_alias(index, "admin index"))
    else:
        _local_index_callback(index_group, index)
        app.add_typer(index_group, name="index")
    app.command("reindex", hidden=True)(_alias(reindex, "admin reindex"))
    old_embeddings = typer.Typer(hidden=True, no_args_is_help=True)
    old_embeddings.command("import")(
        _alias(embeddings_import, "admin embeddings import")
    )
    app.add_typer(old_embeddings, name="embeddings", hidden=True)


def _alias(command: Callable[..., None], new_name: str) -> Callable[..., None]:
    """Wrap *command* so its old name runs it and points at *new_name*."""

    @functools.wraps(command)
    def run(*args: Any, **kwargs: Any) -> None:
        typer.echo(
            f"Note: this command is now 'markdown-vault-mcp {new_name}'.",
            err=True,
        )
        command(*args, **kwargs)

    return run


def _local_index_callback(group: typer.Typer, index: Callable[..., None]) -> None:
    @group.callback(invoke_without_command=True)
    def run(
        ctx: typer.Context,
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        index_path: str | None = typer.Option(None, help="Local SQLite index file."),
        force: bool = typer.Option(False, help="Drop and rebuild the local index."),
    ) -> None:
        """Retain the shipped local index invocation when no child is selected."""
        if ctx.invoked_subcommand is not None:
            if any(
                (source := ctx.get_parameter_source(name)) is not None
                and source.name == "COMMANDLINE"
                for name in ("source_dir", "index_path", "force")
            ):
                raise typer.BadParameter(
                    "Parent local options cannot be combined with hosted reindex."
                )
            return
        _alias(index, "admin index")(
            source_dir=source_dir, index_path=index_path, force=force
        )


def repair_log(
    path: str = typer.Argument(
        ..., help="Vault-relative path of the affected log.md, such as guides/log.md."
    ),
    source_dir: str | None = typer.Option(
        None, help=f"Path to markdown vault (overrides ${_ENV_PREFIX}_SOURCE_DIR)."
    ),
    apply: str | None = typer.Option(
        None,
        "--apply",
        metavar="ETAG",
        help=(
            "Remove the blocks the preview showed, for the etag it printed; "
            "refused when the log changed since."
        ),
    ),
) -> None:
    """Preview, then remove, frontmatter blocks stacked at the start of a log.md.

    Versions 4.1.0 and 4.2.0-rc.0 could repeat a log's frontmatter block. By
    default the command prints the exact repeats it would remove. Review them,
    since identical blocks can be written on purpose. Applying keeps the
    complete original as log.md.frontmatter-repair.bak beside the log.
    """
    from markdown_vault_mcp.exceptions import MarkdownMCPError

    try:
        if apply is None:
            _print_preview(_preview(path, source_dir), source_dir)
        else:
            _apply(path, source_dir, apply)
    except MarkdownMCPError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc


def _preview(path: str, source_dir: str | None) -> LogRepairPreview:
    """Build a vault that writes nothing and preview the repair of *path*.

    The index stays in memory and Git settings are ignored, so a preview runs
    beside a server without touching its index, state or checkout.
    """
    from markdown_vault_mcp.cli import _build_vault

    built = _build_vault(source_dir, ":memory:", scratch_state=True, shared_files=True)
    try:
        return built.vault.writer.okf_preview_log_repair(path)
    finally:
        built.close()


def _print_preview(preview: LogRepairPreview, source_dir: str | None) -> None:
    """Print the exact removal and the command that applies it, quoted to paste."""
    import shlex

    if preview.status != "review_required":
        typer.echo(f"Nothing to repair in {preview.path}. {preview.explanation}")
        return
    blocks = preview.removed_blocks
    typer.echo(
        f"{preview.path}: {blocks} repeated frontmatter block"
        f"{'' if blocks == 1 else 's'} to remove, bytes {preview.removal_start} "
        f"to {preview.removal_end - 1}. {preview.explanation}"
    )
    # The removal parsed as YAML, which admits no terminal control characters.
    typer.echo(">>> removal begins")
    typer.echo(preview.removal_text, nl=False)
    typer.echo("<<< removal ends")
    words = ["markdown-vault-mcp", "admin", "repair-log", preview.path]
    if source_dir:
        words += ["--source-dir", source_dir]
    words += ["--apply", preview.etag]
    typer.echo(f"To remove it, run: {shlex.join(words)}")


def _apply(path: str, source_dir: str | None, etag: str) -> None:
    """Apply the reviewed repair of *path* and report the recovery file.

    Raises:
        typer.Exit: With code 1 when the log changed since the preview, or the
            repair stopped part-way.
    """
    from markdown_vault_mcp._okf_log_repair import LogRepairInterrupted
    from markdown_vault_mcp.cli import _build_owning_vault
    from markdown_vault_mcp.exceptions import ConcurrentModificationError

    built = _build_owning_vault("admin repair-log", source_dir)
    try:
        result = built.vault.writer.okf_apply_log_repair(path, etag=etag)
    except ConcurrentModificationError as exc:
        typer.echo(
            f"ERROR: {path} changed since the preview, so nothing was changed. "
            "Preview it again and review the new removal.",
            err=True,
        )
        raise typer.Exit(1) from exc
    except LogRepairInterrupted as exc:
        typer.echo(f"ERROR: {exc} Cause: {exc.__cause__}", err=True)
        raise typer.Exit(1) from exc
    finally:
        built.close()
    blocks = result.removed_blocks
    typer.echo(
        f"Repaired {result.path}: removed {blocks} block"
        f"{'' if blocks == 1 else 's'}. The complete original is at "
        f"{result.recovery_path}; inspect both before deleting it."
    )
