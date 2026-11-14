"""Stable typed diagnostics preserve statuses, channels and terminal framing."""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Literal
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from markdown_vault_mcp._cli_client import ClientError
from markdown_vault_mcp._cli_errors import diagnostic, render_error
from markdown_vault_mcp._read_payloads import ReadPayloadError
from markdown_vault_mcp.cli import app
from markdown_vault_mcp.exceptions import (
    ConfigurationError,
    EmbeddingsNotPublishedError,
    InvalidRequestError,
    MarkdownMCPError,
)


@pytest.mark.parametrize(
    "code,kind,reason",
    [(2, "usage", "invalid_request"), (1, "operation", "operation_failed")],
)
def test_legacy_error_compatibility(code: int, kind: str, reason: str) -> None:
    error = ClientError("safe", code)
    assert error.args == ("safe",)
    assert str(error) == "safe"
    assert error.code == code
    assert (error.kind, error.reason, error.hint) == (kind, reason, None)


@pytest.mark.parametrize("hint", [None, "", "Recovery"])
def test_json_error_omits_absent_hint(hint: str | None) -> None:
    with patch("typer.echo") as echo:
        render_error("search", ClientError("safe", hint=hint), "json")
    echo.assert_called_once()
    assert echo.call_args.kwargs == {"err": True}
    expected: dict[str, Any] = {
        "kind": "usage",
        "code": "invalid_request",
        "message": "safe",
        "exit_code": 2,
    }
    if hint:
        expected["hint"] = hint
    assert json.loads(echo.call_args.args[0]) == {"error": expected}


@given(
    st.text(st.integers(0, 0x10FFFF).map(chr)),
    st.text(st.integers(0, 0x10FFFF).map(chr)),
)
@settings(max_examples=60, deadline=None)
def test_message_hint_framing(message: str, hint: str) -> None:
    error = ClientError(message, kind="remote", reason="remote_tool_failed", hint=hint)
    outputs: tuple[Literal["text", "agent", "json"], ...] = ("text", "agent", "json")
    for output in outputs:
        with patch("typer.echo") as echo:
            render_error("note append", error, output)
        lines = [call.args[0] for call in echo.call_args_list]
        assert all(call.kwargs == {"err": True} for call in echo.call_args_list)
        assert all(len(line.splitlines()) <= 1 for line in lines)
        assert all(
            not unicodedata.category(c).startswith("C") and c not in "\u2028\u2029"
            for line in lines
            for c in line
        )
        if output == "json":
            data = json.loads(lines[0])["error"]
            assert data["message"] == message
            assert data.get("hint") == (hint or None)
        else:
            assert lines[0].startswith(
                "ERR note append remote code=remote_tool_failed:"
                if output == "agent"
                else "ERROR:"
            )
            if hint:
                assert lines[1].startswith("Hint:" if output == "agent" else "hint:")


@pytest.mark.parametrize(
    "error,reason,code",
    [
        (ConfigurationError("safe"), "configuration_error", 1),
        (InvalidRequestError("safe"), "invalid_request", 1),
        (MarkdownMCPError("safe"), "operation_failed", 1),
        (ReadPayloadError("safe"), "invalid_request", 2),
        (ReadPayloadError("safe", 1), "invalid_payload", 1),
    ],
)
def test_typed_conversion(error: Exception, reason: str, code: int) -> None:
    converted = diagnostic(error)
    assert (str(converted), converted.reason, converted.code) == ("safe", reason, code)
    assert converted.hint


@pytest.mark.parametrize(
    "command",
    [
        ["search", "x"],
        ["read", "a.md"],
        ["links", "to", "a.md"],
        ["query", "count"],
        ["query", "path", "a"],
        ["note", "append", "a.md", "x"],
    ],
)
def test_conflicting_modes_choose_json_without_io(command: list[str]) -> None:
    with patch(
        "markdown_vault_mcp._cli_client._profile",
        side_effect=AssertionError("profile read"),
    ):
        result = CliRunner().invoke(app, [*command, "--json", "--agent"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "conflicting_options"


@pytest.mark.parametrize(
    "boundary,command",
    [
        ("markdown_vault_mcp.cli._local_search", ["search", "x"]),
        ("markdown_vault_mcp._cli_discovery.local_read", ["read", "a.md"]),
        ("markdown_vault_mcp._cli_query.local_read", ["query", "count"]),
    ],
)
@pytest.mark.parametrize(
    "error,reason",
    [
        (ConfigurationError("safe"), "configuration_error"),
        (MarkdownMCPError("safe"), "operation_failed"),
        (EmbeddingsNotPublishedError(failed_batches=1), "operation_failed"),
        (InvalidRequestError("safe"), "invalid_request"),
    ],
)
def test_local_typed_failures_in_every_boundary(
    boundary: str, command: list[str], error: Exception, reason: str
) -> None:
    with patch(boundary, side_effect=error):
        result = CliRunner().invoke(app, [*command, "--local", "--json"])
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == reason


def test_search_unrelated_valueerror_propagates() -> None:
    with patch(
        "markdown_vault_mcp.cli._local_search", side_effect=ValueError("backend")
    ):
        result = CliRunner().invoke(app, ["search", "x", "--local", "--json"])
    assert type(result.exception) is ValueError
    assert result.stderr == ""


@pytest.mark.parametrize(
    "options", [[], ["--agent"], ["--json"], ["--json", "--agent"]]
)
def test_verify_keeps_unconditional_exit_one(options: list[str]) -> None:
    with (
        patch(
            "markdown_vault_mcp._cli_write.resolve_settings",
            side_effect=AssertionError("profile"),
        ),
        patch(
            "markdown_vault_mcp._cli_remote.call_write",
            side_effect=AssertionError("RPC"),
        ),
    ):
        result = CliRunner().invoke(app, ["okf", "verify", "a.md", *options])
    assert result.exit_code == 1
    assert result.stdout == ""
    if "--json" in options:
        assert json.loads(result.stderr)["error"]["code"] == "elicitation_required"
    elif "--agent" in options:
        assert result.stderr.startswith(
            "ERR okf verify operation code=elicitation_required:"
        )
    else:
        assert result.stderr.startswith("ERROR:")


@pytest.mark.parametrize(
    "command,reason",
    [
        (["note", "delete", "a.md", "--if-match", ""], "invalid_if_match"),
        (
            ["note", "write", "a.md", "x", "--create", "--if-match", "e"],
            "conflicting_options",
        ),
        (["note", "write", "a.md", "x", "--frontmatter", "[]"], "invalid_frontmatter"),
        (["note", "write", "a.md", "x", "--frontmatter", "bad"], "invalid_frontmatter"),
        (
            ["fetch", "https://example.com", "a.md", "--download-timeout", "nan"],
            "invalid_download_timeout",
        ),
        (["note", "append", "a.md", "x"], "server_url_required"),
        (["query", "count", "--mode", "semantic"], "invalid_request"),
    ],
)
def test_argument_refusal_reasons(command: list[str], reason: str) -> None:
    with patch(
        "markdown_vault_mcp._cli_remote.call_write", side_effect=AssertionError("RPC")
    ):
        result = CliRunner().invoke(app, [*command, "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stderr)["error"]["code"] == reason
