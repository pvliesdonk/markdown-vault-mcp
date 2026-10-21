"""CLI access to the native reader facet, with lazy backend selection."""

from __future__ import annotations

from typing import Any, Literal

import typer

from markdown_vault_mcp._cli_client import ClientError, ClientOptions, resolve_settings
from markdown_vault_mcp._cli_output import render_reply, terminal_text
from markdown_vault_mcp._cli_read import local_read, run_read
from markdown_vault_mcp._read_payloads import ReadPayloadError, parse_filters
from markdown_vault_mcp.exceptions import ConfigurationError, MarkdownMCPError


def _run(
    ctx: typer.Context, tool: str, params: dict[str, Any], names: tuple[str, ...]
) -> None:
    try:
        if params["json_output"] and params["agent"]:
            raise ClientError("--json and --agent cannot be combined.")
        arguments: dict[str, Any] = {
            name: params[name] for name in names if params[name] is not None
        }
        if "filters" in arguments:
            arguments["filters"] = parse_filters(arguments["filters"])
        if params.get("wait_for_pending_writes"):
            arguments["wait_for_pending_writes"] = True
        options = ClientOptions(params["server"], params["local"], params["timeout"])
        settings = resolve_settings(options)
        source = params["source_dir"]
        if source is not None and settings.url is not None:
            raise ClientError(
                "--source-dir conflicts with a remote server; use --local."
            )
        reply = run_read(
            tool,
            arguments,
            options,
            lambda: local_read(tool, arguments, source),
            settings=settings,
        )
        output: Literal["text", "json", "agent"] = (
            "json" if params["json_output"] else "agent" if params["agent"] else "text"
        )
        render_reply(ctx.info_name or tool, reply, output)
    except (ClientError, ReadPayloadError) as exc:
        typer.echo(f"ERROR: {terminal_text(str(exc))}", err=True)
        raise typer.Exit(exc.code) from None
    except (MarkdownMCPError, ConfigurationError) as exc:
        typer.echo(f"ERROR: {terminal_text(str(exc))}", err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        raise typer.Exit(130) from None


def register(app: typer.Typer) -> None:
    """Register note retrieval and exploration commands.

    Args:
        app: Domain CLI receiving the reader commands.
    """

    @app.command("read")
    def read(  # noqa: PLR0913, RUF100 -- flat CLI options match native read controls
        ctx: typer.Context,
        path: str = typer.Argument(..., help="Vault-relative note or attachment path."),
        section: str | None = typer.Option(None, help="Read one note section."),
        revision: str | None = typer.Option(
            None, help="Read a historical note revision."
        ),
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        server: str | None = typer.Option(
            None, help="Complete hosted MCP endpoint URL."
        ),
        local: bool = typer.Option(
            False, help="Override a configured remote endpoint."
        ),
        timeout: float | None = typer.Option(None, help="Remote deadline in seconds."),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Return the original structured payload, including attachment base64.",
        ),
        agent: bool = typer.Option(
            False, help="Return escaped agent records; omit attachment bytes."
        ),
    ) -> None:
        """Read a note, section, revision or allowed attachment."""
        _run(ctx, "read", locals(), ("path", "section", "revision"))

    @app.command("list")
    def documents(  # noqa: PLR0913, RUF100 -- flat CLI options expose native listing predicates
        ctx: typer.Context,
        folder: str | None = typer.Option(
            None, help="Folder subtree; empty means root notes."
        ),
        pattern: str | None = typer.Option(
            None, help="Path glob, such as Journal/*.md."
        ),
        include_attachments: bool = typer.Option(
            False, help="Include allowed attachment metadata."
        ),
        filters: list[str] | None = typer.Option(  # noqa: B008 -- Typer consumes parameter metadata
            None, "--filter", help="Repeat KEY=VALUE predicates (AND)."
        ),
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        server: str | None = typer.Option(
            None, help="Complete hosted MCP endpoint URL."
        ),
        local: bool = typer.Option(
            False, help="Override a configured remote endpoint."
        ),
        timeout: float | None = typer.Option(None, help="Remote deadline in seconds."),
        json_output: bool = typer.Option(
            False, "--json", help="Return structured metadata."
        ),
        agent: bool = typer.Option(False, help="Return escaped agent records."),
        wait_for_pending_writes: bool = typer.Option(
            False, help="Wait for the remote server's pending writes."
        ),
    ) -> None:
        """List note metadata with optional folder, glob and frontmatter filters."""
        _run(
            ctx,
            "list_documents",
            locals(),
            ("folder", "pattern", "include_attachments", "filters"),
        )

    def simple(  # noqa: PLR0913, RUF100 -- shared CLI connection/output options
        ctx: typer.Context,
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        server: str | None = typer.Option(
            None, help="Complete hosted MCP endpoint URL."
        ),
        local: bool = typer.Option(
            False, help="Override a configured remote endpoint."
        ),
        timeout: float | None = typer.Option(None, help="Remote deadline in seconds."),
        json_output: bool = typer.Option(
            False, "--json", help="Return structured values."
        ),
        agent: bool = typer.Option(False, help="Return escaped agent records."),
        wait_for_pending_writes: bool = typer.Option(
            False, help="Wait for the remote server's pending writes."
        ),
    ) -> None:
        """Inspect indexed folders or vault statistics and capabilities."""
        _run(
            ctx, "list_folders" if ctx.info_name == "folders" else "stats", locals(), ()
        )

    app.command("folders", help="List indexed vault folders.")(simple)
    app.command("stats", help="Inspect vault statistics and search capabilities.")(
        simple
    )

    @app.command("tags")
    def tags(  # noqa: PLR0913, RUF100 -- flat CLI options preserve tag selection and connection controls
        ctx: typer.Context,
        field: str = typer.Option(
            "tags", help="Indexed frontmatter field to enumerate."
        ),
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        server: str | None = typer.Option(
            None, help="Complete hosted MCP endpoint URL."
        ),
        local: bool = typer.Option(
            False, help="Override a configured remote endpoint."
        ),
        timeout: float | None = typer.Option(None, help="Remote deadline in seconds."),
        json_output: bool = typer.Option(
            False, "--json", help="Return structured values."
        ),
        agent: bool = typer.Option(False, help="Return escaped agent records."),
        wait_for_pending_writes: bool = typer.Option(
            False, help="Wait for the remote server's pending writes."
        ),
    ) -> None:
        """List values of an indexed frontmatter field."""
        _run(ctx, "list_tags", locals(), ("field",))

    @app.command("recent")
    def recent(  # noqa: PLR0913, RUF100 -- flat CLI options preserve native recent-note controls
        ctx: typer.Context,
        limit: int = typer.Option(
            20, "--limit", "-n", min=1, help="Maximum notes (default 20)."
        ),
        folder: str | None = typer.Option(
            None, help="Folder subtree; empty means root notes."
        ),
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        server: str | None = typer.Option(
            None, help="Complete hosted MCP endpoint URL."
        ),
        local: bool = typer.Option(
            False, help="Override a configured remote endpoint."
        ),
        timeout: float | None = typer.Option(None, help="Remote deadline in seconds."),
        json_output: bool = typer.Option(
            False, "--json", help="Return structured metadata."
        ),
        agent: bool = typer.Option(False, help="Return escaped agent records."),
        wait_for_pending_writes: bool = typer.Option(
            False, help="Wait for the remote server's pending writes."
        ),
    ) -> None:
        """List recently modified notes."""
        _run(ctx, "get_recent", locals(), ("limit", "folder"))

    @app.command("toc")
    def toc(  # noqa: PLR0913, RUF100 -- flat CLI options preserve note and subtree outline controls
        ctx: typer.Context,
        path: str = typer.Argument(..., help="Note path or folder prefix."),
        max_level: int | None = typer.Option(
            None, min=1, help="Maximum heading level."
        ),
        max_notes: int = typer.Option(
            200, min=1, help="Maximum subtree notes (default 200)."
        ),
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        server: str | None = typer.Option(
            None, help="Complete hosted MCP endpoint URL."
        ),
        local: bool = typer.Option(
            False, help="Override a configured remote endpoint."
        ),
        timeout: float | None = typer.Option(None, help="Remote deadline in seconds."),
        json_output: bool = typer.Option(
            False, "--json", help="Return structured headings and truncation."
        ),
        agent: bool = typer.Option(False, help="Return escaped agent records."),
        wait_for_pending_writes: bool = typer.Option(
            False, help="Wait for the remote server's pending writes."
        ),
    ) -> None:
        """Inspect note headings or a bounded folder outline."""
        _run(ctx, "get_toc", locals(), ("path", "max_level", "max_notes"))

    @app.command("similar")
    def similar(  # noqa: PLR0913, RUF100 -- flat CLI options preserve native stored-vector scoping
        ctx: typer.Context,
        path: str = typer.Argument(..., help="Seed note path."),
        limit: int = typer.Option(
            10, "--limit", "-n", min=1, help="Maximum notes (default 10)."
        ),
        folder: str | None = typer.Option(
            None, help="Folder subtree; empty means root notes."
        ),
        filters: list[str] | None = typer.Option(  # noqa: B008 -- Typer consumes parameter metadata
            None, "--filter", help="Repeat KEY=VALUE predicates (AND)."
        ),
        chunks_per_file: int | None = typer.Option(
            None, min=1, help="Maximum sections per note."
        ),
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        server: str | None = typer.Option(
            None, help="Complete hosted MCP endpoint URL."
        ),
        local: bool = typer.Option(
            False, help="Override a configured remote endpoint."
        ),
        timeout: float | None = typer.Option(None, help="Remote deadline in seconds."),
        json_output: bool = typer.Option(
            False, "--json", help="Return structured neighbors and sections."
        ),
        agent: bool = typer.Option(False, help="Return escaped agent records."),
        wait_for_pending_writes: bool = typer.Option(
            False, help="Wait for the remote server's pending writes."
        ),
    ) -> None:
        """Find neighbors using existing stored vectors; never build embeddings."""
        _run(
            ctx,
            "get_similar",
            locals(),
            ("path", "limit", "folder", "filters", "chunks_per_file"),
        )

    @app.command("context")
    def context(  # noqa: PLR0913, RUF100 -- flat CLI options preserve native dossier limits
        ctx: typer.Context,
        path: str = typer.Argument(..., help="Note path."),
        similar_limit: int = typer.Option(
            5, min=0, help="Similar notes; 0 skips them (default 5)."
        ),
        link_limit: int = typer.Option(
            10, min=0, help="Backlink and outlink limits (default 10)."
        ),
        source_dir: str | None = typer.Option(None, help="Local vault directory."),
        server: str | None = typer.Option(
            None, help="Complete hosted MCP endpoint URL."
        ),
        local: bool = typer.Option(
            False, help="Override a configured remote endpoint."
        ),
        timeout: float | None = typer.Option(None, help="Remote deadline in seconds."),
        json_output: bool = typer.Option(
            False, "--json", help="Return the structured context dossier."
        ),
        agent: bool = typer.Option(False, help="Return escaped agent records."),
        wait_for_pending_writes: bool = typer.Option(
            False, help="Wait for the remote server's pending writes."
        ),
    ) -> None:
        """Collect note links, neighbors, folder peers and conventions."""
        _run(ctx, "get_context", locals(), ("path", "similar_limit", "link_limit"))
