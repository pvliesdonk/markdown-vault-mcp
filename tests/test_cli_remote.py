"""Native MCP reads preserve result shapes without constructing a local vault."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from typer.testing import CliRunner

from markdown_vault_mcp._cli_client import ClientError, ClientOptions, ClientSettings
from markdown_vault_mcp._cli_remote import call_read
from markdown_vault_mcp.cli import app
from tests.fixtures.cli_http import serve_http

if TYPE_CHECKING:
    from pathlib import Path

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
            assert error.value.reason == (
                "remote_tool_failed" if kind == "error" else "invalid_remote_payload"
            )
            assert error.value.hint
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
    with serve_http(server.http_app()) as origin:
        settings = ClientSettings(origin + "/mcp")
        with pytest.raises(ClientError, match=r"server.*support") as error:
            call_read(settings, "search", {"query": "x", "filters": {"tags": "x"}})
        assert error.value.reason == (
            "remote_tool_unsupported" if missing else "remote_options_unsupported"
        )


def test_remote_deadline_bounds_slow_tool() -> None:
    server = FastMCP("slow-vault")

    @server.tool()
    async def search(query: str) -> list[dict[str, Any]]:  # noqa: ARG001 -- synthetic tool schema
        await asyncio.sleep(10)
        return []

    with serve_http(server.http_app()) as origin:
        settings = ClientSettings(origin + "/mcp", timeout=0.2)
        with pytest.raises(ClientError, match="timed out") as error:
            call_read(settings, "search", {"query": "x"})
        assert (error.value.kind, error.value.reason) == ("timeout", "remote_timeout")


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

    result = SimpleNamespace(
        is_error=False,
        structured_content={"result": [{"path": "a.md", "score": score}]},
        meta={},
    )
    with pytest.raises(ClientError):
        _payload(result, "search")


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
        settings = ClientSettings(origin + "/mcp", token="wrong-secret")
        with pytest.raises(ClientError, match="bearer authentication") as error:
            call_read(settings, "search", {"query": "x"})
        assert "wrong-secret" not in str(error.value)
        assert error.value.reason == "remote_connection_failed"
        assert "wrong-secret" not in (error.value.hint or "")
        assert re.search(r"failed \((?!ExceptionGroup)\w+\)", str(error.value))


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

        with serve_http(
            Starlette(routes=[Route("/mcp", redirect, methods=["GET", "POST"])])
        ) as origin:
            settings = ClientSettings(origin + "/mcp", token="never-forward-secret")
            with pytest.raises(ClientError) as error:
                call_read(settings, "search", {"query": "x"})
            assert error.value.reason == "remote_connection_failed"
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
    settings = ClientSettings("https://example.com/mcp")
    with pytest.raises(ClientError, match=r"markdown-vault-mcp\[mcp\]") as error:
        call_read(settings, "search", {"query": "x"})
    assert error.value.reason == "remote_client_unavailable"
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

    result = CallToolResult(content=[])
    with pytest.raises(ClientError):
        _payload(result, "search")
    settings = ClientSettings(None)
    with pytest.raises(ClientError):
        call_read(settings, "search", {})


def test_interrupt_diagnostic_exits_130() -> None:
    with patch(
        "markdown_vault_mcp._cli_remote.call_read", side_effect=KeyboardInterrupt
    ):
        result = CliRunner().invoke(
            app, ["search", "x", "--server", "https://example.com/mcp"]
        )
    assert result.exit_code == 130


def test_failure_kind_names_root_cause_without_text() -> None:
    from markdown_vault_mcp._cli_remote import _failure_kind

    root = ConnectionRefusedError("secret-bearing text")
    wrapper = RuntimeError("Client failed to connect")
    wrapper.__cause__ = root
    assert _failure_kind(ExceptionGroup("g", [wrapper])) == "ConnectionRefusedError"
    assert _failure_kind(ExceptionGroup("g", [ValueError(), KeyError()])) == (
        "ExceptionGroup"
    )
    looped = ValueError()
    looped.__cause__ = KeyError()
    looped.__cause__.__cause__ = looped
    assert _failure_kind(looped) == "ValueError"


@pytest.mark.parametrize("etag", ["fresh", None, "", 42])
def test_write_reads_exact_etag_or_aborts(etag: Any) -> None:
    from fastmcp import Client

    from markdown_vault_mcp._cli_remote import call_write

    original = Client.list_tools
    discoveries: list[str] = []

    async def discover(self: Any, *args: Any, **kwargs: Any) -> Any:
        discoveries.append("list_tools")
        return await original(self, *args, **kwargs)

    calls: list[tuple[str, Any]] = []
    server = FastMCP("write-etag")

    @server.tool()
    def read(path: str) -> dict[str, Any]:
        calls.append(("read", path))
        return {"etag": etag}

    @server.tool()
    def write(path: str, content: str, if_match: str) -> dict[str, Any]:
        calls.append(
            ("write", {"path": path, "content": content, "if_match": if_match})
        )
        return {"path": path, "etag": "next"}

    with (
        serve_http(server.http_app()) as origin,
        patch("fastmcp.Client", wraps=Client) as clients,
        patch.object(Client, "list_tools", discover),
    ):
        settings = ClientSettings(origin + "/mcp")
        if etag == "fresh":
            assert call_write(
                settings,
                "write",
                {"path": "a.md", "content": ""},
                read_etag_from="a.md",
            ).data == {"path": "a.md", "etag": "next"}
            assert calls == [
                ("read", "a.md"),
                ("write", {"path": "a.md", "content": "", "if_match": etag}),
            ]
            assert clients.call_count == 1
            assert discoveries == ["list_tools"]
        else:
            with pytest.raises(ClientError, match="etag") as error:
                call_write(
                    settings,
                    "write",
                    {"path": "a.md", "content": ""},
                    read_etag_from="a.md",
                )
            assert calls == [("read", "a.md")]
            assert error.value.reason == "missing_remote_etag"
            assert "no mutation" in (error.value.hint or "")


@pytest.mark.parametrize(
    ("failure", "message", "expected_calls", "extra_args"),
    [
        ("missing", "write", [], {}),
        ("schema", "write", [], {"unknown": "option"}),
        ("read-missing", "read", [], {}),
        ("read-schema", "read", [], {}),
        ("read-error", "Read cap exceeded", ["read"], {}),
        ("conflict-auto", "Conflict: stale etag", ["read", "write"], {}),
        ("conflict-explicit", "Conflict: stale etag", ["write"], {"if_match": "stale"}),
    ],
)
def test_write_checks_before_calls_and_preserves_errors(
    failure: str, message: str, expected_calls: list[str], extra_args: dict[str, str]
) -> None:
    from fastmcp import Client

    from markdown_vault_mcp._cli_remote import call_write

    server = FastMCP("write-failures")
    calls: list[str] = []
    original = Client.call_tool_mcp

    async def submit(
        self: Any, name: str, arguments: Any, *args: Any, **kwargs: Any
    ) -> Any:
        calls.append(name)
        return await original(self, name, arguments, *args, **kwargs)

    if failure == "read-schema":

        @server.tool()
        def read(other: str) -> dict[str, str]:
            raise AssertionError(other)
    elif failure != "read-missing":

        @server.tool()
        def read(path: str) -> dict[str, str]:
            assert path == "a.md"
            if failure == "read-error":
                raise ToolError("Read cap exceeded")
            return {"etag": "live"}

    if failure != "missing":

        @server.tool()
        def write(path: str, content: str, if_match: str | None = None) -> ToolResult:
            assert path == "a.md" and content == "new"
            assert if_match == ("stale" if failure == "conflict-explicit" else "live")
            raise ToolError("Conflict: stale etag\x1b[31m")

    args = {"path": "a.md", "content": "new", **extra_args}
    with (
        serve_http(server.http_app()) as origin,
        patch.object(Client, "call_tool_mcp", submit),
        pytest.raises(ClientError) as error,
    ):
        call_write(
            ClientSettings(origin + "/mcp"),
            "write",
            args,
            read_etag_from=None if failure == "conflict-explicit" else "a.md",
        )
    assert message in str(error.value)
    assert error.value.reason == (
        "remote_tool_unsupported"
        if failure in {"missing", "read-missing"}
        else "remote_options_unsupported"
        if failure in {"schema", "read-schema"}
        else "remote_tool_failed"
    )
    if "write" in expected_calls:
        assert "state" in (error.value.hint or "") and "before retrying" in (
            error.value.hint or ""
        )
    assert calls == expected_calls


def test_write_deadline_covers_read_and_submitted_mutation() -> None:
    from markdown_vault_mcp._cli_remote import call_write

    calls: list[str] = []
    server = FastMCP("write-deadline")

    @server.tool()
    async def read(path: str) -> dict[str, str]:
        calls.append("read")
        assert path == "a.md"
        await asyncio.sleep(0.3)
        return {"etag": "live"}

    @server.tool()
    async def write(path: str, if_match: str) -> dict[str, str]:
        calls.append("write")
        assert path == "a.md" and if_match == "live"
        await asyncio.sleep(0.4)
        return {"etag": "next"}

    with (
        serve_http(server.http_app()) as origin,
        pytest.raises(ClientError, match="outcome may be unknown") as error,
    ):
        call_write(
            ClientSettings(origin + "/mcp", timeout=0.6),
            "write",
            {"path": "a.md"},
            read_etag_from="a.md",
        )
    assert calls == ["read", "write"]
    assert error.value.reason == "remote_timeout"
    assert "state before retrying" in (error.value.hint or "")


def test_write_slow_native_reindex_returns_job_before_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hosted reindex returns its accepted job while native work continues."""
    from markdown_vault_mcp.facets.index import IndexFacet
    from markdown_vault_mcp.server import make_server
    from tests.fixtures.reindex import delayed_reindex

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "index.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_JOBS_SOFT_DEADLINE_S", "0.15")
    with delayed_reindex(1.0) as slow_reindex:
        monkeypatch.setattr(IndexFacet, "reindex_async", slow_reindex)
        server = make_server(transport="http")
        with serve_http(server.http_app()) as origin:
            result = CliRunner().invoke(
                app,
                [
                    "index",
                    "reindex",
                    "--server",
                    origin + "/mcp",
                    "--timeout",
                    "0.5",
                    "--json",
                ],
            )
            assert result.exit_code == 0, result.output
            data = json.loads(result.stdout)
            assert data["status"] == "working"
            assert data["job_id"]
            assert data["poll_with"] == "get_job_result"


