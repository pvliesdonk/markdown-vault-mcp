"""Hosted write command contracts using only synthetic servers and fixture data."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from tests.fixtures.cli_http import serve_http

CASES = [
    (
        ["note", "write", "a.md", "  literal\n", "--create"],
        "write",
        {"path": "a.md", "content": "  literal\n"},
    ),
    (
        [
            "note",
            "write",
            "a.md",
            "",
            "--if-match",
            "explicit",
            "--frontmatter",
            '{"tags": ["x"]}',
        ],
        "write",
        {
            "path": "a.md",
            "content": "",
            "if_match": "explicit",
            "frontmatter": {"tags": ["x"]},
        },
    ),
    (
        ["note", "write", "a.md", "new"],
        "write",
        {"path": "a.md", "content": "new", "if_match": "live"},
    ),
    (
        ["note", "edit", "a.md", "--old-text", "old", "--new-text", ""],
        "edit",
        {"path": "a.md", "old_text": "old", "new_text": "", "if_match": "live"},
    ),
    (
        [
            "note",
            "edit",
            "a.md",
            "--line-start",
            "1",
            "--line-end",
            "2",
            "--old-text",
            "",
            "--new-text",
            "new",
            "--if-match",
            "explicit",
        ],
        "edit",
        {
            "path": "a.md",
            "old_text": "",
            "new_text": "new",
            "line_start": 1,
            "line_end": 2,
            "if_match": "explicit",
        },
    ),
    (["note", "append", "a.md", ""], "append", {"path": "a.md", "content": ""}),
    (
        [
            "note",
            "append",
            "a.md",
            "x",
            "--if-match",
            "explicit",
            "--create-if-missing",
        ],
        "append",
        {
            "path": "a.md",
            "content": "x",
            "if_match": "explicit",
            "create_if_missing": True,
        },
    ),
    (
        ["note", "delete", "a.md", "--if-match", "explicit"],
        "delete",
        {"path": "a.md", "if_match": "explicit"},
    ),
    (
        ["note", "rename", "a.md", "b.md"],
        "rename",
        {"old_path": "a.md", "new_path": "b.md", "update_links": True},
    ),
    (
        ["folder", "move", "old", "new"],
        "move_folder",
        {"old_dir": "old", "new_dir": "new"},
    ),
    (
        [
            "fetch",
            "https://example.com",
            "a.md",
            "--download-timeout",
            "5",
            "--frontmatter",
            "{}",
        ],
        "fetch",
        {
            "url": "https://example.com",
            "path": "a.md",
            "timeout_s": 5.0,
            "frontmatter": {},
        },
    ),
    (["okf", "generate", "index"], "okf_generate_index", {}),
    (["okf", "seed", "log", "--folder", "team"], "okf_seed_log", {"folder": "team"}),
    (["git", "sync"], "git_sync", {}),
    (
        ["git", "sync", "--direction", "pull", "--dry-run"],
        "git_sync",
        {"direction": "pull", "dry_run": True},
    ),
    (["index", "reindex", "--force"], "reindex", {"force": True}),
]


@pytest.mark.parametrize(("command", "tool", "expected"), CASES)
def test_native_wire_mapping(
    command: list[str], tool: str, expected: dict[str, Any]
) -> None:
    from fastmcp.server.middleware import Middleware, MiddlewareContext
    from mcp.types import CallToolResult

    calls: list[tuple[str, dict[str, Any]]] = []
    envelope = {"status": "working", "job_id": "synthetic", "poll_with": "job_status"}

    class Capture(Middleware):
        async def on_call_tool(
            self, context: MiddlewareContext[Any], call_next: Any
        ) -> Any:
            del call_next
            calls.append((context.message.name, context.message.arguments))
            return CallToolResult(
                content=[],
                structured_content={"etag": "live"}
                if context.message.name == "read"
                else envelope,
            )

    server = FastMCP("wire-capture", middleware=[Capture()])

    # Advertise exact native properties; middleware captures the unmodified wire arguments.
    @server.tool(name=tool)
    def mutation(  # noqa: PLR0913, RUF100 -- synthetic native schema covers all adapters
        path: str = "",
        content: str = "",
        if_match: str | None = None,
        frontmatter: dict[str, Any] | None = None,
        old_text: str | None = None,
        new_text: str = "",
        line_start: int | None = None,
        line_end: int | None = None,
        create_if_missing: bool = False,
        old_path: str = "",
        new_path: str = "",
        update_links: bool = False,
        old_dir: str = "",
        new_dir: str = "",
        url: str = "",
        timeout_s: float = 30,
        folder: str = "",
        direction: str = "both",
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        return dict(locals())

    @server.tool()
    def read(path: str) -> dict[str, str]:
        return {"etag": path}

    with (
        serve_http(server.http_app()) as origin,
        patch(
            "markdown_vault_mcp.cli._build_vault",
            side_effect=AssertionError("local vault"),
        ),
        patch(
            "markdown_vault_mcp.config.ProjectConfig.from_env",
            side_effect=AssertionError("local config"),
        ),
    ):
        result = CliRunner().invoke(
            app, [*command, "--server", origin + "/mcp", "--json"]
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == envelope
    assert calls[-1] == (tool, expected)
    automatic = expected.get("if_match") == "live"
    assert [name for name, _ in calls] == (["read", tool] if automatic else [tool])


@pytest.mark.parametrize(
    "options",
    [
        ["note", "write", "a.md", "x", "--create", "--if-match", "e"],
        ["note", "write", "a.md", "x", "--frontmatter", "[]"],
        ["note", "write", "a.md", "x", "--frontmatter", "bad"],
        ["note", "delete", "a.md", "--if-match", ""],
        ["note", "edit", "a.md", "--new-text", "x"],
        ["note", "edit", "a.md", "--line-start", "2", "--new-text", "x"],
        [
            "note",
            "edit",
            "a.md",
            "--line-start",
            "2",
            "--line-end",
            "1",
            "--new-text",
            "x",
        ],
        [
            "note",
            "edit",
            "a.md",
            "--line-start",
            "0",
            "--line-end",
            "1",
            "--new-text",
            "x",
        ],
        ["fetch", "https://example.com", "a.md", "--download-timeout", "nan"],
        ["fetch", "https://example.com", "a.md", "--download-timeout", "0"],
        ["git", "sync", "--direction", "invalid"],
        ["index", "reindex", "--json", "--agent"],
        ["note", "append", "a.md", "x", "--local"],
        ["note", "write", "a.md"],
    ],
)
def test_invalid_inputs_never_connect(options: list[str]) -> None:
    with patch(
        "markdown_vault_mcp._cli_remote.call_write", side_effect=AssertionError("RPC")
    ):
        result = CliRunner().invoke(
            app, [*options, "--server", "https://example.com/mcp"]
        )
    assert result.exit_code == 2, result.output


@pytest.mark.parametrize(
    "configuration", ["malformed", "trust-auth", "elicit", "hidden"]
)
def test_verify_refuses_before_configuration(
    configuration: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_CONFIG", "/absent/malformed.toml")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY_MODE", configuration)
    with (
        patch(
            "markdown_vault_mcp._cli_client.resolve_settings",
            side_effect=AssertionError("credentials"),
        ),
        patch(
            "markdown_vault_mcp._cli_remote.call_write",
            side_effect=AssertionError("RPC"),
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "okf",
                "verify",
                "a.md",
                "--server",
                "invalid",
                "--timeout",
                "-1",
                "--json",
            ],
        )
    assert result.exit_code == 1, result.output
    assert "This CLI cannot ask for human review confirmation" in result.stderr
    assert "Nothing was written." in result.stderr


def test_missing_remote_selection_never_builds_local() -> None:
    with patch(
        "markdown_vault_mcp.cli._build_vault", side_effect=AssertionError("local vault")
    ):
        result = CliRunner().invoke(app, ["note", "write", "a.md", "x", "--create"])
    assert result.exit_code == 2
    assert "server" in result.stderr


@pytest.mark.parametrize("protect", [True, False])
def test_native_create_policy_is_not_invented(protect: bool) -> None:
    server = FastMCP("native-create")
    contents = ["existing"]

    @server.tool()
    def write(path: str, content: str) -> dict[str, str]:
        if protect:
            raise ToolError("Overwrite protection requires an etag\x1b[31m")
        contents[0] = content
        return {"path": path}

    with serve_http(server.http_app()) as origin:
        result = CliRunner().invoke(
            app,
            ["note", "write", "a.md", "new", "--create", "--server", origin + "/mcp"],
        )
    assert result.exit_code == (1 if protect else 0), result.output
    assert contents == (["existing"] if protect else ["new"])
    assert "\x1b" not in result.stderr
    if protect:
        assert "Overwrite protection requires an etag" in result.stderr


def test_explicit_stale_etag_is_not_retried() -> None:
    server = FastMCP("stale-etag")
    calls: list[str] = []

    @server.tool()
    def edit(path: str, old_text: str, new_text: str, if_match: str) -> dict[str, str]:
        calls.append(if_match)
        assert (path, old_text, new_text) == ("a.md", "old", "new")
        raise ToolError("Conflict: note changed")

    with serve_http(server.http_app()) as origin:
        result = CliRunner().invoke(
            app,
            [
                "note",
                "edit",
                "a.md",
                "--old-text",
                "old",
                "--new-text",
                "new",
                "--if-match",
                "stale",
                "--server",
                origin + "/mcp",
            ],
        )
    assert result.exit_code == 1, result.output
    assert "Conflict: note changed" in result.stderr
    assert calls == ["stale"]


def test_writer_profile_and_agent_output(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp._cli_remote import RemoteReply

    profile = tmp_path / "client.toml"
    profile.write_text(
        '[remote]\nurl="https://example.com/mcp"\ntoken_env="SYNTHETIC_WRITE_TOKEN"\ntimeout_seconds=12\n'
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_CONFIG", str(profile))
    monkeypatch.setenv("SYNTHETIC_WRITE_TOKEN", "fixture-secret")
    payload = {"path": "café\x1b[31m.md", "status": "completed"}
    with patch(
        "markdown_vault_mcp._cli_remote.call_write", return_value=RemoteReply(payload)
    ) as remote:
        result = CliRunner().invoke(
            app, ["note", "append", "a.md", "", "--agent", "--timeout", "7"]
        )
    assert result.exit_code == 0, result.output
    settings = remote.call_args.args[0]
    assert (settings.url, settings.token, settings.timeout) == (
        "https://example.com/mcp",
        "fixture-secret",
        7,
    )
    assert result.stdout.startswith("OK note append")
    assert "café" in result.stdout and "\x1b" not in result.stdout
    assert "fixture-secret" not in result.output


def test_writer_interrupt_exit_status() -> None:
    with patch(
        "markdown_vault_mcp._cli_remote.call_write", side_effect=KeyboardInterrupt
    ):
        result = CliRunner().invoke(
            app, ["index", "reindex", "--server", "https://example.com/mcp"]
        )
    assert result.exit_code == 130
