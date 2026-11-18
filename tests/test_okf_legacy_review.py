"""The legacy review capability exposes its required input."""

from typing import Any

import pytest
from fastmcp import Client

from tests.conftest import _parse_tool_data
from tests.server_factory import make_server


@pytest.mark.usefixtures("_clear_env")
async def test_legacy_alias_read_and_review(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("Body\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")

    async def confirm(*_args: Any) -> bool:
        return True

    async with Client(
        make_server(), mode="legacy", elicitation_handler=confirm
    ) as client:
        read = _parse_tool_data(await client.call_tool("read", {"path": "note.md"}))
        assert set(read["okf"]) == {"snapshot"}
        await client.call_tool(
            "okf_verify",
            {"path": "note.md", "reviewed_snapshot": read["okf"]["snapshot"]},
        )
