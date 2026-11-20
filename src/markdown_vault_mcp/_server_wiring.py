"""The vault's project-specific wiring of ``make_server`` (DOMAIN-WIRING).

``server.py`` is template-owned: its ``DOMAIN-WIRING`` block keeps the
template's own example text and calls :func:`wire_domain` once, so the vault's
wiring lives here rather than in lines a ``copier update`` re-renders. The
steps run in the order the block used to run them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastmcp import FastMCP
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from fastmcp.tools import Tool
    from mcp import types as mt

    from markdown_vault_mcp.config import ProjectConfig

logger = logging.getLogger(__name__)


def wire_domain(mcp: FastMCP, config: ProjectConfig, transport: str) -> None:
    """Apply the vault's wiring to a server ``make_server`` has scaffolded.

    Args:
        mcp: The server being built.
        config: The configuration ``make_server`` loaded.
        transport: ``"stdio"`` / ``"http"`` / ``"sse"``; gates the HTTP-only
            routes.
    """
    from markdown_vault_mcp._webhooks import register_webhook_routes

    _register_scope_and_prompts(mcp, config)
    _hand_off_to_service(config, transport)
    _contribute_identity(mcp, config)

    # The provider is resolved later, in the lifespan, so this line can only
    # say what the configuration asks for; the provider's own load logs
    # whether it came up (#1708).
    logger.info(
        "vault_startup mode=%s vault=%s embeddings=%s",
        "read-only" if config.read_only else "read-write",
        config.source_dir,
        "configured"
        if config.indexing.vector_storage_configured
        or (config.embeddings.provider or "").strip()
        else "disabled",
    )

    # Push-webhook endpoints (#530, #1178).  The routes, their credential
    # gates and the startup warnings all live in _webhooks.py, which owns the
    # handlers too — one call keeps a second host from branching here.
    register_webhook_routes(mcp, config, transport)

    _register_transfer(mcp, config, transport)
    _hide_by_mode(mcp, config)
    _register_jobs(mcp, config)
    _hide_by_feature(mcp, config)


class _AuthorDiscovery(Middleware):
    """List ``okf_author`` only while the vault's OKF bundle is active.

    Whether a bundle is declared can change after startup, so the static
    visibility rules cannot decide it; each listing consults the live vault.
    """

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Drop the authoring tool from a listing while OKF is inactive."""
        from markdown_vault_mcp._tools._common import okf_active
        from markdown_vault_mcp.domain import get_vault
        from markdown_vault_mcp.exceptions import ConfigurationError

        tools = await call_next(context)
        if context.fastmcp_context is None:
            return tools
        try:
            if await okf_active(get_vault(context.fastmcp_context)):
                return tools
        except (ConfigurationError, RuntimeError):
            return tools  # no vault to consult: list as configured
        return [tool for tool in tools if "okf-author" not in tool.tags]


def _gate_author(mcp: FastMCP, config: ProjectConfig) -> None:
    """Hide ``okf_author`` unless configured, then unless the bundle is active."""
    if config.content.okf_capabilities.author:
        mcp.add_middleware(_AuthorDiscovery())
    else:
        mcp.disable(tags={"okf-author"})


def _register_scope_and_prompts(mcp: FastMCP, config: ProjectConfig) -> None:
    """Add the commit-scope middleware and the config-dependent prompts."""
    from markdown_vault_mcp._server_commit_scope import CommitScopeMiddleware
    from markdown_vault_mcp.prompts import register_domain_prompts

    # Group each tool call's writes into one commit (#1264). Registered even on
    # a read-only or non-git vault: the middleware only binds a contextvar and
    # closes the scope afterward, and the dispatcher ignores scopes when no
    # write callback is configured, so the cost is a contextvar set per call.
    mcp.add_middleware(CommitScopeMiddleware())

    # Config-dependent prompts (create_from_template + user prompts): registered
    # here, not at the template-mandated no-arg register_prompts(mcp), so their
    # folders come from this already-loaded config rather than a second env
    # read (#609). User prompts silently override the built-ins registered
    # before.
    register_domain_prompts(
        mcp,
        config.content.templates_folder,
        config.content.prompts_folder,
        summarize_tool_available=config.summarize.has_provider(),
    )


