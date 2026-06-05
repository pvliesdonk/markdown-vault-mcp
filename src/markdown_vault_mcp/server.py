"""Generic FastMCP server for markdown vaults.

Exposes :class:`~markdown_vault_mcp.vault.Vault` methods as MCP tools
with proper ``ToolAnnotations``.  Uses a lifespan hook to build the
``Vault`` once at startup and tear it down on shutdown.

The server is configured entirely via environment variables (see
:mod:`markdown_vault_mcp.config`).  Call :func:`make_server` to build a
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
    ServerConfig,
    build_auth,
    register_server_info_tool,
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
from ._server_deps import make_vault_lifespan
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
        "A searchable markdown document vault. "
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


def make_server(transport: str = "stdio") -> FastMCP:
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

    Args:
        transport: ``"stdio"`` / ``"http"`` / ``"sse"`` / ``"streamable-http"``.
            Used to gate HTTP-only wiring (e.g. the GitHub webhook route) and
            as ``transport=%s`` in the startup log.

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
    # build_auth returns None only for mode="none" or precondition-miss inside an
    # OIDC builder (missing required fields).  pvl-core 2.0 raises ConfigurationError
    # on actual discovery failures (httpx missing, network error, malformed discovery
    # doc), so this no longer needs to defend against the "discovery silently failed"
    # case — fail-fast at startup means we never reach this line in that scenario.
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
        "Server config: version=%s name=%s transport=%s auth=%s mode=%s vault=%s embeddings=%s",
        pkg_ver,
        server_name,
        transport,
        auth_mode,
        "read-only" if is_read_only else "read-write",
        config.source_dir,
        "enabled" if config.indexing.embeddings_path else "disabled",
    )

    mcp = FastMCP(
        server_name,
        instructions=instructions,
        icons=_SERVER_ICON,
        lifespan=make_vault_lifespan(config),
        auth=auth,
    )

    # include_traceback=None infers from root log level (-v→DEBUG→tracebacks); transform_errors=False lets exceptions propagate to FastMCP's own handlers.
    wire_middleware_stack(mcp, include_traceback=None, transform_errors=False)

    # Optional: enable opt-in per-subject authorization on tools / resources /
    # prompts.  See fastmcp-pvl-core's README "Authorization" section for the
    # design.  Tools, resources, and prompts opt in by setting
    # ``meta={"required_scope": "<scope>"}``; absence of the key means
    # unrestricted.  The middleware is only installed when ``acl_path`` is set.
    #
    # from fastmcp_pvl_core import (
    #     AuthorizationMiddleware,
    #     load_acl,
    #     make_acl_authorizer,
    # )
    #
    # if config.acl_path is not None:
    #     authorizer = make_acl_authorizer(load_acl(config.acl_path))
    #     mcp.add_middleware(AuthorizationMiddleware(authorizer=authorizer))

    register_tools(mcp)
    register_resources(mcp)
    register_apps(mcp)
    register_prompts(
        mcp,
        templates_folder=config.content.templates_folder,
        prompts_folder=config.content.prompts_folder,
    )

    # ``register_server_info_tool`` is intentionally read-only and stays
    # enabled in read-only mode (no ``tags={"write"}``) — operators need
    # ``get_server_info`` to confirm the deployed version regardless of
    # the read/write posture.
    register_server_info_tool(
        mcp,
        server_name=server_name,
        server_version=pkg_ver,
        # DOMAIN-UPSTREAM-START — wire upstream version reporting for servers
        # that talk to a remote service (paperless-mcp, etc.). The provider is
        # a zero-arg callable; the simplest pattern is a module-level upstream
        # client (typically constructed from env vars at import time) whose
        # version method is referenced here.
        # Uncomment the kwargs below as additional arguments to this call:
        # upstream_version=lambda: _upstream_client.remote_version(),
        # upstream_label="paperless",
        # DOMAIN-UPSTREAM-END
    )

    # DOMAIN-WIRING-START — project-specific wiring (custom HTTP routes,
    # transforms, mode toggles, alternative middleware, additional registrations);
    # kept across copier update.
    # GitHub webhook endpoint — only when secret is configured and transport
    # is HTTP/SSE (stdio has no HTTP server to receive POST requests).
    if config.sync.github_webhook_secret and transport != "stdio":
        from markdown_vault_mcp._github_webhook import make_webhook_handler

        mcp.custom_route("/github-webhook", methods=["POST"])(
            make_webhook_handler(config.sync.github_webhook_secret)
        )
    # DOMAIN-WIRING-END

    # DOMAIN-FILE-EXCHANGE-START — file-exchange wiring sentinel, kept across
    # copier update.  Intentionally empty: this server does not use pvl-core's
    # file-exchange helpers.  The one-time upload/download link tooling that
    # previously lived here was removed (#620); a fresh MV-local
    # implementation is tracked in #622.
    # DOMAIN-FILE-EXCHANGE-END

    # --- Visibility: hide write-tagged components in read-only mode ---

    if is_read_only:
        mcp.disable(tags={"write"})

    # Hide git-managed tools (e.g. git_sync) when not in managed git mode.
    # The two disable passes compose: a tool tagged {"write", "git-managed"}
    # is hidden if either condition fires (set-union on disabled tags).
    #
    # Check the config directly rather than constructing a strategy via
    # ``config.to_vault_kwargs()`` — that call builds an embedding
    # provider (slow, GBs of memory) and may run ``git clone`` as a side
    # effect.  The runtime check inside the ``git_sync`` tool body
    # (``isinstance(strategy, GitWriteStrategy) and strategy._managed``)
    # stays aligned with this gate via the same ``config.git.repo_url``
    # value: managed mode requires an explicit remote URL.  See #220 for
    # the broader cleanup of duplicate ``to_vault_kwargs`` calls.
    if config.git.repo_url is None:
        mcp.disable(tags={"git-managed"})

    return mcp
