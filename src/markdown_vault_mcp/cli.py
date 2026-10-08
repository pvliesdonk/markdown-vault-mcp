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
        # Inside the guard: since pvl-core 9 ``build_event_store`` raises
        # ``ConfigurationError`` for a malformed ``MARKDOWN_VAULT_MCP_KV_STORE_URL``
        # or legacy ``MARKDOWN_VAULT_MCP_EVENT_STORE_URL``, which must get the
        # same one-line exit as every other operator value (#647).
        event_store = (
            build_event_store(_ENV_PREFIX, config.server)
            if transport == "http"
            else None
        )
    except ConfigurationError as exc:
        # A malformed or missing operator value is one actionable line on
        # stderr, not Typer's Rich traceback: the message already names the
        # variable and the problem (#616). stderr keeps stdout clean for the
        # stdio transport.
        typer.echo(f"ERROR: configuration error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if transport == "http":
        # pvl-core runs uvicorn.  It pins ``lifespan="on"`` (FastMCP's
        # startup/shutdown hooks run through the ASGI lifespan protocol),
        # ``log_config=None`` (uvicorn must not reinstall its own handlers
        # over the root chain ``_root`` set up) and the SIGTERM drain window
        # from ``MARKDOWN_VAULT_MCP_SHUTDOWN_GRACE_S`` (default 3s, so
        # containers stop cleanly).  ``None`` for host or port means "not
        # given on the command line"; ``run_http`` then reads ``config.server``.
        run_http(
            server.http_app(path=path, event_store=event_store),
            config=config.server,
            host=host,
            port=port,
        )
    else:
        server.run(transport=transport)


# DOMAIN-COMMANDS-START — add domain @app.command()s (and their helpers) below; kept across copier update
# Domain CLI subcommands live here so the rest of this file stays byte-identical
# to the template and applies cleanly on copier update. Use function-local
# imports for domain modules (as ``serve`` does) to keep the top-level import
# surface template-owned. Module-level ``TYPE_CHECKING`` guards are fine — they
# are erased at runtime.

from typing import TYPE_CHECKING, NamedTuple  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.config_sections._assembly import VaultInstances
    from markdown_vault_mcp.exceptions import VectorIndexUnusableError
    from markdown_vault_mcp.vault import Vault


class _BuiltVault(NamedTuple):
    """A vault a command built, with the collaborators built for it (#1765)."""

    vault: Vault
    instances: VaultInstances

    def close(self) -> None:
        """Close the vault, then the collaborators it was given.

        The vault closes only what it opened; the git strategy closes last,
        so its push flush follows the drained commits.
        """
        self.vault.close()
        self.instances.close()


def _build_vault(
    source_dir: str | None = None,
    index_path: str | None = None,
    *,
    scratch_state: bool = False,
    in_memory_vectors: bool = False,
    shared_files: bool = False,
) -> _BuiltVault:
    """Build a synchronous Vault from env vars + optional CLI overrides.

    Uses the same settings-first ``to_vault_settings`` / ``to_vault_instances``
    assembly as the server path (#1158), but constructs a bare Vault (no
    background tasks, file watcher, or index writer — those belong to the
    server's lifespan).

    Args:
        source_dir: Overrides ``{PREFIX}_SOURCE_DIR`` when given (set into the
            environment before ``ProjectConfig.from_env()`` reads it).
        index_path: Overrides the resolved SQLite index path when given.
        scratch_state: Keep the change-tracking state in memory, ignoring
            ``STATE_PATH``, for a one-shot in-memory index whose state must
            not overwrite a running server's (#1691).
        in_memory_vectors: Resolve the embedding provider even when no file
            would keep the vectors (no ``EMBEDDINGS_PATH`` and no on-disk
            index), so they are embedded in memory. Off by default: the
            provider is then never resolved, since a batch command would pay
            it for vectors it throws away (#1708).
        shared_files: Leave the index files on disk as they are, since a
            running server may own them: an on-disk index opens read-only
            (#1758) and an unusable vector sidecar makes a search raise
            instead of being rebuilt (#1734).

    Returns:
        The constructed :class:`~markdown_vault_mcp.vault.Vault` (index not
        built) with the collaborators built for it; call ``close()`` on the
        pair when done.
    """
    import dataclasses
    import os
    from pathlib import Path

    from markdown_vault_mcp.config_sections._assembly import (
        source_dir_problem,
        to_vault_instances,
        to_vault_settings,
    )
    from markdown_vault_mcp.vault import Vault

    # --source-dir overrides the env var: set it before from_env() reads it.
    # Deliberate process-env mutation — safe for a single-shot, single-threaded CLI.
    if source_dir:
        os.environ[f"{_ENV_PREFIX}_SOURCE_DIR"] = source_dir
    config = ProjectConfig.from_env()
    # A batch command over an absent or inaccessible directory would index
    # nothing and exit 0; refuse with the reason, the way ``serve`` reports a
    # configuration error (a missing directory names the variable to set).
    problem = source_dir_problem(config)
    if problem is not None:
        typer.echo(f"ERROR: configuration error: {problem[1]}", err=True)
        raise typer.Exit(code=1)
    if not in_memory_vectors and not _vectors_kept(config, index_path):
        # Nothing would keep the vectors, so drop the provider before it is
        # resolved: FastEmbed would load, or download, its model for nothing.
        config = dataclasses.replace(config, embedding_provider=None)
    instances = to_vault_instances(config)
    settings = to_vault_settings(config, instances=instances)
    if index_path:
        settings = dataclasses.replace(settings, index_path=Path(index_path))
    if scratch_state:
        settings = dataclasses.replace(settings, state_path=None)
    if shared_files:
        settings = dataclasses.replace(settings, owns_index_files=False)
    return _BuiltVault(
        Vault(
            source_dir=config.source_dir,
            settings=settings,
            embedding_provider=instances.embedding_provider,
            summarizer=instances.summarizer,
            git_strategy=instances.git_strategy,
            on_write=instances.on_write,
        ),
        instances,
    )


def _vectors_kept(config: ProjectConfig, index_path: str | None) -> bool:
    """Report whether a file would keep the vectors this command embeds.

    Args:
        config: The configuration the command read.
        index_path: The ``--index-path`` override, if given.

    Returns:
        ``True`` when ``EMBEDDINGS_PATH`` is set or the index is on disk,
        where the vectors default beside it (#1708).
    """
    if config.indexing.embeddings_path is not None:
        return True
    index = index_path or config.indexing.index_path
    return index is not None and str(index) != ":memory:"


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
    built = _build_vault(source_dir, index_path)
    try:
        stats = built.vault.index.build_index(force=force)
        typer.echo(
            f"Indexed {stats.documents_indexed} documents, "
            f"{stats.chunks_indexed} chunks"
        )
        try:
            n = built.vault.index.build_embeddings(force=force)
            typer.echo(f"Embedded {n} chunks")
        except EmbeddingsNotConfiguredError:
            pass  # embeddings not configured
    finally:
        built.close()


def _embed_in_memory(vault: Vault) -> None:
    """Embed the vault for one search by meaning when no file keeps the vectors.

    Vectors that ``EMBEDDINGS_PATH`` or an on-disk index keep are only read:
    building them here would write files a running server may share (#1708).

    Args:
        vault: A vault whose in-memory index is already built.
    """
    status = vault.index.embeddings_status()
    if status["available"] and status["path"] is None:
        vault.index.build_embeddings()


def _unusable_vectors_message(exc: VectorIndexUnusableError) -> str:
    """Explain why search refused stored vectors, and what to do instead.

    Args:
        exc: The refusal; its cause tells a provider mismatch from damage.

    Returns:
        The ``ERROR:`` line for stderr.
    """
    from markdown_vault_mcp.vector_index import VectorIndexCompatibilityError

    if isinstance(exc.__cause__, VectorIndexCompatibilityError):
        remedy = "Search with the embedding settings that built them"
    else:
        remedy = (
            "To rebuild them, run index with the settings that built them "
            "while no server uses them"
        )
    return (
        f"ERROR: cannot search by meaning: {exc} The vectors are left as they "
        f"are, since a running server may use them. {remedy}, or search with "
        "--mode keyword."
    )


def _require_index_file(index_path: Path) -> None:
    """Refuse a search of an on-disk index that has not been built.

    The index is opened read-only, which cannot create it; creating it here
    would write a file a server may be about to own (#1758).

    Args:
        index_path: The configured ``INDEX_PATH``.

    Raises:
        typer.Exit: With code 1 when no file is at the path.
    """
    from markdown_vault_mcp.utils.fs import path_exists

    if not path_exists(index_path):
        typer.echo(
            f"ERROR: no index at {index_path}. Build it with index, or start "
            "the server that owns it.",
            err=True,
        )
        raise typer.Exit(code=1)


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
    from markdown_vault_mcp.exceptions import VectorIndexUnusableError

    quiet_http_loggers()
    # An in-memory index is empty until built, so build it, keeping its state
    # in memory too: STATE_PATH may be a running server's (#1691). An on-disk
    # index is only read: build_index() would rebuild a server's index
    # whenever this shell's settings differ from the ones it was built with,
    # and even opening it for writing commits the rank weights (#1758).
    configured_index = ProjectConfig.from_env().indexing.index_path
    in_memory = configured_index is None or str(configured_index) == ":memory:"
    if configured_index is not None and not in_memory:
        _require_index_file(configured_index)
    # Only a valid vector mode embeds: a mistyped one must fail in the search
    # below before a paid provider has embedded the vault (#1708).
    by_meaning = mode in ("semantic", "hybrid")
    # Vectors on disk are only read too: a server rebuilds a sidecar that
    # does not fit its provider, and one in another shell's would rebuild it
    # under the server (#1734).
    built = _build_vault(
        source_dir,
        None,
        scratch_state=in_memory,
        in_memory_vectors=by_meaning,
        shared_files=True,
    )
    try:
        if in_memory:
            built.vault.index.build_index()
            if by_meaning:
                _embed_in_memory(built.vault)
        try:
            results = built.vault.reader.search(
                query,
                limit=limit,
                mode=cast("Literal['keyword', 'semantic', 'hybrid']", mode),
                folder=folder,
            )
        except VectorIndexUnusableError as exc:
            typer.echo(_unusable_vectors_message(exc), err=True)
            raise typer.Exit(code=1) from exc
    finally:
        built.close()
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
    built = _build_vault(source_dir, index_path)
    try:
        # reindex() needs a built index (#525); build_index() is a cheap no-op
        # when the index is already populated (a SQL row-count check, no
        # filesystem scan) and the recorded build provenance still matches
        # this process (#1124).
        built.vault.index.build_index(force=force)
        result = built.vault.index.reindex()
        typer.echo(
            f"Reindex: {result.added} added, {result.modified} modified, "
            f"{result.deleted} deleted, {result.unchanged} unchanged, "
            f"{result.skipped} skipped"
        )
        try:
            # Converges vectors to FTS chunks (#665).
            n = built.vault.index.build_embeddings()
            typer.echo(f"Embedded {n} chunks")
        except EmbeddingsNotConfiguredError:
            pass  # embeddings not configured
    finally:
        built.close()


# DOMAIN-COMMANDS-END


def main() -> None:
    """CLI entry point — used by ``[project.scripts]`` in pyproject.toml."""
    app()


if __name__ == "__main__":
    main()
