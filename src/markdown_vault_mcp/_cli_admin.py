"""The commands that write: the ``admin`` group and the old names (#2005).

``index``, ``reindex`` and ``embeddings import`` change the shared index or the
stored vectors, so they live under ``admin``; every other command except
``serve`` only reads (:mod:`._cli_reader`). The names they shipped under stay
as hidden top-level aliases that run the same command and name the new form
on stderr.
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
) -> None:
    """Register the ``admin`` group and the hidden aliases for its old names.

    Args:
        app: The Typer app receiving ``admin`` and the aliases.
        index: The full-build command.
        reindex: The incremental reindex command.
        embeddings_import: The command that copies vectors from another store.
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
