"""CLI adapter for the read-only bundle inventory."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import asdict, replace
from typing import Annotated, Any

import typer

from markdown_vault_mcp._cli_client import ClientError, ClientOptions, resolve_settings
from markdown_vault_mcp._cli_options import Agent, Local, Server, SourceDir, Timeout  # noqa: TC001 -- Typer resolves annotations at runtime
from markdown_vault_mcp._cli_output import output_mode, render_reply, terminal_text
from markdown_vault_mcp._cli_read import _build_reader_vault, run_read
from markdown_vault_mcp.bundle_inventory import (
    BundleTreeRequest,
    ParseState,
    SearchState,
    TypeState,
)
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._assembly import to_vault_settings
from markdown_vault_mcp.exceptions import (
    ConfigurationError,
    IndexUnavailableError,
    MarkdownMCPError,
)


def _local(arguments: dict[str, Any], source: str | None) -> dict[str, Any]:
    config = replace(
        ProjectConfig.from_env(),
        embedding_provider=None,
        summarize_provider=None,
        summarize_openai_api_key=None,
        summarize_openai_base_url=None,
    )
    path = config.index_path
    existing = path is not None and str(path) != ":memory:" and path.is_file()
    if not existing:
        state_path = to_vault_settings(config).effective_state_path(config.source_dir)
        config = replace(config, index_path=None, state_path=state_path)
    built = _build_reader_vault(config, source, False)
    try:
        if existing:
            with suppress(IndexUnavailableError):
                built.vault.index.use_existing_index()
        request = BundleTreeRequest(
            root=arguments["root"],
            entry_limit=arguments["entry_limit"],
            cursor=arguments["cursor"],
        )
        return asdict(
            built.vault.reader.okf_inventory(
                request,
                type_state=arguments["type_state"],
                parse_state=arguments["parse_state"],
                search_state=arguments["search_state"],
            )
        )
    finally:
        built.close()


def register(app: typer.Typer) -> None:
    """Register the bundle inventory command."""

    @app.command("okf-inventory")
    def okf_inventory(  # noqa: PLR0913, RUF100 -- CLI exposes independent tool and connection options
        root: str = typer.Argument(
            ..., help="Vault-relative bundle root; empty for vault root."
        ),
        type_state: TypeState | None = None,
        parse_state: ParseState | None = None,
        search_state: SearchState | None = None,
        entry_limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
        cursor: str | None = None,
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
        agent: Agent = False,
    ) -> None:
        """Inspect concept candidates, including notes outside search."""
        try:
            if json_output and agent:
                raise ClientError("--json and --agent cannot be combined.")
            arguments = {
                "root": root,
                "type_state": type_state,
                "parse_state": parse_state,
                "search_state": search_state,
                "entry_limit": entry_limit,
                "cursor": cursor,
            }
            options = ClientOptions(server, local, timeout)
            settings = resolve_settings(options)
            if source_dir is not None and settings.url is not None:
                raise ClientError(
                    "--source-dir conflicts with a remote server; use --local."
                )
            reply = run_read(
                "okf_inventory",
                arguments,
                options,
                lambda: _local(arguments, source_dir),
                settings=settings,
            )
            render_reply("okf-inventory", reply, output_mode(json_output, agent))
        except (ClientError, ConfigurationError, MarkdownMCPError, ValueError) as exc:
            typer.echo(f"ERROR: {terminal_text(str(exc))}", err=True)
            raise typer.Exit(1) from None
