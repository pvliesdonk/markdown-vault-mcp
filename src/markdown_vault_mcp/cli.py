"""Command-line interface for Markdown Vault MCP."""

from __future__ import annotations

from typing import Literal

import typer
from fastmcp_pvl_core import (
    ConfigurationError,
    build_event_store,
    configure_logging_from_env,
    maybe_start_debugpy,
    normalise_http_path,
    run_http,
)

from markdown_vault_mcp.config import _ENV_PREFIX, ProjectConfig

app = typer.Typer(
    name="markdown-vault-mcp",
    help="Generic markdown vault MCP with hybrid search",
    no_args_is_help=True,
    add_completion=False,
)

Transport = Literal["stdio", "http", "sse"]


@app.callback()
def _root(
    verbose: bool = typer.Option(
        False, "-v", "--verbose", help="Enable debug logging."
    ),
) -> None:
    """Root callback — bootstraps logging for every subcommand.

    pvl-core owns the root logger.  ``configure_logging_from_env`` installs
    the one console handler chain every logger in the process renders
    through — ``markdown_vault_mcp.*``, ``fastmcp.*`` and uvicorn alike —
    picks Rich or JSON from ``MARKDOWN_VAULT_MCP_LOG_FORMAT`` (unset: Rich on
    a terminal, JSON anywhere else), resolves the level from ``-v`` or
    ``MARKDOWN_VAULT_MCP_LOG_LEVEL``, and governs the noisy third-party
    loggers (``httpx``, ``httpcore``, the MCP SDK request line) itself.
    Nothing to attach or quiet here: a second root handler would render
    every line twice.  The call is idempotent, so ``make_server()``
    repeating it in the same process is safe.
    """
    configure_logging_from_env(_ENV_PREFIX, verbose=verbose)


