"""Domain-specific server-instructions contributions.

pvl-core 6 composes server instructions from snippets rather than from one
templated string: every contributor calls
:func:`fastmcp_pvl_core.instructions_for` and adds its prose with a semantic
role and the tool names it depends on, and
:func:`fastmcp_pvl_core.finalize_instructions` renders the collection once,
after tool visibility is resolved.

This module holds the markdown-vault half of that:
:func:`_domain_snippets` selects the guidance that applies to a
configuration, and :func:`contribute_instructions` — called by
:func:`~markdown_vault_mcp.server.make_server` from its ``DOMAIN-WIRING``
block — hands it to the builder. Living outside the template-owned
``server.py`` keeps the domain prose from re-conflicting on a
``copier update``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastmcp_pvl_core import InstructionRole, instructions_for

from markdown_vault_mcp._write_tools import gated_tool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastmcp import FastMCP

#: Tools the write-guidance fragment tells the model to call. Resolved
#: through :func:`~markdown_vault_mcp._write_tools.gated_tool` at import
#: time so a rename that updates ``WRITE_TOOL_NAMES`` fails loudly here
#: instead of leaving a stale name that silently prunes the fragment away
#: on every server.
_WRITE_SNIPPET_TOOLS = tuple(
    gated_tool(name)
    for name in ("write", "edit", "append", "rename", "delete", "move_folder")
)


@dataclass(frozen=True)
class Snippet:
    """One guidance fragment with the builder metadata it is added under.

    Attributes:
        text: Model-facing prose, with no leading or trailing whitespace.
        role: The semantic pvl-core instruction role for this fragment.
        requires_tools: Names of the tools the fragment tells the model to
            call. When any of them is unregistered or hidden by the operator's
            ``TOOLS_ALLOW`` / ``TOOLS_DENY`` lists, pvl-core drops the whole
            fragment at finalize — so a name belongs here only when its
            absence would make the fragment actively wrong, not when the
            fragment merely mentions it or offers it as an alternative.
    """

    text: str
    role: InstructionRole
    requires_tools: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class GuidanceConfig:
    """The configuration the domain guidance branches on.

    One object rather than a keyword per switch: every fragment below is
    selected by some combination of these, and the set grows each time the
    server gains a switch an agent has to be told about (``okf_write`` was
    the fifth, #1431). Passing them as a value keeps both entry points at one
    argument and makes "what does the guidance depend on?" a type rather than
    a signature to re-read.

    Attributes:
        read_only: Whether write tools are disabled.
        conventions_file: The configured per-folder conventions filename, or
            ``None`` when conventions are not configured.
        summarize_note_limit: The configured summarize note limit, or ``None``
            when the summarize tool is not configured.
        okf_mode: The configured OKF mode (``"auto"`` / ``"off"`` / ``"on"``).
        okf_write: Whether the enforced-write layer maintains the reserved
            files, which decides whether the agent is asked to.
    """

    read_only: bool
    conventions_file: str | None = None
    summarize_note_limit: int | None = None
    okf_mode: str = "off"
    okf_write: bool = False


def _domain_snippets(config: GuidanceConfig) -> list[Snippet]:
    """Select the domain guidance fragments that apply, in composition order.

    Only fragments that apply to this configuration are returned; a fragment
    that does not apply is absent from the list rather than present as an
    empty string.

    Configuration gates stay Python conditionals here rather than becoming
    ``requires_tools`` declarations: pvl-core's pruning models the operator
    allow/deny rule and registration, and explicitly *not* the
    ``mcp.disable(tags=...)`` transforms this server uses for read-only
    mode, managed-git mode, the summarize backend, the apps UI, and the
    two OKF layers (``okf`` and ``okf-enforce``) — all six of them. A
    disabled-but-registered tool still counts as exposed, so only the
    conditionals below can keep its guidance out.

    The read-only/read-write announcement is composed here rather than by
    pvl-core, which dropped it in 4.11.2 (pvl-core #222) because nothing in
    the library enforced it — a server could be told to announce read-only
    while every write tool stayed listable and callable. This server does
    enforce it: write-tagged components are never registered when
    ``config.read_only`` is set, and ``DocumentManager`` raises ``ReadOnlyError``
    underneath. The sentence is therefore true here, so it is kept and owned
    domain-side (#1114).

    The folder-conventions sentence is emitted whenever *config.conventions_file*
    is configured, not gated on convention files existing on disk: in
    managed-git mode the clone happens inside the server lifespan, after
    instructions are composed, so a file-presence check here would silently
    miss on first boot.

    ``read_only`` selects both the write-guidance sentence and the
    announcement above. ``summarize_note_limit`` is surfaced so calling models
    can plan folder splits before their first call (#925); without it the
    guidance points at the client-side ``summarize-subtree`` prompt instead
    (#1035). Any ``okf_mode`` other than ``"off"`` emits the OKF sentence, on
    the same permits-rather-than-detects reasoning as the conventions one, and
    ``okf_write`` decides which upkeep it asks for: without the enforced-write
    layer nobody else keeps ``log.md`` / ``index.md`` current, and with it the
    server regenerates a written folder's ``index.md``, so telling the agent
    to edit that file invites work the next write replaces (#1431).

    Args:
        config: The configuration the fragments below branch on.

    Returns:
        The applicable guidance fragments, in the order they are composed.
    """
    # Enforced here, so announced here — see above. Keep the deployment mode
    # first among INSTANCE facts because it is the most consequential routing
    # constraint for a caller.
    snippets = [
        Snippet(
            "This instance is READ-ONLY; write tools are not available."
            if config.read_only
            else "This instance is READ-WRITE; write tools are available.",
            InstructionRole.INSTANCE,
        )
    ]
    snippets.append(
        Snippet(
            "Notes use vault-relative paths. 'search' finds; 'read' returns full "
            "content; 'list_documents' enumerates; 'stats' reports capabilities. "
            "Omit 'mode' for automatic search; use 'keyword' for exact terms.",
            InstructionRole.CAPABILITIES,
            ("search", "read", "list_documents", "stats"),
        )
    )
    snippets.append(
        Snippet(
            "No 'summarize' tool is configured; use the 'summarize-subtree' "
            "prompt for multi-note or folder summaries.",
            InstructionRole.INSTANCE,
        )
        if config.summarize_note_limit is None
        else Snippet(
            f"'summarize' handles at most {config.summarize_note_limit} notes per call; "
            "split larger folders with 'get_toc'.",
            InstructionRole.INSTANCE,
            ("summarize", "get_toc"),
        )
    )
    if config.conventions_file is not None:
        snippets.append(
            Snippet(
                "Before changing or linking notes, call "
                "'get_conventions(path)'; follow it and write-result "
                f"'conventions' ('{config.conventions_file}' configured).",
                InstructionRole.INSTANCE,
                ("get_conventions",),
            )
        )
    if config.okf_mode != "off":
        # Who keeps the reserved files current decides what to tell the agent.
        # Under OKF_WRITE the server regenerates a written folder's 'index.md'
        # from the listing, so asking the agent to edit it invites work the
        # next write silently replaces (#1431).
        upkeep = (
            "Leave 'log.md'/'index.md' to the server."
            if config.okf_write
            else "For edits, update 'log.md'/'index.md'."
        )
        snippets.append(
            Snippet(
                "If 'stats.okf' reports an OKF bundle ('okf_version' in root "
                "'index.md'), discount deprecated, stale, or unverified notes by "
                f"trust tier. {upkeep} Use root-relative Markdown links.",
                InstructionRole.INSTANCE,
            )
        )
    if not config.read_only:
        snippets.append(
            Snippet(
                "Use 'write'/'edit'/'append' to change notes, "
                "'rename'/'move_folder' to move, and 'delete' to remove. Writes "
                "update the index; never call 'reindex' afterward.",
                InstructionRole.WORKFLOWS,
                _WRITE_SNIPPET_TOOLS,
            )
        )
    return snippets


def contribute_instructions(mcp: FastMCP, config: GuidanceConfig) -> None:
    """Add this server's guidance to *mcp*'s instructions builder.

    Call before :func:`fastmcp_pvl_core.finalize_instructions`, which renders
    the collection once and freezes the builder. Adds no identity snippet:
    ``server.py`` contributes the single shaped identity pvl-core requires.

    Args:
        mcp: The server whose builder to contribute to.
        read_only: Whether write tools are disabled.
        conventions_file: The configured per-folder conventions filename, or
            ``None`` when conventions are not configured.
        summarize_note_limit: The configured summarize note limit, or ``None``
            when the summarize tool is not configured.
        okf_mode: The configured OKF mode (``"auto"`` / ``"off"`` / ``"on"``).
        okf_write: Whether the enforced-write layer maintains the reserved
            files, which decides whether the agent is asked to.
    """
    builder = instructions_for(mcp)
    for snippet in _domain_snippets(config):
        builder.add(
            snippet.text,
            role=snippet.role,
            requires_tools=snippet.requires_tools,
        )
