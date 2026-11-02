"""CLI access to native link-graph queries through the shared read runner."""

from __future__ import annotations

from typing import Annotated

import typer

from markdown_vault_mcp._cli_discovery import _run
from markdown_vault_mcp._cli_options import (
    Agent,
    Local,
    Server,
    SourceDir,
    Timeout,
    WaitForPendingWrites,
)

_Json = Annotated[bool, typer.Option("--json", help="Return native structured values.")]


def register(app: typer.Typer) -> None:
    """Register the six native link-graph commands.

    Args:
        app: Domain CLI receiving the graph commands.
    """

    def links(  # noqa: PLR0913, RUF100 -- native predicates plus shared CLI connection/output options
        ctx: typer.Context,
        path: str = typer.Argument(..., help="Exact case-sensitive note path."),
        limit: int | None = typer.Option(
            None, "--limit", "-n", help="Maximum links; omit for all."
        ),
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """List links to or from an indexed note."""
        tool = "get_backlinks" if ctx.info_name == "backlinks" else "get_outlinks"
        _run(ctx, tool, locals(), ("path", "limit"))

    app.command("backlinks", help="List notes linking to an exact note path.")(links)
    app.command("outlinks", help="List a note's links, including missing targets.")(
        links
    )

    @app.command("broken-links")
    def broken_links(  # noqa: PLR0913, RUF100 -- native folder plus shared CLI connection/output options
        ctx: typer.Context,
        folder: str | None = typer.Option(
            None, help="Source folder subtree; empty means root notes."
        ),
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """List links whose note targets are missing."""
        _run(ctx, "get_broken_links", locals(), ("folder",))

    @app.command("orphans")
    def orphans(  # noqa: PLR0913, RUF100 -- shared CLI connection/output options
        ctx: typer.Context,
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """List all notes without inbound or outbound links."""
        _run(ctx, "get_orphan_notes", locals(), ())

    @app.command("hubs")
    def hubs(  # noqa: PLR0913, RUF100 -- native limit plus shared CLI connection/output options
        ctx: typer.Context,
        limit: int = typer.Option(
            10, "--limit", "-n", help="Maximum notes, ranked by backlink count."
        ),
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """List the notes with the most inbound links."""
        _run(ctx, "get_most_linked", locals(), ("limit",))

    @app.command("connection")
    def connection(  # noqa: PLR0913, RUF100 -- exact endpoints/depth plus shared CLI connection/output options
        ctx: typer.Context,
        source: str = typer.Argument(
            ..., help="Exact case-sensitive starting note path."
        ),
        target: str = typer.Argument(
            ..., help="Exact case-sensitive destination note path."
        ),
        max_depth: int = typer.Option(
            10, help="Maximum hops; native traversal clamps to 1-10."
        ),
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """Find the shortest undirected link path between two notes."""
        _run(ctx, "get_connection_path", locals(), ("source", "target", "max_depth"))