def _hand_off_to_service(config: ProjectConfig, transport: str) -> None:
    """Give the lifespan's Service this config and transport; quiet httpx."""
    from markdown_vault_mcp._http_logging import quiet_http_loggers
    from markdown_vault_mcp.domain import set_pending_config, set_pending_transport

    # Hand the already-loaded config to the no-arg server_lifespan's Service so
    # it builds the vault from this config, not a second from_env() read (#609).
    set_pending_config(config)
    # Same handoff for the transport, which is an argument here rather than a
    # config field: the Service needs it to tell a webhook that can deliver from
    # one whose route this transport never mounts, before deciding whether the
    # file watcher stands down (#1263).
    set_pending_transport(transport)
    # Quiet httpx/httpcore per-request INFO on the serve path (#792); the CLI's
    # index/search/reindex commands do the same before their own vault builds.
    quiet_http_loggers()


def _contribute_identity(mcp: FastMCP, config: ProjectConfig) -> None:
    """Attach the vault icon and contribute the domain guidance."""
    from markdown_vault_mcp._icons import _SERVER_ICON
    from markdown_vault_mcp._instructions import (
        GuidanceConfig,
        contribute_instructions,
    )

    # Domain server identity: attach the vault icon. Applied post-construction
    # so make_server()'s body stays byte-identical to the template skeleton.
    # FastMCP's ``icons`` property is read-only (only the constructor accepts
    # icons, which the skeleton body does not), so write the low-level server
    # field it reads.
    mcp._mcp_server.icons = _SERVER_ICON

    # Contribute the read-only-aware, conventions-aware domain guidance to
    # the instructions builder (``_instructions_builder``). Nothing is rendered here: the skeleton
    # body's finalize_instructions() call, after apply_tool_visibility(), is
    # the single point that serialises every contributor's snippets, prunes
    # those naming a tool the operator hid, appends
    # MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA, and honours the legacy
    # MARKDOWN_VAULT_MCP_INSTRUCTIONS full replacement. Assigning
    # mcp.instructions here instead would be dead: finalize overwrites it.
    contribute_instructions(
        mcp,
        GuidanceConfig(
            read_only=config.read_only,
            conventions_file=config.content.conventions_file,
            summarize_note_limit=(
                config.summarize.max_notes if config.summarize.has_provider() else None
            ),
            okf_mode=config.content.okf_mode,
            okf_write=config.content.okf_write,
            okf_capabilities=config.content.okf_capabilities,
            boot_reindex=config.boot_reindex,
        ),
    )


def _register_transfer(mcp: FastMCP, config: ProjectConfig, transport: str) -> None:
    """Mount the capability-link transfer routes on an HTTP transport."""
    # One-time capability-link transfer (#622, #979) via ``_transfer_links``.
    # HTTP/SSE only and only with base_url set: the /transfer/{token}
    # route needs an HTTP server, and register_transfer_routes raises without a
    # public base URL. ``_transfer_links`` owns the route, the KV-backed token
    # store, and the two generic create_*_link tools (their names, titles, hints, icons,
    # and the ``write`` tag on upload that the read-only disable pass
    # honours); the domain supplies only the VaultTransferSink (note/attachment
    # read + write) and its validator. The optional notes add vault-specific
    # context to the generic tool descriptions without changing their shape.
    if transport == "stdio" or not config.server.base_url:
        return
    from markdown_vault_mcp._transfer_links import register_transfer_routes
    from markdown_vault_mcp._transfer_sink import VaultTransferSink

    transfer_sink = VaultTransferSink(config)
    register_transfer_routes(
        mcp,
        config.server,
        config.transfer,
        sink=transfer_sink,
        validate=transfer_sink.validate,
        download_note=(
            "For this server, ref is a vault-relative path to an existing "
            "note (a .md file) or attachment."
            # The bundle refs work only where the sink accepts them (#1695).
            + (
                ""
                if config.content.okf_mode == "off"
                else " Or pass okf-bundle for the whole vault as an Open "
                "Knowledge Format bundle (a zip); prefix a folder path with "
                "okf-bundle: (okf-bundle:projects) for one folder."
            )
        ),
        upload_note=(
            "ref is a vault-relative path for a note (.md) or an allowed "
            "attachment. "
            # Upload links take no if_match, so on a write-protected vault they
            # cannot prove a read and only create files.
            + (
                "It must not exist yet."
                if config.write_protect_existing
                else "An existing file at ref is replaced."
            )
        ),
    )


