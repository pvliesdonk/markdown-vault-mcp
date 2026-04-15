"""Generic FastMCP server for markdown collections.

Exposes :class:`~markdown_vault_mcp.collection.Collection` methods as MCP tools
with proper ``ToolAnnotations``.  Uses a lifespan hook to build the
``Collection`` once at startup and tear it down on shutdown.

The server is configured entirely via environment variables (see
:mod:`markdown_vault_mcp.config`).  Call :func:`create_server` to build a
configured :class:`~fastmcp.FastMCP` instance.
"""

from __future__ import annotations

import logging
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import (
    LoggingMiddleware,
    StructuredLoggingMiddleware,
)
from fastmcp.server.middleware.timing import TimingMiddleware

from markdown_vault_mcp.config import (
    _build_bearer_auth,
    _build_oidc_auth,
    _build_remote_auth,
    _resolve_auth_mode,
    load_config,
)

from ._icons import _SERVER_ICON
from ._server_apps import register_apps
from ._server_deps import make_collection_lifespan
from ._server_prompts import register_prompts
from ._server_resources import register_resources
from ._server_tools import register_tools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def _build_default_instructions(*, read_only: bool) -> str:
    """Build the default instructions string based on read-only state.

    Args:
        read_only: Whether write tools are disabled on this instance.

    Returns:
        Instructions string suitable for the ``instructions`` parameter
        of :class:`~fastmcp.FastMCP`.
    """
    write_line = (
        "This instance is READ-ONLY — write tools are not available."
        if read_only
        else (
            "This instance is READ-WRITE — use 'write' to create, 'edit' for "
            "targeted changes (read first), 'rename' to move "
            "(pass update_links=True to fix links in other notes), 'delete' to remove. "
            "All write operations update the search index immediately — never call "
            "'reindex' after write, edit, delete, or rename."
        )
    )
    return (
        "A searchable markdown document collection. "
        "Paths are always relative (e.g. 'Journal/note.md'). "
        f"{write_line} "
        "Use 'search' (mode='hybrid' preferred when available) to find documents, "
        "'read' for full content, 'list_documents' to enumerate, 'stats' to check "
        "capabilities. "
        "'browse_vault' and 'show_context' open a visual UI for the user — do not "
        "call them to retrieve vault content; use 'search', 'read', 'list_documents', "
        "or 'get_context' instead."
    )


def create_server(transport: str = "stdio") -> FastMCP:
    """Create and configure the FastMCP server.

    Reads configuration from environment variables via :func:`load_config`.
    Write tools are tagged with ``{"write"}`` and hidden via
    ``mcp.disable(tags={"write"})`` when ``READ_ONLY=true``.

    Returns:
        A fully configured :class:`~fastmcp.FastMCP` instance ready to run.
    """
    config = load_config()
    is_read_only = config.read_only

    server_name = config.server_name
    default_instructions = _build_default_instructions(read_only=is_read_only)
    instructions = config.instructions or default_instructions

    bearer_auth = _build_bearer_auth(config)
    oidc_mode = _resolve_auth_mode(config)

    oidc_auth = None
    if oidc_mode == "remote":
        oidc_auth = _build_remote_auth(config)
    elif oidc_mode == "oidc-proxy":
        oidc_auth = _build_oidc_auth(config)

    if oidc_mode and not oidc_auth:
        logger.warning(
            "OIDC auth mode '%s' was selected but auth failed to initialize — "
            "server will start without OIDC",
            oidc_mode,
        )

    if bearer_auth and oidc_auth:
        from fastmcp.server.auth import MultiAuth

        # Override required_scopes to empty — OIDC's required_scopes
        # (e.g. ["openid"]) would otherwise propagate to the HTTP
        # middleware and reject bearer tokens that lack "openid".
        auth = MultiAuth(server=oidc_auth, verifiers=[bearer_auth], required_scopes=[])
        auth_mode = f"multi({oidc_mode}+bearer)"
        logger.info(
            "Multi-auth enabled: bearer token + OIDC %s (either accepted)", oidc_mode
        )
    elif bearer_auth:
        auth = bearer_auth
        auth_mode = "bearer"
        logger.info("Bearer token auth enabled")
    elif oidc_auth:
        auth = oidc_auth
        auth_mode = oidc_mode or "oidc"
        logger.info("OIDC auth enabled (mode: %s)", oidc_mode)
    else:
        auth = None
        auth_mode = "none"
        logger.warning(
            "No auth configured — server accepts unauthenticated connections"
        )

    try:
        pkg_ver = _pkg_version("markdown-vault-mcp")
    except PackageNotFoundError:
        pkg_ver = "unknown"

    logger.info(
        "Server config: version=%s name=%s auth=%s mode=%s vault=%s embeddings=%s",
        pkg_ver,
        server_name,
        auth_mode,
        "read-only" if is_read_only else "read-write",
        config.source_dir,
        "enabled" if config.embeddings_path else "disabled",
    )

    mcp = FastMCP(
        server_name,
        instructions=instructions,
        icons=_SERVER_ICON,
        lifespan=make_collection_lifespan(config),
        auth=auth,
    )

    # --- Middleware stack ---
    # Order matters: outermost runs first.
    #  1. ErrorHandlingMiddleware — catches unhandled exceptions
    #  2. TimingMiddleware — records tool invocation duration
    #  3. LoggingMiddleware — rich or structured (JSON) output
    # Capture root log level *now* — works correctly when create_server() is
    # called after CLI verbose setup.  In library/test usage where logging is
    # configured later, tracebacks default to off (safe for production).
    include_traceback = logging.getLogger().isEnabledFor(logging.DEBUG)
    mcp.add_middleware(
        ErrorHandlingMiddleware(
            include_traceback=include_traceback, transform_errors=False
        )
    )
    mcp.add_middleware(TimingMiddleware())
    rich_logging = os.environ.get("FASTMCP_ENABLE_RICH_LOGGING", "true").strip().lower()
    if rich_logging in ("false", "0", "no"):
        mcp.add_middleware(StructuredLoggingMiddleware())
    else:
        mcp.add_middleware(LoggingMiddleware())

    register_tools(mcp, transport=transport)
    register_resources(mcp)
    register_apps(mcp)
    register_prompts(
        mcp,
        templates_folder=config.templates_folder,
        prompts_folder=config.prompts_folder,
    )

    # --- Artifact download endpoint (HTTP transports only) ---

    if transport != "stdio":
        from markdown_vault_mcp.artifacts import make_artifact_handler

        mcp.custom_route("/artifacts/{token}", methods=["GET"])(make_artifact_handler())

    # --- Visibility: hide write-tagged components in read-only mode ---

    if is_read_only:
        mcp.disable(tags={"write"})

    return mcp
