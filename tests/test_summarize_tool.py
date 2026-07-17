"""Server-level tests for the summarize soft-deadline promotion + get_summary (#937).

Builds a real ``make_server`` with a fake summarization backend injected (so no
network call happens) and drives the ``summarize`` / ``get_summary`` tools
through a FastMCP client.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

import markdown_vault_mcp.summarizer as summ_mod
from markdown_vault_mcp.server import make_server
from markdown_vault_mcp.summarizer import Summarizer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_SUMMARIZE_ENV = (
    "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY",
    "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
    "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL",
    "MARKDOWN_VAULT_MCP_SUMMARIZE_TIMEOUT",
    "MARKDOWN_VAULT_MCP_SUMMARIZE_INLINE_TIMEOUT",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
)


class _FakeSummarizer(Summarizer):
    """Fake backend: optional delay, then return text or raise."""

    def __init__(
        self, *, text: str = "SUMMARY", delay: float = 0.0, error: str | None = None
    ) -> None:
        self._text = text
        self._delay = delay
        self._error = error

    def summarize(self, system: str, user: str) -> str:  # noqa: ARG002
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise RuntimeError(self._error)
        return self._text

    @property
    def provider_name(self) -> str:
        return "fake"


@pytest.fixture
def summarize_server(
    vault_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., Any]]:
    """Factory building a summarize-enabled server around a fake backend."""

    def _build(fake: Summarizer, *, inline_timeout: float) -> object:
        for name in _SUMMARIZE_ENV:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        # A base URL enables the summarize tool without a real key/network.
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "http://fake.invalid/v1"
        )
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_SUMMARIZE_INLINE_TIMEOUT", str(inline_timeout)
        )
        monkeypatch.setattr(summ_mod, "get_summarizer", lambda _cfg: fake)
        return make_server()

    yield _build


async def _poll_summary(client: Client, job_id: str, *, tries: int = 40) -> dict:
    """Poll get_summary until it leaves in_progress (or give up)."""
    last: dict = {}
    for _ in range(tries):
        res = await client.call_tool("get_summary", {"job_id": job_id})
        last = res.data
        if last.get("status") != "in_progress":
            return last
        await asyncio.sleep(0.1)
    return last


class TestSummarizePromotion:
    async def test_fast_summary_returns_inline_completed(
        self, summarize_server
    ) -> None:
        server = summarize_server(_FakeSummarizer(text="QUICK"), inline_timeout=10.0)
        async with Client(server) as client:
            res = await client.call_tool("summarize", {"paths": ["simple.md"]})
        assert res.data["status"] == "completed"
        assert res.data["summary"] == "QUICK"
        assert "job_id" not in res.data

    async def test_slow_summary_promotes_then_completes(self, summarize_server) -> None:
        server = summarize_server(
            _FakeSummarizer(text="LATE", delay=0.5), inline_timeout=0.15
        )
        async with Client(server) as client:
            res = await client.call_tool("summarize", {"paths": ["simple.md"]})
            assert res.data["status"] == "in_progress"
            job_id = res.data["job_id"]
            assert job_id
            final = await _poll_summary(client, job_id)
        assert final["status"] == "completed"
        assert final["summary"] == "LATE"
        assert final["job_id"] == job_id

    async def test_slow_failure_is_reported_via_get_summary(
        self, summarize_server
    ) -> None:
        server = summarize_server(
            _FakeSummarizer(delay=0.4, error="backend exploded"),
            inline_timeout=0.15,
        )
        async with Client(server) as client:
            res = await client.call_tool("summarize", {"paths": ["simple.md"]})
            assert res.data["status"] == "in_progress"
            final = await _poll_summary(client, res.data["job_id"])
        assert final["status"] == "failed"
        assert "backend exploded" in final["error"]

    async def test_get_summary_unknown_job_is_not_found(self, summarize_server) -> None:
        server = summarize_server(_FakeSummarizer(), inline_timeout=10.0)
        async with Client(server) as client:
            res = await client.call_tool("get_summary", {"job_id": "bogus"})
        assert res.data["status"] == "not_found"

    async def test_get_summary_has_readonly_annotations(self, summarize_server) -> None:
        server = summarize_server(_FakeSummarizer(), inline_timeout=10.0)
        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
        assert "get_summary" in tools
        ann = tools["get_summary"].annotations
        assert ann is not None
        assert ann.title == "Get Summary"
        assert ann.readOnlyHint is True
        assert ann.destructiveHint is False
