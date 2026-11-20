"""Long-running tool registration and the job poller (#2281).

Ported from fastmcp-pvl-core v10.2.0: ``tests/test_jobs.py``
(``TestRegistration``, ``TestInstructionsSnippet``) and
``tests/test_jobs_outcomes.py``.

A client that does not negotiate the tasks extension is modelled by
suppressing FastMCP ``Client``'s internal extension fold-in: FastMCP's own
client would otherwise run every long-running call as a native task and
never reach the fallback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import FunctionTool
from mcp.types import TextContent

from markdown_vault_mcp._instructions_builder import (
    finalize_instructions,
    instructions_for,
)
from markdown_vault_mcp._job_store import JOB_POLL_TOOL_NAME
from markdown_vault_mcp._job_tools import (
    register_job_tools,
    register_long_running_tool,
)
from markdown_vault_mcp._jobs import Jobs, build_jobs
from markdown_vault_mcp._pvl_core import ServerConfig
from markdown_vault_mcp._request_logging import wire_middleware_stack
from markdown_vault_mcp._tasks import configure_task_backend
from markdown_vault_mcp._tool_boundary import FAULT_MESSAGE, is_tool_boundary
from markdown_vault_mcp._tool_visibility import apply_tool_visibility
from markdown_vault_mcp.config_sections import JobsConfig

_BOUNDARY_LOGGER = "markdown_vault_mcp._tool_boundary"
_FAST_CONFIG = JobsConfig(soft_deadline_s=0.15, result_ttl_s=60.0)
_MEMORY = ServerConfig(kv_store_url="memory://")


def _jobs(jobs_config: JobsConfig = _FAST_CONFIG) -> Jobs:
    return build_jobs(_MEMORY, jobs_config)


async def _drain(jobs: Jobs) -> None:
    """Wait for every promoted task (and outcome writer) to finish."""
    while jobs._background:
        await asyncio.wait(set(jobs._background))


@pytest.fixture
def non_task_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fastmcp.client.client.build_internal_client_extensions",
        lambda _callback: [],
    )


@pytest.fixture(params=["native", "inline"])
def call_path(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run a test on both paths a call can end on before the deadline.

    ``native``: FastMCP's client negotiates tasks, so the tool runs as a
    SEP-2663 task. ``inline``: a client without the tasks extension.
    """
    if request.param == "inline":
        monkeypatch.setattr(
            "fastmcp.client.client.build_internal_client_extensions",
            lambda _callback: [],
        )
    return str(request.param)


def _server(*, max_per_subject: int = 10) -> FastMCP:
    mcp = FastMCP("t", mask_error_details=False)
    wire_middleware_stack(mcp)
    configure_task_backend(mcp, "APP", _MEMORY)
    jobs = build_jobs(
        _MEMORY,
        JobsConfig(
            soft_deadline_s=0.05, result_ttl_s=60.0, max_per_subject=max_per_subject
        ),
    )
    register_job_tools(mcp, jobs)

    @register_long_running_tool(mcp, jobs)
    async def slow_crash() -> dict[str, Any]:
        await asyncio.sleep(0.2)
        raise RuntimeError("internal detail /srv/secret")

    @register_long_running_tool(mcp, jobs)
    async def fast_crash() -> dict[str, Any]:
        raise RuntimeError("internal detail /srv/secret")

    @register_long_running_tool(mcp, jobs)
    async def slow_ok() -> dict[str, Any]:
        await asyncio.sleep(0.2)
        return {"ok": True}

    return mcp


def _text(result: Any) -> str:
    assert isinstance(result.content[0], TextContent)
    return result.content[0].text


def _loud_fastmcp_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.startswith("fastmcp")
    ]