def _hide_by_mode(mcp: FastMCP, config: ProjectConfig) -> None:
    """Hide tools by mode and report inert OKF writes on a read-only vault."""
    # --- Visibility: hide write-tagged components in read-only mode ---
    if config.read_only:
        mcp.disable(tags={"write"})
        # Shared writable and read-only configurations remain valid (#1434).
        if config.content.okf_write:
            logger.info(
                "okf_write_inert reason=read_only okf_verify=%s",
                config.content.okf_verify,
            )

    # Hide git-managed tools (e.g. git_sync) when not in managed git mode.
    # The two disable passes compose: a tool tagged {"write", "git-managed"}
    # is hidden if either condition fires (set-union on disabled tags).
    #
    # Check the config directly rather than constructing a strategy via
    # ``to_vault_instances(config)`` — that call builds an embedding
    # provider (slow, GBs of memory) and may run ``git clone`` as a side
    # effect.  The runtime check inside the ``git_sync`` tool body
    # (``isinstance(strategy, Syncer) and strategy.is_managed``)
    # stays aligned with this gate via the same ``config.git.repo_url``
    # value: managed mode requires an explicit remote URL.  See #220 for
    # the broader cleanup of duplicate assembly calls.
    if config.git.repo_url is None:
        mcp.disable(tags={"git-managed"})


def _register_jobs(mcp: FastMCP, config: ProjectConfig) -> None:
    """Register the dual-mode long-running tools and the job poller."""
    # The dual-mode long-running tools (#1033) are registered here rather
    # than in the config-free register_tools() layer: the Jobs mechanics
    # their slow calls promote onto are built from this already-loaded
    # config (the register_domain_prompts pattern, #609). A client that
    # speaks MCP tasks gets native task execution; any other client past
    # the JOBS_SOFT_DEADLINE_S soft deadline gets a job handle to poll via
    # the generic get_job_result tool.
    from markdown_vault_mcp._job_tools import register_job_tools
    from markdown_vault_mcp._jobs import build_jobs
    from markdown_vault_mcp._tools import index as index_tools
    from markdown_vault_mcp._tools import summarize as summarize_tools

    # With no KV backend configured, build_kv_store's default is
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


def _hide_by_feature(mcp: FastMCP, config: ProjectConfig) -> None:
    """Hide summarize, the Apps UI tools and the OKF tools when off."""
    from markdown_vault_mcp._tools import summarize as summarize_tools

    # Hide the LLM-backed summarize tool unless a summarization backend is
    # configured (an OpenAI-compatible API key or base URL). Provider-neutral:
    # the check lives on config.summarize, never referencing a specific
    # provider. Checked directly
    # (not via to_vault_instances(), which builds an embedding provider and may
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

    if not config.okf_log_assist or not config.summarize.has_provider():
        mcp.disable(tags={"okf-log-assist"})

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
    _gate_author(mcp, config)
    if config.content.okf_capabilities.verify == "off":
        # Verification is configurable independently of authoring.
        mcp.disable(tags={"okf-enforce"})
