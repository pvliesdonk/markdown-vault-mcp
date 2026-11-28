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


@pytest.mark.parametrize("uri", ["toc://vault/simple.md", "similar://vault/simple.md"])
@pytest.mark.parametrize(
    "case",
    [
        (
            "timeout",
            "The search index is still building. The request was fine. Retry shortly.",
        ),
        (
            "never_built",
            "The search index has not been built. The request was fine. An operator should build the index before you retry.",
        ),
        (
            "build_failed",
            "The search index build failed. The request was fine. An operator should check the index before you retry.",
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
        if reason == "build_failed":
            records = [
                r
                for r in caplog.records
                if r.name == "fastmcp.server.server"
                and r.levelno == logging.ERROR
                and r.exc_info
            ]
            assert records
            assert records[-1].exc_info is not None
            assert "secret build detail" in str(records[-1].exc_info[1].__cause__)
        if reason == "timeout" and uri.startswith("toc:"):
            state.mark_built()
            assert await client.read_resource(uri)


@pytest.mark.parametrize(
    "resource",
    [
        ("toc://vault/simple.md", "get_toc"),
        ("similar://vault/simple.md", "get_similar"),
    ],
)
@pytest.mark.parametrize(
    "fault",
    [
        (
            "SQLITE_BUSY",
            "The search index is busy. The request was fine. Retry shortly.",
            logging.WARNING,
        ),
        (
            "SQLITE_IOERR",
            "The search index could not be read. The request was fine. An operator should check the index before you retry.",
            logging.ERROR,
        ),
        (None, "Error reading resource", logging.ERROR),
    ],
)
async def test_resource_handler_faults(
    resource: tuple[str, str],
    fault: tuple[str | None, str, int],
    client: Client[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    uri, method = resource
    error_name, expected, level = fault
    monkeypatch.setattr(IndexFacet, "is_queryable", lambda _self: True)

    def fail(*_args: object, **_kwargs: object) -> None:
        if error_name is None:
            raise RuntimeError("secret detail")
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
    if error_name == "SQLITE_IOERR":
        record = next(
            r
            for r in caplog.records
            if r.name == "fastmcp.server.server"
            and r.levelno == logging.ERROR
            and r.exc_info
        )
        assert record.exc_info is not None
        readiness_error = record.exc_info[1].__cause__
        assert readiness_error is not None
        assert "secret detail" in str(readiness_error.__cause__)