async def _poll_until_done(client: Client, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        polled = (
            await client.call_tool(JOB_POLL_TOOL_NAME, {"job_id": job_id})
        ).structured_content
        assert polled is not None
        if polled["status"] != "working":
            return polled
        await asyncio.sleep(0.02)
    raise AssertionError("job never finished")


class TestRegistration:
    async def test_wrapped_tool_inline_and_promoted(self) -> None:
        mcp = FastMCP("t")
        jobs = _jobs()

        @register_long_running_tool(mcp, jobs)
        async def echo(text: str, delay: float = 0.0) -> dict[str, Any]:
            await asyncio.sleep(delay)
            return {"echo": text}

        register_job_tools(mcp, jobs)

        inline = await mcp.call_tool("echo", {"text": "hi"})
        assert inline.structured_content == {"echo": "hi"}

        promoted = await mcp.call_tool("echo", {"text": "later", "delay": 0.4})
        handle = promoted.structured_content
        assert handle is not None
        assert handle["status"] == "working"
        await _drain(jobs)
        polled = await mcp.call_tool(JOB_POLL_TOOL_NAME, {"job_id": handle["job_id"]})
        assert polled.structured_content is not None
        assert polled.structured_content["status"] == "completed"
        assert polled.structured_content["result"] == {"echo": "later"}

    async def test_wrapped_tool_registered_task_optional(self) -> None:
        mcp = FastMCP("t")

        @register_long_running_tool(mcp, _jobs())
        async def echo(text: str) -> dict[str, Any]:
            return {"echo": text}

        tool = await mcp.get_tool("echo")
        assert tool is not None
        assert tool.task_config.mode == "optional"

    def test_task_kwarg_rejected(self) -> None:
        with pytest.raises(ValueError, match="task"):
            register_long_running_tool(FastMCP("t"), _jobs(), task=True)

    async def test_unknown_job_id_is_tool_error(self) -> None:
        mcp = FastMCP("t")
        register_job_tools(mcp, _jobs())
        with pytest.raises(ToolError, match="unknown"):
            await mcp.call_tool(JOB_POLL_TOOL_NAME, {"job_id": "nope"})

    async def test_note_appended_to_description(self) -> None:
        mcp = FastMCP("t")
        register_job_tools(mcp, _jobs(), note="Summaries expire nightly.")
        tool = await mcp.get_tool(JOB_POLL_TOOL_NAME)
        assert tool is not None
        assert tool.description is not None
        assert tool.description.endswith("Summaries expire nightly.")
        assert "background job" in tool.description


class TestInstructionsSnippet:
    def test_polling_guidance_present(self) -> None:
        mcp = FastMCP("t")
        register_job_tools(mcp, _jobs())
        instructions_for(mcp).identity("t", "T.")
        text = finalize_instructions(mcp, _MEMORY, env_prefix="T")
        assert (
            "A long-running tool returns a job id when this client cannot run it "
            "as a task; poll get_job_result with that id until the status is "
            "completed or failed, honouring retry_after_s, instead of invoking "
            "the tool again."
        ) in text

    def test_dropped_when_poll_tool_hidden(self) -> None:
        mcp = FastMCP("t")
        register_job_tools(mcp, _jobs())
        instructions_for(mcp).identity("t", "T.")
        cfg = ServerConfig(kv_store_url="memory://", tools_deny=(JOB_POLL_TOOL_NAME,))
        apply_tool_visibility(mcp, cfg)
        assert JOB_POLL_TOOL_NAME not in finalize_instructions(mcp, cfg, env_prefix="T")


class TestOutcomes:
    async def test_every_registered_tool_carries_the_boundary(self) -> None:
        mcp = _server()
        for name in (JOB_POLL_TOOL_NAME, "slow_crash"):
            tool = await mcp.get_tool(name)
            assert isinstance(tool, FunctionTool), name
            assert is_tool_boundary(tool.fn), name

    async def test_unknown_job_id_is_a_request_to_change(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with Client(_server()) as client:
            with caplog.at_level(logging.DEBUG):
                result = await client.call_tool_mcp(
                    JOB_POLL_TOOL_NAME, {"job_id": "nope"}
                )

        assert result.is_error is True
        assert _text(result) == (
            "No job 'nope' for this caller: the id is unknown, has expired, or "
            "belongs to another caller. Run the original tool again if you still "
            "need its result."
        )
        assert _loud_fastmcp_records(caplog) == []

    @pytest.mark.usefixtures("call_path")
    async def test_fault_before_the_deadline_is_the_fault_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with Client(_server()) as client:
            with caplog.at_level(logging.DEBUG):
                result = await client.call_tool_mcp("fast_crash", {})

        assert result.is_error is True
        assert _text(result) == FAULT_MESSAGE
        # One traceback, from the inner boundary around the domain coroutine.
        boundary = [r for r in caplog.records if r.name == _BOUNDARY_LOGGER]
        assert [(r.levelno, r.exc_info is not None) for r in boundary] == [
            (logging.ERROR, True)
        ]
        assert "function=fast_crash" in boundary[0].getMessage()

    @pytest.mark.usefixtures("non_task_client")
    async def test_fault_after_promotion_is_the_fault_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with Client(_server()) as client:
            with caplog.at_level(logging.DEBUG):
                handle = (await client.call_tool("slow_crash", {})).structured_content
                assert handle is not None
                assert handle["status"] == "working"
                polled = await _poll_until_done(client, handle["job_id"])

        # The poller never sees the exception's text, masked or not.
        assert polled["status"] == "failed"
        assert polled["error"] == FAULT_MESSAGE
        boundary = [r for r in caplog.records if r.name == _BOUNDARY_LOGGER]
        assert [(r.levelno, r.exc_info is not None) for r in boundary] == [
            (logging.ERROR, True)
        ]
        # job_failed follows the boundary's ToolError: ERROR, no second traceback.
        (failed,) = [
            r for r in caplog.records if r.getMessage().startswith("job_failed ")
        ]
        assert failed.levelno == logging.ERROR
        assert not failed.exc_info

    @pytest.mark.usefixtures("non_task_client")
    async def test_job_cap_is_a_request_to_retry_later(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with Client(_server(max_per_subject=1)) as client:
            first = (await client.call_tool("slow_ok", {})).structured_content
            assert first is not None
            assert first["status"] == "working"
            with caplog.at_level(logging.DEBUG):
                result = await client.call_tool_mcp("slow_ok", {})

        assert result.is_error is True
        assert _text(result) == (
            "slow_ok ran past its foreground time limit, and this caller already "
            "has the maximum number of background jobs, so it was stopped. Retry "
            "it later, once older jobs have expired."
        )
        assert _loud_fastmcp_records(caplog) == []

    @pytest.mark.usefixtures("call_path")
    async def test_domain_tool_error_passes_through(self) -> None:
        mcp = FastMCP("t")
        configure_task_backend(mcp, "APP", _MEMORY)
        jobs = build_jobs(_MEMORY, JobsConfig(soft_deadline_s=5.0, result_ttl_s=60.0))

        @register_long_running_tool(mcp, jobs)
        async def refuse() -> dict[str, Any]:
            raise ToolError("No note at 'a.md'.", log_level=logging.INFO)

        async with Client(mcp) as client:
            result = await client.call_tool_mcp("refuse", {})
        assert result.is_error is True
        assert _text(result) == "No note at 'a.md'."
