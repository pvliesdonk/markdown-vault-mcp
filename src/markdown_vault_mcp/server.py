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
    ServerConfig,  # noqa: F401  — re-exported for downstream projects' convenience
    apply_tool_visibility,
    build_auth,
    build_event_store,  # noqa: F401  — re-exported for downstream projects' convenience
    build_kv_store,  # noqa: F401  — re-exported for downstream projects' convenience
    configure_logging_from_env,
    configure_task_backend,
    env,
    finalize_instructions,
    instructions_for,
    register_server_info_tool,
    resolve_auth_mode,
    wire_middleware_stack,
)

from markdown_vault_mcp._server_apps import register_apps
from markdown_vault_mcp._server_deps import server_lifespan
from markdown_vault_mcp._server_prompts import register_prompts
from markdown_vault_mcp._server_resources import register_resources
from markdown_vault_mcp._server_tools import register_tools
from markdown_vault_mcp.config import ProjectConfig

logger = logging.getLogger(__name__)

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"


def make_server(
    *,
    transport: str = "stdio",
    config: ProjectConfig | None = None,
) -> FastMCP:
    """Construct the Markdown Vault MCP FastMCP server.

    Args:
        transport: ``"stdio"`` / ``"http"`` / ``"sse"``.  Gates any
            transport-specific wiring added in the DOMAIN-WIRING block
            (e.g. HTTP-only custom routes, which cannot be served under
            stdio) and appears as ``transport=%s`` in the startup log.
        config: Optional pre-loaded config; default loads from env.

    Returns:
        A configured :class:`fastmcp.FastMCP` instance.
    """
    config = config or ProjectConfig.from_env()
    configure_logging_from_env()

    # Background-task backend (SEP-1686 / Docket).  Unconditional and
    # template-owned: pydocket ships in fastmcp-pvl-core's base dependencies,
    # so the backend is always configurable, and whether this server actually
    # uses tasks is decided by registering ``task=True`` tools — not by
    # packaging or by an opt-in switch here.  It mutates fastmcp's
    # process-global settings, which fastmcp reads lazily at root-lifespan
    # entry, so doing it inside ``make_server`` covers both CLI paths (
    # ``server.run(...)`` and the uvicorn ``http_app()`` one).
    # ``MARKDOWN_VAULT_MCP_TASKS_URL`` selects the backend; unset, a
    # ``redis://`` ``MARKDOWN_VAULT_MCP_KV_STORE_URL`` is reused so one URL
    # configures every stateful subsystem, and otherwise fastmcp's
    # ``memory://`` default applies.  The queue name is derived from the env
    # prefix, so two servers sharing one Redis do not share a queue.
    configure_task_backend(_ENV_PREFIX, config.server)

    # Operator override: SERVER_NAME renames this instance (falls back when
    # unset/empty).  Instructions are composed by pvl-core's InstructionsBuilder
    # below and finalised last; see finalize_instructions() at the end.
    server_name = env(_ENV_PREFIX, "SERVER_NAME", "markdown-vault-mcp")

    auth = build_auth(config.server)
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
        "Server config: version=%s name=%s transport=%s auth=%s",
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

    wire_middleware_stack(mcp)

    # Server instructions are composed, not templated: every contributor adds
    # a snippet to the builder (identity here; core register_* helpers add
    # their workflow prose; domain code adds its own via
    # ``instructions_for(mcp).add(text, priority=WORKFLOWS, tools=(...))`` in
    # the DOMAIN-WIRING block, using the ``IDENTITY < DOCS < CAPABILITIES <
    # WORKFLOWS < INSTANCE < OPERATOR`` anchors pvl-core exports — never
    # ``priority=0``, which is ``IDENTITY`` and must stay unique), and
    # ``finalize_instructions`` renders them once, after tool visibility.
    instructions_for(mcp).identity("Generic markdown vault MCP with hybrid search")
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

    # DOMAIN-WIRING-START — project-specific wiring (custom HTTP routes,
    # transforms, mode toggles, alternative middleware, additional registrations);
    # kept across copier update. Leave empty for projects that don't customise
    # make_server() beyond the standard scaffold.
    from markdown_vault_mcp._http_logging import quiet_http_loggers
    from markdown_vault_mcp._icons import _SERVER_ICON
    from markdown_vault_mcp._instructions import contribute_instructions
    from markdown_vault_mcp._server_prompts import register_domain_prompts
    from markdown_vault_mcp.domain import set_pending_config

    is_read_only = config.read_only

    # Config-dependent prompts (create_from_template + user prompts): registered
    # here, not at the template-mandated no-arg register_prompts(mcp) above, so
    # their folders come from this already-loaded config rather than a second env
    # read (#609). User prompts silently override the built-ins registered above.
    register_domain_prompts(
        mcp,
        config.content.templates_folder,
        config.content.prompts_folder,
        summarize_tool_available=config.summarize.has_provider(),
    )

    # Hand the already-loaded config to the no-arg server_lifespan's Service so it
    # builds the vault from this config, not a second from_env() read (#609).
    set_pending_config(config)
    # Quiet httpx/httpcore per-request INFO on the serve path (#792); the CLI's
    # index/search/reindex commands do the same before their own vault builds.
    quiet_http_loggers()

    # Domain server identity: attach the vault icon. Applied post-construction
    # so make_server()'s body stays byte-identical to the template skeleton.
    # FastMCP's ``icons`` property is read-only (only the constructor accepts
    # icons, which the skeleton body does not), so write the low-level server
    # field it reads.
    mcp._mcp_server.icons = _SERVER_ICON

    # Contribute the read-only-aware, conventions-aware domain guidance to
    # pvl-core's instructions builder. Nothing is rendered here: the skeleton
    # body's finalize_instructions() call, after apply_tool_visibility(), is
    # the single point that serialises every contributor's snippets, prunes
    # those naming a tool the operator hid, appends
    # MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA, and honours the legacy
    # MARKDOWN_VAULT_MCP_INSTRUCTIONS full replacement. Assigning
    # mcp.instructions here instead would be dead: finalize overwrites it.
    contribute_instructions(
        mcp,
        read_only=is_read_only,
        conventions_file=config.content.conventions_file,
        summarize_note_limit=(
            config.summarize.max_notes if config.summarize.has_provider() else None
        ),
        okf_mode=config.content.okf_mode,
    )

    # Honor the passed config's server name the same way as instructions/icons.
    # The skeleton body sources the client-facing name from the SERVER_NAME env
    # var, which config.server_name mirrors for from_env configs; a
    # programmatically-built config can diverge. Act only when they differ (a
    # no-op on the from_env path). FastMCP.name is read-only, so write the
    # low-level field. get_server_info is intentionally NOT re-registered here:
    # it is registered once in the skeleton body (with the DOMAIN-UPSTREAM
    # block), so re-registering would be a second, silently-diverging call site
    # for that upstream wiring. Its reported server_name stays the SERVER_NAME
    # env identity (a deployment-verification value that matches on the from_env
    # path), while the live instance name honors the passed config.
    if config.server_name != server_name:
        mcp._mcp_server.name = config.server_name

    logger.info(
        "vault_startup mode=%s vault=%s embeddings=%s",
        "read-only" if is_read_only else "read-write",
        config.source_dir,
        "enabled" if config.indexing.embeddings_path else "disabled",
    )

    # GitHub webhook endpoint — only when secret is configured and transport
    # is HTTP/SSE (stdio has no HTTP server to receive POST requests).
    if config.sync.github_webhook_secret and transport != "stdio":
        from markdown_vault_mcp._github_webhook import make_webhook_handler

        if config.git.repo_url is None and config.git.token is None:
            # The route still mounts and answers 200, but every delivery is a
            # no-op: this deployment has no managed remote to pull from.  Say
            # so at startup rather than leaving the operator to infer it from
            # per-delivery logs (#1128).
            logger.warning(
                "github_webhook_inert: GITHUB_WEBHOOK_SECRET is set but no "
                "managed git remote is configured, so push deliveries have "
                "nothing to pull — set GIT_REPO_URL to enable sync, or unset "
                "GITHUB_WEBHOOK_SECRET to drop the endpoint"
            )
        mcp.custom_route("/github-webhook", methods=["POST"])(
            make_webhook_handler(config.sync.github_webhook_secret)
        )

    # One-time capability-link transfer (#622, #979) via pvl-core's shared
    # framework. HTTP/SSE only and only with base_url set: the /transfer/{token}
    # route needs an HTTP server, and register_transfer_routes raises without a
    # public base URL. pvl-core owns the route, the KV-backed token store, and
    # the two generic create_*_link tools (their names, titles, hints, icons,
    # and the ``write`` tag on upload that the read-only disable pass below
    # honours); the domain supplies only the VaultTransferSink (note/attachment
    # read + write) and its validator. The optional notes add vault-specific
    # context to the generic tool descriptions without changing their shape.
    if transport != "stdio" and config.server.base_url:
        from fastmcp_pvl_core import register_transfer_routes

        from markdown_vault_mcp._transfer_sink import VaultTransferSink

        _transfer_sink = VaultTransferSink(config)
        register_transfer_routes(
            mcp,
            config.server,
            config.transfer,
            sink=_transfer_sink,
            validate=_transfer_sink.validate,
            download_note=(
                "For this server, ref is a vault-relative path to an existing "
                "note (a .md file) or attachment."
            ),
            upload_note=(
                "For this server, ref is the vault-relative destination path: a "
                "note (.md) or an attachment whose extension is in the "
                "configured allowlist."
            ),
        )

    # --- Visibility: hide write-tagged components in read-only mode ---
    if is_read_only:
        mcp.disable(tags={"write"})

    # Hide git-managed tools (e.g. git_sync) when not in managed git mode.
    # The two disable passes compose: a tool tagged {"write", "git-managed"}
    # is hidden if either condition fires (set-union on disabled tags).
    #
    # Check the config directly rather than constructing a strategy via
    # ``to_vault_kwargs(config)`` — that call builds an embedding
    # provider (slow, GBs of memory) and may run ``git clone`` as a side
    # effect.  The runtime check inside the ``git_sync`` tool body
    # (``isinstance(strategy, GitWriteStrategy) and strategy._managed``)
    # stays aligned with this gate via the same ``config.git.repo_url``
    # value: managed mode requires an explicit remote URL.  See #220 for
    # the broader cleanup of duplicate ``to_vault_kwargs`` calls.
    if config.git.repo_url is None:
        mcp.disable(tags={"git-managed"})

    # The dual-mode long-running tools (#1033) are registered here rather
    # than in the config-free register_tools() layer: the Jobs mechanics
    # their slow calls promote onto are built from this already-loaded
    # config (the register_domain_prompts pattern, #609). A client that
    # speaks MCP tasks gets native task execution; any other client past
    # the JOBS_SOFT_DEADLINE_S soft deadline gets a job handle to poll via
    # the generic get_job_result tool.
    from fastmcp_pvl_core import build_jobs, register_job_tools

    from markdown_vault_mcp._server_tools import index as index_tools
    from markdown_vault_mcp._server_tools import summarize as summarize_tools

    # With no KV backend configured, core's default (>=4.11.1) is
    # file:///data/state where that directory is usable (the Docker image)
    # and memory:// everywhere else (bare-metal/uvx installs, CI) — so this
    # needs no domain-side backend selection.
    jobs = build_jobs(config.server, config.jobs)
    summarize_tools.register(mcp, jobs)
    index_tools.register_index_jobs(mcp, jobs)
    register_job_tools(
        mcp,
        jobs,
        note=(
            "On this server, background jobs come from slow summarize, "
            "reindex, and build_embeddings calls."
        ),
    )

    # Hide the LLM-backed summarize tool unless a summarization backend is
    # configured (an OpenAI-compatible API key or base URL). Provider-neutral:
    # the check lives on config.summarize, never referencing a specific
    # provider. Checked directly
    # (not via to_vault_kwargs(), which builds an embedding provider and may
    # clone a git repo as a side effect — see the git-managed gate above).
    # The generic jobs poller stays visible either way: reindex and
    # build_embeddings produce job handles regardless of the summarize
    # backend.
    if not config.summarize.has_provider():
        mcp.disable(tags={"summarize"})
    else:
        # Substitute the live note limit into the tool description so calling
        # models can plan folder splits before their first call (#925).
        summarize_tools.apply_summarize_limits(
            mcp, max_notes=config.summarize.max_notes
        )

    # Hide MCP-Apps UI tools (browse_vault, show_context) when the client
    # does not render the MCP Apps panels. Set
    # MARKDOWN_VAULT_MCP_DISABLE_APPS_UI=true to remove them from the tool
    # listing (saves a few tokens; the LLM cannot call them anyway).
    if config.disable_apps_ui:
        mcp.disable(tags={"apps-ui"})
    if config.content.okf_mode == "off":
        # OKF semantics vetoed by the operator: hide the OKF tool surface
        # entirely (same pattern as the apps-ui toggle).
        mcp.disable(tags={"okf"})
    if not config.content.okf_write or config.content.okf_verify == "off":
        # The enforced-write layer (#964) is opt-in: hide its tool surface
        # (okf_verify) unless OKF_WRITE is enabled. Provenance stamping and
        # verification invalidation are behaviours gated in the write path,
        # not tools, so they need no disable pass here. OKF_VERIFY=off (#990)
        # hides okf_verify even with the layer on, so attestation happens
        # solely via external tooling beyond the model's reach.
        mcp.disable(tags={"okf-enforce"})
    # DOMAIN-WIRING-END

    # Operator tool visibility (MARKDOWN_VAULT_MCP_TOOLS_ALLOW /
    # MARKDOWN_VAULT_MCP_TOOLS_DENY) applies last: fastmcp resolves visibility
    # transforms in call order, so the operator's lists win over any
    # visibility calls in the wiring above, and pvl-core's zero-tools-exposed
    # diagnostic judges the full registered tool set.
    apply_tool_visibility(mcp, config.server)

    # Render the composed instructions exactly once, after visibility: a
    # snippet whose tools are hidden is dropped, MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA
    # is appended, and the legacy MARKDOWN_VAULT_MCP_INSTRUCTIONS full-replace
    # still wins (with a deprecation warning).  Must stay the last call that
    # touches tools or instructions.
    finalize_instructions(mcp, config.server, env_prefix=_ENV_PREFIX)

    return mcp
