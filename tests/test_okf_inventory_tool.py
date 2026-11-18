"""Read-only bundle inventory across its public adapters."""

import pytest

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


def test_inventory_filters_malformed_and_non_admitted(tmp_path):
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    (tmp_path / "broken.md").write_text("---\ntype: [\n---\ntext")
    (tmp_path / "missing.md").write_text("text")
    (tmp_path / "typed.md").write_text("---\ntype: concept\ntitle: Typed\n---\ntext")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        malformed = vault.reader.okf_inventory(
            BundleTreeRequest(""), parse_state="malformed_metadata"
        )
        assert [entry.path for entry in malformed.entries] == ["broken.md"]
        vault.index.build_index()
        rejected = vault.reader.okf_inventory(
            BundleTreeRequest(""), search_state="non_admitted"
        )
        assert [entry.path for entry in rejected.entries] == ["missing.md"]
    finally:
        vault.close()


def test_local_keeps_configured_state_directory_private(
    tmp_path, monkeypatch, _clear_env
):
    import json

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    state = tmp_path / "vectors"
    state.mkdir()
    (state / "secret.md").write_text("private")
    (tmp_path / "note.md").write_text("visible")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
    result = CliRunner().invoke(app, ["okf-inventory", "", "--local", "--json"])
    assert result.exit_code == 0, result.output
    assert [entry["path"] for entry in json.loads(result.stdout)["entries"]] == [
        "note.md"
    ]


async def test_boundary_remains_visible_to_tool_and_cli(
    tmp_path, monkeypatch, _clear_env
):
    import json

    from fastmcp import Client
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
    from tests.server_factory import make_server

    path = tmp_path
    for _ in range(32):
        path = path / "d"
        path.mkdir()
    (path / "note.md").write_text("text")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    async with Client(make_server()) as client:
        report = (
            await client.call_tool(
                "okf_inventory", {"root": "", "type_state": "absent"}
            )
        ).structured_content
    cli = CliRunner().invoke(
        app, ["okf-inventory", "", "--type-state", "absent", "--local", "--json"]
    )
    assert cli.exit_code == 0, cli.output
    local = json.loads(cli.stdout)
    for page in (report, local):
        boundary = [row for row in page["entries"] if row["reason"] == "depth_limit"]
        assert [row["path"] for row in boundary] == ["/".join(["d"] * 32)]
        assert page["enumeration"] == "unavailable"


def test_cli_source_override_never_adopts_other_index(
    tmp_path, monkeypatch, _clear_env
):
    import json

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    original, other = tmp_path / "original", tmp_path / "other"
    original.mkdir()
    other.mkdir()
    (original / "note.md").write_text("original")
    (other / "note.md").write_text("other")
    index = tmp_path / "index.db"
    vault = Vault(source_dir=original, settings=VaultSettings(index_path=index))
    try:
        vault.index.build_index()
    finally:
        vault.close()
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(original))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    result = CliRunner().invoke(
        app, ["okf-inventory", "", "--source-dir", str(other), "--local", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["entries"][0]["search_state"] == "not_evaluated"


def test_cli_corrupt_index_falls_back_but_newer_index_refuses(
    tmp_path, monkeypatch, _clear_env
):
    import json
    import sqlite3

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("text")
    index = tmp_path / "index.db"
    index.write_bytes(b"bad index")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    args = ["okf-inventory", "", "--local", "--json"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["entries"][0]["search_state"] == "not_evaluated"
    index.unlink()
    with sqlite3.connect(index) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES ('schema_version', '99')")
    newer = CliRunner().invoke(app, args)
    assert newer.exit_code == 1


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--json", "--agent"], "--json and --agent"),
        (
            ["--source-dir", ".", "--server", "http://localhost:8000/mcp"],
            "--source-dir conflicts",
        ),
    ],
)
def test_cli_rejects_conflicting_options(
    tmp_path, monkeypatch, _clear_env, args, message
):
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["okf-inventory", "", *args])
    assert result.exit_code == 1
    assert message in result.output
