"""MCP Apps tools distinguish internal faults from invalid requests (#1640)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_apps import _hashed
from markdown_vault_mcp.facets.reader import ReaderFacet
from tests.conftest import wait_for_mcp_writer_drain
from tests.server_factory import make_server


async def _call(name: str, arguments: dict[str, Any]) -> Any:
    async with Client(make_server()) as client:
        await wait_for_mcp_writer_drain(client)
        return await client.call_tool_mcp(name, arguments)


@pytest.mark.usefixtures("_mcp_env")
@pytest.mark.parametrize(
    ("name", "method", "arguments"),
    [
        (_hashed("vault_context"), "get_context", {"path": "simple.md"}),
        ("show_context", "get_context", {"path": "simple.md"}),
        (_hashed("vault_search"), "search", {"query": "simple", "mode": "keyword"}),
    ],
)
async def test_internal_value_error_is_a_logged_fault(
    name: str,
    method: str,
    arguments: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A plain ValueError is a fault for each app tool, not a successful result."""

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ValueError("corrupt index")

    monkeypatch.setattr(ReaderFacet, method, broken)
    caplog.set_level(logging.INFO)
    result = await _call(name, arguments)
    assert result.is_error
    assert "server-side error; the request itself was fine" in result.content[0].text
    boundary = [r for r in caplog.records if r.getMessage().startswith("tool_failed")]
    assert len(boundary) == 1
    assert boundary[0].levelno == logging.ERROR


@pytest.mark.usefixtures("_mcp_env")
async def test_path_outside_vault_is_not_a_missing_note(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An escaped path is a refusal, not a missing-note summary."""
    caplog.set_level(logging.INFO, logger="fastmcp.middleware.requests")
    result = await _call("show_context", {"path": "../escape.md"})
    assert result.is_error
    assert "not found" not in result.content[0].text.lower()
    failed = [
        r for r in caplog.records if r.getMessage().startswith("tool_call_failed")
    ]
    assert failed and failed[-1].levelno == logging.INFO
