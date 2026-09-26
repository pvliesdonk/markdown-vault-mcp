"""Markdown Vault MCP — FastMCP server entry point.

Composes the primitives from ``fastmcp-pvl-core`` into a
project-specific ``make_server()``.  See
https://gofastmcp.com/servers for the FastMCP server surface and
``fastmcp-pvl-core``'s README for the composable helpers used below.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastmcp import FastMCP
from fastmcp_pvl_core import (
    HealthCheck,
    ServerConfig,  # noqa: F401  — re-exported for downstream projects' convenience
    apply_tool_visibility,
    build_auth,
    build_event_store,  # noqa: F401  — re-exported for downstream projects' convenience
    build_kv_store,  # noqa: F401  — re-exported for downstream projects' convenience
    configure_logging_from_env,
    configure_task_backend,
    env,  # also used by DOMAIN-WIRING additions, so no new import is needed there
    finalize_instructions,
    get_current_auth_mode,
    instructions_for,
    normalise_http_path,
    register_health_routes,
    register_server_info_tool,
    wire_middleware_stack,
)

from markdown_vault_mcp._server_apps import register_apps
from markdown_vault_mcp._server_deps import (
    bind_config,
    server_lifespan,
)
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.prompts import register_prompts
from markdown_vault_mcp.resources import register_resources
from markdown_vault_mcp.tools import register_tools

logger = logging.getLogger(__name__)

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"


def make_server(
    *,
    transport: str = "stdio",
    config: ProjectConfig | None = None,
    http_path: str | None = None,
) -> FastMCP:
    """Construct the Markdown Vault MCP FastMCP server.

    Args:
        transport: ``"stdio"`` / ``"http"`` / ``"sse"``.  Gates any
            transport-specific wiring added in the DOMAIN-WIRING block
            (e.g. HTTP-only custom routes, which cannot be served under
            stdio), gates the template's own liveness and readiness
            routes, which register for ``"http"`` alone, and appears as
            ``transport=%s`` in the startup log.
        config: Optional pre-loaded config; default loads from env.  Bound
            to the server before the registrars run, so ``register_*``
            code reads it back with ``_server_deps.config_for(mcp)`` and
            handlers with ``Depends(get_config)``.
        http_path: The MCP mount path the caller will hand to
            ``http_app(path=...)``.  The health routes derive their prefix
            from it, so the CLI passes the value it resolved; unset, the
            same ``MARKDOWN_VAULT_MCP_HTTP_PATH``-or-``/mcp`` fallback the
            CLI uses applies, so a direct ``make_server(transport="http")``
            and ``serve --transport http`` publish the routes at the same
            place.

    Returns:
        A configured :class:`fastmcp.FastMCP` instance.
    """
    config = config or ProjectConfig.from_env()
    configure_logging_from_env(_ENV_PREFIX)
    mount_path = normalise_http_path(http_path or env(_ENV_PREFIX, "HTTP_PATH"))

    # One source for the name, so `FastMCP(name=...)` below and the shaped
    # instruction identity cannot disagree.  `ProjectConfig.server_name`
    # defaults to `MARKDOWN_VAULT_MCP_SERVER_NAME` (falling back to the project
    # name) via a default_factory, so the operator override still works
    # unchanged — but a config passed in programmatically now wins, which
    # reading the environment here would have ignored.  Instructions are
    # composed by pvl-core's InstructionsBuilder below and finalised last; see
    # finalize_instructions() at the end.
    server_name = config.server_name

    auth = build_auth(config.server)
    # pvl-core records the mode ``build_auth`` resolved and announces it as
    # ``auth_mode_resolved mode=… source=…`` (pvl-core#310); reading it back
    # here replaces a second ``resolve_auth_mode`` run and a second
    # announcement (#605).
    auth_mode = get_current_auth_mode() or "none"
    if transport == "stdio" and auth is not None:
        # FastMCP applies auth on the HTTP transports only; stdio inherits
        # the security of its local execution environment, so the provider
        # built above is never consulted there. Say so rather than let the
        # ``auth=`` field below read as enforcement (#617).
        logger.warning("auth_configured_but_stdio_skips_enforcement mode=%s", auth_mode)

    try:
        pkg_ver = _pkg_version("markdown-vault-mcp")
    except PackageNotFoundError:
        pkg_ver = "unknown"

    logger.info(
        "server_configured version=%s name=%s transport=%s auth=%s",
        pkg_ver,
        server_name,
        transport,
        auth_mode,
    )

    mcp = FastMCP(
        name=server_name,
        lifespan=server_lifespan,
        auth=auth,
    )

    # Make the resolved config reachable from the registrars below without
    # threading it through their signatures, which are project-owned and
    # vary: ``config_for(mcp)`` at registration time, ``Depends(get_config)``
    # in a handler — both in ``_server_deps``.  A subsystem a registrar builds
    # from the environment instead silently disagrees with a ``config`` passed
    # in here (#534).
    bind_config(mcp, config)

    wire_middleware_stack(mcp)

    # Background-task backend (SEP-2663 / Docket).  Unconditional and
    # template-owned: fastmcp-tasks (and pydocket with it) ships in
    # fastmcp-pvl-core's base dependencies, so the backend is always
    # configurable, and whether this server actually uses tasks is decided
    # by registering ``task=True`` tools — not by packaging or by an opt-in
    # switch here.  The helper registers the SEP-2663 tasks extension on
    # ``mcp`` with the resolved backend — fastmcp refuses to start a server
    # carrying task-enabled tools without one — so doing it inside
    # ``make_server`` covers both CLI paths (``server.run(...)`` and the
    # uvicorn ``http_app()`` one).
    # ``MARKDOWN_VAULT_MCP_TASKS_URL`` selects the backend; unset, a
    # ``redis://`` ``MARKDOWN_VAULT_MCP_KV_STORE_URL`` is reused so one URL
    # configures every stateful subsystem, and otherwise the ``memory://``
    # default applies.  The queue name is derived from the env prefix, so
    # two servers sharing one Redis do not share a queue.
    configure_task_backend(mcp, _ENV_PREFIX, config.server)

    # Server instructions are composed, not templated: every contributor adds
    # a snippet to the builder (identity here; core register_* helpers add
    # their workflow prose; domain code adds its own via
    # ``instructions_for(mcp).add(text, role=InstructionRole.WORKFLOWS,
    # requires_tools=(...))`` in the DOMAIN-WIRING block. General contributors
    # may use only INSTANCE, CAPABILITIES, and WORKFLOWS; pvl-core reserves the
    # shaped identity, operator routing/policy, and documentation roles.
    # ``finalize_instructions`` renders them once, after tool visibility.
    instructions_for(mcp).identity(
        server_name, "Generic markdown vault MCP with hybrid search"
    )
    # The docs site publishes llms.txt per version (mkdocs-llmstxt, mike);
    # `/latest/` resolves once the first release has published the site.
    instructions_for(mcp).documentation(
        "https://pvliesdonk.github.io/markdown-vault-mcp/latest/llms.txt"
    )

    register_tools(mcp)
    register_resources(mcp)
    register_prompts(mcp)
    register_apps(mcp)

    register_server_info_tool(
        mcp,
        server_name=server_name,
        server_version=pkg_ver,
        # DOMAIN-UPSTREAM-START — wire upstream version reporting for servers
        # that talk to a remote service (paperless-mcp, etc.). The provider is
        # a zero-arg callable; the simplest pattern is a module-level upstream
        # client (typically constructed from env vars at import time) whose
        # version method is referenced here. ``CurrentContext()`` is a FastMCP
        # DI marker — it only resolves to a live context when used as a
        # parameter default in a tool/resource handler, so it cannot be called
        # directly from a zero-arg provider.
        # Uncomment the kwargs below as additional arguments to this call:
        # upstream_version=lambda: _upstream_client.remote_version(),
        # upstream_label="paperless",
        # DOMAIN-UPSTREAM-END
    )

    # Readiness checks for ``<prefix>/health/ready``, registered below once
    # the DOMAIN-WIRING block has had its say.  pvl-core contributes the
    # ``kv_store`` write probe itself; this dict is the domain hook, filled
    # from inside the block (the name ``kv_store`` is reserved).  A check is
    # a zero-arg callable, sync or async, answering "does this make the
    # server unable to serve" — falsy or raising means not-ready and the
    # route answers 503.  Nothing about *how* it answers is prescribed:
    # a cached flag a background task keeps fresh is as valid as a live
    # round-trip, and anything touching the network should be async so the
    # five-second ceiling applies.  A partial degradation you would rather
    # report than be taken out of rotation for belongs in ``get_server_info``.
    # Two shapes that both fit:
    #
    #   health_checks["upstream_key"] = lambda: _keepalive.last_ok   # cached flag
    #   health_checks["index"] = _index.is_loaded                    # async probe
    health_checks: dict[str, HealthCheck] = {}

    # DOMAIN-WIRING-START — project-specific wiring (custom HTTP routes,
    # transforms, mode toggles, alternative middleware, additional registrations);
    # kept across copier update. Leave empty for projects that don't customise
    # make_server() beyond the standard scaffold.
    #
    # -- Transfer subsystem (capability-link upload + download) ----------------
    #
    # Wiring the /transfer/{token} route needs HTTP transport (the route cannot
    # be served under stdio) and, at build time, base_url — pvl-core raises
    # ConfigurationError when it is unset, so gate only on the transport and let
    # that error surface a misconfigured deployment rather than silently
    # dropping the feature. Requires fastmcp-pvl-core >= 4.8.0.
    #
    # First compose a TransferConfig into ProjectConfig (config.py): add
    # ``TransferConfig`` to its ``from fastmcp_pvl_core import (...)`` block, then
    # a ``transfer: TransferConfig = field(default_factory=TransferConfig)`` field
    # in CONFIG-FIELDS and ``transfer=TransferConfig.from_env(_ENV_PREFIX),`` in
    # CONFIG-FROM-ENV. The second line is required — without it the
    # MARKDOWN_VAULT_MCP_TRANSFER_* env vars are ignored and the defaults always win.
    #
    # Path 1 — the generic tools, the common case. Registers create_download_link
    # and create_upload_link with pvl-core's shared metadata (names, icons, tags):
    #
    # if transport != "stdio":
    #     from fastmcp_pvl_core import register_transfer_routes
    #
    #     register_transfer_routes(
    #         mcp,
    #         config.server,
    #         config.transfer,          # TransferConfig composed into ProjectConfig
    #         sink=_my_transfer_sink,   # implements TransferSink (read/write)
    #         validate=_my_validator,   # TransferValidator: (ref, kind) -> handle
    #         # download_note/upload_note (optional) append a domain sentence to
    #         # the generic tool descriptions — context only, no shape change.
    #     )
    #
    # _my_validator rejects a ref only by raising
    # ToolError(msg, log_level=logging.INFO), and the model reads msg. Any other
    # exception, a ValueError included, is reported to the model as a server
    # fault ("the request was fine; retry later").
    #
    # Path 2 — your own tool over the same capability-link machinery, when the
    # generic pair cannot express it (a different name, a domain-accurate
    # description, domain-specific parameters). build_transfer_links mounts the
    # route and returns a minter, registering no tools; your tool validates the
    # caller ref itself, then mints over the already-validated sink handle:
    #
    # if transport != "stdio":
    #     from fastmcp_pvl_core import (
    #         add_transfer_workflow,
    #         build_transfer_links,
    #         tool_boundary,
    #     )
    #
    #     links = build_transfer_links(
    #         mcp, config.server, config.transfer, sink=_my_transfer_sink
    #     )
    #
    #     @mcp.tool
    #     @tool_boundary
    #     async def share_document(doc_id: str) -> dict[str, object]:
    #         """Mint a one-shot download link for a document."""
    #         handle = _resolve_and_check(doc_id)  # your validation -> sink handle
    #         return await links.mint_download(handle)
    #
    #     # Contribute the core's capability-link workflow prose for your tool
    #     # (dropped automatically if the tool is hidden by TOOLS_DENY):
    #     add_transfer_workflow(mcp, download_tool="share_document")
    #
    # The vault's own wiring lives in _server_wiring.py, so this block keeps
    # the template's text above.
    from markdown_vault_mcp._server_wiring import wire_domain

    wire_domain(mcp, config, transport)
    # DOMAIN-WIRING-END

    # Unauthenticated liveness (``<prefix>/health``, static 200) and readiness
    # (``<prefix>/health/ready``, 503 when any check fails) routes for a
    # container orchestrator; ``compose.yml`` probes the first.  They sit
    # outside the MCP mount and outside auth, which is what a probe needs.
    # ``<prefix>`` is the mount path minus a conventional trailing ``mcp``
    # segment, so the default ``/mcp`` publishes ``/health`` and
    # ``/scholar/mcp`` publishes ``/scholar/health`` — two servers on one
    # hostname never collide.  Registered for the http transport only: it is
    # the one the CLI mounts at ``http_path``, so it is the only one where
    # the derived prefix is a fact rather than a guess, and under stdio there
    # is no HTTP app at all.  ``MARKDOWN_VAULT_MCP_HEALTH_DETAIL`` (``status``
    # / ``standard`` / ``full``) decides how much the bodies say.
    if transport == "http":
        register_health_routes(
            mcp,
            config.server,
            server_version=pkg_ver,
            http_path=mount_path,
            env_prefix=_ENV_PREFIX,
            checks=health_checks,
        )

    # Operator tool visibility (MARKDOWN_VAULT_MCP_TOOLS_ALLOW /
    # MARKDOWN_VAULT_MCP_TOOLS_DENY) applies last: fastmcp resolves visibility
    # transforms in call order, so the operator's lists win over any
    # visibility calls in the wiring above, and pvl-core's zero-tools-exposed
    # diagnostic judges the full registered tool set.
    apply_tool_visibility(mcp, config.server)

    # Render the composed instructions exactly once, after visibility: a
    # snippet whose required tools are hidden is dropped,
    # MARKDOWN_VAULT_MCP_INSTANCE_DESCRIPTION supplies operator routing,
    # MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA supplies operator policy, and the
    # legacy MARKDOWN_VAULT_MCP_INSTRUCTIONS full replacement still wins with a
    # deprecation warning. Must run synchronously and stay the last call that
    # touches tools or instructions.
    finalize_instructions(mcp, config.server, env_prefix=_ENV_PREFIX)

    return mcp
