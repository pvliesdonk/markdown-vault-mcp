"""The MCP Apps tools end through ``library_outcomes`` and ``tool_boundary`` (#1640).

A missing note or another request the caller must change is an INFO
``ToolError`` carrying the library's message; any other exception, a plain
``ValueError`` included, is a server fault logged once at ERROR and reported
with the boundary's fixed message, never as a successful-looking result.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_apps import _hashed
from markdown_vault_mcp.facets.reader import ReaderFacet
from tests.conftest import wait_for_mcp_writer_drain
from tests.server_factory import make_server

# tool_boundary's fixed outcome-4 text, kept short of its exact wording.
_FAULT = "server-side error; the request itself was fine"

# A server-side detail that must reach neither the model nor the failure line.
_SECRET = "/srv/private/vector-index.npy"


def _failed(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("tool_call_failed")]


async def _call(name: str, arguments: dict[str, Any]) -> Any:
    async with Client(make_server()) as client:
        await wait_for_mcp_writer_drain(client)
        return await client.call_tool_mcp(name, arguments)


@pytest.mark.usefixtures("_mcp_env")
class TestNoteNotFound:
    """A missing note is a request to change, not a result."""

    @pytest.mark.parametrize("name", [_hashed("vault_context"), "show_context"])
    async def test_missing_note_is_info_tool_error(
        self, name: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="fastmcp.middleware.requests")
        result = await _call(name, {"path": "nonexistent.md"})
        assert result.is_error
        text = result.content[0].text
        assert "Document not found: 'nonexistent.md'" in text
        failed = _failed(caplog)
        assert failed and failed[-1].levelno == logging.INFO
        assert failed[-1].exc_info is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_path_outside_vault_is_info_tool_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="fastmcp.middleware.requests")
        result = await _call("show_context", {"path": "../escape.md"})
        assert result.is_error
        assert "not found" not in result.content[0].text.lower()
        failed = _failed(caplog)
        assert failed and failed[-1].levelno == logging.INFO


@pytest.mark.usefixtures("_mcp_env")
class TestVaultSearchOutcomes:
    """vault_search raises instead of returning an ``{"error": ...}`` entry."""

    async def test_semantic_without_embeddings_is_info_tool_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="fastmcp.middleware.requests")
        result = await _call(
            _hashed("vault_search"), {"query": "simple", "mode": "semantic"}
        )
        assert result.is_error
        failed = _failed(caplog)
        assert failed and failed[-1].levelno == logging.INFO
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_internal_value_error_is_a_logged_fault(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _broken(*_args: object, **_kwargs: object) -> None:
            raise ValueError(f"vector index rebuild failed at {_SECRET}")

        monkeypatch.setattr(ReaderFacet, "search", _broken)
        caplog.set_level(logging.INFO)
        result = await _call(
            _hashed("vault_search"), {"query": "simple", "mode": "keyword"}
        )
        assert result.is_error
        text = result.content[0].text
        assert _FAULT in text
        assert _SECRET not in text
        boundary = [
            r for r in caplog.records if r.getMessage().startswith("tool_failed")
        ]
        assert len(boundary) == 1
        assert boundary[0].levelno == logging.ERROR
        assert boundary[0].exc_info is not None
        assert _SECRET not in boundary[0].getMessage()
        failed = _failed(caplog)
        assert failed and failed[-1].levelno == logging.ERROR
        assert all(_SECRET not in r.getMessage() for r in failed)


@pytest.mark.usefixtures("_mcp_env")
class TestVaultContextFault:
    """An internal ValueError from get_context is a fault, not "not found"."""

    @pytest.mark.parametrize("name", [_hashed("vault_context"), "show_context"])
    async def test_internal_value_error_is_a_logged_fault(
        self,
        name: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def _broken(*_args: object, **_kwargs: object) -> None:
            raise ValueError(f"corrupt row in {_SECRET}")

        monkeypatch.setattr(ReaderFacet, "get_context", _broken)
        caplog.set_level(logging.INFO)
        result = await _call(name, {"path": "simple.md"})
        assert result.is_error
        assert _FAULT in result.content[0].text
        boundary = [
            r for r in caplog.records if r.getMessage().startswith("tool_failed")
        ]
        assert len(boundary) == 1 and boundary[0].levelno == logging.ERROR
