"""Generic FastMCP server for markdown collections.

Exposes :class:`~markdown_vault_mcp.collection.Collection` methods as MCP tools
with proper ``ToolAnnotations``.  Uses a lifespan hook to build the
``Collection`` once at startup and tear it down on shutdown.

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
    ArtifactStore,
    FileExchange,
    FileExchangeCapability,
    ServerConfig,
    build_auth,
    register_file_exchange_capability,
    resolve_auth_mode,
    set_artifact_store,
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

from ._file_exchange import set_file_exchange, start_sweep_timer
from ._icons import _SERVER_ICON
from ._server_apps import register_apps
from ._server_deps import make_collection_lifespan
from ._server_prompts import register_prompts
from ._server_resources import register_resources
from ._server_tools import register_tools

#: MIME types this server consumes when accepting file_refs from other
#: producers. ``*/*`` is the catch-all spec §3.3 sentinel meaning "we
#: accept any binary blob the operator's attachment allowlist permits".
_FILE_EXCHANGE_CONSUMES = (
    "text/markdown",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/svg+xml",
    "application/pdf",
    "application/octet-stream",
    "*/*",
)

#: MIME types this server can emit as file_refs (binary attachments
#: returned by ``read``).  Markdown notes are emitted in-band as text,
#: not via file_ref, so the list focuses on the binary surface.
_FILE_EXCHANGE_PRODUCES = (
    "application/octet-stream",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/svg+xml",
    "image/gif",
    "application/pdf",
    "text/markdown",
)

logger = logging.getLogger(__name__)


#: Lifetime applied to every artifact token.  Long enough to cover slow
#: downloads (LLMs sometimes re-fetch after a delay) but short enough
#: that a forgotten link doesn't linger indefinitely.  Per-call
#: ``ttl_seconds`` on ``create_download_link`` overrides this on a
#: per-token basis.
_ARTIFACT_TTL_SECONDS = 3600


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

    register_tools(mcp, transport=transport, base_url_configured=bool(config.base_url))
    register_resources(mcp)
    register_apps(mcp)
    register_prompts(
        mcp,
        templates_folder=config.templates_folder,
        prompts_folder=config.prompts_folder,
    )

    # --- Artifact download endpoint (HTTP transports only) ---
    # Construct the store here (not in lifespan) so the HTTP route closure
    # can bind to a concrete instance. The tool handler reaches the same
    # instance via fastmcp_pvl_core.get_artifact_store(). Skip when no
    # base_url is configured: put_ephemeral needs it for URL construction,
    # so registering the route without it would surface a confusing
    # RuntimeError on first call instead of a startup-time skip.
    if transport != "stdio" and config.base_url:
        artifact_store = ArtifactStore(
            ttl_seconds=_ARTIFACT_TTL_SECONDS, base_url=config.base_url
        )
        set_artifact_store(artifact_store)
        ArtifactStore.register_route(mcp, artifact_store)
    elif transport != "stdio":
        # HTTP transport without BASE_URL is a valid configuration (the
        # MCP protocol still works), but create_download_link can't
        # function — we already gate the tool registration on this in
        # register_tools().  Surface a clear startup signal so operators
        # don't have to discover the missing tool via "why isn't it in
        # the list?" later.
        logger.warning(
            "create_download_link unavailable: MARKDOWN_VAULT_MCP_BASE_URL "
            "is not set, so the artifact route + tool are skipped. Set "
            "BASE_URL to expose download links."
        )

    # --- File exchange (MCP File Exchange v0.3) ---
    # Always construct an instance — `from_env` returns an unconfigured
    # sentinel when MCP_EXCHANGE_DIR is unset, so `is_configured` gates
    # the runtime behaviour without forcing every caller to special-case
    # a missing instance.
    fx = FileExchange.from_env(default_namespace=server_name)
    set_file_exchange(fx)
    transfer_methods = _resolve_transfer_methods(
        fx, transport=transport, base_url=config.base_url
    )
    if transfer_methods:
        # Only advertise the capability when at least one transfer method
        # is actually available. Registering with an empty
        # ``transfer_methods`` (stdio + no MCP_EXCHANGE_DIR) would tell
        # peers "I speak v0.3" while offering no way to move bytes —
        # misleading per spec §3.9 which describes the capability as the
        # signal that *some* transport is offered.
        register_file_exchange_capability(
            mcp,
            FileExchangeCapability(
                namespace=fx.namespace if fx.is_configured else server_name,
                exchange_id=fx.exchange_id if fx.is_configured else None,
                produces=_FILE_EXCHANGE_PRODUCES,
                consumes=_FILE_EXCHANGE_CONSUMES,
                transfer_methods=transfer_methods,
            ),
        )
    else:
        logger.info(
            "File exchange capability not advertised: no transfer methods "
            "available (set MCP_EXCHANGE_DIR for the exchange transfer, or "
            "switch to an HTTP transport with BASE_URL set for the http "
            "transfer)."
        )
    if fx.is_configured:
        # Producer-side periodic eviction. The lifespan stops the timer
        # in its `finally` block; one final sweep also runs there to
        # release expired files at shutdown.
        start_sweep_timer(fx)
        logger.info(
            "File exchange enabled: namespace=%s exchange_id=%s",
            fx.namespace,
            fx.exchange_id,
        )

    # --- Visibility: hide write-tagged components in read-only mode ---

    if is_read_only:
        mcp.disable(tags={"write"})

    return mcp


def _resolve_transfer_methods(
    fx: FileExchange,
    *,
    transport: str,
    base_url: str | None,
) -> dict[str, dict[str, str]]:
    """Build the ``transfer_methods`` dict advertised in the capability.

    ``exchange`` is included only when ``fx.is_configured``; ``http``
    is included only when an HTTP transport is in use AND ``base_url``
    is set, because ``create_download_link`` is the http-side handle
    and that tool itself is gated on the same conditions.

    The two methods are independent — a stdio server with
    ``MCP_EXCHANGE_DIR`` set advertises ``exchange`` only, an HTTP
    server without exchange advertises ``http`` only.
    """
    methods: dict[str, dict[str, str]] = {}
    if fx.is_configured:
        methods["exchange"] = {}
    if transport != "stdio" and base_url:
        methods["http"] = {"tool": "create_download_link"}
    return methods
