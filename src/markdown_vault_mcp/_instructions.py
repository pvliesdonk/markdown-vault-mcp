"""Domain-specific server-instructions contributions.

pvl-core 5 composes server instructions from snippets rather than from one
templated string: every contributor calls
:func:`fastmcp_pvl_core.instructions_for` and adds its prose with a priority
and the tool names it depends on, and
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

from fastmcp_pvl_core import IDENTITY, INSTANCE, WORKFLOWS, instructions_for

from markdown_vault_mcp._write_tools import gated_tool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastmcp import FastMCP

#: Priority for the vault prelude: immediately after the identity line the
#: template contributes, and before the documentation pointer at ``DOCS``.
#: Deliberately *not* ``IDENTITY`` — pvl-core requires exactly one snippet at
#: that priority and raises ``ConfigurationError`` on a second, whichever
#: method added it.
PRELUDE = IDENTITY + 10

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
        priority: The pvl-core sort anchor this fragment is added at.
        tools: Names of the tools the fragment tells the model to call. When
            any of them is unregistered or hidden by the operator's
            ``TOOLS_ALLOW`` / ``TOOLS_DENY`` lists, pvl-core drops the whole
            fragment at finalize — so a name belongs here only when its
            absence would make the fragment actively wrong, not when the
            fragment merely mentions it or offers it as an alternative.
    """

    text: str
    priority: int
    tools: tuple[str, ...] = field(default=())


