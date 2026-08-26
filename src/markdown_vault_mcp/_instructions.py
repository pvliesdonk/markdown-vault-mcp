"""Domain-specific server-instructions composition.

Holds :func:`contribute_instructions`, which adds the markdown-vault guidance
to pvl-core's per-server ``InstructionsBuilder`` from ``make_server()``'s
``DOMAIN-WIRING`` block, and :func:`build_default_instructions`, the pure
text builder behind it. Lives outside the template-owned ``server.py`` so the
domain prose never re-conflicts on a ``copier update``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp_pvl_core import WORKFLOWS, instructions_for

if TYPE_CHECKING:
    from fastmcp import FastMCP


def build_default_instructions(
    *,
    read_only: bool,
    conventions_file: str | None = None,
    summarize_note_limit: int | None = None,
    okf_mode: str = "off",
) -> str:
    """Build the domain guidance string based on read-only state.

    Returns the vault-specific prose that :func:`contribute_instructions`
    hands to pvl-core's ``InstructionsBuilder``; identity, the documentation
    pointer, and the operator env contract are owned by the template skeleton
    and ``finalize_instructions`` (pvl-core 5).

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
        The composed instructions string.
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
            "(read first), 'append' to add to the end of a note (no read "
            "needed), 'rename' to move (pass update_links=True to fix links "
            "in other notes), 'delete' to remove. Use 'move_folder(old_dir, new_dir)' "
            "to move an entire folder subtree and rewrite all vault links in one call. "
            "All write operations update the "
            "search index immediately — never call 'reindex' after write, edit, "
            "append, delete, or rename."
        )
    )
    search_guidance = (
        " Use 'search' (mode='hybrid' preferred when available) to find documents, "
        "'read' for full content, 'list_documents' to enumerate, 'stats' to check "
        "capabilities. 'browse_vault' and 'show_context' open a visual UI for the "
        "user — do not call them to retrieve vault content; use 'search', 'read', "
        "'list_documents', or 'get_context' instead."
    )
    conventions_guidance = (
        ""
        if conventions_file is None
        else (
            f" Folders may carry authoring conventions in '{conventions_file}' "
            "files; call 'get_conventions(path)' before creating or "
            "restructuring notes, and follow any 'conventions' returned by "
            "write/edit results."
        )
    )
    summarize_guidance = (
        (
            " No summarization backend is configured, so there is no "
            "'summarize' tool; for multi-note or folder summaries use the "
            "'summarize-subtree' prompt, which summarizes in batches "
            "(delegated to subagents when available) instead of pulling "
            "every note into your context."
        )
        if summarize_note_limit is None
        else (
            f" The 'summarize' tool reads at most {summarize_note_limit} "
            "notes per call; for a folder larger than that (check with "
            "'get_toc'), call it once per subfolder and combine the results."
        )
    )
    okf_guidance = (
        ""
        if okf_mode == "off"
        else (
            " When the vault is an OKF bundle (Open Knowledge Format — "
            "'okf_version' declared in the root 'index.md'; check the 'okf' "
            "section of 'stats'), search/read results carry an 'okf' key with "
            "each note's type, lifecycle status, staleness, and trust tier "
            "(unverified / machine-confirmed / human-reviewed) — weigh "
            "deprecated, stale, or unverified notes accordingly. When editing "
            "such a vault, follow OKF conventions: record changes in 'log.md', "
            "keep 'index.md' listings current, and prefer root-relative "
            "markdown links for new cross-references."
        )
    )
    # Enforced here, so announced here — see the docstring. Placed last so
    # the snippet keeps the established word order: domain prose, then the
    # mode line.
    write_line = (
        " This instance is READ-ONLY — write tools are not available."
        if read_only
        else " This instance is READ-WRITE — write tools are available."
    )
    return (
        f"{prelude}{write_guidance}{search_guidance}"
        f"{summarize_guidance}{conventions_guidance}{okf_guidance}{write_line}"
    )


def contribute_instructions(
    mcp: FastMCP,
    *,
    read_only: bool,
    conventions_file: str | None = None,
    summarize_note_limit: int | None = None,
    okf_mode: str = "off",
) -> None:
    """Add the domain guidance to *mcp*'s ``InstructionsBuilder``.

    One ``WORKFLOWS`` snippet carrying :func:`build_default_instructions`'s
    text. The snippet is composed from the same config gates that disable the
    tools it mentions (read-only, summarize backend, OKF mode) rather than
    tool-gated via ``tools=``: a per-tool gate would drop whole guidance
    sentences when a single named UI tool (e.g. ``browse_vault`` under
    ``DISABLE_APPS_UI``) is hidden. ``finalize_instructions`` in the skeleton
    body renders it after identity and the documentation pointer.

    Args:
        mcp: The server whose builder receives the snippet.
        read_only: Whether write tools are disabled.
        conventions_file: The configured per-folder conventions filename, or
            ``None`` when conventions are not configured.
        summarize_note_limit: The configured summarize note limit, or ``None``
            when the summarize tool is not configured.
        okf_mode: The configured OKF mode (``"auto"`` / ``"off"`` / ``"on"``).
    """
    instructions_for(mcp).add(
        build_default_instructions(
            read_only=read_only,
            conventions_file=conventions_file,
            summarize_note_limit=summarize_note_limit,
            okf_mode=okf_mode,
        ),
        priority=WORKFLOWS,
    )
