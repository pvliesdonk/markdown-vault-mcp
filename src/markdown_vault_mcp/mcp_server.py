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
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp.server.event_store import EventStore

from fastmcp import FastMCP
from fastmcp_pvl_core import (
    ArtifactStore,
    ServerConfig,
    build_auth,
    resolve_auth_mode,
    wire_middleware_stack,
)
from fastmcp_pvl_core import (
    build_event_store as _core_build_event_store,
)
from fastmcp_pvl_core import (
    build_instructions as _core_build_instructions,
)

from markdown_vault_mcp.config import (
    _ENV_PREFIX,
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
# Event store
# ---------------------------------------------------------------------------


def build_event_store(url: str | None = None) -> EventStore:
    """Build an ``EventStore`` for SSE polling/resumability.

    Thin shim over :func:`fastmcp_pvl_core.build_event_store`: wraps the
    legacy URL-only call shape used by ``cli.py`` and delegates the actual
    backend selection (file-tree vs in-memory) to the shared core helper.

    Args:
        url: Event store URL from ``MARKDOWN_VAULT_MCP_EVENT_STORE_URL``.

    Returns:
        A configured :class:`~fastmcp.server.event_store.EventStore`.
    """
    return _core_build_event_store(_ENV_PREFIX, ServerConfig(event_store_url=url))


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def _build_default_instructions(*, read_only: bool) -> str:
    """Build the default instructions string based on read-only state.

    Composes MV's domain-specific guidance into a ``domain_line`` and
    delegates to :func:`fastmcp_pvl_core.build_instructions` for the
    read-only/read-write line and operator override hint.
    """
    prelude = (
        "A searchable markdown document collection. "
        "Paths are always relative (e.g. 'Journal/note.md')."
    )
    write_guidance = (
        ""
        if read_only
        else (
            " Write tools: use 'write' to create, 'edit' for targeted changes "
            "(read first), 'rename' to move (pass update_links=True to fix links "
            "in other notes), 'delete' to remove. All write operations update the "
            "search index immediately — never call 'reindex' after write, edit, "
            "delete, or rename."
        )
    )
    search_guidance = (
        " Use 'search' (mode='hybrid' preferred when available) to find documents, "
        "'read' for full content, 'list_documents' to enumerate, 'stats' to check "
        "capabilities. 'browse_vault' and 'show_context' open a visual UI for the "
        "user — do not call them to retrieve vault content; use 'search', 'read', "
        "'list_documents', or 'get_context' instead."
    )
    domain_line = f"{prelude}{write_guidance}{search_guidance}"
    return _core_build_instructions(
        read_only=read_only,
        env_prefix=_ENV_PREFIX,
        domain_line=domain_line,
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

    auth = build_auth(config.server)
    # Collapse to "none" whenever build_auth actually returned None (e.g.
    # OIDC discovery failed) so the log reflects the real security posture,
    # not whatever resolve_auth_mode would report from field presence alone.
    auth_mode = resolve_auth_mode(config.server) if auth is not None else "none"
    if auth_mode == "none":
        logger.warning(
            "No auth configured — server accepts unauthenticated connections"
        )
    else:
        logger.info("Auth enabled: mode=%s", auth_mode)

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

    # include_traceback=None infers from root log level (-v→DEBUG→tracebacks); transform_errors=False lets exceptions propagate to FastMCP's own handlers.
    wire_middleware_stack(mcp, include_traceback=None, transform_errors=False)

    register_tools(mcp, transport=transport)
    register_resources(mcp)
    register_apps(mcp)
    register_prompts(
        mcp,
        templates_folder=config.templates_folder,
        prompts_folder=config.prompts_folder,
    )

    # --- Artifact download endpoint (HTTP transports only) ---
    # The store is constructed here (not in lifespan) so the HTTP route
    # closure can bind to a concrete instance.  The tool handler reaches
    # the same instance via get_artifact_store() in markdown_vault_mcp.artifacts.
    # Skipped entirely on stdio where the create_download_link tool isn't
    # registered — no need to hold bytes in memory for a feature we don't expose.
    if transport != "stdio":
        from markdown_vault_mcp.artifacts import (
            ARTIFACT_TTL_SECONDS,
            set_artifact_store,
        )

        artifact_store = ArtifactStore(ttl_seconds=ARTIFACT_TTL_SECONDS)
        set_artifact_store(artifact_store)
        ArtifactStore.register_route(mcp, artifact_store)

    # --- Visibility: hide write-tagged components in read-only mode ---

    if is_read_only:
        mcp.disable(tags={"write"})

    return mcp