@pytest.mark.parametrize("malformed", [False, True])
def test_write_protocol_failures_require_state_check(malformed: bool) -> None:
    from markdown_vault_mcp._cli_remote import call_write

    calls: list[str] = []
    server = FastMCP("malformed-write")

    @server.tool()
    def append(path: str, content: str) -> ToolResult:
        calls.append(path + content)
        return ToolResult(
            content=[TextContent(type="text", text="not JSON")] if malformed else []
        )

    with serve_http(server.http_app()) as origin, pytest.raises(ClientError) as error:
        call_write(
            ClientSettings(origin + "/mcp"), "append", {"path": "a.md", "content": "x"}
        )
    assert calls == ["a.mdx"]
    assert error.value.reason == "invalid_remote_payload"
    assert "state before retrying" in (error.value.hint or "")


@pytest.mark.parametrize("mode", ["--json", "--agent"])
def test_late_write_payload_failure_requires_state_check(
    tmp_path: Path, mode: str
) -> None:
    note = tmp_path / "synthetic-note.md"
    note.write_text("Before")
    calls: list[tuple[str, str]] = []
    server = FastMCP("late-invalid-write")

    @server.tool()
    def append(path: str, content: str) -> ToolResult:
        calls.append((path, content))
        note.write_text(note.read_text() + content)
        return ToolResult(content=[TextContent(type="text", text='{"score": NaN}')])

    with serve_http(server.http_app()) as origin:
        result = CliRunner().invoke(
            app,
            ["note", "append", note.name, "After", "--server", origin + "/mcp", mode],
        )
    assert calls == [(note.name, "After")]
    assert note.read_text() == "BeforeAfter"
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    if mode == "--json":
        error = json.loads(result.stderr.splitlines()[-1])["error"]
        assert (error["kind"], error["code"], error["exit_code"]) == (
            "operation",
            "invalid_payload",
            1,
        )
        hint = error["hint"]
    else:
        assert "ERR note append operation code=invalid_payload:" in result.stderr
        hint = result.stderr.splitlines()[-1]
        assert hint.startswith("Hint:")
    assert "current state before retrying" in hint
    assert "outcome may be unknown" in hint


def test_write_connection_failure_requires_state_check() -> None:
    from markdown_vault_mcp._cli_remote import call_write

    with (
        patch(
            "markdown_vault_mcp._cli_remote._call", side_effect=OSError("secret-token")
        ),
        pytest.raises(ClientError) as error,
    ):
        call_write(ClientSettings("https://example.com/mcp"), "append", {})
    assert error.value.reason == "remote_connection_failed"
    assert "secret-token" not in str(error.value)
    assert "state before retrying" in (error.value.hint or "")


def test_remote_url_required_is_typed() -> None:
    with pytest.raises(ClientError) as error:
        call_read(ClientSettings(None), "search", {})
    assert (error.value.code, error.value.reason) == (2, "server_url_required")
