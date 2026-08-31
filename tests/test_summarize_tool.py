"""Server-level tests for the dual-mode summarize tool + get_job_result (#1033).

Builds a real ``make_server`` with a fake summarization backend injected (so no
network call happens) and drives the ``summarize`` / ``get_job_result`` tools
through a FastMCP client. Promotion is exercised by shrinking the jobs
subsystem's soft deadline (``JOBS_SOFT_DEADLINE_S``) below the fake backend's
delay.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import markdown_vault_mcp.summarizer as summ_mod
from markdown_vault_mcp.summarizer import Summarizer
from tests.server_factory import make_server

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_SUMMARIZE_ENV = (
    "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY",
    "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
    "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL",
    "MARKDOWN_VAULT_MCP_SUMMARIZE_TIMEOUT",
    "MARKDOWN_VAULT_MCP_JOBS_SOFT_DEADLINE_S",
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

    def _build(fake: Summarizer, *, soft_deadline: float) -> object:
        for name in _SUMMARIZE_ENV:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        # A base URL enables the summarize tool without a real key/network.
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL", "http://fake.invalid/v1"
        )
        monkeypatch.setenv(
            "MARKDOWN_VAULT_MCP_JOBS_SOFT_DEADLINE_S", str(soft_deadline)
        )
        monkeypatch.setattr(summ_mod, "get_summarizer", lambda _cfg: fake)
        return make_server()

    yield _build


async def _poll_job(client: Client, job_id: str, *, tries: int = 40) -> dict:
    """Poll get_job_result until it leaves "working" (or give up)."""
    last: dict = {}
    for _ in range(tries):
        res = await client.call_tool("get_job_result", {"job_id": job_id})
        last = res.data
        if last.get("status") != "working":
            return last
        await asyncio.sleep(0.1)
    return last


class TestSummarizeDualMode:
    async def test_fast_summary_returns_inline_completed(
        self, summarize_server
    ) -> None:
        server = summarize_server(_FakeSummarizer(text="QUICK"), soft_deadline=10.0)
        async with Client(server) as client:
            res = await client.call_tool("summarize", {"paths": ["simple.md"]})
        assert res.data["status"] == "completed"
        assert res.data["summary"] == "QUICK"
        assert "job_id" not in res.data

    async def test_slow_summary_promotes_then_completes(self, summarize_server) -> None:
        server = summarize_server(
            _FakeSummarizer(text="LATE", delay=0.5), soft_deadline=0.15
        )
        async with Client(server) as client:
            res = await client.call_tool("summarize", {"paths": ["simple.md"]})
            assert res.data["status"] == "working"
            assert res.data["poll_with"] == "get_job_result"
            job_id = res.data["job_id"]
            assert job_id
            final = await _poll_job(client, job_id)
        assert final["status"] == "completed"
        assert final["result"]["summary"] == "LATE"
        assert final["job_id"] == job_id

    async def test_slow_failure_is_reported_via_get_job_result(
        self, summarize_server
    ) -> None:
        server = summarize_server(
            _FakeSummarizer(delay=0.4, error="backend exploded"),
            soft_deadline=0.15,
        )
        async with Client(server) as client:
            res = await client.call_tool("summarize", {"paths": ["simple.md"]})
            assert res.data["status"] == "working"
            final = await _poll_job(client, res.data["job_id"])
        assert final["status"] == "failed"
        assert "backend exploded" in final["error"]

    async def test_fast_failure_raises_inline(self, summarize_server) -> None:
        server = summarize_server(
            _FakeSummarizer(error="backend exploded"), soft_deadline=10.0
        )
        async with Client(server) as client:
            with pytest.raises(ToolError, match="backend exploded"):
                await client.call_tool("summarize", {"paths": ["simple.md"]})

    async def test_get_job_result_unknown_job_raises(self, summarize_server) -> None:
        server = summarize_server(_FakeSummarizer(), soft_deadline=10.0)
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool("get_job_result", {"job_id": "bogus"})

    async def test_get_job_result_has_readonly_annotations(
        self, summarize_server
    ) -> None:
        server = summarize_server(_FakeSummarizer(), soft_deadline=10.0)
        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
        assert "get_job_result" in tools
        ann = tools["get_job_result"].annotations
        assert ann is not None
        assert ann.title == "Get Job Result"
        assert ann.readOnlyHint is True
        assert ann.destructiveHint is False

    async def test_summarize_hidden_but_job_tools_stay_without_backend(
        self, vault_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No backend hides summarize, but the generic jobs poller stays:
        reindex and build_embeddings produce job handles regardless of the
        summarize backend (#1033)."""
        for name in _SUMMARIZE_ENV:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
        server = make_server()
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
        assert "summarize" not in names
        assert "get_job_result" in names
