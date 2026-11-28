"""The replace_text tool over an in-memory MCP client (#2031)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.conftest import wait_for_mcp_writer_drain
from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.md").write_text(
        "---\ntitle: foo\n---\nfoo bar foo\n", encoding="utf-8"
    )
    (tmp_path / "two.md").write_text("a foo here\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def server(vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_dir))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    return make_server()


async def test_dry_run_by_default_then_apply(server: FastMCP, vault_dir: Path) -> None:
    async with Client(server) as client:
        await wait_for_mcp_writer_drain(client)
        args = {"pattern": "foo", "replacement": "baz", "folders": [""]}
        preview = (await client.call_tool("replace_text", args)).structured_content
        assert preview["dry_run"] is True
        assert [(n["path"], n["replacements"]) for n in preview["notes"]] == [
            ("a/one.md", 2),
            ("two.md", 1),
        ]
        assert "+baz bar baz" in preview["notes"][0]["diff"]
        assert "foo" in (vault_dir / "two.md").read_text(encoding="utf-8")

        applied = (
            await client.call_tool(
                "replace_text", {**args, "dry_run": False, "plan": preview["plan"]}
            )
        ).structured_content
        assert applied["dry_run"] is False
        assert (applied["applied"], applied["skipped"], applied["refused"]) == (2, 0, 0)
        assert [n["status"] for n in applied["notes"]] == ["applied", "applied"]
        assert (vault_dir / "a" / "one.md").read_text(encoding="utf-8") == (
            "---\ntitle: foo\n---\nbaz bar baz\n"
        )


async def test_apply_without_a_plan_is_refused(server: FastMCP) -> None:
    async with Client(server) as client:
        with pytest.raises(ToolError, match="dry_run=false needs plan"):
            await client.call_tool(
                "replace_text", {"pattern": "foo", "folders": [""], "dry_run": False}
            )


async def test_a_stale_plan_names_the_dry_run(server: FastMCP, vault_dir: Path) -> None:
    async with Client(server) as client:
        await wait_for_mcp_writer_drain(client)
        args = {"pattern": "foo", "folders": [""]}
        plan = (await client.call_tool("replace_text", args)).structured_content["plan"]
        (vault_dir / "two.md").write_text("foo changed\n", encoding="utf-8")
        with pytest.raises(ToolError, match=r"replace_text .*dry_run=true"):
            await client.call_tool(
                "replace_text", {**args, "dry_run": False, "plan": plan}
            )
        assert (vault_dir / "two.md").read_text(encoding="utf-8") == "foo changed\n"


@pytest.mark.parametrize(
    ("args", "match"),
    [
        ({"pattern": "", "folders": [""]}, r"pattern is empty.*replace_text again"),
        ({"pattern": "foo"}, r"no notes selected.*replace_text again"),
        ({"pattern": "(", "regex": True, "folders": [""]}, "not a valid regular"),
        ({"pattern": "foo", "folders": ["nope"]}, "Folder not found: 'nope'"),
    ],
)
async def test_refusals_say_what_to_change(
    server: FastMCP, args: dict[str, object], match: str
) -> None:
    async with Client(server) as client:
        with pytest.raises(ToolError, match=match):
            await client.call_tool("replace_text", args)
