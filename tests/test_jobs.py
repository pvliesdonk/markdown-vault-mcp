"""The ``Jobs`` mechanics: inline, promotion, native task, subject scoping (#2307).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_jobs.py``
(``TestRunWithDeadline``, ``TestSubjectScoping``,
``TestUnconfiguredDefaultBackend``). Core's ``Jobs.start``/``Jobs.defer``
cases did not come along with those verbs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from markdown_vault_mcp._job_store import (
    JOB_POLL_TOOL_NAME,
    JobLimitExceededError,
    JobNotFoundError,
)
from markdown_vault_mcp._jobs import Jobs, build_jobs
from markdown_vault_mcp._pvl_core import ServerConfig
from markdown_vault_mcp._tool_boundary import FAULT_MESSAGE
from markdown_vault_mcp.config_sections import JobsConfig

_LOGGER = "markdown_vault_mcp._jobs"
_FAST_CONFIG = JobsConfig(soft_deadline_s=0.15, result_ttl_s=60.0)


def _jobs(jobs_config: JobsConfig = _FAST_CONFIG) -> Jobs:
    return build_jobs(ServerConfig(kv_store_url="memory://"), jobs_config)


async def _drain(jobs: Jobs) -> None:
    """Wait for every promoted task (and outcome writer) to finish."""
    while jobs._background:
        await asyncio.wait(set(jobs._background))


def _job_failed(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    (record,) = [r for r in caplog.records if r.getMessage().startswith("job_failed ")]
    return record


class TestRunWithDeadline:
    async def test_fast_work_returns_inline(self) -> None:
        async def work() -> dict[str, Any]:
            return {"ok": True}

        assert await _jobs().run_with_deadline(work(), tool="t") == {"ok": True}

    async def test_fast_failure_raises_inline(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def work() -> dict[str, Any]:
            raise ValueError("bad input")

        with (
            caplog.at_level(logging.DEBUG, logger=_LOGGER),
            pytest.raises(ValueError, match="bad input"),
        ):
            await _jobs().run_with_deadline(work(), tool="t")
        # An inline failure is still a foreground call: the trace fires
        # before the result re-raises.
        assert any(
            r.getMessage().startswith("job_ran_inline tool=t ")
            for r in caplog.records
            if r.name == _LOGGER
        )

    async def test_inline_completion_leaves_a_debug_trace(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def work() -> dict[str, Any]:
            return {"ok": True}

        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            await _jobs().run_with_deadline(work(), tool="t")
        lines = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
        assert len(lines) == 1
        assert lines[0].startswith("job_ran_inline tool=t elapsed_s=")

    async def test_slow_work_promotes_to_handle(self) -> None:
        jobs = _jobs()

        async def work() -> dict[str, Any]:
            await asyncio.sleep(0.4)
            return {"late": True}

        handle = await jobs.run_with_deadline(work(), tool="my_tool")
        assert handle["status"] == "working"
        assert handle["poll_with"] == JOB_POLL_TOOL_NAME
        assert "my_tool" in handle["message"]
        await _drain(jobs)
        payload = await jobs.poll(handle["job_id"])
        assert payload["status"] == "completed"
        assert payload["result"] == {"late": True}

    async def test_failure_after_promotion_reported_via_poll(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # An unclassified exception is a fault: the poller gets the fault
        # message, never the exception's own text, and the log line is ERROR
        # with the traceback, as the boundary would log it.
        jobs = _jobs()

        async def work() -> dict[str, Any]:
            await asyncio.sleep(0.3)
            raise RuntimeError("backend exploded")

        handle = await jobs.run_with_deadline(work(), tool="t")
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            await _drain(jobs)
        payload = await jobs.poll(handle["job_id"])
        assert payload["status"] == "failed"
        assert payload["error"] == FAULT_MESSAGE
        assert payload["result"] is None
        record = _job_failed(caplog)
        assert record.levelno == logging.ERROR
        assert "error_type=RuntimeError" in record.getMessage()
        assert "backend exploded" not in record.getMessage()
        assert record.exc_info is not None

    async def test_tool_error_after_promotion_keeps_its_message_and_level(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        jobs = _jobs()

        async def work() -> dict[str, Any]:
            await asyncio.sleep(0.3)
            raise ToolError("No note at 'a.md'.", log_level=logging.INFO)

        handle = await jobs.run_with_deadline(work(), tool="t")
        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            await _drain(jobs)
        payload = await jobs.poll(handle["job_id"])
        assert payload["status"] == "failed"
        assert payload["error"] == "No note at 'a.md'."
        record = _job_failed(caplog)
        assert record.levelno == logging.INFO
        assert not record.exc_info

    async def test_cancelled_promoted_task_reported_cancelled(self) -> None:
        jobs = _jobs()

        async def work() -> dict[str, Any]:
            await asyncio.sleep(30.0)
            return {}

        handle = await jobs.run_with_deadline(work(), tool="t")
        for task in set(jobs._background):
            task.cancel()
        await _drain(jobs)
        assert (await jobs.poll(handle["job_id"]))["status"] == "cancelled"

    async def test_non_mapping_result_wrapped_as_value(self) -> None:
        jobs = _jobs()

        async def work() -> str:
            await asyncio.sleep(0.3)
            return "just text"

        handle = await jobs.run_with_deadline(work(), tool="t")
        await _drain(jobs)
        payload = await jobs.poll(handle["job_id"])
        assert payload["result"] == {"value": "just text"}

    async def test_working_poll_carries_progress_hints(self) -> None:
        jobs = _jobs()

        async def work() -> dict[str, Any]:
            await asyncio.sleep(0.5)
            return {}

        handle = await jobs.run_with_deadline(work(), tool="t")
        payload = await jobs.poll(handle["job_id"])
        assert payload["status"] == "working"
        assert payload["running_for_s"] >= 0.0
        assert payload["retry_after_s"] > 0
        await _drain(jobs)

    async def test_native_task_context_runs_direct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Inside a Docket worker the verb must not double-manage the work:
        # the coroutine runs to completion with no store record.
        class _Ctx:
            task_id = "t1"
            task_scope = None

        monkeypatch.setattr("fastmcp_tasks.context.get_task_context", lambda: _Ctx())
        jobs = _jobs(JobsConfig(soft_deadline_s=0.01, result_ttl_s=60.0))

        async def work() -> dict[str, Any]:
            await asyncio.sleep(0.1)  # would promote in foreground mode
            return {"native": True}

        assert await jobs.run_with_deadline(work(), tool="t") == {"native": True}
        assert not jobs._background

    async def test_cap_rejection_cancels_the_promoted_work(self) -> None:
        # A promotion rejected by the per-subject cap must not orphan the
        # already-running task: it is cancelled before the error surfaces.
        jobs = _jobs(
            JobsConfig(soft_deadline_s=0.05, result_ttl_s=60.0, max_per_subject=1)
        )
        cancelled = asyncio.Event()

        async def slow(marker: asyncio.Event | None = None) -> dict[str, Any]:
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                if marker is not None:
                    marker.set()
                raise
            return {}

        first = await jobs.run_with_deadline(slow(), tool="t")
        assert first["status"] == "working"
        with pytest.raises(JobLimitExceededError):
            await jobs.run_with_deadline(slow(cancelled), tool="t")
        assert cancelled.is_set()
        assert len(jobs._background) == 1
        for task in set(jobs._background):
            task.cancel()
        await _drain(jobs)

    async def test_cap_rejection_returns_result_if_work_finished_meanwhile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Race window: the work completes while the cap rejection is being
        # decided, so its result is the inline answer; no error, no orphan.
        jobs = _jobs(JobsConfig(soft_deadline_s=0.05, result_ttl_s=60.0))

        async def slow_reject(*_args: Any, **_kwargs: Any) -> None:
            await asyncio.sleep(0.2)  # the work (0.1s left) finishes in here
            raise JobLimitExceededError("cap")

        monkeypatch.setattr(jobs._store, "create", slow_reject)

        async def work() -> dict[str, Any]:
            await asyncio.sleep(0.15)
            return {"made_it": True}

        assert await jobs.run_with_deadline(work(), tool="t") == {"made_it": True}
        assert not jobs._background


class TestSubjectScoping:
    async def test_scope_captured_at_promotion_isolated_on_poll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        jobs = _jobs()
        current = {"subject": "alice"}
        monkeypatch.setattr(
            "markdown_vault_mcp._pvl_core.get_subject", lambda: current["subject"]
        )

        async def work() -> dict[str, Any]:
            await asyncio.sleep(0.3)
            return {}

        handle = await jobs.run_with_deadline(work(), tool="t")
        await _drain(jobs)
        assert (await jobs.poll(handle["job_id"]))["status"] == "completed"
        current["subject"] = "bob"
        with pytest.raises(JobNotFoundError):
            await jobs.poll(handle["job_id"])

    async def test_no_subject_uses_the_anonymous_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        jobs = _jobs()
        current: dict[str, str | None] = {"subject": None}
        monkeypatch.setattr(
            "markdown_vault_mcp._pvl_core.get_subject", lambda: current["subject"]
        )

        async def work() -> dict[str, Any]:
            await asyncio.sleep(0.3)
            return {}

        handle = await jobs.run_with_deadline(work(), tool="t")
        await _drain(jobs)
        current["subject"] = "anonymous"
        assert (await jobs.poll(handle["job_id"]))["status"] == "completed"


class TestUnconfiguredDefaultBackend:
    async def test_build_jobs_works_without_a_kv_url_or_writable_default_dir(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Server construction must not crash on a host with no /data: CI
        # runners, uvx installs, the stdio plugin channel.
        monkeypatch.setattr(
            "markdown_vault_mcp._kv_store._DEFAULT_KV_STORE_DIR",
            str(tmp_path / "absent-mount" / "state"),
        )
        jobs = build_jobs(ServerConfig(), _FAST_CONFIG)

        async def work() -> dict[str, Any]:
            return {"ok": True}

        assert await jobs.run_with_deadline(work(), tool="t") == {"ok": True}