@app.command()
def serve(
    transport: Transport = typer.Option(
        "stdio", help="MCP transport (stdio / http / sse)."
    ),
    host: str | None = typer.Option(
        None, help=f"Bind host (http only; default: ${_ENV_PREFIX}_HOST or 127.0.0.1)."
    ),
    port: int | None = typer.Option(
        None, help=f"Bind port (http only; default: ${_ENV_PREFIX}_PORT or 8000)."
    ),
    http_path: str | None = typer.Option(
        None,
        "--http-path",
        "--path",
        help=(f"Mount path (http only, default: ${_ENV_PREFIX}_HTTP_PATH or /mcp)."),
    ),
) -> None:
    """Run the MCP server."""
    import os

    from markdown_vault_mcp.server import make_server

    # Optional remote-debugger listener — placed in ``serve`` (not the
    # typer root callback) so non-server commands like ``--help``,
    # ``--version``, or future ``dump-config``-style subcommands are
    # never blocked by ``MARKDOWN_VAULT_MCP_DEBUG_WAIT=true``.  No-op
    # unless ``MARKDOWN_VAULT_MCP_DEBUG_PORT`` is set; ``debugpy`` is only
    # present when the image was built with ``--build-arg DEBUG=true``
    # (a missing import logs a WARNING and continues).  ``_root`` has
    # already installed pvl-core's root handler chain by the time ``serve``
    # runs, so the helper's INFO/WARNING logs render through it rather
    # than Python's lastResort.
    maybe_start_debugpy(_ENV_PREFIX)

    try:
        config = ProjectConfig.from_env()
        # Resolved once, ahead of ``make_server``: the health routes it
        # registers derive their prefix from the mount path, so the value
        # handed to ``http_app(path=...)`` below and the one the server saw
        # must be the same object, not two reads that could drift.
        path = normalise_http_path(
            http_path or os.environ.get(f"{_ENV_PREFIX}_HTTP_PATH")
        )
        server = make_server(transport=transport, config=config, http_path=path)

        if transport == "http":
            # build_event_store is one of the ConfigurationError-raising
            # builders too (a malformed MARKDOWN_VAULT_MCP_EVENT_STORE_URL),
            # so it stays inside this try alongside make_server — one
            # actionable stderr line for every operator-input mistake in
            # this branch, not just the ones make_server surfaces.
            event_store = build_event_store(_ENV_PREFIX, config.server)
            # pvl-core runs uvicorn.  It pins ``lifespan="on"`` (FastMCP's
            # startup/shutdown hooks run through the ASGI lifespan protocol),
            # ``log_config=None`` (uvicorn must not reinstall its own handlers
            # over the root chain ``_root`` set up) and the SIGTERM drain
            # window from ``MARKDOWN_VAULT_MCP_SHUTDOWN_GRACE_S`` (default
            # 3s, so containers stop cleanly).  ``None`` for host or port
            # means "not given on the command line"; ``run_http`` then reads
            # ``config.server``.
            run_http(
                server.http_app(path=path, event_store=event_store),
                config=config.server,
                host=host,
                port=port,
            )
        else:
            server.run(transport=transport)
    except ConfigurationError as exc:
        # A malformed or missing operator value is one actionable line on
        # stderr, not Typer's Rich traceback: the message already names the
        # variable and the problem (#616). stderr keeps stdout clean for the
        # stdio transport.
        typer.echo(f"ERROR: configuration error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


# DOMAIN-COMMANDS-START — add domain @app.command()s (and their helpers) below; kept across copier update
# Domain CLI subcommands live here so the rest of this file stays byte-identical
# to the template and applies cleanly on copier update. Use function-local
# imports for domain modules (as ``serve`` does) to keep the top-level import
# surface template-owned. Module-level ``TYPE_CHECKING`` guards are fine — they
# are erased at runtime.

from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from markdown_vault_mcp.vault import Vault


def _build_vault(source_dir: str | None = None, index_path: str | None = None) -> Vault:
    """Build a synchronous Vault from env vars + optional CLI overrides.

    Uses the same settings-first ``to_vault_settings`` / ``to_vault_instances``
    assembly as the server path (#1158), but constructs a bare Vault (no
    background tasks, file watcher, or index writer — those belong to the
    server's lifespan).

    Args:
        source_dir: Overrides ``{PREFIX}_SOURCE_DIR`` when given (set into the
            environment before ``ProjectConfig.from_env()`` reads it).
        index_path: Overrides the resolved SQLite index path when given.

    Returns:
        A constructed :class:`~markdown_vault_mcp.vault.Vault` (index not built).
    """
    import dataclasses
    import os
    from pathlib import Path

    from markdown_vault_mcp.config_sections._assembly import (
        to_vault_instances,
        to_vault_settings,
    )
    from markdown_vault_mcp.vault import Vault

    # --source-dir overrides the env var: set it before from_env() reads it.
    # Deliberate process-env mutation — safe for a single-shot, single-threaded CLI.
    if source_dir:
        os.environ[f"{_ENV_PREFIX}_SOURCE_DIR"] = source_dir
    config = ProjectConfig.from_env()
    instances = to_vault_instances(config)
    settings = to_vault_settings(config, instances=instances)
    if index_path:
        settings = dataclasses.replace(settings, index_path=Path(index_path))
    return Vault(
        source_dir=config.source_dir,
        settings=settings,
        embedding_provider=instances.embedding_provider,
        summarizer=instances.summarizer,
        git_strategy=instances.git_strategy,
        on_write=instances.on_write,
    )


@app.command()
def index(
    source_dir: str | None = typer.Option(
        None, help=f"Path to markdown vault (overrides ${_ENV_PREFIX}_SOURCE_DIR)."
    ),
    index_path: str | None = typer.Option(
        None, help=f"Path to SQLite index file (overrides ${_ENV_PREFIX}_INDEX_PATH)."
    ),
    force: bool = typer.Option(False, help="Drop and rebuild the index from scratch."),
) -> None:
    """Build the full-text search index."""
    from markdown_vault_mcp._http_logging import quiet_http_loggers
    from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

    quiet_http_loggers()
    vault = _build_vault(source_dir, index_path)
    stats = vault.index.build_index(force=force)
    typer.echo(
        f"Indexed {stats.documents_indexed} documents, {stats.chunks_indexed} chunks"
    )
    try:
        n = vault.index.build_embeddings(force=force)
        typer.echo(f"Embedded {n} chunks")
    except EmbeddingsNotConfiguredError:
        pass  # embeddings not configured


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    source_dir: str | None = typer.Option(
        None, help=f"Path to markdown vault (overrides ${_ENV_PREFIX}_SOURCE_DIR)."
    ),
    limit: int = typer.Option(10, "-n", "--limit", help="Max results (default: 10)."),
    mode: str = typer.Option(
        "keyword",
        "-m",
        "--mode",
        help="keyword / semantic / hybrid (default: keyword).",
    ),
    folder: str | None = typer.Option(None, help="Restrict to folder."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
) -> None:
    """Search the vault."""
    import json
    from dataclasses import asdict
    from typing import cast

    from markdown_vault_mcp._http_logging import quiet_http_loggers

    quiet_http_loggers()
    vault = _build_vault(source_dir, None)
    results = vault.reader.search(
        query,
        limit=limit,
        mode=cast("Literal['keyword', 'semantic', 'hybrid']", mode),
        folder=folder,
    )
    if json_output:
        typer.echo(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for r in results:
            typer.echo(f"  {r.path} ({r.score:.4f})")
            if r.title:
                typer.echo(f"    {r.title}")


@app.command()
def reindex(
    source_dir: str | None = typer.Option(
        None, help=f"Path to markdown vault (overrides ${_ENV_PREFIX}_SOURCE_DIR)."
    ),
    index_path: str | None = typer.Option(
        None, help=f"Path to SQLite index file (overrides ${_ENV_PREFIX}_INDEX_PATH)."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Drop the index and re-parse every file, ignoring change detection. "
            "An upgrade that changes extraction rebuilds by itself on the next "
            "run; use this only for an index you have reason to distrust."
        ),
    ),
) -> None:
    """Incrementally reindex the vault."""
    from markdown_vault_mcp._http_logging import quiet_http_loggers
    from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

    quiet_http_loggers()
    vault = _build_vault(source_dir, index_path)
    # reindex() needs a built index (#525); build_index() is a cheap no-op when
    # the index is already populated (a SQL row-count check, no filesystem scan)
    # and the recorded build provenance still matches this process (#1124).
    vault.index.build_index(force=force)
    result = vault.index.reindex()
    typer.echo(
        f"Reindex: {result.added} added, {result.modified} modified, "
        f"{result.deleted} deleted, {result.unchanged} unchanged, "
        f"{result.skipped} skipped"
    )
    try:
        n = vault.index.build_embeddings()  # converges vectors to FTS chunks (#665)
        typer.echo(f"Embedded {n} chunks")
    except EmbeddingsNotConfiguredError:
        pass  # embeddings not configured


# DOMAIN-COMMANDS-END


def main() -> None:
    """CLI entry point — used by ``[project.scripts]`` in pyproject.toml."""
    app()


if __name__ == "__main__":
    main()
