"""The vault's project-specific wiring of ``make_server`` (DOMAIN-WIRING).

``server.py`` is template-owned: its ``DOMAIN-WIRING`` block keeps the
template's own example text and calls :func:`wire_domain` once, so the vault's
wiring lives here rather than in lines a ``copier update`` re-renders. The
steps run in the order the block used to run them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

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

    logger.info(
        "vault_startup mode=%s vault=%s embeddings=%s",
        "read-only" if config.read_only else "read-write",
        config.source_dir,
        "enabled" if config.indexing.embeddings_path else "disabled",
    )

    # Push-webhook endpoints (#530, #1178).  The routes, their credential
    # gates and the startup warnings all live in _webhooks.py, which owns the
    # handlers too — one call keeps a second host from branching here.
    register_webhook_routes(mcp, config, transport)

    _register_transfer(mcp, config, transport)
    _hide_by_mode(mcp, config)
    _register_jobs(mcp, config)
    _hide_by_feature(mcp, config)


def _register_scope_and_prompts(mcp: FastMCP, config: ProjectConfig) -> None:
    """Add the commit-scope middleware and the config-dependent prompts."""
    from markdown_vault_mcp._commit_scope import CommitScopeMiddleware
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
    # pvl-core's instructions builder. Nothing is rendered here: the skeleton
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
        ),
    )


def _register_transfer(mcp: FastMCP, config: ProjectConfig, transport: str) -> None:
    """Mount the capability-link transfer routes on an HTTP transport."""
    # One-time capability-link transfer (#622, #979) via pvl-core's shared
    # framework. HTTP/SSE only and only with base_url set: the /transfer/{token}
    # route needs an HTTP server, and register_transfer_routes raises without a
    # public base URL. pvl-core owns the route, the KV-backed token store, and
    # the two generic create_*_link tools (their names, titles, hints, icons,
    # and the ``write`` tag on upload that the read-only disable pass
    # honours); the domain supplies only the VaultTransferSink (note/attachment
    # read + write) and its validator. The optional notes add vault-specific
    # context to the generic tool descriptions without changing their shape.
    if transport == "stdio" or not config.server.base_url:
        return
    from fastmcp_pvl_core import register_transfer_routes

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
        ),
        upload_note=(
            "ref is a vault-relative note (.md) or allowed attachment path. "
            "Requires a new file unless WRITE_PROTECT_EXISTING=false; "
            "upload links have no if_match."
        ),
    )


def _hide_by_mode(mcp: FastMCP, config: ProjectConfig) -> None:
    """Hide write tools on a read-only vault and git_sync outside managed git."""
    # --- Visibility: hide write-tagged components in read-only mode ---
    if config.read_only:
        mcp.disable(tags={"write"})

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
    from fastmcp_pvl_core import build_jobs, register_job_tools

    from markdown_vault_mcp._tools import index as index_tools
    from markdown_vault_mcp._tools import summarize as summarize_tools

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
