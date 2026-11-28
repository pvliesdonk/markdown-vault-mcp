"""Index readiness outcomes at the resource protocol boundary."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
from mcp.shared.exceptions import MCPError

from markdown_vault_mcp.domain import get_vault_singleton
from markdown_vault_mcp.facets.index import IndexFacet
from markdown_vault_mcp.facets.reader import ReaderFacet
from markdown_vault_mcp.indexing.readiness import ReadinessState

if TYPE_CHECKING:
    from fastmcp import Client


@pytest.mark.parametrize(
    "uri,case",
    [
        (
            "toc://vault/simple.md",
            ("timeout", "The search index is still building. Retry shortly."),
        ),
        (
            "toc://vault/simple.md",
            (
                "never_built",
                "The search index has not been built. An operator should build the index before you retry.",
            ),
        ),
        (
            "toc://vault/simple.md",
            (
                "build_failed",
                "The search index build failed. An operator should check the index before you retry.",
            ),
        ),
        (
            "similar://vault/simple.md",
            (
                "build_failed",
                "The search index build failed. An operator should check the index before you retry.",
            ),
        ),
    ],
)
async def test_resource_index_unavailable(
    uri: str,
    case: tuple[str, str],
    client: Client[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reason, message = case
    state = ReadinessState()
    if reason != "never_built":
        state.begin_async_build()
    if reason == "build_failed":
        state.fail_build(RuntimeError("secret build detail"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S", "0")
    with monkeypatch.context() as readiness_patch:
        readiness_patch.setattr(get_vault_singleton()._coordinator, "_readiness", state)
        with caplog.at_level(logging.WARNING), pytest.raises(MCPError) as error:
            await client.read_resource(uri)
        assert message in str(error.value)
        assert "secret build detail" not in str(error.value)
        level = logging.WARNING if reason == "timeout" else logging.ERROR
        records = [
            r
            for r in caplog.records
            if r.name == "fastmcp.server.server" and r.levelno == level and r.exc_info
        ]
        assert records
        if reason == "build_failed":
            assert records[-1].exc_info is not None
            assert "secret build detail" in str(records[-1].exc_info[1].__cause__)


@pytest.mark.parametrize(
    "fault",
    [
        (
            "SQLITE_BUSY",
            "The search index is busy. Retry shortly.",
            logging.WARNING,
        ),
        (
            "SQLITE_IOERR",
            "The search index could not be read. An operator should check the index before you retry.",
            logging.ERROR,
        ),
    ],
)
async def test_resource_handler_faults(
    fault: tuple[str, str, int],
    client: Client[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    uri, method = "toc://vault/simple.md", "get_toc"
    error_name, expected, level = fault
    monkeypatch.setattr(IndexFacet, "is_queryable", lambda _self: True)

    def fail(*_args: object, **_kwargs: object) -> None:
        exc = sqlite3.OperationalError("secret detail")
        exc.sqlite_errorname = error_name
        raise exc

    monkeypatch.setattr(ReaderFacet, method, fail)
    with caplog.at_level(logging.WARNING), pytest.raises(MCPError) as error:
        await client.read_resource(uri)
    assert expected in str(error.value)
    assert "secret detail" not in str(error.value)
    assert any(
        r.name == "fastmcp.server.server" and r.levelno == level and r.exc_info
        for r in caplog.records
    )
