"""Native MCP reads preserve result shapes without constructing a local vault."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from typer.testing import CliRunner

from markdown_vault_mcp._cli_client import ClientError, ClientOptions, ClientSettings
from markdown_vault_mcp._cli_remote import call_read
from markdown_vault_mcp.cli import app
from tests.fixtures.cli_http import serve_http

HITS = [
    {
        "path": "remote.md",
        "title": "Remote note",
        "score": 1.0,
        "sections": [],
        "frontmatter": {},
        "search_type": "keyword",
    }
]


def test_remote_search_uses_server_with_invalid_local_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FastMCP("synthetic-vault")

    @server.tool()
    def search(
        query: str,
        limit: int = 10,
        mode: str = "keyword",
        folder: str | None = None,
    ) -> list[dict[str, Any]]:
        return (
            HITS[:limit]
            if query == "remote" and mode == "keyword" and folder is None
            else []
        )

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", "/absent/local/vault")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "invalid-provider")
    with (
        serve_http(server.http_app(path="/proxy/vault")) as origin,
        patch(
            "markdown_vault_mcp.cli._build_vault",
            side_effect=AssertionError("local vault constructed"),
        ),
    ):
        result = CliRunner().invoke(
            app, ["search", "remote", "--server", origin + "/proxy/vault", "--json"]
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == HITS


@pytest.mark.parametrize(
    "kind", ["structured", "text", "error", "malformed", "bad-title"]
)
def test_remote_result_and_freshness(kind: str) -> None:
    server = FastMCP("result-shapes")

    @server.tool()
    def search(query: str) -> ToolResult:  # noqa: ARG001 -- synthetic tool schema
        if kind == "error":
            raise ValueError("secret-server-detail")
        if kind == "text":
            return ToolResult(
                content=[TextContent(type="text", text=json.dumps(HITS))],
                meta={"index_stale": True},
            )
        if kind == "malformed":
            return ToolResult(content=[TextContent(type="text", text="not JSON")])
        if kind == "bad-title":
            return ToolResult(structured_content={"result": [{**HITS[0], "title": 42}]})
        return ToolResult(
            structured_content={"result": HITS}, meta={"index_stale": True}
        )

    with serve_http(server.http_app()) as origin:
        settings = ClientSettings(origin + "/mcp", token="secret-token")
        if kind in {"error", "malformed", "bad-title"}:
            with pytest.raises(ClientError) as error:
                call_read(settings, "search", {"query": "x"})
            assert error.value.code == 1
            assert "secret" not in str(error.value)
        else:
            reply = call_read(settings, "search", {"query": "x"})
            assert reply.data == HITS
            assert reply.meta["index_stale"] is True


@pytest.mark.parametrize("missing", [False, True])
def test_remote_rejects_unsupported_argument_before_call(missing: bool) -> None:
    server = FastMCP("old-vault")

    @server.tool()
    def search(query: str) -> list[dict[str, Any]]:  # noqa: ARG001 -- synthetic tool schema
        raise AssertionError("unsupported request was called")

    if missing:
        server = FastMCP("no-search-tool")
    with (
        serve_http(server.http_app()) as origin,
        pytest.raises(ClientError, match=r"server.*support"),
    ):
        call_read(
            ClientSettings(origin + "/mcp"),
            "search",
            {"query": "x", "filters": {"tags": "x"}},
        )


def test_remote_deadline_bounds_slow_tool() -> None:
    server = FastMCP("slow-vault")

    @server.tool()
    async def search(query: str) -> list[dict[str, Any]]:  # noqa: ARG001 -- synthetic tool schema
        await asyncio.sleep(10)
        return []

    with (
        serve_http(server.http_app()) as origin,
        pytest.raises(ClientError, match="timed out"),
    ):
        call_read(
            ClientSettings(origin + "/mcp", timeout=0.2), "search", {"query": "x"}
        )


def test_local_selection_never_imports_remote_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from markdown_vault_mcp._cli_read import run_read

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_CLIENT_TOKEN", "")
    assert run_read("search", {}, ClientOptions(local=True), lambda: HITS).data == HITS


@pytest.mark.parametrize("score", [True, float("nan"), float("inf")])
def test_invalid_remote_scores_fail(score: float | bool) -> None:
    from types import SimpleNamespace

    from markdown_vault_mcp._cli_remote import _payload

    with pytest.raises(ClientError):
        _payload(
            SimpleNamespace(
                is_error=False,
                structured_content={"result": [{"path": "a.md", "score": score}]},
                meta={},
            ),
            "search",
        )


def test_authenticated_transport_and_unauthorized_diagnostic() -> None:
    from starlette.responses import Response

    server = FastMCP("protected-vault")

    @server.tool()
    def search(query: str) -> list[dict[str, Any]]:
        return HITS if query else []

    application = server.http_app()

    async def protected(scope: Any, receive: Any, send: Any) -> None:
        if (
            scope["type"] == "http"
            and dict(scope["headers"]).get(b"authorization")
            != b"Bearer synthetic-secret"
        ):
            await Response(status_code=401)(scope, receive, send)
        else:
            await application(scope, receive, send)

    with serve_http(protected) as origin:
        assert (
            call_read(
                ClientSettings(origin + "/mcp", token="synthetic-secret"),
                "search",
                {"query": "x"},
            ).data
            == HITS
        )
        with pytest.raises(ClientError, match="bearer authentication") as error:
            call_read(
                ClientSettings(origin + "/mcp", token="wrong-secret"),
                "search",
                {"query": "x"},
            )
        assert "wrong-secret" not in str(error.value)


def test_cross_origin_redirect_is_not_followed() -> None:
    from starlette.applications import Starlette
    from starlette.responses import RedirectResponse, Response
    from starlette.routing import Route

    received: list[Any] = []

    async def sink(request: Any) -> Response:
        received.append(request.headers)
        return Response(status_code=500)

    with serve_http(
        Starlette(routes=[Route("/capture", sink, methods=["GET", "POST"])])
    ) as target:

        async def redirect(_request: Any) -> RedirectResponse:
            return RedirectResponse(target + "/capture", status_code=307)

        with (
            serve_http(
                Starlette(routes=[Route("/mcp", redirect, methods=["GET", "POST"])])
            ) as origin,
            pytest.raises(ClientError),
        ):
            call_read(
                ClientSettings(origin + "/mcp", token="never-forward-secret"),
                "search",
                {"query": "x"},
            )
    assert received == []


def test_remote_extra_missing_has_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    original = builtins.__import__

    def missing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fastmcp":
            raise ImportError("synthetic missing extra")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ClientError, match=r"markdown-vault-mcp\[mcp\]"):
        call_read(ClientSettings("https://example.com/mcp"), "search", {"query": "x"})
    from markdown_vault_mcp._cli_read import run_read

    assert run_read("search", {}, ClientOptions(local=True), lambda: HITS).data == HITS


def test_human_remote_output_escapes_terminal_controls() -> None:
    from markdown_vault_mcp._cli_remote import RemoteReply

    hits = [{"path": "café\x1b[31m.md", "title": "title\r\nforged", "score": 1.0}]
    with patch(
        "markdown_vault_mcp._cli_remote.call_read", return_value=RemoteReply(hits)
    ):
        result = CliRunner().invoke(
            app, ["search", "x", "--server", "https://example.com/mcp"], color=True
        )
    assert result.exit_code == 0, result.output
    assert "café" in result.stdout
    assert "\x1b" not in result.stdout
    assert "title\r\nforged" not in result.stdout


def test_interrupt_closes_remote_command_with_exit_130() -> None:
    import signal
    import subprocess
    import sys
    import threading

    started = threading.Event()
    server = FastMCP("interrupted-vault")

    @server.tool()
    async def search(
        query: str, limit: int = 10, mode: str = "keyword", folder: str | None = None
    ) -> list[dict[str, Any]]:
        if query != "x" or limit != 10 or mode != "keyword" or folder is not None:
            return []
        started.set()
        await asyncio.sleep(30)
        return []

    with serve_http(server.http_app()) as origin:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "markdown_vault_mcp.cli",
                "search",
                "x",
                "--server",
                origin + "/mcp",
                "--json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert started.wait(30), (
                process.poll(),
                process.communicate(timeout=1)
                if process.poll() is not None
                else "Child did not reach the MCP read",
            )
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 130, (stdout, stderr)
            assert "Traceback" not in stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


@pytest.mark.parametrize(
    "options", [["--source-dir", "."], ["--local"], ["--mode", "typo"]]
)
def test_invalid_remote_selection_fails_before_io(options: list[str]) -> None:
    with (
        patch(
            "markdown_vault_mcp.cli._build_vault",
            side_effect=AssertionError("local construction"),
        ),
        patch(
            "markdown_vault_mcp._cli_remote.call_read",
            side_effect=AssertionError("network call"),
        ),
    ):
        result = CliRunner().invoke(
            app, ["search", "x", "--server", "https://example.com/mcp", *options]
        )
    assert result.exit_code == 2, result.output


def test_unusable_content_and_local_only_remote_settings_fail() -> None:
    from mcp.types import CallToolResult

    from markdown_vault_mcp._cli_remote import _payload

    with pytest.raises(ClientError):
        _payload(CallToolResult(content=[]), "search")
    with pytest.raises(ClientError):
        call_read(ClientSettings(None), "search", {})


def test_interrupt_diagnostic_exits_130() -> None:
    with patch(
        "markdown_vault_mcp._cli_remote.call_read", side_effect=KeyboardInterrupt
    ):
        result = CliRunner().invoke(
            app, ["search", "x", "--server", "https://example.com/mcp"]
        )
    assert result.exit_code == 130
