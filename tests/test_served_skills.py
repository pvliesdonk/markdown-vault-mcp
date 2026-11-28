"""Skills served over MCP (SEP-2640): what ``make_server`` advertises and serves."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import MCPError
from fastmcp.skills import SkillsClientExtension

from markdown_vault_mcp._skills import (
    SKILLS_ROOT,
    disable_skills,
    register_skills,
    skill_file_uris,
)
from markdown_vault_mcp.server import make_server

_WORKFLOW_URI = "skill://vault-workflow/SKILL.md"
_OKF_URI = "skill://okf-spec/SKILL.md"
_OKF_DIR = SKILLS_ROOT / "okf-spec"

# SPEC.md at open-knowledge-format/open-knowledge-format@527e81aff5, as the git
# blob id that commit's tree lists for it, so anyone can check it against
# `gh api repos/open-knowledge-format/open-knowledge-format/git/trees/527e81aff5`.
_OKF_COMMIT = "527e81aff55992be21507e97004286789126f88c"
_OKF_SPEC_BLOB = "0dc3f7aa0fc710e588ba918eb115935d41eb2943"

# Claude's scan limits on a served skill (reference/claude-mcp-client.md).
_MAX_FILE_BYTES = 100_000
_MAX_SKILL_BYTES = 400_000


def _server(vault: Path, monkeypatch: pytest.MonkeyPatch, okf_mode: str) -> FastMCP:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", okf_mode)
    return make_server()


@pytest.fixture
def server(
    _clear_env: None, vault_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FastMCP:
    """A server over the fixture vault with OKF on auto, the default."""
    return _server(vault_path, monkeypatch, "auto")


@pytest.fixture
def okf_off_server(
    _clear_env: None, vault_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FastMCP:
    """A server over the fixture vault with OKF turned off."""
    return _server(vault_path, monkeypatch, "off")


def _skills_client(server: FastMCP) -> Client:
    """A client that negotiates the skills extension, as Claude does."""
    return Client(server, extensions=[SkillsClientExtension()])


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(
        b"blob %d\0" % len(data) + data, usedforsecurity=False
    ).hexdigest()


async def test_skills_list_names_the_bundled_skills(server: FastMCP) -> None:
    async with _skills_client(server) as client:
        skills = await client.list_skills()
    by_uri = {s.uri: s for s in skills}
    assert sorted(by_uri) == [_OKF_URI, _WORKFLOW_URI]
    assert by_uri[_WORKFLOW_URI].frontmatter.name == "vault-workflow"
    assert by_uri[_OKF_URI].frontmatter.name == "okf-spec"
    assert all(s.frontmatter.description for s in skills)


async def test_skill_file_reads_back_byte_for_byte(server: FastMCP) -> None:
    async with _skills_client(server) as client:
        skill = await client.read_skill(_WORKFLOW_URI)
    on_disk = (SKILLS_ROOT / "vault-workflow" / "SKILL.md").read_bytes()
    assert skill.name == "vault-workflow"
    assert skill.files["SKILL.md"] == on_disk


async def test_okf_skill_serves_the_spec_beside_its_licence(server: FastMCP) -> None:
    async with _skills_client(server) as client:
        skill = await client.read_skill(_OKF_URI)
    assert sorted(skill.files) == ["LICENSE.md", "PROVENANCE.md", "SKILL.md", "SPEC.md"]
    assert skill.files["SPEC.md"] == (_OKF_DIR / "SPEC.md").read_bytes()


async def test_okf_off_hides_the_okf_skill(okf_off_server: FastMCP) -> None:
    async with _skills_client(okf_off_server) as client:
        skills = await client.list_skills()
        uris = {str(r.uri) for r in await client.list_resources()}
        with pytest.raises(MCPError):
            await client.read_resource("skill://okf-spec/SPEC.md")
        with pytest.raises(MCPError):
            await client.get_skill(_OKF_URI)
    assert [s.uri for s in skills] == [_WORKFLOW_URI]
    assert not uris & skill_file_uris("okf-spec")
    assert _WORKFLOW_URI in uris


async def test_okf_off_hides_the_okf_skill_from_plain_clients(
    okf_off_server: FastMCP,
) -> None:
    async with Client(okf_off_server) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        with pytest.raises(MCPError):
            await client.read_resource("skill://okf-spec/_manifest")
    assert not any(uri.startswith("skill://okf-spec/") for uri in uris)


def test_skill_file_uris_cover_every_file_and_the_manifest() -> None:
    assert skill_file_uris("okf-spec") == {
        f"skill://okf-spec/{name}"
        for name in ("LICENSE.md", "PROVENANCE.md", "SKILL.md", "SPEC.md", "_manifest")
    }


async def test_disable_skills_leaves_skills_of_other_features_alone() -> None:
    mcp = FastMCP("probe")
    register_skills(mcp)
    disable_skills(mcp, {"summarize"})
    async with _skills_client(mcp) as client:
        skills = await client.list_skills()
    assert sorted(s.uri for s in skills) == [_OKF_URI, _WORKFLOW_URI]


def test_plugin_only_skills_stay_out_of_the_package() -> None:
    """vault-setup repairs a server that is down; vault-summarize needs the
    plugin's vault-mapper agent. Neither can be served by the server."""
    served = sorted(p.parent.name for p in SKILLS_ROOT.glob("*/SKILL.md"))
    assert served == ["okf-spec", "vault-workflow"]


@pytest.mark.parametrize(
    "folder", sorted(p.parent for p in SKILLS_ROOT.glob("*/SKILL.md"))
)
def test_served_skill_fits_claudes_scan_limits(folder: Path) -> None:
    sizes = [p.stat().st_size for p in folder.rglob("*") if p.is_file()]
    assert max(sizes) <= _MAX_FILE_BYTES
    assert sum(sizes) <= _MAX_SKILL_BYTES


def test_bundled_okf_spec_is_the_pinned_upstream_revision() -> None:
    """SPEC.md is OKF 0.3 exactly as upstream ships it at the pinned commit.

    A failure means the file changed without a deliberate move to another OKF
    version: a new SPEC.md, PROVENANCE.md, this pin and
    docs/design/reference/okf-v0.3.md move together, in a server release.
    """
    spec = (_OKF_DIR / "SPEC.md").read_bytes()
    assert _git_blob_id(spec) == _OKF_SPEC_BLOB
    assert spec.startswith(b"# Open Knowledge Format (OKF)\n\n**Version 0.3**\n")


def test_okf_provenance_names_the_version_commit_and_licence() -> None:
    provenance = (_OKF_DIR / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "Version 0.3" in provenance
    assert _OKF_COMMIT in provenance
    assert "Apache License, Version 2.0" in provenance
    licence = (_OKF_DIR / "LICENSE.md").read_text(encoding="utf-8")
    assert "Apache License" in licence
    assert "Version 2.0, January 2004" in licence


def test_okf_provenance_matches_the_v03_reference() -> None:
    """The commit served is the one the v0.3 reference page records."""
    reference = (
        Path(__file__).parents[1] / "docs" / "design" / "reference" / "okf-v0.3.md"
    ).read_text(encoding="utf-8")
    match = re.search(r"SPEC\.md at commit ([0-9a-f]{7,40})", reference)
    assert match is not None
    assert _OKF_COMMIT.startswith(match.group(1))
