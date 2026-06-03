"""Command-line interface for markdown-vault-mcp.

Provides ``serve``, ``index``, ``search``, and ``reindex`` subcommands.
The entry point is :func:`main`, registered as ``markdown-vault-mcp`` in
``pyproject.toml``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp_pvl_core import (
    configure_logging_from_env,
    maybe_start_debugpy,
    normalise_http_path,
)

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.config import _ENV_PREFIX, load_config

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)

_PROG = "markdown-vault-mcp"


def _build_collection(args: argparse.Namespace) -> Collection:
    """Build a Collection from environment variables and CLI overrides.

    Delegates to :meth:`~markdown_vault_mcp.config.CollectionConfig.to_collection_kwargs`
    so the CLI path stays in sync with the server path in
    :func:`~markdown_vault_mcp._server_deps.make_collection_lifespan`. CLI
    arguments ``--source-dir`` and ``--index-path`` override the corresponding
    environment variables when provided.

    Args:
        args: Parsed CLI arguments (may contain ``source_dir`` and
            ``index_path`` attributes).

    Returns:
        A fully initialised :class:`Collection` (index not yet built).
    """
    # CLI --source-dir overrides env var.
    source_dir_override = getattr(args, "source_dir", None)
    if source_dir_override:
        os.environ[f"{_ENV_PREFIX}_SOURCE_DIR"] = source_dir_override

    config = load_config()
    kwargs = config.to_collection_kwargs()

    # CLI --index-path overrides env var / config default.
    index_path_override = getattr(args, "index_path", None)
    if index_path_override:
        kwargs["index_path"] = Path(index_path_override)

    return Collection(**kwargs)


def _cmd_serve(args: argparse.Namespace) -> None:
    """Run the MCP server."""
    try:
        from markdown_vault_mcp.server import build_event_store, make_server
    except ImportError:
        logger.error(
            "FastMCP is not installed. Install with: "
            "pip install markdown-vault-mcp[mcp]"
        )
        sys.exit(1)

    # Optional remote-debugger listener — placed in serve (not main) so
    # non-server commands (index/search/reindex/--help) are never blocked
    # by MARKDOWN_VAULT_MCP_DEBUG_WAIT=true.  No-op unless
    # MARKDOWN_VAULT_MCP_DEBUG_PORT is set; debugpy is only present when
    # the image was built with --build-arg DEBUG=true (a missing import
    # logs a WARNING and continues).
    maybe_start_debugpy(_ENV_PREFIX)

    transport = args.transport
    server = make_server(transport=transport)
    env_http_path = os.environ.get(f"{_ENV_PREFIX}_HTTP_PATH")
    http_path = normalise_http_path(args.http_path or env_http_path)
    if transport != "http" and (
        args.host != "127.0.0.1" or args.port != 8000 or args.http_path is not None
    ):
        logger.warning(
            "--host, --port and --http-path are only used with --transport http"
        )
    if transport == "http":
        import uvicorn

        config = load_config()
        event_store = build_event_store(config.server.event_store_url)
        # FastMCP's run() doesn't pass event_store through to http_app(),
        # so we build the ASGI app and run uvicorn directly.
        app = server.http_app(
            path=http_path,
            transport="http",
            event_store=event_store,
        )
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            timeout_graceful_shutdown=3,
            lifespan="on",
        )
    else:
        server.run(transport=transport)


def _cmd_index(args: argparse.Namespace) -> None:
    """Build the full-text search index."""
    collection = _build_collection(args)
    stats = collection.build_index(force=args.force)
    logger.info(
        "Indexed %d documents, %d chunks",
        stats.documents_indexed,
        stats.chunks_indexed,
    )
    print(f"Indexed {stats.documents_indexed} documents, {stats.chunks_indexed} chunks")
    try:
        n = collection.build_embeddings(force=args.force)
        logger.info("Embedded %d chunks", n)
        print(f"Embedded {n} chunks")
    except ValueError:
        pass  # embeddings not configured


def _cmd_search(args: argparse.Namespace) -> None:
    """Search the collection."""
    collection = _build_collection(args)

    results = collection.search(
        args.query,
        limit=args.limit,
        mode=args.mode,
        folder=args.folder,
    )
    logger.info(
        "Search complete: %d results for query=%r mode=%s",
        len(results),
        args.query,
        args.mode,
    )

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for r in results:
            score = f" ({r.score:.4f})"
            print(f"  {r.path}{score}")
            if r.title:
                print(f"    {r.title}")


def _cmd_reindex(args: argparse.Namespace) -> None:
    """Incrementally reindex the collection."""
    collection = _build_collection(args)
    # reindex() requires a built index (bucket 4 readiness contract,
    # issue #525). build_index() short-circuits in O(1) on a coherent
    # persisted DB, so this is free on the warm path and correct on cold.
    collection.build_index()
    result = collection.reindex()
    logger.info(
        "Reindex complete: %d added, %d modified, %d deleted, %d unchanged",
        result.added,
        result.modified,
        result.deleted,
        result.unchanged,
    )
    print(
        f"Reindex: {result.added} added, {result.modified} modified, "
        f"{result.deleted} deleted, {result.unchanged} unchanged"
    )
    try:
        should_force = result.added > 0 or result.modified > 0 or result.deleted > 0
        n = collection.build_embeddings(force=should_force)
        logger.info("Embedded %d chunks", n)
        print(f"Embedded {n} chunks")
    except ValueError:
        pass  # embeddings not configured


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Generic markdown collection MCP server",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # serve — flags mirror fastmcp_pvl_core.make_serve_parser so future
    # adoption (once core exposes add_serve_args) is a straight swap.
    serve_parser = sub.add_parser("serve", help="run the MCP server")
    serve_parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "http"],
        default="stdio",
        help="MCP transport: stdio (default), sse, or http (streamable-http)",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to bind to for http transport (default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="port for http transport (default: 8000)",
    )
    serve_parser.add_argument(
        # --path is kept as an alias so existing Dockerfiles and service
        # units keep working; new invocations should prefer --http-path.
        "--http-path",
        "--path",
        default=None,
        help=(
            "mount path for http transport (default: "
            "$MARKDOWN_VAULT_MCP_HTTP_PATH or /mcp)"
        ),
    )

    # index
    index_parser = sub.add_parser("index", help="build the full-text search index")
    index_parser.add_argument(
        "--source-dir",
        help=f"path to markdown collection (overrides {_ENV_PREFIX}_SOURCE_DIR)",
    )
    index_parser.add_argument(
        "--index-path",
        help=f"path to SQLite index file (overrides {_ENV_PREFIX}_INDEX_PATH)",
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="drop and rebuild the index from scratch",
    )

    # search
    search_parser = sub.add_parser("search", help="search the collection")
    search_parser.add_argument("query", help="search query")
    search_parser.add_argument(
        "--source-dir",
        help=f"path to markdown collection (overrides {_ENV_PREFIX}_SOURCE_DIR)",
    )
    search_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="max results (default: 10)",
    )
    search_parser.add_argument(
        "-m",
        "--mode",
        choices=["keyword", "semantic", "hybrid"],
        default="keyword",
        help="search mode (default: keyword)",
    )
    search_parser.add_argument(
        "--folder",
        help="restrict to folder",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="output results as JSON",
    )

    # reindex
    reindex_parser = sub.add_parser(
        "reindex", help="incrementally reindex the collection"
    )
    reindex_parser.add_argument(
        "--source-dir",
        help=f"path to markdown collection (overrides {_ENV_PREFIX}_SOURCE_DIR)",
    )
    reindex_parser.add_argument(
        "--index-path",
        help=f"path to SQLite index file (overrides {_ENV_PREFIX}_INDEX_PATH)",
    )

    return parser


_COMMANDS = {
    "serve": _cmd_serve,
    "index": _cmd_index,
    "search": _cmd_search,
    "reindex": _cmd_reindex,
}


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    configure_logging_from_env(verbose=args.verbose)

    # Root handler for markdown_vault_mcp.* — FastMCP's configure_logging only covers its own logger tree.
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)

    # Silence httpx/httpcore at DEBUG — kept inline, core doesn't own these deps.
    if args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    cmd = _COMMANDS[args.command]
    try:
        cmd(args)
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
