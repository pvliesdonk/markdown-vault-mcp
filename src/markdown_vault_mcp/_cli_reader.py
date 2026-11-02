"""The read-only commands, registered as one unit a CLI mounts whole (#2005).

Nothing registered here writes the index, the stored vectors or the vault; the
commands that do live in the ``admin`` group (:mod:`._cli_admin`). The
``markdown-vault-mcp`` app mounts these beside ``serve`` and ``admin``, and a
client-only script can mount them alone (#2000). The shared local-vault helpers
are still imported lazily from :mod:`markdown_vault_mcp.cli`.
"""

from __future__ import annotations

import typer

from markdown_vault_mcp.config import _ENV_PREFIX


def register_read_commands(app: typer.Typer) -> None:
    """Register every command that only reads the vault.

    Args:
        app: The Typer app receiving ``search`` and the reader, graph,
            query and path commands.
    """
    from markdown_vault_mcp._cli_discovery import register as register_discovery
    from markdown_vault_mcp._cli_graph import register as register_graph
    from markdown_vault_mcp._cli_paths import register as register_paths
    from markdown_vault_mcp._cli_query import register as register_queries

    app.command()(search)
    register_discovery(app)
    links = typer.Typer(
        help="Follow and audit the links between notes.", no_args_is_help=True
    )
    register_graph(links)
    app.add_typer(links, name="links")
    query = typer.Typer(
        help="Exact queries over every matching note, without ranking.",
        no_args_is_help=True,
    )
    register_queries(query)
    register_paths(query)
    app.add_typer(query, name="query")


def search(  # noqa: PLR0913, RUF100 -- one CLI option per search control
    query: str = typer.Argument(..., help="Search query."),
    source_dir: str | None = typer.Option(
        None, help=f"Path to markdown vault (overrides ${_ENV_PREFIX}_SOURCE_DIR)."
    ),
    limit: int = typer.Option(10, "-n", "--limit", help="Max results (default: 10)."),
    mode: str = typer.Option(
        "keyword",
        "-m",
        "--mode",
        help="keyword / semantic / hybrid / auto (default: keyword).",
    ),
    folder: str | None = typer.Option(None, help="Restrict to folder."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
    agent: bool = typer.Option(
        False, "--agent", help="Output an agent header and escaped JSON records."
    ),
    filters: list[str] | None = typer.Option(  # noqa: B008 -- Typer consumes parameter metadata
        None, "--filter", help="Repeat KEY=VALUE frontmatter filters (AND)."
    ),
    chunks_per_file: int | None = typer.Option(
        None, min=1, help="Maximum matching sections per note."
    ),
    snippet_words: int | None = typer.Option(
        None, min=0, help="Snippet width in words; 0 returns full indexed sections."
    ),
    wait_for_pending_writes: bool = typer.Option(
        False, help="Wait for the remote server's pending writes."
    ),
    server: str | None = typer.Option(None, help="Complete hosted MCP endpoint URL."),
    local: bool = typer.Option(
        False, "--local", help="Use the local vault despite a client profile."
    ),
    timeout: float | None = typer.Option(
        None, help="Remote operation deadline in seconds (default 30)."
    ),
) -> None:
    """Search the local vault or a hosted MCP server."""
    from markdown_vault_mcp._cli_client import (
        ClientError,
        ClientOptions,
        resolve_settings,
    )
    from markdown_vault_mcp._cli_output import output_mode, render_reply
    from markdown_vault_mcp._cli_read import run_read
    from markdown_vault_mcp._http_logging import quiet_http_loggers
    from markdown_vault_mcp._read_payloads import (
        ReadPayloadError,
        SearchOptions,
        parse_filters,
    )
    from markdown_vault_mcp.cli import _local_search
    from markdown_vault_mcp.exceptions import InvalidRequestError

    quiet_http_loggers()
    try:
        if json_output and agent:
            raise ClientError("--json and --agent cannot be combined.")
        controls = SearchOptions(
            mode,
            parse_filters(filters or []),
            chunks_per_file,
            snippet_words,
            wait_for_pending_writes,
        )
        options = ClientOptions(server, local, timeout)
        settings = resolve_settings(options)
        if source_dir is not None and settings.url is not None:
            raise ClientError(
                "--source-dir conflicts with a remote server; use --local."
            )
        arguments = controls.arguments(query, limit, folder)
        reply = run_read(
            "search",
            arguments,
            options,
            lambda: _local_search(query, source_dir, arguments),
            settings=settings,
        )
        reply.meta["requested_mode"] = mode
        render_reply("search", reply, output_mode(json_output, agent))
    except ClientError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.code) from None
    except InvalidRequestError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from None
    except ReadPayloadError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.code) from None
    except KeyboardInterrupt:
        raise typer.Exit(130) from None
