"""Missing-type filtering covers the indexed note population."""

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from tests.conftest import _CLEAR_VARS, _parse_tool_data, wait_for_mcp_writer_drain
from tests.server_factory import make_server


@pytest.mark.parametrize("tool", ["search", "list_documents"])
async def test_missing_type_filter(
    tool: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only admitted non-reserved notes without a non-empty type match."""
    notes = {
        "index.md": '---\nokf_version: "0.2"\n---\nZebra',
        "log.md": "Zebra",
        "nested/index.md": "Zebra",
        "absent.md": "Zebra",
        "null.md": "---\ntype: null\n---\nZebra",
        "empty.md": '---\ntype: ""\n---\nZebra',
        "spaces.md": '---\ntype: "  "\n---\nZebra',
        "number.md": "---\ntype: 42\n---\nZebra",
        "typed.md": "---\ntype: Playbook\n---\nZebra",
        "excluded.md": "Zebra",
    }
    for path, content in notes.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EXCLUDE", "excluded.md")
    async with Client(make_server()) as client:
        await wait_for_mcp_writer_drain(client)
        args: dict[str, Any] = {"filters": {"type": None}}
        if tool == "search":
            args.update(query="zebra", mode="keyword", limit=50)
        result = _parse_tool_data(await client.call_tool(tool, args))
        assert {row["path"] for row in result} == {
            "absent.md",
            "null.md",
            "empty.md",
            "spaces.md",
            "number.md",
        }
        args["filters"] = {"type": None, "status": "stable"}
        result = _parse_tool_data(await client.call_tool(tool, args))
        assert {row["path"] for row in result} == {
            "absent.md",
            "null.md",
            "empty.md",
            "spaces.md",
            "number.md",
        }
        args["filters"] = {"type": "Playbook"}
        result = _parse_tool_data(await client.call_tool(tool, args))
        assert [row["path"] for row in result] == ["typed.md"]
