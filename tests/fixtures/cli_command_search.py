"""Synthetic live command tree: no callbacks or defaults may run in discovery."""

from __future__ import annotations

import typer


def explode() -> str:
    """Reject unintended invocation while discovering metadata."""
    raise AssertionError("discovery evaluated a callback/default")


def command_tree() -> typer.Typer:
    """Build visible, pruned, runnable and help-only command examples.

    Returns:
        An isolated application with synthetic metadata only.
    """
    app = typer.Typer(name="fixture", add_completion=False)
    app.callback()(lambda: None)

    @app.command(short_help="Read a record.\n\nLong unrelated banana example.")
    def read(
        record: str,
        suffix: str = typer.Argument("tail"),
        labels: list[str] = typer.Argument(None),  # noqa: B008 -- native variadic metadata
        secret: str = typer.Option(default_factory=explode),
    ) -> None:
        raise AssertionError((record, suffix, labels, secret))

    @app.command()
    def alpha() -> None:
        """Tie match."""
        explode()

    @app.command()
    def beta() -> None:
        """Tie match."""
        explode()

    for name, kwargs in (("hidden", {"hidden": True}), ("old", {"deprecated": True})):
        group = typer.Typer(**kwargs)
        group.command("child", help="Tie match.")(explode)
        app.add_typer(group, name=name, **kwargs)
    app.command("hidden-leaf", hidden=True, help="Tie match.")(explode)
    app.command("old-leaf", deprecated=True, help="Tie match.")(explode)
    app.command("serve", help="Tie match.")(explode)
    app.command("help", help="Tie match.")(explode)
    app.command("completion", help="Tie match.")(explode)
    okf = typer.Typer()
    okf.command("verify", help="Tie match.")(explode)
    app.add_typer(okf, name="okf")
    for name, invoke, help_only in (
        ("runnable", True, False),
        ("requires-child", False, False),
        ("help-only", True, True),
    ):
        group = typer.Typer(invoke_without_command=invoke, no_args_is_help=help_only)
        group.callback(help="Tie match.")(explode)
        group.command("child", help="Tie match.")(explode)
        app.add_typer(group, name=name)
    return app
