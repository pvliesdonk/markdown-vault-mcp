"""Thin hosted mutation adapters; server tools retain all write policies."""

from __future__ import annotations

import json
import math
from enum import StrEnum
from typing import Annotated, Any

import typer

from markdown_vault_mcp import _cli_remote
from markdown_vault_mcp._cli_client import ClientError, ClientOptions, resolve_settings
from markdown_vault_mcp._cli_options import Agent, Server, Timeout  # noqa: TC001 -- Typer evaluates aliases at registration
from markdown_vault_mcp._cli_output import (
    command_label,
    output_mode,
    render_reply,
    terminal_text,
)
from markdown_vault_mcp._read_payloads import ReadPayloadError

JsonOutput = Annotated[
    bool, typer.Option("--json", help="Return the native structured result.")
]
IfMatch = Annotated[
    str | None, typer.Option(help="Expected note etag; stale values are refused.")
]
Frontmatter = Annotated[str | None, typer.Option(help="Frontmatter as a JSON object.")]


class _Direction(StrEnum):
    pull = "pull"
    push = "push"
    both = "both"


def _arguments(
    tool: str, params: dict[str, Any], names: tuple[str, ...]
) -> dict[str, Any]:
    arguments = {
        name: params[name]
        for name in names
        if params[name] is not None and params[name] is not False
    }
    if params.get("if_match") == "":
        raise ClientError("--if-match must be nonempty.")
    if params.get("create") and params.get("if_match") is not None:
        raise ClientError("--create and --if-match cannot be combined.")
    if "frontmatter" in arguments:
        try:
            arguments["frontmatter"] = json.loads(arguments["frontmatter"])
        except ValueError:
            raise ClientError("--frontmatter must be a JSON object.") from None
        if not isinstance(arguments["frontmatter"], dict):
            raise ClientError("--frontmatter must be a JSON object.")
    if tool == "edit":
        _edit_selector(arguments)
    if "download_timeout" in arguments:
        seconds = arguments.pop("download_timeout")
        if not math.isfinite(seconds) or seconds <= 0:
            raise ClientError("--download-timeout must be positive and finite.")
        arguments["timeout_s"] = seconds
    if tool == "rename":
        arguments["update_links"] = True
    return arguments


def _edit_selector(arguments: dict[str, Any]) -> None:
    start, end = arguments.get("line_start"), arguments.get("line_end")
    if (start is None) != (end is None):
        raise ClientError("Supply both --line-start and --line-end.")
    if start is not None and (start <= 0 or end < start):
        raise ClientError("Line bounds must be positive and ordered.")
    if start is None and "old_text" not in arguments:
        raise ClientError("Supply --old-text or a complete line range.")


def _run(
    ctx: typer.Context, tool: str, params: dict[str, Any], names: tuple[str, ...]
) -> None:
    try:
        if params["json_output"] and params["agent"]:
            raise ClientError("--json and --agent cannot be combined.")
        arguments = _arguments(tool, params, names)
        settings = resolve_settings(
            ClientOptions(server=params["server"], timeout=params["timeout"])
        )
        if settings.url is None:
            raise ClientError(
                "Hosted writes require --server or a configured remote endpoint."
            )
        read_path = None
        if (
            tool in {"write", "edit"}
            and "if_match" not in arguments
            and not params.get("create")
        ):
            read_path = arguments["path"]
        reply = _cli_remote.call_write(
            settings, tool, arguments, read_etag_from=read_path
        )
        render_reply(
            command_label(ctx),
            reply,
            output_mode(params["json_output"], params["agent"]),
        )
    except (ClientError, ReadPayloadError) as exc:
        typer.echo(f"ERROR: {terminal_text(str(exc))}", err=True)
        raise typer.Exit(exc.code) from None
    except KeyboardInterrupt:
        raise typer.Exit(130) from None


