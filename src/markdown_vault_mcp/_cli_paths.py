"""CLI access to bounded native path discovery through the shared read runner."""

from __future__ import annotations

import typer

from markdown_vault_mcp._cli_discovery import _run
from markdown_vault_mcp._cli_options import (
    Agent,
    Folder,
    Local,
    Server,
    SourceDir,
    Timeout,
    WaitForPendingWrites,
)


def _query(value: str) -> str:
    from markdown_vault_mcp.query_types import PathQuery

    try:
        PathQuery(query=value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return value


def register(app: typer.Typer) -> None:
    """Register the bounded path-discovery command.

    Args:
        app: Domain CLI receiving the path command.
    """

    @app.command("search-path")
    def search_path(  # noqa: PLR0913, RUF100 -- native predicates plus shared CLI connection/output options
        ctx: typer.Context,
        query: str = typer.Argument(
            ..., callback=_query, help="Literal path text, 1-256 code points."
        ),
        fuzzy: bool = typer.Option(False, help="Try bounded ASCII typo matching."),
        folder: Folder = None,
        filters: list[str] | None = typer.Option(  # noqa: B008 -- Typer consumes parameter metadata
            None, "--filter", help="Indexed frontmatter KEY=VALUE; repeat for AND."
        ),
        limit: int = typer.Option(
            100,
            "--limit",
            "-n",
            min=1,
            max=1000,
            help="Maximum returned notes, 1-1000 (default 100).",
        ),
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: bool = typer.Option(
            False, "--json", help="Return the full native object."
        ),
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """Discover exact paths before optional bounded typo matches."""
        _run(
            ctx,
            "search_paths",
            locals(),
            ("query", "folder", "filters", "fuzzy", "limit"),
        )
