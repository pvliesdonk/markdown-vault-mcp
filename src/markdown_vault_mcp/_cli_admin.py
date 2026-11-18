"""The commands that write: the ``admin`` group and the old names (#2005).

``index``, ``reindex`` and ``embeddings import`` change the shared index or the
stored vectors, and ``repair-log`` changes one ``log.md`` (#1403), so they live
under ``admin``; every other command except ``serve`` only reads
(:mod:`._cli_reader`). The names the first three shipped under stay as hidden
top-level aliases that run the same command and name the new form on stderr.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import typer

from markdown_vault_mcp.config import _ENV_PREFIX

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp._okf_log_repair import LogRepairPreview


def register_okf_commands(app: typer.Typer) -> None:
    """Register deliberate OKF migrations."""
    okf = typer.Typer(help="Inspect and repair an OKF vault.", no_args_is_help=True)
    okf.command("strip-seeded-frontmatter")(strip_seeded_frontmatter)
    app.add_typer(okf, name="okf")


def strip_seeded_frontmatter(
    source_dir: str | None = typer.Option(
        None, help=f"Path to markdown vault (overrides ${_ENV_PREFIX}_SOURCE_DIR)."
    ),
    apply: bool = typer.Option(False, "--apply", help="Remove the listed keys."),
) -> None:
    """List historical placeholder fields in reserved files; apply to remove them."""
    from markdown_vault_mcp._okf_reserved_repair import ReservedFrontmatterRepair
    from markdown_vault_mcp.cli import _build_owning_vault, _build_vault
    from markdown_vault_mcp.exceptions import MarkdownMCPError

    try:
        built = (
            _build_owning_vault("okf strip-seeded-frontmatter", source_dir)
            if apply
            else _build_vault(
                source_dir, ":memory:", scratch_state=True, shared_files=True
            )
        )
        try:
            items = ReservedFrontmatterRepair(built.vault).run(
                apply=apply, allow_protected=True
            )
            if not items:
                typer.echo("Nothing to repair.")
            action = "Removed" if apply else "Would remove"
            for item in items:
                if item.skip_reason:
                    typer.echo(f"Skipped {item.path}: {item.skip_reason}")
                for key in item.keys:
                    typer.echo(f"{action} {item.path}: {key}")
            if any(item.skip_reason for item in items):
                raise typer.Exit(1)
        finally:
            built.close()
    except (MarkdownMCPError, OSError) as exc:
        if apply:
            typer.echo(
                "This batch has no rollback; inspect the vault before retrying.",
                err=True,
            )
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc


def register_admin_commands(
    app: typer.Typer,
    index: Callable[..., None],
    reindex: Callable[..., None],
    embeddings_import: Callable[..., None],
) -> None:
    """Register the ``admin`` group and the hidden aliases for its old names.

    Args:
        app: The Typer app receiving ``admin`` and the aliases.
        index: The full-build command.
        reindex: The incremental reindex command.
        embeddings_import: The command that copies vectors from another store.
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

    app.command("index", hidden=True)(_alias(index, "admin index"))
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
        result = built.vault.writer.okf_apply_log_repair(
            path, etag=etag, allow_protected=True
        )
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
