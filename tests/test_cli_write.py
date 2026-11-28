"""Hosted command mapping at the transport boundary; server policy stays native."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp._cli_client import ClientError, ClientSettings
from markdown_vault_mcp._cli_remote import RemoteReply
from markdown_vault_mcp.cli import app

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
    automatic = expected.get("if_match") == "live"
    arguments = {
        key: value
        for key, value in expected.items()
        if not (automatic and key == "if_match")
    }
    payload = (
        {"status": "working", "job_id": "synthetic", "poll_with": "job_status"}
        if tool == "reindex"
        else {"path": "a.md"}
    )
    reply = RemoteReply(payload)
    with (
        patch(
            "markdown_vault_mcp._cli_remote.call_write", return_value=reply
        ) as remote,
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
            app, [*command, "--server", "https://example.com/mcp", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == payload
        remote.assert_called_once_with(
            ClientSettings("https://example.com/mcp"),
            tool,
            arguments,
            read_etag_from="a.md" if automatic else None,
        )
        if command[-1] == "--create":
            remote.side_effect = ClientError(
                "Overwrite protection requires an etag\x1b[31m", 1
            )
            error = CliRunner().invoke(
                app, [*command, "--server", "https://example.com/mcp"]
            )
            assert error.exit_code == 1
            assert "Overwrite protection requires an etag" in error.stderr
            assert "\x1b" not in error.stderr


@pytest.mark.parametrize(
    "options",
    [
        ["note", "write", "a.md", "x", "--create", "--if-match", "e"],
        ["note", "write", "a.md", "x", "--frontmatter", "[]"],
        ["note", "write", "a.md", "x", "--frontmatter", "bad"],
        ["note", "write", "a.md", "x", "--frontmatter", '{"x":NaN}'],
        ["note", "write", "a.md", "x", "--frontmatter", '{"x":Infinity}'],
        ["note", "write", "a.md", "x", "--frontmatter", '{"x":-Infinity}'],
        ["note", "write", "a.md", "x", "--frontmatter", '{"nested":{"x":[1e999]}}'],
        ["note", "delete", "a.md", "--if-match", ""],
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


def test_verify_refuses_before_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_CONFIG", "/absent/malformed.toml")
    with (
        patch(
            "markdown_vault_mcp._cli_write.resolve_settings",
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


def test_writer_forwards_settings_and_agent_mode(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "client.toml"
    profile.write_text(
        '[remote]\nurl="https://example.com/mcp"\ntoken_env="SYNTHETIC_WRITE_TOKEN"\ntimeout_seconds=12\n'
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_CONFIG", str(profile))
    monkeypatch.setenv("SYNTHETIC_WRITE_TOKEN", "fixture-secret")
    reply = RemoteReply({"path": "café\x1b[31m.md", "status": "completed"})
    with (
        patch(
            "markdown_vault_mcp._cli_remote.call_write", return_value=reply
        ) as remote,
        patch("markdown_vault_mcp._cli_write.render_reply") as render,
    ):
        result = CliRunner().invoke(
            app, ["note", "append", "a.md", "", "--agent", "--timeout", "7"]
        )
    assert result.exit_code == 0, result.output
    assert remote.call_args.args[0] == ClientSettings(
        "https://example.com/mcp", token="fixture-secret", timeout=7
    )
    render.assert_called_once_with("note append", reply, "agent")


def test_writer_interrupt_exit_status() -> None:
    with patch(
        "markdown_vault_mcp._cli_remote.call_write", side_effect=KeyboardInterrupt
    ):
        result = CliRunner().invoke(
            app, ["index", "reindex", "--server", "https://example.com/mcp"]
        )
    assert result.exit_code == 130
