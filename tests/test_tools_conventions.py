"""End-to-end tests for the folder-conventions feature via the MCP server.

Covers the ``get_conventions`` tool, the ``conventions`` enrichment on
``write`` / ``edit`` / ``get_context`` results, index exclusion of the
convention files themselves, the ``config://vault`` fields, and the
server-instructions pointer sentence.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from fastmcp import Client

from markdown_vault_mcp.server import make_server

from .conftest import _CLEAR_VARS, wait_for_mcp_writer_drain

ROOT_RULES = "Vault-wide: use sentence-case headings."
RESOURCE_RULES = (
    "Reference material. Keep notes self-contained; do not link out to "
    "1-Projects or Journal."
)


@pytest.fixture
def conventions_vault(tmp_path: Path) -> Path:
    """A writable vault with root + 3-Resources convention files."""
    vault = tmp_path / "vault"
    resources = vault / "3-Resources"
    projects = vault / "1-Projects"
    resources.mkdir(parents=True)
    projects.mkdir()
    (vault / "_conventions.md").write_text(ROOT_RULES, encoding="utf-8")
    (resources / "_conventions.md").write_text(RESOURCE_RULES, encoding="utf-8")
    (resources / "existing.md").write_text("# Existing\nBody.", encoding="utf-8")
    (projects / "plan.md").write_text("# Plan\nBody.", encoding="utf-8")
    return vault


@pytest.fixture
def _conventions_env(conventions_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(conventions_vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)


def _structured(result: Any) -> dict[str, Any]:
    data = result.structured_content
    assert isinstance(data, dict)
    return data


@pytest.mark.usefixtures("_conventions_env")
class TestGetConventionsTool:
    async def test_note_path_returns_chain_root_first(self) -> None:
        async with Client(make_server()) as client:
            result = await client.call_tool(
                "get_conventions", {"path": "3-Resources/CRA.md"}
            )
        data = _structured(result)
        assert [e["folder"] for e in data["conventions"]] == ["", "3-Resources"]
        assert data["conventions"][0]["content"] == ROOT_RULES
        assert data["conventions"][1]["content"] == RESOURCE_RULES
        # Targeted lookups skip the vault-wide folder walk.
        assert "convention_folders" not in data

    async def test_discovery_mode_default_path(self) -> None:
        async with Client(make_server()) as client:
            result = await client.call_tool("get_conventions", {})
        data = _structured(result)
        assert [e["folder"] for e in data["conventions"]] == [""]
        assert data["convention_folders"] == ["", "3-Resources"]

    async def test_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_CONVENTIONS_FILE", "none")
        async with Client(make_server()) as client:
            result = await client.call_tool("get_conventions", {})
        data = _structured(result)
        assert data["conventions"] == []
        assert data["convention_folders"] == []


@pytest.mark.usefixtures("_conventions_env")
class TestWriteFlowEnrichment:
    async def test_write_returns_conventions(self) -> None:
        async with Client(make_server()) as client:
            result = await client.call_tool(
                "write",
                {"path": "3-Resources/CRA.md", "content": "# CRA\nReference."},
            )
        data = _structured(result)
        assert data["created"] is True
        contents = [e["content"] for e in data["conventions"]]
        assert contents == [ROOT_RULES, RESOURCE_RULES]

    async def test_write_omits_key_when_no_conventions(
        self, conventions_vault: Path
    ) -> None:
        (conventions_vault / "_conventions.md").unlink()
        (conventions_vault / "3-Resources" / "_conventions.md").unlink()
        async with Client(make_server()) as client:
            result = await client.call_tool(
                "write", {"path": "1-Projects/new.md", "content": "x"}
            )
        data = _structured(result)
        assert "conventions" not in data

    async def test_edit_returns_conventions(self) -> None:
        async with Client(make_server()) as client:
            result = await client.call_tool(
                "edit",
                {
                    "path": "3-Resources/existing.md",
                    "old_text": "Body.",
                    "new_text": "New body.",
                },
            )
        data = _structured(result)
        assert data["replacements"] == 1
        assert [e["folder"] for e in data["conventions"]] == ["", "3-Resources"]

    async def test_get_context_includes_conventions(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "get_context",
                {"path": "3-Resources/existing.md", "similar_limit": 0},
            )
        data = _structured(result)
        assert [e["folder"] for e in data["conventions"]] == ["", "3-Resources"]


@pytest.mark.usefixtures("_conventions_env")
class TestIndexExclusion:
    async def test_convention_files_invisible_to_search_and_list(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            listed = await client.call_tool("list_documents", {})
            searched = await client.call_tool("search", {"query": "self-contained"})
        paths = [d["path"] for d in listed.structured_content["result"]]
        assert "3-Resources/existing.md" in paths
        assert all("_conventions.md" not in p for p in paths)
        hits = searched.structured_content["result"]
        assert all("_conventions.md" not in h["path"] for h in hits)

    async def test_convention_file_readable_from_disk(self) -> None:
        async with Client(make_server()) as client:
            result = await client.call_tool(
                "read", {"path": "3-Resources/_conventions.md"}
            )
        data = _structured(result)
        assert RESOURCE_RULES in data["content"]

    async def test_writing_convention_file_does_not_enter_index(self) -> None:
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            await client.call_tool(
                "write",
                {"path": "1-Projects/_conventions.md", "content": "Project rules."},
            )
            await wait_for_mcp_writer_drain(client)
            listed = await client.call_tool("list_documents", {})
            conventions = await client.call_tool(
                "get_conventions", {"path": "1-Projects/plan.md"}
            )
        paths = [d["path"] for d in listed.structured_content["result"]]
        assert all("_conventions.md" not in p for p in paths)
        # ...but the freshly written file is immediately live for lookups.
        chain = _structured(conventions)["conventions"]
        assert [e["folder"] for e in chain] == ["", "1-Projects"]
        assert chain[1]["content"] == "Project rules."


@pytest.mark.usefixtures("_conventions_env")
class TestConfigSurface:
    async def test_config_resource_reports_conventions(self) -> None:
        async with Client(make_server()) as client:
            resource = await client.read_resource("config://vault")
        payload = json.loads(resource[0].text)
        assert payload["conventions_file"] == "_conventions.md"
        assert payload["convention_folders"] == ["", "3-Resources"]

    async def test_instructions_mention_conventions(self) -> None:
        server = make_server()
        assert "_conventions.md" in (server.instructions or "")
        assert "get_conventions" in (server.instructions or "")

    async def test_instructions_omit_conventions_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_CONVENTIONS_FILE", "none")
        server = make_server()
        assert "get_conventions" not in (server.instructions or "")


class TestConventionsPayloadHelper:
    def test_invalid_path_yields_empty_not_raise(self, tmp_path: Path) -> None:
        """A traversal path degrades to [] so the calling tool never breaks."""
        from markdown_vault_mcp._server_tools._common import conventions_payload
        from markdown_vault_mcp.vault import Vault

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "_conventions.md").write_text("Rules.", encoding="utf-8")
        col = Vault(source_dir=vault_dir)
        try:
            assert conventions_payload(col, "../outside/note.md") == []
            payload = conventions_payload(col, "note.md")
            assert [e["content"] for e in payload] == ["Rules."]
        finally:
            col.close()
