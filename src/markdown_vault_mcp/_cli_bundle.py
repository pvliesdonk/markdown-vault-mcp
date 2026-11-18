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
    InspectionPolicy,
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
    StoreCorruptError,
    StoreUnavailableError,
)
from markdown_vault_mcp.utils.fs import could_be_regular_file


def _local(
    arguments: dict[str, Any], source: str | None, request: BundleTreeRequest
) -> dict[str, Any]:
    config = replace(
        ProjectConfig.from_env(),
        embedding_provider=None,
        summarize_provider=None,
        summarize_openai_api_key=None,
        summarize_openai_base_url=None,
    )
    settings = to_vault_settings(config)
    policy = InspectionPolicy(
        settings.conventions_file,
        (state,)
        if (state := settings.effective_state_path(config.source_dir)) is not None
        else (),
        settings.effective_embeddings_path(),
        settings.index_path,
    )
    path = config.index_path
    existing = (
        source is None
        and path is not None
        and str(path) != ":memory:"
        and could_be_regular_file(path)
    )
    if not existing:
        config = replace(config, index_path=None)
    try:
        built = _build_reader_vault(
            config, source, not existing, inspection_policy=policy
        )
    except (StoreCorruptError, StoreUnavailableError) as exc:
        if not existing or exc.backend != "sqlite" or exc.location != str(path):
            raise
        config = replace(config, index_path=None)
        existing = False
        built = _build_reader_vault(config, source, True, inspection_policy=policy)
    try:
        if existing:
            with suppress(IndexUnavailableError):
                built.vault.index.use_existing_index()
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
        type_state: Annotated[
            TypeState | None, typer.Option(help="Filter by type presence.")
        ] = None,
        parse_state: Annotated[
            ParseState | None, typer.Option(help="Filter by parsing outcome.")
        ] = None,
        search_state: Annotated[
            SearchState | None, typer.Option(help="Filter by search admission.")
        ] = None,
        entry_limit: Annotated[
            int, typer.Option(min=1, max=1000, help="Maximum returned rows.")
        ] = 100,
        cursor: Annotated[
            str | None, typer.Option(help="Continue from the previous next_cursor.")
        ] = None,
        source_dir: SourceDir = None,
        server: Server = None,
        local: Local = False,
        timeout: Timeout = None,
        json_output: Annotated[
            bool, typer.Option("--json", help="Return the structured report.")
        ] = False,
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
            request = BundleTreeRequest(
                root=root, entry_limit=entry_limit, cursor=cursor
            )
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
                lambda: _local(arguments, source_dir, request),
                settings=settings,
            )
            render_reply("okf-inventory", reply, output_mode(json_output, agent))
        except (ClientError, ConfigurationError, MarkdownMCPError, ValueError) as exc:
            typer.echo(f"ERROR: {terminal_text(str(exc))}", err=True)
            raise typer.Exit(1) from None
