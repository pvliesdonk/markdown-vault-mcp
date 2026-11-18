"""Read-only bundle inventory across its public adapters."""

from markdown_vault_mcp.vault import Vault


def test_untyped_inventory_excludes_reserved_and_keeps_cursor(tmp_path):
    (tmp_path / "index.md").write_text("# index")
    (tmp_path / "log.md").write_text("# log")
    (tmp_path / "note.md").write_text("# note")
    vault = Vault(source_dir=tmp_path)
    try:
        first = vault.reader.okf_inventory("", type_state="absent", entry_limit=1)
        assert first.entries == ()
        assert first.next_cursor == "index.md"
        second = vault.reader.okf_inventory(
            "", type_state="absent", cursor=first.next_cursor
        )
        assert [row.path for row in second.entries] == ["note.md"]
        assert second.entries[0].search_state == "not_evaluated"
    finally:
        vault.close()


async def test_tool_and_cli_read_only_parity(tmp_path, monkeypatch, _clear_env):
    import json

    from fastmcp import Client
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
    from tests.server_factory import make_server

    (tmp_path / "note.md").write_text("# note")
    (tmp_path / "index.md").write_text("# index")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    async with Client(make_server()) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        tool = tools["okf_inventory"]
        assert tool.annotations.read_only_hint is True
        assert tool.icons
        result = await client.call_tool(
            "okf_inventory", {"root": "", "type_state": "absent"}
        )
        payload = result.structured_content
        assert [entry["path"] for entry in payload["entries"]] == ["note.md"]
    cli = CliRunner().invoke(
        app, ["okf-inventory", "", "--type-state", "absent", "--local", "--json"]
    )
    assert cli.exit_code == 0, cli.output
    cli_payload = json.loads(cli.stdout)
    # A server may already have an index while the local read has none.
    assert cli_payload["entries"][0]["search_state"] == "not_evaluated"
    assert payload["entries"][0]["search_state"] == "searchable"
    payload["entries"][0]["search_state"] = "not_evaluated"
    assert cli_payload == payload


async def test_invalid_scope_is_actionable(tmp_path, monkeypatch, _clear_env):
    import pytest
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.server_factory import make_server

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match="relative root"):
            await client.call_tool("okf_inventory", {"root": "../outside"})
