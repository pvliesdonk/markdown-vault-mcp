"""MCP wiring for the bounded shared current-file inventory."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp_pvl_core import is_tool_boundary

from markdown_vault_mcp._file_inventory import ScanLimits, Window, scan_current_files
from markdown_vault_mcp._tools.inventory import register
from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
from tests.fixtures.inventory import inventory_server

_START = "2026-11-14T00:00:00Z"
_END = "2026-11-15T00:00:00Z"
_STAMP = 1794614400 * 1_000_000_000
_ARGS = {"start": _START, "end": _END}


def _file(root: Path, name: str, stamp: int = _STAMP) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"malformed: [")
    os.utime(path, ns=(stamp, stamp))


async def test_served_inventory_is_metadata_only_and_has_no_host_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "untyped.md",
        "malformed.md",
        "file.pdf",
        "forbidden.exe",
        ".hidden/note.md",
        "excluded/note.md",
        "_conventions.md",
    ):
        _file(tmp_path, name)
    _file(tmp_path, "end.md", _STAMP + 86400 * 1_000_000_000)
    server = inventory_server(tmp_path, monkeypatch)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        tool = tools["list_changed_files"]
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.title and tool.icons
        assert "write" not in tools
        assert set(tool.input_schema["properties"]) == {
            "start",
            "end",
            "max_entries",
            "max_seconds",
        }
        original_open = Path.open

        def checked_open(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path.is_relative_to(tmp_path):
                raise AssertionError("vault content read")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", checked_open):
            response = await client.call_tool("list_changed_files", _ARGS)
        result = response.data
        assert set(result) == {
            "start",
            "end",
            "interval",
            "basis",
            "complete",
            "incomplete_reasons",
            "items",
        }
        assert result["complete"] is True
        assert [item["path"] for item in result["items"]] == [
            "file.pdf",
            "malformed.md",
            "untyped.md",
        ]
        assert str(tmp_path) not in json.dumps(result)
        assert all(not Path(item["path"]).is_absolute() for item in result["items"])
        partial = await client.call_tool(
            "list_changed_files", {**_ARGS, "max_entries": 1}
        )
        assert partial.data["complete"] is False
        assert partial.data["incomplete_reasons"] == ["scan_limit"]
        assert str(tmp_path) not in json.dumps(partial.data)
    registry = await server.local_provider.list_tools()
    registered = next(item for item in registry if item.name == "list_changed_files")
    assert is_tool_boundary(registered.fn)


@pytest.mark.parametrize(
    "arguments",
    [
        {"start": "2026-11-14"},
        {"end": _START},
        {"max_entries": 0},
        {"max_entries": 1_000_001},
        {"max_entries": True},
        {"max_seconds": 301},
        {"max_seconds": 0},
        {"start": "x" * 129},
        {"source_dir": "/not-the-served-vault"},
    ],
)
async def test_mcp_refuses_invalid_bounds_and_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any]
) -> None:
    server = inventory_server(tmp_path, monkeypatch)
    with patch(
        "markdown_vault_mcp._tools.inventory.scan_current_files",
        side_effect=AssertionError("scan reached"),
    ):
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("list_changed_files", {**_ARGS, **arguments})


async def test_operator_visibility_applies_without_new_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_TOOLS_DENY", "list_changed_files")
    # The explicit config's ServerConfig is authoritative, not ambient env.
    from dataclasses import replace

    from markdown_vault_mcp._server_deps import config_for
    from tests.server_factory import make_server

    initial = inventory_server(tmp_path, monkeypatch)
    config = config_for(initial)
    server = make_server(
        config=replace(
            config, server=replace(config.server, tools_deny=("list_changed_files",))
        )
    )
    async with Client(server) as client:
        assert "list_changed_files" not in {
            tool.name for tool in await client.list_tools()
        }
        with pytest.raises(ToolError):
            await client.call_tool("list_changed_files", _ARGS)


async def test_mcp_output_caps_use_shared_scanner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(1001):
        _file(tmp_path, f"{i:04}.md")
    server = inventory_server(tmp_path, monkeypatch)
    async with Client(server) as client:
        result = (await client.call_tool("list_changed_files", _ARGS)).data
    assert len(result["items"]) == 1000
    assert result["incomplete_reasons"] == ["result_limit"]
    assert result["complete"] is False
    assert len(json.dumps(result, ensure_ascii=True).encode()) < 1_048_576
    tiny = scan_current_files(
        tmp_path, Window(_START, _END), VaultSettings(), ScanLimits(result_bytes=1)
    )
    assert tiny["items"] == []
    assert tiny["incomplete_reasons"] == ["result_limit"]
    cancelled = Event()
    cancelled.set()
    stopped = scan_current_files(
        tmp_path, Window(_START, _END), VaultSettings(), ScanLimits(cancel=cancelled)
    )
    assert stopped["incomplete_reasons"] == ["cancelled"]
    assert stopped["items"] == []


async def test_cancelled_call_signals_worker() -> None:
    server = FastMCP("inventory-cancellation")
    register(server)
    tool = next(
        tool
        for tool in await server.local_provider.list_tools()
        if tool.name == "list_changed_files"
    )
    started, finished = Event(), Event()

    def observe(_service: Any, _window: Window, limits: ScanLimits) -> dict[str, Any]:
        started.set()
        assert limits.items == 1000 and limits.result_bytes == 1_000_000
        assert limits.cancel is not None
        try:
            assert limits.cancel.wait(3)
        finally:
            finished.set()
        return {}

    with patch("markdown_vault_mcp._tools.inventory._observe", side_effect=observe):
        task = asyncio.create_task(tool.fn(**_ARGS, service=SimpleNamespace()))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(finished.wait, 3)


async def test_unavailable_root_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "absent-sensitive-host-name"
    server = inventory_server(root, monkeypatch)
    async with Client(server) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool("list_changed_files", _ARGS)
    assert "configured vault is unavailable" in str(error.value)
    assert str(root) not in str(error.value)


@pytest.mark.parametrize("conventions_file", [None, "rules.md"])
async def test_mcp_uses_effective_conventions_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conventions_file: str | None
) -> None:
    _file(tmp_path, "_conventions.md")
    _file(tmp_path, "rules.md")
    server = inventory_server(tmp_path, monkeypatch, conventions_file=conventions_file)
    async with Client(server) as client:
        result = (await client.call_tool("list_changed_files", _ARGS)).data
    assert result["complete"] is True
    assert [item["path"] for item in result["items"]] == (
        ["_conventions.md", "rules.md"]
        if conventions_file is None
        else ["_conventions.md"]
    )
