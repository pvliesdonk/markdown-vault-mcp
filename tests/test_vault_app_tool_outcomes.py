"""MCP Apps tools distinguish internal faults from invalid requests (#1640)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp._server_apps import _hashed
from markdown_vault_mcp.facets.reader import ReaderFacet

if TYPE_CHECKING:
    from fastmcp import Client


@pytest.mark.parametrize("fault", [True, False], ids=["fault", "refusal"])
@pytest.mark.parametrize(
    "case",
    [
        (
            _hashed("vault_context"),
            "get_context",
            {"path": "simple.md"},
            {"path": "missing.md"},
            "Document not found: 'missing.md'",
        ),
        (
            "show_context",
            "get_context",
            {"path": "simple.md"},
            {"path": "missing.md"},
            "Document not found: 'missing.md'",
        ),
        (
            _hashed("vault_search"),
            "search",
            {"query": "simple", "mode": "hybrid"},
            {"query": "simple", "mode": "hybrid"},
            "Semantic search is not enabled",
        ),
    ],
)
async def test_internal_value_error_is_a_logged_fault(
    case: tuple[str, str, dict[str, Any], dict[str, Any], str],
    fault: bool,
    client: Client[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each app tool passes faults and refusals through its outcome boundaries."""
    name, method, arguments, refusal_arguments, message = case

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ValueError("synthetic failure")

    if fault:
        monkeypatch.setattr(ReaderFacet, method, broken)
    caplog.set_level(logging.INFO)
    result = await client.call_tool_mcp(name, arguments if fault else refusal_arguments)
    assert result.is_error
    boundary = [r for r in caplog.records if r.getMessage().startswith("tool_failed")]
    if fault:
        assert len(boundary) == 1
        assert boundary[0].levelno == logging.ERROR
    else:
        assert message in result.content[0].text
        failed = [
            r for r in caplog.records if r.getMessage().startswith("tool_call_failed")
        ]
        assert failed and failed[-1].levelno == logging.INFO
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
