"""The ``Jobs`` mechanics behind the long-running tools, and their factory.

Ported from ``fastmcp_pvl_core._jobs.manager`` 10.2.0 (#2307). Only what
``_job_tools`` calls came along: :meth:`Jobs.run_with_deadline` and
:meth:`Jobs.poll`. Core's ``Jobs.start`` and ``Jobs.defer``, for tools that
background their work by hand, have no caller here.

This store is a **fallback**. Most clients do not negotiate the SEP-2663
tasks extension yet; where one does, the native task is the background
mechanism, and :meth:`Jobs.run_with_deadline` yields to it rather than
starting a second one. The client decides task or foreground before the
tool body runs, and nothing promotes a foreground call later, so the store
is idle only where every client negotiates tasks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import FastMCPError

from markdown_vault_mcp import _subject
from markdown_vault_mcp._job_store import (
    JOB_POLL_TOOL_NAME,
    JOB_RETRY_AFTER_S,
    JobHandle,
    JobLimitExceededError,
    JobStore,
)
from markdown_vault_mcp._kv_store import build_kv_store
from markdown_vault_mcp._tool_boundary import FAULT_MESSAGE

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from markdown_vault_mcp.config_sections import JobsConfig
    from markdown_vault_mcp.config_sections.server import ServerConfig

logger = logging.getLogger(__name__)

_ANONYMOUS_SCOPE = "anonymous"
"""Scope for callers with no resolvable subject."""


def _current_scope() -> str:
    """Resolve the calling subject's job scope.

    ``get_subject`` unifies subject extraction across every auth mode
    (bearer, OIDC, stdio's ``"local"``). ``None`` (auth configured but no
    token, or no request context) collapses to one anonymous scope. Looked up
    on ``_subject`` at call time, so a test can patch it there.
    """
    subject = _subject.get_subject()
    return subject if subject else _ANONYMOUS_SCOPE


def _as_result_mapping(value: Any) -> dict[str, Any]:
    """Normalise a tool return value into a JSON-object result payload.

    A mapping is stored as-is; anything else is wrapped as ``{"value": ...}``
    so the stored record, and the polling tool's ``result``, is always a JSON
    object.
    """
    if isinstance(value, dict):
        return value
    return {"value": value}


def _native_task_active() -> bool:
    """Whether this call is running inside a native SEP-2663 task.

    Imported at call time so the task context is read where the call runs.
    """
    from fastmcp_tasks.context import get_task_context

    return get_task_context() is not None


class Jobs:
    """Dual-mode execution and job-handle mechanics for one server.

    Construct via :func:`build_jobs`. Every verb resolves the caller's
    subject scope itself, so a caller only ever sees its own jobs. A promoted
    job runs on the serving process and dies with it; its record then reports
    ``working`` until the TTL removes it, never a fabricated result. Durable
    cross-restart execution is the native task path's job, not the fallback's.
    """

    def __init__(self, store: JobStore, config: JobsConfig) -> None:
        """Wire the mechanics over *store*.

        Args:
            store: The job record store.
            config: The jobs settings (soft deadline, TTL, cap).
        """
        self._store = store
        self._config = config
        # Strong references to promoted tasks so the event loop cannot
        # garbage-collect them mid-run; discarded on completion.
        self._background: set[asyncio.Task[Any]] = set()

    async def run_with_deadline(
        self, coro: Coroutine[Any, Any, Any], *, tool: str
    ) -> Any:
        """Run *coro* dual-mode: native task, inline, or promoted.

        Args:
            coro: The domain work.
            tool: The registered tool name this call serves, echoed in the
                promotion log and the handle message.

        Returns:
            The coroutine's own result when it finishes natively or within
            the soft deadline; a :class:`~markdown_vault_mcp._job_store.JobHandle`
            payload when the work was promoted.

        Raises:
            Exception: Whatever *coro* raises before the deadline, unchanged.
                A failure after promotion is reported through polling.
            JobLimitExceededError: If the caller is at its live-job cap at
                promotion time. The running work is cancelled first, unless
                it finished while the rejection was decided, in which case its
                result is returned inline.
        """
        if _native_task_active():
            # Native execution: Docket owns lifecycle, results and TTL.
            return await coro

        started_at = time.time()
        task: asyncio.Task[Any] = asyncio.ensure_future(coro)
        done, _pending = await asyncio.wait(
            {task}, timeout=self._config.soft_deadline_s
        )
        if task in done:
            # The one fallback outcome with no INFO line: a client that did
            # not negotiate tasks, served within the deadline. Logged at DEBUG
            # so whether the fallback is still needed stays observable.
            logger.debug(
                "job_ran_inline tool=%s elapsed_s=%s",
                tool,
                round(time.time() - started_at, 3),
            )
            return task.result()  # re-raises an inline failure unchanged

        # Capture the scope now: the request context ends when this call
        # returns the handle, and the done-callback fires outside any request.
        scope = _current_scope()
        try:
            record = await self._store.create(scope, started_at=started_at)
        except JobLimitExceededError:
            # The task had to start before its record could exist, so a cap
            # rejection must not orphan it: a finished task's outcome is the
            # inline answer; otherwise the work is stopped.
            if task.done() and not task.cancelled():
                return task.result()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        task.add_done_callback(
            lambda t: self._schedule_outcome(scope, record.job_id, t)
        )
        logger.info(
            "job_promoted tool=%s job_id=%s soft_deadline_s=%s",
            tool,
            record.job_id,
            self._config.soft_deadline_s,
        )
        return self._handle(record.job_id, tool=tool)

    async def poll(self, job_id: str) -> dict[str, Any]:
        """Return the polling payload for *job_id*, the body of ``get_job_result``.

        A terminal outcome is collected as it is returned: the job stops
        counting against the caller's cap, and the same poll keeps answering
        for a short grace period.

        Args:
            job_id: The id a promoted call returned.

        Returns:
            ``job_id``, ``status``, ``result`` and ``error``; while working,
            also ``running_for_s``, ``retry_after_s`` and ``message``.

        Raises:
            JobNotFoundError: Unknown or expired id, or another subject's.
        """
        scope = _current_scope()
        record = await self._store.get(scope, job_id)
        if record.status != "working":
            await self._store.collect(scope, job_id)
        payload: dict[str, Any] = {
            "job_id": record.job_id,
            "status": record.status,
            "result": record.result,
            "error": record.error,
        }
        if record.status == "working":
            payload["running_for_s"] = round(time.time() - record.started_at, 1)
            payload["retry_after_s"] = JOB_RETRY_AFTER_S
            payload["message"] = (
                "Still running. Poll again with the same job_id in a few seconds."
            )
        return payload

    def _handle(self, job_id: str, *, tool: str) -> JobHandle:
        """Build the promotion payload."""
        return JobHandle(
            status="working",
            job_id=job_id,
            poll_with=JOB_POLL_TOOL_NAME,
            retry_after_s=JOB_RETRY_AFTER_S,
            message=(
                f"{tool} is still running after "
                f"{self._config.soft_deadline_s:g}s and now continues in "
                f"the background. Call {JOB_POLL_TOOL_NAME} with "
                f"job_id={job_id!r} to retrieve the outcome (poll every "
                "few seconds)."
            ),
        )

    def _schedule_outcome(
        self, scope: str, job_id: str, task: asyncio.Task[Any]
    ) -> None:
        """Done-callback: record the finished task's outcome in the store.

        Runs synchronously on the loop; the store write is scheduled as a
        task and strong-referenced like the job itself.
        """
        writer = asyncio.ensure_future(self._record_outcome(scope, job_id, task))
        self._background.add(writer)
        writer.add_done_callback(self._background.discard)

    async def _record_outcome(
        self, scope: str, job_id: str, task: asyncio.Task[Any]
    ) -> None:
        if task.cancelled():
            await self._store.cancel(scope, job_id)
            return
        exc = task.exception()
        if exc is None:
            await self._store.finish(scope, job_id, _as_result_mapping(task.result()))
            return
        # The poll result carries the same text, and this line the same level,
        # as the call would have produced had it failed inline (the
        # designing-tool-outcomes skill).
        if isinstance(exc, FastMCPError):
            # Classified already: a ToolError the work raised on purpose, or a
            # tool_boundary fault whose traceback is already logged.
            logger.log(exc.log_level, "job_failed job_id=%s error=%s", job_id, exc)
            error = str(exc)
        else:
            # Work without a boundary: the boundary's rule. Its own text can
            # carry internals, so the poller never sees it.
            logger.error(
                "job_failed job_id=%s error_type=%s",
                job_id,
                type(exc).__name__,
                exc_info=exc,
            )
            error = FAULT_MESSAGE
        await self._store.fail(scope, job_id, error)


def build_jobs(config: ServerConfig, jobs_config: JobsConfig) -> Jobs:
    """Build the jobs mechanics, a store plus :class:`Jobs`, with no tools.

    Build one ``Jobs`` per server and share it between every long-running
    tool and ``register_job_tools``, so every handle resolves through the one
    poller. Records land in the shared key-value backend under the ``jobs``
    namespace, so ``KV_STORE_URL`` covers them.

    Args:
        config: Server configuration; its ``kv_store_url`` selects the store.
        jobs_config: The jobs settings (soft deadline, TTL, cap).

    Returns:
        A :class:`Jobs` ready for ``run_with_deadline`` and ``poll``.
    """
    storage = build_kv_store(config, namespace="jobs")
    store = JobStore(
        storage,
        result_ttl_s=jobs_config.result_ttl_s,
        max_per_subject=jobs_config.max_per_subject,
    )
    return Jobs(store, jobs_config)
