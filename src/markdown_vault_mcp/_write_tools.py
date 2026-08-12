"""Single source for the user-facing write-tool enumeration (#1009).

``read_only=True`` hides every component tagged ``write`` via
``mcp.disable(tags={"write"})`` (see ``server.py``). Several user-facing
texts *intentionally* enumerate those tools so operators know what the
flag gates — but hand-copied lists drift. This module is the single
source they all derive from:

- ``config.py``'s ``read_only`` field help interpolates
  :func:`write_tools_phrase` at import time; ``scripts/gen_config_surface.py``
  propagates it into ``server.json``, the config-wizard spec, the env
  examples, and the README env-var table.
- The hand-written sites (README prose, the guides, the mcpb manifest,
  the design doc) are pinned to the rendered phrase by
  ``tests/test_write_tool_enumeration.py``, which also builds the full
  tool registry and asserts the write-tagged set equals
  :data:`WRITE_TOOL_NAMES` — so the constant cannot drift from the
  registry either.

The ``write`` tag is deliberately a strict subset of
``readOnlyHint=False``: ``reindex`` and ``build_embeddings`` mutate index
state, not the vault, and stay available on a read-only server.
"""

from __future__ import annotations

#: Every tool registered with ``tags={"write"}``, in user-facing order.
#: ``git_sync`` additionally requires managed git mode;
#: ``create_upload_link`` (registered by fastmcp-pvl-core's transfer
#: routes) additionally requires an HTTP transport.
WRITE_TOOL_NAMES: tuple[str, ...] = (
    "write",
    "edit",
    "delete",
    "rename",
    "move_folder",
    "fetch",
    "git_sync",
    "okf_convert_links",
    "okf_generate_index",
    "okf_seed_log",
    "okf_verify",
    "create_upload_link",
)

_OKF_PREFIX = "okf_"


def write_tools_phrase(*, markdown: bool = False) -> str:
    """Render the write-tool enumeration for user-facing prose.

    The ``okf_*`` family is collapsed to one token so the phrase stays
    readable as the family grows.

    Args:
        markdown: Wrap each tool name in backticks (for Markdown sites);
            plain text otherwise (env-file comments, JSON descriptions).

    Returns:
        A comma-separated enumeration such as
        ``"write, edit, ..., git_sync, the okf_* tools, create_upload_link"``.
    """

    def fmt(name: str) -> str:
        return f"`{name}`" if markdown else name

    tokens: list[str] = []
    okf_collapsed = False
    for name in WRITE_TOOL_NAMES:
        if name.startswith(_OKF_PREFIX):
            if not okf_collapsed:
                tokens.append(f"the {fmt(_OKF_PREFIX + '*')} tools")
                okf_collapsed = True
        else:
            tokens.append(fmt(name))
    return ", ".join(tokens)