def register_write_commands(app: typer.Typer) -> typer.Typer:  # noqa: C901, RUF100 -- twelve declarative CLI leaves share one registration seam
    """Register hosted writers independently of the reader and local admin facets.

    Args:
        app: CLI receiving the hosted command groups.

    Returns:
        Unmounted index group for composition with the shipped local callback.
    """
    note = typer.Typer(help="Change hosted notes.", no_args_is_help=True)
    folder = typer.Typer(help="Change hosted folders.", no_args_is_help=True)
    okf = typer.Typer(
        help="Maintain hosted Open Knowledge Format files.", no_args_is_help=True
    )
    generate = typer.Typer(no_args_is_help=True)
    seed = typer.Typer(no_args_is_help=True)
    git = typer.Typer(help="Synchronize hosted Git storage.", no_args_is_help=True)
    index = typer.Typer(help="Build a local index or request hosted reindexing.")

    @note.command("write")
    def write(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        path: str,
        content: str,
        create: bool = typer.Option(
            False,
            help="Skip the etag read for native creation. Servers allowing blind overwrites can replace an existing note.",
        ),
        if_match: IfMatch = None,
        frontmatter: Frontmatter = None,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Replace a note using a live etag, or request native creation."""
        _run(ctx, "write", locals(), ("path", "content", "frontmatter", "if_match"))

    @note.command("edit")
    def edit(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        path: str,
        new_text: str = typer.Option(
            ..., help="Literal replacement; empty deletes the selection."
        ),
        old_text: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        if_match: IfMatch = None,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Edit text, an inclusive one-based line range, or text within that range."""
        _run(
            ctx,
            "edit",
            locals(),
            ("path", "new_text", "old_text", "line_start", "line_end", "if_match"),
        )

    @note.command("append")
    def append(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        path: str,
        content: str,
        if_match: IfMatch = None,
        create_if_missing: bool = False,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Append literal content to a hosted note."""
        _run(
            ctx,
            "append",
            locals(),
            ("path", "content", "if_match", "create_if_missing"),
        )

    @note.command("delete")
    def delete(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        path: str,
        if_match: IfMatch = None,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Delete a hosted note, optionally checking its etag."""
        _run(ctx, "delete", locals(), ("path", "if_match"))

    @note.command("rename")
    def rename(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        old_path: str,
        new_path: str,
        if_match: IfMatch = None,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Rename a hosted note and update links to it."""
        _run(ctx, "rename", locals(), ("old_path", "new_path", "if_match"))

    @folder.command("move")
    def move(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        old_dir: str,
        new_dir: str,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Move a hosted folder through the server's native policy."""
        _run(ctx, "move_folder", locals(), ("old_dir", "new_dir"))

    @app.command("fetch")
    def fetch(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        url: str,
        path: str,
        frontmatter: Frontmatter = None,
        if_match: IfMatch = None,
        download_timeout: float | None = typer.Option(
            None, help="Server download limit in seconds; separate from --timeout."
        ),
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Fetch a URL on the server into a hosted note."""
        _run(
            ctx,
            "fetch",
            locals(),
            ("url", "path", "frontmatter", "if_match", "download_timeout"),
        )

    def okf_file(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        folder: str | None = None,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Generate the hosted index or seed its changelog."""
        tool = "okf_generate_index" if ctx.info_name == "index" else "okf_seed_log"
        _run(ctx, tool, locals(), ("folder",))

    generate.command("index")(okf_file)
    seed.command("log")(okf_file)
    okf.add_typer(generate, name="generate")
    okf.add_typer(seed, name="seed")

    @okf.command("verify")
    def verify(
        path: str,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Refuse review attestation; use a client supporting human elicitation."""
        del path, server, timeout, json_output, agent
        typer.echo(
            "This CLI cannot ask for human review confirmation; use an MCP client that supports elicitation. Nothing was written.",
            err=True,
        )
        raise typer.Exit(1)

    @git.command("sync")
    def sync(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        direction: _Direction | None = None,
        dry_run: bool = False,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Synchronize the hosted Git repository."""
        _run(ctx, "git_sync", locals(), ("direction", "dry_run"))

    @index.command("reindex")
    def reindex(  # noqa: PLR0913, RUF100 -- flat native CLI controls and connection options
        ctx: typer.Context,
        force: bool = False,
        server: Server = None,
        timeout: Timeout = None,
        json_output: JsonOutput = False,
        agent: Agent = False,
    ) -> None:
        """Request hosted reindexing; a working result is accepted work."""
        _run(ctx, "reindex", locals(), ("force",))

    for name, group in (("note", note), ("folder", folder), ("okf", okf), ("git", git)):
        app.add_typer(group, name=name)
    return index
