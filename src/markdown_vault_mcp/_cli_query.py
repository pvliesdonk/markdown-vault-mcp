"""Thin command adapters for exact native note-query populations."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import typer

from markdown_vault_mcp._cli_client import ClientError, ClientOptions, resolve_settings
from markdown_vault_mcp._cli_errors import diagnostic, render_error
from markdown_vault_mcp._cli_options import (
    Agent,
    Folder,
    Local,
    Server,
    SourceDir,
    Timeout,
    WaitForPendingWrites,
)
from markdown_vault_mcp._cli_output import (
    command_label,
    output_mode,
    render_reply,
)
from markdown_vault_mcp._cli_read import local_read, run_read
from markdown_vault_mcp._read_payloads import (
    ReadPayloadError,
    parse_filters,
    query_request,
    validate_query_payload,
)
from markdown_vault_mcp.exceptions import ConfigurationError, MarkdownMCPError

_Json = Annotated[
    bool, typer.Option("--json", help="Return the original structured payload.")
]
_Filters = Annotated[
    list[str] | None,
    typer.Option("--filter", help="Repeat unique KEY=VALUE predicates (AND)."),
]
_Pattern = Annotated[str | None, typer.Option(help="Case-sensitive path glob.")]
_Path = Annotated[str | None, typer.Option(help="Case-sensitive exact note path.")]
_After = Annotated[
    str | None,
    typer.Option(help="Inclusive modification bound, RFC3339 with timezone."),
]
_Before = Annotated[
    str | None,
    typer.Option(help="Exclusive modification bound, RFC3339 with timezone."),
]
_Offset = Annotated[int, typer.Option(min=0, help="Live-view note offset.")]
_Limit = Annotated[int, typer.Option(min=1, max=1000, help="Maximum returned notes.")]
_Chunks = Annotated[
    int, typer.Option(min=1, help="Maximum matching sections per note.")
]
_Words = Annotated[
    int, typer.Option(min=0, help="Excerpt words; zero returns the full section.")
]
_Scope = Annotated[str, typer.Option(help="Lexical population: all, metadata or body.")]
_Mode = Annotated[
    str, typer.Option(help="Exact populations support keyword mode only.")
]


def _arguments(
    command: str | None, tool: str, params: dict[str, Any]
) -> dict[str, Any]:
    if params["json_output"] and params["agent"]:
        raise ClientError(
            "--json and --agent cannot be combined.",
            reason="conflicting_options",
            hint="Choose either --json or --agent.",
        )
    if params.get("mode", "keyword") != "keyword":
        raise ClientError(
            "Exact queries require --mode keyword; use search for ranked semantic or hybrid results.",
            reason="invalid_request",
            hint="Use --mode keyword for exact queries, or search for ranked semantic or hybrid results.",
        )
    names = (
        "query",
        "scope",
        "path",
        "folder",
        "pattern",
        "modified_after",
        "modified_before",
        "offset",
        "limit",
        "chunks_per_file",
        "snippet_words",
        "by",
        "field",
    )
    arguments = {name: params[name] for name in names if params.get(name) is not None}
    arguments["filters"] = parse_filters(params["filters"] or [])
    if command in {"metadata", "body", "note"}:
        arguments["scope"] = "metadata" if command == "metadata" else "body"
    if command == "note":
        arguments.update(offset=0, limit=1)
    query_request(tool, arguments)
    if params["wait_for_pending_writes"]:
        arguments["wait_for_pending_writes"] = True
    return arguments


def _run(ctx: typer.Context, tool: str, params: dict[str, Any]) -> None:
    try:
        arguments = _arguments(ctx.info_name, tool, params)
        options = ClientOptions(params["server"], params["local"], params["timeout"])
        settings = resolve_settings(options)
        source = params["source_dir"]
        if source is not None and settings.url is not None:
            raise ClientError(
                "--source-dir conflicts with a remote server; use --local.",
                reason="conflicting_options",
                hint="Use --local with --source-dir, or omit --source-dir for a remote server.",
            )
        reply = run_read(
            tool,
            arguments,
            options,
            lambda: local_read(tool, arguments, source),
            settings=settings,
        )
        validate_query_payload(
            tool,
            reply.data,
            remote_meta=reply.meta if settings.url is not None else None,
        )
        output: Literal["text", "json", "agent"] = output_mode(
            params["json_output"], params["agent"]
        )
        render_reply(command_label(ctx) or tool, reply, output)
    except (ClientError, ReadPayloadError, MarkdownMCPError, ConfigurationError) as exc:
        error = diagnostic(exc)
        render_error(
            command_label(ctx) or tool,
            error,
            output_mode(params["json_output"], params["agent"]),
        )
        raise typer.Exit(error.code) from None
    except KeyboardInterrupt:
        raise typer.Exit(130) from None
    except Exception:
        # Unexpected backend/serialization faults are server failures, never input errors.
        render_error(
            command_label(ctx) or tool,
            ClientError(
                "Query failed; check the vault index and server logs.",
                1,
                kind="internal",
                reason="query_failed",
                hint="Check the vault index and server logs.",
            ),
            output_mode(params["json_output"], params["agent"]),
        )
        raise typer.Exit(1) from None


def register(app: typer.Typer) -> None:
    """Register exact query, count and aggregation commands.

    Args:
        app: Domain CLI receiving the commands.
    """

    @app.command("metadata")
    def search_metadata(  # noqa: PLR0913, RUF100 -- flat CLI options expose native query controls
        ctx: typer.Context,
        query: str = typer.Argument(
            "", help="Metadata keyword expression; blank enumerates notes."
        ),
        path: _Path = None,
        offset: _Offset = 0,
        limit: _Limit = 20,
        folder: Folder = None,
        pattern: _Pattern = None,
        filters: _Filters = None,
        modified_after: _After = None,
        modified_before: _Before = None,
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """Query indexed note metadata without body excerpts."""
        _run(ctx, "query_documents", locals())

    @app.command("body")
    def search_body(  # noqa: PLR0913, RUF100 -- flat CLI options expose native query controls
        ctx: typer.Context,
        query: str = typer.Argument(..., help="Body keyword expression."),
        path: _Path = None,
        offset: _Offset = 0,
        limit: _Limit = 20,
        chunks_per_file: _Chunks = 2,
        snippet_words: _Words = 200,
        folder: Folder = None,
        pattern: _Pattern = None,
        filters: _Filters = None,
        modified_after: _After = None,
        modified_before: _Before = None,
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """Query note bodies with bounded matching sections."""
        _run(ctx, "query_documents", locals())

    @app.command("note")
    def search_in(  # noqa: PLR0913, RUF100 -- flat CLI options expose native query controls
        ctx: typer.Context,
        path: str = typer.Argument(..., help="Case-sensitive exact note path."),
        query: str = typer.Argument(..., help="Body keyword expression."),
        chunks_per_file: _Chunks = 2,
        snippet_words: _Words = 200,
        folder: Folder = None,
        pattern: _Pattern = None,
        filters: _Filters = None,
        modified_after: _After = None,
        modified_before: _Before = None,
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """Query body sections within exactly one note."""
        _run(ctx, "query_documents", locals())

    @app.command("count")
    def count(  # noqa: PLR0913, RUF100 -- flat CLI options expose native query controls
        ctx: typer.Context,
        query: str = typer.Argument(
            "", help="Keyword expression; blank counts all matching notes."
        ),
        scope: _Scope = "all",
        mode: _Mode = "keyword",
        path: _Path = None,
        folder: Folder = None,
        pattern: _Pattern = None,
        filters: _Filters = None,
        modified_after: _After = None,
        modified_before: _Before = None,
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """Count distinct notes across the complete matching population."""
        _run(ctx, "count_documents", locals())

    @app.command("aggregate")
    def aggregate(  # noqa: PLR0913, RUF100 -- flat CLI options expose native query controls
        ctx: typer.Context,
        query: str = typer.Argument(
            "", help="Keyword expression; blank includes all matching notes."
        ),
        by: str = typer.Option(..., help="Group by folder, tag or month."),
        field: str = typer.Option("tags", help="Indexed field for tag grouping."),
        limit: Annotated[
            int,
            typer.Option(
                min=1,
                max=1000,
                help="Maximum output buckets; never caps matching notes.",
            ),
        ] = 50,
        scope: _Scope = "all",
        mode: _Mode = "keyword",
        path: _Path = None,
        folder: Folder = None,
        pattern: _Pattern = None,
        filters: _Filters = None,
        modified_after: _After = None,
        modified_before: _Before = None,
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: _Json = False,
        agent: Agent = False,
        wait_for_pending_writes: WaitForPendingWrites = False,
    ) -> None:
        """Group the complete matching note population into bounded buckets."""
        _run(ctx, "aggregate_documents", locals())
