"""Lockstep guards for the single-sourced write-tool enumeration (#1009).

``_write_tools.WRITE_TOOL_NAMES`` is the single source every user-facing
enumeration of the ``read_only``-gated tools derives from. Two directions
are pinned here:

- registry → constant: the actual ``tags={"write"}`` tool set must equal
  the constant, so a new/renamed write tool fails loudly until the
  constant (and, transitively, every derived text) is updated;
- constant → docs: every hand-written site that names the set must carry
  the currently rendered phrase, so none of them can silently drift.

The *generated* sites (``server.json``, the wizard spec, the env
examples, the README env-var table) are not asserted here — they derive
from ``config.py``'s ``read_only`` help via ``scripts/gen_config_surface.py``,
whose ``--check`` mode guards them in CI.
"""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from markdown_vault_mcp._write_tools import WRITE_TOOL_NAMES, write_tools_phrase

REPO_ROOT = Path(__file__).resolve().parent.parent

# (repo-relative path, phrase variant expected in it). Markdown sites carry
# the backticked rendering; the mcpb manifest is JSON prose, so plain.
_DOC_SITES: tuple[tuple[str, str], ...] = (
    ("README.md", write_tools_phrase(markdown=True)),
    ("docs/guides/claude-code-plugin.md", write_tools_phrase(markdown=True)),
    ("docs/guides/claude-desktop.md", write_tools_phrase(markdown=True)),
    ("docs/guides/docker.md", write_tools_phrase(markdown=True)),
    ("docs/design/design.md", write_tools_phrase(markdown=True)),
    ("packaging/mcpb/manifest.json.in", write_tools_phrase()),
)


class TestRegistryLockstep:
    """The constant must mirror the actual write-tagged tool registry."""

    @pytest.mark.usefixtures("_mcp_env")
    async def test_write_tagged_registry_matches_constant(self) -> None:
        """``WRITE_TOOL_NAMES`` equals the actual ``tags={"write"}`` tool set.

        Builds the full surface via the ``register_*`` functions plus core's
        transfer routes — bypassing ``make_server``'s tag-based disable
        passes, mirroring ``test_every_registered_tool_has_title`` — so
        conditionally-visible tools (``git_sync``, ``create_upload_link``)
        are asserted too, and reads the unfiltered registry via
        ``_list_tools()``.
        """
        from fastmcp import FastMCP
        from fastmcp_pvl_core import register_transfer_routes

        from markdown_vault_mcp._server_apps import register_apps
        from markdown_vault_mcp._server_tools import register_tools
        from markdown_vault_mcp._transfer_sink import VaultTransferSink
        from markdown_vault_mcp.config import ProjectConfig

        mcp = FastMCP("write-tag-lockstep")
        register_tools(mcp)
        register_apps(mcp)
        config = ProjectConfig.from_env()
        config = replace(
            config,
            server=replace(
                config.server,
                base_url="https://example.test",
                kv_store_url="memory://",
            ),
        )
        sink = VaultTransferSink(config)
        register_transfer_routes(
            mcp, config.server, config.transfer, sink=sink, validate=sink.validate
        )

        tools = await mcp._list_tools()
        write_tagged = {t.name for t in tools if "write" in (t.tags or set())}
        assert write_tagged == set(WRITE_TOOL_NAMES), (
            "tags={'write'} registry and _write_tools.WRITE_TOOL_NAMES "
            "disagree — update the constant (and re-run "
            "scripts/gen_config_surface.py) when adding/renaming a write tool"
        )

    def test_constant_has_no_duplicates(self) -> None:
        assert len(WRITE_TOOL_NAMES) == len(set(WRITE_TOOL_NAMES))


class TestDerivedTextLockstep:
    """Every intentional user-facing enumeration derives from the constant."""

    def test_read_only_help_carries_phrase(self) -> None:
        """The ``read_only`` field help (source of every generated artifact)
        interpolates the derived phrase rather than hand-listing tools."""
        from markdown_vault_mcp.config import ProjectConfig

        (read_only,) = [f for f in fields(ProjectConfig) if f.name == "read_only"]
        assert write_tools_phrase() in read_only.metadata["help"]

    @pytest.mark.parametrize(("rel_path", "phrase"), _DOC_SITES)
    def test_doc_site_carries_current_phrase(self, rel_path: str, phrase: str) -> None:
        """Hand-written sites must contain the current rendering verbatim.

        Whitespace is collapsed before matching so prose may wrap the phrase
        across lines.
        """
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert phrase in " ".join(text.split()), (
            f"{rel_path} does not carry the current write-tool enumeration; "
            f"update it to include: {phrase}"
        )

    def test_gated_tool_accessor(self) -> None:
        """``gated_tool`` returns members verbatim and rejects non-members,
        so prose singling out one gated tool fails loudly on a rename."""
        from markdown_vault_mcp._write_tools import gated_tool

        assert gated_tool("git_sync") == "git_sync"
        with pytest.raises(ValueError, match="not a write-tagged tool"):
            gated_tool("reindex")

    def test_phrase_renderings(self) -> None:
        """Pin both renderings: okf family collapsed, others verbatim."""
        plain = write_tools_phrase()
        assert plain.startswith("write, edit, append, delete, rename, ")
        assert "the okf_* tools" in plain
        assert "okf_convert_links" not in plain
        assert write_tools_phrase(markdown=True).count("`") == 2 * (
            len([n for n in WRITE_TOOL_NAMES if not n.startswith("okf_")]) + 1
        )
