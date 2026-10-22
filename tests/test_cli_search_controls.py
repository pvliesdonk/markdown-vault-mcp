"""Search controls preserve the same request and payload at either boundary."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from tests.fixtures.cli_http import serve_http

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid", "auto"])
def test_remote_controls(mode: str) -> None:
    server = FastMCP("search-controls")
    received: list[dict[str, Any]] = []

    @server.tool()
    def search(  # noqa: PLR0913, RUF100 -- native flat MCP schema; structural-only rule
        query: str,
        limit: int = 10,
        mode: str | None = None,
        folder: str | None = None,
        filters: dict[str, str] | None = None,
        chunks_per_file: int | None = None,
        snippet_words: int | None = None,
        wait_for_pending_writes: bool = False,
    ) -> list[dict[str, Any]]:
        received.append(
            {key: value for key, value in locals().items() if key != "received"}
        )
        return []

    with serve_http(server.http_app()) as origin:
        result = CliRunner().invoke(
            app,
            [
                "search",
                "needle",
                "--server",
                origin + "/mcp",
                "--mode",
                mode,
                "--filter",
                "tags=文=字",
                "--filter",
                "kind=note",
                "--folder",
                "",
                "--chunks-per-file",
                "3",
                "--snippet-words",
                "0",
                "--wait-for-pending-writes",
                "-n",
                "7",
                "--json",
            ],
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert received == [
        {
            "query": "needle",
            "limit": 7,
            "mode": None if mode == "auto" else mode,
            "folder": "",
            "filters": {"tags": "文=字", "kind": "note"},
            "chunks_per_file": 3,
            "snippet_words": 0,
            "wait_for_pending_writes": True,
        }
    ]


@pytest.mark.parametrize(
    "flags",
    [
        ["--mode", "typo"],
        ["--chunks-per-file", "0"],
        ["--snippet-words", "-1"],
        ["--filter", "missing-equals"],
        ["--filter", "=empty-key"],
        ["--filter", "tag=a", "--filter", "tag=b"],
        ["--json", "--agent"],
    ],
)
def test_invalid_controls_fail_before_io(flags: list[str]) -> None:
    with patch(
        "markdown_vault_mcp.cli._build_vault", side_effect=AssertionError("I/O")
    ):
        result = CliRunner().invoke(app, ["search", "needle", *flags])
    assert result.exit_code == 2, result.output
    assert "ERROR" in result.stderr or "Error" in result.stderr
    assert result.stdout == ""


def test_local_filters_snippets_and_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEXED_FIELDS", "tags")
    (tmp_path / "a.md").write_text(
        "---\ntags: [kept]\n---\n# Title\nneedle one two three\n"
    )
    (tmp_path / "b.md").write_text(
        "---\ntags: [other]\n---\n# Other\nneedle four five\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "search",
            "needle",
            "--local",
            "--source-dir",
            str(tmp_path),
            "--mode",
            "auto",
            "--filter",
            "tags=kept",
            "--chunks-per-file",
            "1",
            "--snippet-words",
            "0",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    hits = json.loads(result.stdout)
    assert [h["path"] for h in hits] == ["a.md"]
    assert "one two three" in hits[0]["sections"][0]["content"]


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_explicit_vector_mode_refuses_missing_embeddings(
    tmp_path: Path, mode: str
) -> None:
    (tmp_path / "a.md").write_text("needle")
    result = CliRunner().invoke(
        app,
        [
            "search",
            "needle",
            "--local",
            "--source-dir",
            str(tmp_path),
            "--mode",
            mode,
        ],
    )
    assert result.exit_code == 1
    assert "not enabled" in result.stderr.lower()
    assert "keyword" in result.stderr.lower()
    assert result.stdout == ""


def test_default_request_does_not_require_new_remote_options() -> None:
    server = FastMCP("older-search")

    @server.tool()
    def search(
        query: str,
        limit: int = 10,
        mode: str = "keyword",
        folder: str | None = None,
    ) -> list[dict[str, Any]]:
        assert (query, limit, mode, folder) == ("x", 10, "keyword", None)
        return []

    with serve_http(server.http_app()) as origin:
        result = CliRunner().invoke(
            app, ["search", "x", "--server", origin + "/mcp", "--json"]
        )
        unsupported = CliRunner().invoke(
            app, ["search", "x", "--server", origin + "/mcp", "--filter", "tags=a"]
        )
    assert result.exit_code == 0, result.output
    assert unsupported.exit_code == 1
    assert "support" in unsupported.stderr


def test_source_conflict_and_dispatch_use_one_settings_snapshot(tmp_path: Path) -> None:
    from markdown_vault_mcp._cli_client import ClientSettings

    with (
        patch(
            "markdown_vault_mcp._cli_client.resolve_settings",
            return_value=ClientSettings(None),
        ) as resolve,
        patch(
            "markdown_vault_mcp._cli_remote.resolve_settings",
            side_effect=AssertionError("second selection"),
        ),
        patch("markdown_vault_mcp.cli._local_search", return_value=[]),
    ):
        result = CliRunner().invoke(
            app, ["search", "x", "--source-dir", str(tmp_path), "--json"]
        )
    assert result.exit_code == 0, result.exception
    assert json.loads(result.stdout) == []
    resolve.assert_called_once()


@pytest.mark.parametrize("active", [False, True])
async def test_local_payload_agrees_with_native_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active: bool
) -> None:
    from fastmcp import Client

    from tests.conftest import _parse_tool_data, wait_for_mcp_writer_drain
    from tests.server_factory import make_server

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    if active:
        (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Bundle\n')
    (tmp_path / "a.md").write_text(
        "---\nstatus: deprecated\nstale_after: 2020-01-01\n---\n# Title\nneedle one two three\n"
    )
    async with Client(make_server()) as client:
        await wait_for_mcp_writer_drain(client)
        expected = _parse_tool_data(
            await client.call_tool(
                "search", {"query": "needle", "mode": "keyword", "snippet_words": 0}
            )
        )
    result = CliRunner().invoke(
        app, ["search", "needle", "--local", "--snippet-words", "0", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == expected
