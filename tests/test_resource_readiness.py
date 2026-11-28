"""Index readiness outcomes at the resource protocol boundary."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError

from markdown_vault_mcp.exceptions import IndexUnavailableError
from markdown_vault_mcp.facets.index import IndexFacet
from markdown_vault_mcp.facets.reader import ReaderFacet
from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "state.json"))
    return make_server(configure_logging=False)


@pytest.mark.parametrize("uri", ["toc://vault/a.md", "similar://vault/a.md"])
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
    server: FastMCP,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reason, message = case
    async with Client(server) as client:

        def unavailable(_self: IndexFacet) -> bool:
            return False

        def fail(_self: IndexFacet, _timeout: float | None = None) -> None:
            raise IndexUnavailableError("secret detail", reason=reason)  # type: ignore[arg-type]

        monkeypatch.setattr(IndexFacet, "is_queryable", unavailable)
        monkeypatch.setattr(IndexFacet, "wait_until_queryable", fail)
        with caplog.at_level(logging.WARNING), pytest.raises(MCPError) as error:
            await client.read_resource(uri)
    assert message in str(error.value)
    assert "secret detail" not in str(error.value)
    if reason in {"never_built", "build_failed"}:
        assert any(r.levelno >= logging.ERROR and r.exc_info for r in caplog.records)


@pytest.mark.parametrize(
    "resource",
    [("toc://vault/a.md", "get_toc"), ("similar://vault/a.md", "get_similar")],
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
    server: FastMCP,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    uri, method = resource
    error_name, expected, level = fault
    async with Client(server) as client:
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
