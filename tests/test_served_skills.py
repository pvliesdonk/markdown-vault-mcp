"""Skills served over MCP (SEP-2640): what ``make_server`` advertises and serves."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp import Client, FastMCP
from fastmcp.skills import SkillsClientExtension

from markdown_vault_mcp._skills import SKILLS_ROOT
from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from pathlib import Path

_WORKFLOW_URI = "skill://vault-workflow/SKILL.md"


@pytest.fixture
def server(
    _clear_env: None, vault_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FastMCP:
    """A server over the fixture vault, built outside the event loop."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    return make_server()


def _skills_client(server: FastMCP) -> Client:
    """A client that negotiates the skills extension, as Claude does."""
    return Client(server, extensions=[SkillsClientExtension()])


async def test_skills_list_names_the_bundled_skills(server: FastMCP) -> None:
    async with _skills_client(server) as client:
        skills = await client.list_skills()
    assert [s.uri for s in skills] == [_WORKFLOW_URI]
    assert skills[0].frontmatter.name == "vault-workflow"
    assert skills[0].frontmatter.description


async def test_skill_file_reads_back_byte_for_byte(server: FastMCP) -> None:
    async with _skills_client(server) as client:
        skill = await client.read_skill(_WORKFLOW_URI)
    on_disk = (SKILLS_ROOT / "vault-workflow" / "SKILL.md").read_bytes()
    assert skill.name == "vault-workflow"
    assert skill.files["SKILL.md"] == on_disk


def test_plugin_only_skills_stay_out_of_the_package() -> None:
    """vault-setup repairs a server that is down; vault-summarize needs the
    plugin's vault-mapper agent. Neither can be served by the server."""
    served = sorted(p.parent.name for p in SKILLS_ROOT.glob("*/SKILL.md"))
    assert served == ["vault-workflow"]
