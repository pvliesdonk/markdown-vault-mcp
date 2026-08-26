"""Domain-specific server-instructions composition.

Holds :func:`contribute_instructions`, which adds the markdown-vault guidance
to pvl-core's per-server ``InstructionsBuilder`` from ``make_server()``'s
``DOMAIN-WIRING`` block, and :func:`build_default_instructions`, the pure
text builder behind it (kept for direct assertions in tests). Lives outside
the template-owned ``server.py`` so the domain prose never re-conflicts on a
``copier update``.

Identity and the documentation pointer are template-owned (the skeleton body
calls ``instructions_for(mcp).identity(...)`` / ``.documentation(...)``, and
``finalize_instructions`` enforces exactly one identity), so the domain
contributes only capability and workflow prose. Single-tool caveats — e.g.
``browse_vault`` / ``show_context`` being UI panels, not retrieval tools —
live in those tools' own descriptions, not here: instructions carry only
what no single tool description can (#1162).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp_pvl_core import CAPABILITIES, INSTANCE, WORKFLOWS, instructions_for

if TYPE_CHECKING:
    from fastmcp import FastMCP

#: One snippet: (text, priority, tool names the snippet references).
_Snippet = tuple[str, int, tuple[str, ...]]


def _domain_snippets(
    *,
    read_only: bool,
    conventions_file: str | None,
    summarize_note_limit: int | None,
    okf_mode: str,
) -> list[_Snippet]:
    """Compose the domain guidance as ordered, tool-annotated snippets.

    Emission is gated on the same config values that disable the tools each
    snippet mentions (read-only, summarize backend, OKF mode) — pvl-core's
    pruning models only the operator allow-/denylist, not server-side
    ``mcp.disable(...)`` calls, so the config gates here are load-bearing.
    The declared tool names add the operator layer on top: a snippet naming
    an operator-hidden tool is dropped at finalize.

    The read-only/read-write announcement is composed here rather than by
    pvl-core, which dropped it in 4.11.2 (pvl-core #222) because nothing in
    the library enforced it — a server could be told to announce read-only
    while every write tool stayed listable and callable. This server does
    enforce it: write-tagged components are never registered when
    ``read_only`` is set, and ``DocumentManager`` raises ``ReadOnlyError``
    underneath. The sentence is therefore true here, so it is kept and owned
    domain-side (#1114), at ``INSTANCE`` — it is an enforced instance fact.

    The folder-conventions and OKF sentences are emitted whenever their
    config permits them, not gated on files existing on disk: in managed-git
    mode the clone happens inside the server lifespan, after instructions
    are composed, so a file-presence check here would silently miss on
    first boot.
    """
    snippets: list[_Snippet] = [
        (
            "A searchable markdown document vault. "
            "Paths are always relative (e.g. 'Journal/note.md').",
            CAPABILITIES,
            (),
        )
    ]
    if not read_only:
        snippets.append(
            (
                "Write tools: use 'write' to create, 'edit' for targeted "
                "changes (read first), 'append' to add to the end of a note "
                "(no read needed), 'rename' to move (pass update_links=True "
                "to fix links in other notes), 'delete' to remove. Use "
                "'move_folder(old_dir, new_dir)' to move an entire folder "
                "subtree and rewrite all vault links in one call. All write "
                "operations update the search index immediately — never call "
                "'reindex' after write, edit, append, delete, or rename.",
                WORKFLOWS,
                (
                    "write",
                    "edit",
                    "append",
                    "rename",
                    "delete",
                    "move_folder",
                    "reindex",
                ),
            )
        )
    snippets.append(
        (
            "Use 'search' (mode='hybrid' preferred when available) to find "
            "documents, 'read' for full content, 'list_documents' to "
            "enumerate, 'stats' to check capabilities.",
            WORKFLOWS,
            ("search", "read", "list_documents", "stats"),
        )
    )
    if summarize_note_limit is None:
        snippets.append(
            (
                "No summarization backend is configured, so there is no "
                "'summarize' tool; for multi-note or folder summaries use "
                "the 'summarize-subtree' prompt, which summarizes in batches "
                "(delegated to subagents when available) instead of pulling "
                "every note into your context.",
                WORKFLOWS,
                (),
            )
        )
    else:
        snippets.append(
            (
                f"The 'summarize' tool reads at most {summarize_note_limit} "
                "notes per call; for a folder larger than that (check with "
                "'get_toc'), call it once per subfolder and combine the "
                "results.",
                WORKFLOWS,
                ("summarize", "get_toc"),
            )
        )
    if conventions_file is not None:
        snippets.append(
            (
                f"Folders may carry authoring conventions in "
                f"'{conventions_file}' files; call 'get_conventions(path)' "
                "before creating or restructuring notes, and follow any "
                "'conventions' returned by write/edit results.",
                WORKFLOWS,
                ("get_conventions",),
            )
        )
    if okf_mode != "off":
        snippets.append(
            (
                "When the vault is an OKF bundle (Open Knowledge Format — "
                "'okf_version' declared in the root 'index.md'; check the "
                "'okf' section of 'stats'), search/read results carry an "
                "'okf' key with each note's type, lifecycle status, "
                "staleness, and trust tier (unverified / machine-confirmed / "
                "human-reviewed) — weigh deprecated, stale, or unverified "
                "notes accordingly. When editing such a vault, follow OKF "
                "conventions: record changes in 'log.md', keep 'index.md' "
                "listings current, and prefer root-relative markdown links "
                "for new cross-references.",
                WORKFLOWS,
                ("search", "read", "stats"),
            )
        )
    snippets.append(
        (
            "This instance is READ-ONLY — write tools are not available."
            if read_only
            else "This instance is READ-WRITE — write tools are available.",
            INSTANCE,
            (),
        )
    )
    return snippets


def build_default_instructions(
    *,
    read_only: bool,
    conventions_file: str | None = None,
    summarize_note_limit: int | None = None,
    okf_mode: str = "off",
) -> str:
    """Build the domain guidance text based on read-only state.

    Joins the snippets :func:`contribute_instructions` hands to pvl-core's
    ``InstructionsBuilder``, in contribution order, for tests that assert on
    the domain prose without composing a whole server. The live composed
    text additionally carries the identity line, documentation pointer, and
    any core-contributed workflow prose, rendered by
    ``finalize_instructions``.

    Args:
        read_only: Whether write tools are disabled; selects the
            write-guidance snippet and the read-only/read-write
            announcement.
        conventions_file: The configured per-folder conventions filename, or
            ``None`` when conventions are not configured.
        summarize_note_limit: The configured summarize note limit, surfaced
            so calling models can plan folder splits before their first call
            (#925), or ``None`` when the summarize tool is not configured —
            in which case the guidance points clients at the client-side
            ``summarize-subtree`` prompt instead (#1035).
        okf_mode: The configured OKF mode (``"auto"`` / ``"off"`` /
            ``"on"``). Any mode other than ``"off"`` emits the OKF guidance.

    Returns:
        The domain guidance snippets joined into one string.
    """
    return " ".join(
        text
        for text, _priority, _tools in _domain_snippets(
            read_only=read_only,
            conventions_file=conventions_file,
            summarize_note_limit=summarize_note_limit,
            okf_mode=okf_mode,
        )
    )


def contribute_instructions(
    mcp: FastMCP,
    *,
    read_only: bool,
    conventions_file: str | None = None,
    summarize_note_limit: int | None = None,
    okf_mode: str = "off",
) -> None:
    """Add the domain guidance snippets to *mcp*'s ``InstructionsBuilder``.

    ``finalize_instructions`` in the skeleton body renders them after the
    identity line and documentation pointer, prunes any snippet whose
    declared tools the operator hid, and applies the
    ``MARKDOWN_VAULT_MCP_INSTRUCTIONS`` / ``_EXTRA`` env contract.

    Args:
        mcp: The server whose builder receives the snippets.
        read_only: Whether write tools are disabled.
        conventions_file: The configured per-folder conventions filename, or
            ``None`` when conventions are not configured.
        summarize_note_limit: The configured summarize note limit, or
            ``None`` when the summarize tool is not configured.
        okf_mode: The configured OKF mode (``"auto"`` / ``"off"`` / ``"on"``).
    """
    builder = instructions_for(mcp)
    for text, priority, tools in _domain_snippets(
        read_only=read_only,
        conventions_file=conventions_file,
        summarize_note_limit=summarize_note_limit,
        okf_mode=okf_mode,
    ):
        builder.add(text, priority=priority, tools=tools)
