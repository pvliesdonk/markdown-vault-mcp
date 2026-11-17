"""MCP Apps tools distinguish internal faults from invalid requests (#1640)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp._server_apps import _hashed
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.facets.reader import ReaderFacet

if TYPE_CHECKING:
    from fastmcp import Client


@pytest.mark.parametrize("error_type", [ValueError, InvalidRequestError])
@pytest.mark.parametrize(
    ("name", "method", "arguments"),
    [
        (_hashed("vault_context"), "get_context", {"path": "simple.md"}),
        ("show_context", "get_context", {"path": "simple.md"}),
        (_hashed("vault_search"), "search", {"query": "simple", "mode": "hybrid"}),
    ],
)
async def test_internal_value_error_is_a_logged_fault(
    name: str,
    method: str,
    arguments: dict[str, Any],
    error_type: type[ValueError],
    client: Client[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each app tool passes faults and refusals through its outcome boundaries."""

    def broken(*_args: object, **_kwargs: object) -> None:
        raise error_type("synthetic failure")

    monkeypatch.setattr(ReaderFacet, method, broken)
    caplog.set_level(logging.INFO)
    result = await client.call_tool_mcp(name, arguments)
    assert result.is_error
    boundary = [r for r in caplog.records if r.getMessage().startswith("tool_failed")]
    if error_type is ValueError:
        assert len(boundary) == 1
        assert boundary[0].levelno == logging.ERROR
    else:
        assert "synthetic failure" in result.content[0].text
        failed = [
            r for r in caplog.records if r.getMessage().startswith("tool_call_failed")
        ]
        assert failed and failed[-1].levelno == logging.INFO
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
