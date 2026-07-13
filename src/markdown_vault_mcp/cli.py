"""Command-line interface for Markdown Vault MCP."""

from __future__ import annotations

import logging
from typing import Literal

import typer
from fastmcp_pvl_core import (
    build_event_store,
    configure_logging_from_env,
    maybe_start_debugpy,
    normalise_http_path,
)

from markdown_vault_mcp.config import _ENV_PREFIX, ProjectConfig, to_vault_kwargs

app = typer.Typer(
    name="markdown-vault-mcp",
    help="Generic markdown vault MCP server with FTS5 + semantic search",
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

    ``configure_logging_from_env`` sets the root logger *level* and
    configures FastMCP's own logger tree, but does NOT attach a handler
    to the root logger — so ``markdown_vault_mcp.*`` loggers would have
    no output.  Attach one here.  Kept idempotent via the
    ``if not root.handlers`` guard so repeated calls (e.g. from
    ``make_server()`` on the same process) are safe.
    """
    configure_logging_from_env(verbose=verbose)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    # Quiet httpx/httpcore per-request INFO at the default level (#792); -v shows them.
    http_level = logging.NOTSET if verbose else logging.WARNING
    logging.getLogger("httpx").setLevel(http_level)
    logging.getLogger("httpcore").setLevel(http_level)


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
    # already attached the StreamHandler by the time ``serve`` runs, so
    # the helper's INFO/WARNING logs route through the configured
    # formatter rather than Python's lastResort.
    maybe_start_debugpy(_ENV_PREFIX)

    config = ProjectConfig.from_env()
    server = make_server(transport=transport, config=config)

    if transport == "http":
        import uvicorn

        path = normalise_http_path(
            http_path or os.environ.get(f"{_ENV_PREFIX}_HTTP_PATH")
        )
        event_store = build_event_store(_ENV_PREFIX, config.server)
        # lifespan="on" is essential: FastMCP's server_lifespan (startup/shutdown
        # hooks, including service init) runs through the ASGI lifespan protocol.
        # timeout_graceful_shutdown=3 lets SIGTERM drain requests within 3s so
        # containers (Docker/k8s) stop cleanly.
        uvicorn.run(
            server.http_app(path=path, event_store=event_store),
            host=host if host is not None else config.server.host,
            port=port if port is not None else config.server.port,
            lifespan="on",
            timeout_graceful_shutdown=3,
        )
    else:
        server.run(transport=transport)


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

    Uses the same ``to_vault_kwargs(config)`` bridge as the server path,
    but constructs a bare Vault (no background tasks, file watcher, or index
    writer — those belong to the server's lifespan).

    Args:
        source_dir: Overrides ``{PREFIX}_SOURCE_DIR`` when given (set into the
            environment before ``ProjectConfig.from_env()`` reads it).
        index_path: Overrides the resolved SQLite index path when given.

    Returns:
        A constructed :class:`~markdown_vault_mcp.vault.Vault` (index not built).
    """
    import os
    from pathlib import Path

    from markdown_vault_mcp.vault import Vault

    # --source-dir overrides the env var: set it before from_env() reads it.
    # Deliberate process-env mutation — safe for a single-shot, single-threaded CLI.
    if source_dir:
        os.environ[f"{_ENV_PREFIX}_SOURCE_DIR"] = source_dir
    config = ProjectConfig.from_env()
    kwargs = to_vault_kwargs(config)
    if index_path:
        kwargs["index_path"] = Path(index_path)
    return Vault(**kwargs)


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
    from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

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
) -> None:
    """Incrementally reindex the vault."""
    from markdown_vault_mcp.exceptions import EmbeddingsNotConfiguredError

    vault = _build_vault(source_dir, index_path)
    # reindex() needs a built index (#525); build_index() is a cheap no-op when
    # the index is already populated (a SQL row-count check, no filesystem scan).
    vault.index.build_index()
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
