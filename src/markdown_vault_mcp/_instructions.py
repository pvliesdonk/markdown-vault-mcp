"""Domain-specific server-instructions composition.

Holds :func:`build_default_instructions`, the markdown-vault guidance that
:func:`~markdown_vault_mcp.server.make_server` applies to the FastMCP instance
after construction (in its ``DOMAIN-WIRING`` block). Lives outside the
template-owned ``server.py`` so the domain prose never re-conflicts on a
``copier update``.
"""

from __future__ import annotations

from fastmcp_pvl_core import build_instructions as _core_build_instructions

from markdown_vault_mcp.config import _ENV_PREFIX


def build_default_instructions(
    *,
    read_only: bool,
    conventions_file: str | None = None,
    summarize_note_limit: int | None = None,
) -> str:
    """Build the default instructions string based on read-only state.

    Composes MV's domain-specific guidance into a ``domain_line`` and
    delegates to :func:`fastmcp_pvl_core.build_instructions` for the
    read-only/read-write line and operator override hint.

    The folder-conventions sentence is emitted whenever *conventions_file*
    is configured, not gated on convention files existing on disk: in
    managed-git mode the clone happens inside the server lifespan, after
    instructions are composed, so a file-presence check here would silently
    miss on first boot.

    Args:
        read_only: Whether write tools are disabled; selects the write-guidance
            sentence and the read-only/read-write line.
        conventions_file: The configured per-folder conventions filename, or
            ``None`` when conventions are not configured.
        summarize_note_limit: The configured summarize note limit, surfaced so
            calling models can plan folder splits before their first call
            (#925), or ``None`` when the summarize tool is not configured.

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
            "(read first), 'rename' to move (pass update_links=True to fix links "
            "in other notes), 'delete' to remove. Use 'move_folder(old_dir, new_dir)' "
            "to move an entire folder subtree and rewrite all vault links in one call. "
            "All write operations update the "
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
        ""
        if summarize_note_limit is None
        else (
            f" The 'summarize' tool reads at most {summarize_note_limit} "
            "notes per call; for a folder larger than that (check with "
            "'get_toc'), call it once per subfolder and combine the results."
        )
    )
    domain_line = (
        f"{prelude}{write_guidance}{search_guidance}"
        f"{summarize_guidance}{conventions_guidance}"
    )
    return _core_build_instructions(
        read_only=read_only,
        env_prefix=_ENV_PREFIX,
        domain_line=domain_line,
    )