def _domain_snippets(
    *,
    read_only: bool,
    conventions_file: str | None = None,
    summarize_note_limit: int | None = None,
    okf_mode: str = "off",
) -> list[Snippet]:
    """Select the domain guidance fragments that apply, in composition order.

    Only fragments that apply to this configuration are returned; a fragment
    that does not apply is absent from the list rather than present as an
    empty string.

    Configuration gates stay Python conditionals here rather than becoming
    ``tools`` declarations: pvl-core's pruning models the operator
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
    ``read_only`` is set, and ``DocumentManager`` raises ``ReadOnlyError``
    underneath. The sentence is therefore true here, so it is kept and owned
    domain-side (#1114).

    The folder-conventions sentence is emitted whenever *conventions_file*
    is configured, not gated on convention files existing on disk: in
    managed-git mode the clone happens inside the server lifespan, after
    instructions are composed, so a file-presence check here would silently
    miss on first boot.

    Args:
        read_only: Whether write tools are disabled; selects the write-guidance
            sentence and the read-only/read-write announcement, both of which
            this function owns.
        conventions_file: The configured per-folder conventions filename, or
            ``None`` when conventions are not configured.
        summarize_note_limit: The configured summarize note limit, surfaced so
            calling models can plan folder splits before their first call
            (#925), or ``None`` when the summarize tool is not configured —
            in which case the guidance points clients at the client-side
            ``summarize-subtree`` prompt instead (#1035).
        okf_mode: The configured OKF mode (``"auto"`` / ``"off"`` / ``"on"``).
            Any mode other than ``"off"`` emits the OKF guidance sentence.
            Like the conventions sentence, it is emitted when the mode
            *permits* detection rather than gated on the ``okf_version``
            marker existing on disk — in managed-git mode the clone happens
            after instructions are composed — so the sentence is phrased
            conditionally.

    Returns:
        The applicable guidance fragments, in the order they are composed.
    """
    snippets = [
        Snippet(
            "A searchable markdown document vault. "
            "Paths are always relative (e.g. 'Journal/note.md').",
            PRELUDE,
        )
    ]
    if not read_only:
        snippets.append(
            Snippet(
                "Write tools: use 'write' to create, 'edit' for targeted changes "
                "(read first), 'append' to add to the end of a note (no read "
                "needed), 'rename' to move (pass update_links=True to fix links "
                "in other notes), 'delete' to remove. Use 'move_folder(old_dir, "
                "new_dir)' to move an entire folder subtree and rewrite all vault "
                "links in one call. All write operations update the "
                "search index immediately — never call 'reindex' after write, edit, "
                "append, delete, or rename.",
                WORKFLOWS,
                _WRITE_SNIPPET_TOOLS,
            )
        )
    snippets.append(
        Snippet(
            "Use 'search' (mode='hybrid' preferred when available) to find documents, "
            "'read' for full content, 'list_documents' to enumerate, 'stats' to check "
            "capabilities. 'browse_vault' and 'show_context' open a visual UI for the "
            "user — do not call them to retrieve vault content; use 'search', 'read', "
            "'list_documents', or 'get_context' instead.",
            WORKFLOWS,
            ("search", "read", "list_documents", "stats"),
        )
    )
    snippets.append(
        Snippet(
            "No summarization backend is configured, so there is no "
            "'summarize' tool; for multi-note or folder summaries use the "
            "'summarize-subtree' prompt, which summarizes in batches "
            "(delegated to subagents when available) instead of pulling "
            "every note into your context.",
            WORKFLOWS,
        )
        if summarize_note_limit is None
        else Snippet(
            f"The 'summarize' tool reads at most {summarize_note_limit} "
            "notes per call; for a folder larger than that (check with "
            "'get_toc'), call it once per subfolder and combine the results.",
            WORKFLOWS,
            ("summarize", "get_toc"),
        )
    )
    if conventions_file is not None:
        snippets.append(
            Snippet(
                f"Folders may carry authoring conventions in '{conventions_file}' "
                "files; call 'get_conventions(path)' before creating or "
                "restructuring notes, and follow any 'conventions' returned by "
                "write/edit results.",
                WORKFLOWS,
                ("get_conventions",),
            )
        )
    if okf_mode != "off":
        snippets.append(
            Snippet(
                "When the vault is an OKF bundle (Open Knowledge Format — "
                "'okf_version' declared in the root 'index.md'; check the 'okf' "
                "section of 'stats'), search/read results carry an 'okf' key with "
                "each note's type, lifecycle status, staleness, and trust tier "
                "(unverified / machine-confirmed / human-reviewed) — weigh "
                "deprecated, stale, or unverified notes accordingly. When editing "
                "such a vault, follow OKF conventions: record changes in 'log.md', "
                "keep 'index.md' listings current, and prefer root-relative "
                "markdown links for new cross-references.",
                WORKFLOWS,
            )
        )
    # Enforced here, so announced here — see above. ``INSTANCE`` places it
    # after every workflow fragment and before the operator's
    # ``MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA`` at ``OPERATOR``, preserving
    # the order pvl-core produced before 4.11.2: domain prose, then the mode
    # line, then operator context.
    snippets.append(
        Snippet(
            "This instance is READ-ONLY — write tools are not available."
            if read_only
            else "This instance is READ-WRITE — write tools are available.",
            INSTANCE,
        )
    )
    return snippets


def contribute_instructions(
    mcp: FastMCP,
    *,
    read_only: bool,
    conventions_file: str | None = None,
    summarize_note_limit: int | None = None,
    okf_mode: str = "off",
) -> None:
    """Add this server's guidance to *mcp*'s instructions builder.

    Call before :func:`fastmcp_pvl_core.finalize_instructions`, which renders
    the collection once and freezes the builder. Adds no identity snippet:
    ``server.py`` contributes the single one pvl-core requires, and a second
    at ``IDENTITY`` would raise ``ConfigurationError`` at finalize.

    Args:
        mcp: The server whose builder to contribute to.
        read_only: Whether write tools are disabled.
        conventions_file: The configured per-folder conventions filename, or
            ``None`` when conventions are not configured.
        summarize_note_limit: The configured summarize note limit, or ``None``
            when the summarize tool is not configured.
        okf_mode: The configured OKF mode (``"auto"`` / ``"off"`` / ``"on"``).
    """
    builder = instructions_for(mcp)
    for snippet in _domain_snippets(
        read_only=read_only,
        conventions_file=conventions_file,
        summarize_note_limit=summarize_note_limit,
        okf_mode=okf_mode,
    ):
        builder.add(snippet.text, priority=snippet.priority, tools=snippet.tools)
