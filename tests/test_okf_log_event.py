"""Editorial events group related concept changes into one log entry."""

from datetime import date

import pytest

from markdown_vault_mcp._okf_log_event import deferred_event, record_event
from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.utils.fs import is_regular_file
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_event_groups_paths_and_keeps_same_day_entries(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "area").mkdir()
    calls = []
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=ContentConfig(
                okf_log="events", okf_folders=("area",)
            ).okf_capabilities,
        ),
        on_write=lambda path, _content, _op: calls.append(path.name),
    )
    try:
        for name in ("a one.md", "b#.md", "c.md"):
            vault.writer.write("area/" + name, "# Concept\n")
        assert not is_regular_file(tmp_path / "area" / "log.md")
        paths = ["area/a one.md", "area/b#.md", "area/c.md"]
        record_event(
            vault._doc_mgr, paths, "Changed policy", ("area",), today=date(2026, 1, 2)
        )
        record_event(
            vault._doc_mgr,
            [paths[0]],
            "Second event",
            ("area",),
            today=date(2026, 1, 2),
        )
        log = (tmp_path / "area" / "log.md").read_text()
        assert not log.startswith("---")
        assert log.count("\n- ") == 2
        assert (
            "/area/a%20one.md" in log and r"/area/b\#.md" in log and "/area/c.md" in log
        )
        assert "Changed policy" in log and "Second event" in log
        assert calls.count("log.md") == 2
    finally:
        vault.close()


@pytest.mark.parametrize(
    "description", ["", "x\ny", "x\x00y", "x\u2028y", "x\u2029y", "x" * 501]
)
def test_invalid_description_refused(tmp_path, description):
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        with pytest.raises(InvalidRequestError):
            record_event(vault._doc_mgr, ["a.md"], description, (".",))
    finally:
        vault.close()


def test_deferred_event_suppresses_log_but_keeps_index(tmp_path):
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
    )
    try:
        with deferred_event():
            vault.writer.write("a.md", "# A\n")
        assert not is_regular_file(tmp_path / "log.md")
        assert "a.md" in (tmp_path / "index.md").read_text()
        vault.writer.write("b.md", "# B\n")
        assert "b.md" in (tmp_path / "log.md").read_text()
    finally:
        vault.close()


def test_refuses_non_markdown_and_control_in_path(tmp_path):
    (tmp_path / "note.txt").write_text("text")
    (tmp_path / "bad\nname.md").write_text("text")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        for path in ("note.txt", "bad\nname.md"):
            with pytest.raises(InvalidRequestError):
                record_event(vault._doc_mgr, [path], "Change", (".",))
        assert not is_regular_file(tmp_path / "log.md")
    finally:
        vault.close()


@pytest.mark.anyio
async def test_mcp_event_capability_and_result(tmp_path, monkeypatch):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.conftest import _parse_tool_data
    from tests.server_factory import make_server

    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (tmp_path / "a.md").write_text("# A\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG", "off")
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match="not active"):
            await client.call_tool(
                "okf_log_event", {"paths": ["a.md"], "description": "Change"}
            )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_LOG", "events")
    async with Client(make_server()) as client:
        result = await client.call_tool(
            "okf_log_event", {"paths": ["a.md"], "description": "Change"}
        )
        assert _parse_tool_data(result)["path"] == "log.md"
    assert "Change" in (tmp_path / "log.md").read_text()


def test_routes_to_common_ancestor_and_preserves_existing_log(tmp_path):
    (tmp_path / "team" / "one").mkdir(parents=True)
    (tmp_path / "team" / "two").mkdir()
    (tmp_path / "team" / "one" / "a.md").write_text("# A\n")
    (tmp_path / "team" / "two" / "b.md").write_text("# B\n")
    log = tmp_path / "team" / "log.md"
    log.write_text(
        "---\ntitle: History\n---\n# Log\n\n## 2020-01-01\n\n- Older entry\n"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        result = record_event(
            vault._doc_mgr,
            ["team/one/a.md", "team/two/b.md"],
            "Coordinated change",
            (".", "**"),
            today=date(2026, 1, 2),
        )
        assert result == "team/log.md"
        content = log.read_text()
        assert content.startswith("---\ntitle: History\n---\n")
        assert "- Older entry" in content and "Coordinated change" in content
    finally:
        vault.close()


def test_refuses_missing_reserved_and_aliased_log(tmp_path):
    (tmp_path / "note.md").write_text("# Note\n")
    (tmp_path / "other.md").write_text("# Other\n")
    (tmp_path / "log.md").symlink_to("other.md")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    try:
        for paths in ([], ["missing.md"], ["index.md"], ["note.md"]):
            with pytest.raises(InvalidRequestError):
                record_event(vault._doc_mgr, paths, "Change", (".",))
        assert (tmp_path / "other.md").read_text() == "# Other\n"
    finally:
        vault.close()
