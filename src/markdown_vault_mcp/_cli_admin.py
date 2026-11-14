"""Local index administration and compatibility aliases alongside hosted writers.

Parameter-source behavior: docs/design/reference/typer-parameter-sources.md.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from collections.abc import Callable


def register_admin_commands(
    app: typer.Typer,
    index: Callable[..., None],
    reindex: Callable[..., None],
    embeddings_import: Callable[..., None],
    *,
    index_group: typer.Typer | None = None,
) -> None:
    """Register the ``admin`` group and the hidden aliases for its old names.

    Args:
        app: The Typer app receiving ``admin`` and the aliases.
        index: The full-build command.
        reindex: The incremental reindex command.
        embeddings_import: The command that copies vectors from another store.
        index_group: Optional hosted index group preserving the local invocation.
    """
    admin = typer.Typer(
        help="Commands that write the index or the stored vectors.",
        no_args_is_help=True,
    )
    admin.command("index")(index)
    admin.command("reindex")(reindex)
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
