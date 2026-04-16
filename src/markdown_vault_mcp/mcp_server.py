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
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from fastmcp.server.event_store import EventStore

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import (
    LoggingMiddleware,
    StructuredLoggingMiddleware,
)
from fastmcp.server.middleware.timing import TimingMiddleware

from markdown_vault_mcp.config import (
    build_bearer_auth,
    build_oidc_auth,
    build_remote_auth,
    load_config,
    resolve_auth_mode,
)

from ._icons import _SERVER_ICON
from ._server_apps import register_apps
from ._server_deps import make_collection_lifespan
from ._server_prompts import register_prompts
from ._server_resources import register_resources
from ._server_tools import register_tools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event store
# ---------------------------------------------------------------------------

_DEFAULT_EVENT_STORE_DIR = "/data/state/events"


def build_event_store(url: str | None = None) -> EventStore:
    """Build an ``EventStore`` for SSE polling/resumability.

    Parses the *url* scheme to select a storage backend:

    - ``None`` or empty → ``FileTreeStore`` at :data:`_DEFAULT_EVENT_STORE_DIR`
    - ``file:///path`` → ``FileTreeStore`` at the given path
    - ``memory://`` → in-memory (lost on restart, for development)

    Args:
        url: Event store URL from ``MARKDOWN_VAULT_MCP_EVENT_STORE_URL``.

    Returns:
        A configured :class:`~fastmcp.server.event_store.EventStore`.
    """
    from fastmcp.server.event_store import EventStore as _EventStore

    if not url:
        url = f"file://{_DEFAULT_EVENT_STORE_DIR}"

    parsed = urlparse(url)

    if parsed.scheme == "memory":
        logger.info("Event store: in-memory (sessions lost on restart)")
        return _EventStore(max_events_per_stream=100, ttl=3600)

    if parsed.scheme == "file":
        directory = parsed.path
        if not directory:
            directory = _DEFAULT_EVENT_STORE_DIR
        Path(directory).mkdir(parents=True, exist_ok=True)
        logger.info("Event store: file-backed at %s", directory)

        try:
            from key_value.aio.stores.filetree import FileTreeStore
        except ImportError:
            raise ImportError(
                "FileTreeStore requires fastmcp>=3.0 with key-value support. "
                "Install with: pip install 'markdown-vault-mcp[mcp]'"
            ) from None

        storage = FileTreeStore(data_directory=directory)
        return _EventStore(storage=storage, max_events_per_stream=100, ttl=3600)

    raise ValueError(
        f"Unsupported EVENT_STORE_URL scheme {parsed.scheme!r}. "
        "Use 'file:///path' or 'memory://'."
    )


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

    Server identity is configurable via:

    - ``MARKDOWN_VAULT_MCP_SERVER_NAME``: MCP server name shown to clients
      (default ``"markdown-vault-mcp"``).
    - ``MARKDOWN_VAULT_MCP_INSTRUCTIONS``: system-level instructions injected
      into LLM context (default: dynamic description reflecting read-only state).
    - ``MARKDOWN_VAULT_MCP_PROMPTS_FOLDER``: directory of user-defined ``.md``
      prompt files.  User prompts with the same name as a built-in override the
      built-in.  Default: disabled.

    Returns:
        A fully configured :class:`~fastmcp.FastMCP` instance ready to run.
    """
    config = load_config()
    is_read_only = config.read_only

    server_name = config.server_name
    if config.instructions is not None:
        instructions = config.instructions
    else:
        instructions = _build_default_instructions(read_only=is_read_only)

    bearer_auth = build_bearer_auth(config)
    oidc_mode = resolve_auth_mode(config)

    oidc_auth = None
    if oidc_mode == "remote":
        oidc_auth = build_remote_auth(config)
    elif oidc_mode == "oidc-proxy":
        oidc_auth = build_oidc_auth(config)

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
