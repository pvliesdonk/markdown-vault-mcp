"""Read-only bundle inventory across its public adapters."""

from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
from markdown_vault_mcp.vault import Vault


def test_untyped_inventory_excludes_reserved_and_keeps_cursor(tmp_path):
    (tmp_path / "index.md").write_text("# index")
    (tmp_path / "log.md").write_text("# log")
    (tmp_path / "note.md").write_text("# note")
    vault = Vault(source_dir=tmp_path)
    try:
        first = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=1), type_state="absent"
        )
        assert first.entries == ()
        assert first.next_cursor == "index.md"
        second = vault.reader.okf_inventory(
            BundleTreeRequest("", cursor=first.next_cursor), type_state="absent"
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

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("# note")
    (vault_dir / "index.md").write_text("# index")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_dir))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "index.db"))
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
    assert json.loads(cli.stdout) == payload


async def test_invalid_scope_is_actionable(tmp_path, monkeypatch, _clear_env):
    import pytest
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.server_factory import make_server

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match="relative root"):
            await client.call_tool("okf_inventory", {"root": "../outside"})


def test_inventory_restricts_internal_and_symlink_paths(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "secret.md").write_text("secret")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    vault = Vault(source_dir=root)
    try:
        page = vault.reader.okf_inventory(BundleTreeRequest(""))
        assert not any("secret" in entry.path for entry in page.entries)
        assert (
            vault.reader.okf_inventory(BundleTreeRequest(".git")).state == "unavailable"
        )
        assert (
            vault.reader.okf_inventory(BundleTreeRequest("escape")).state
            == "unavailable"
        )
    finally:
        vault.close()


def test_local_no_index_does_not_create_one(tmp_path, monkeypatch, _clear_env):
    import json

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("text")
    index = tmp_path / "missing.db"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    cli = CliRunner().invoke(app, ["okf-inventory", "", "--local", "--json"])
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.stdout)["entries"][0]["search_state"] == "not_evaluated"
    assert not index.exists()
