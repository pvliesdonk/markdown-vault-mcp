"""Register dual-mode long-running tools and the job poller on the server.

Ported from ``fastmcp_pvl_core._jobs.register`` 10.2.0 (#2281).

Two entry points, both built on the ``Jobs`` mechanics (``build_jobs``):

- :func:`register_long_running_tool` registers a domain coroutine once and
  gives it both behaviours: native SEP-2663 task execution when the request
  is task-augmented (the tool is registered with
  ``TaskConfig(mode="optional")``), and foreground execution with
  soft-deadline promotion to a job handle otherwise.
- :func:`register_job_tools` registers the one polling tool,
  ``get_job_result``, which retrieves every job on the shared ``Jobs``.

The instructions builder is ``_instructions_builder`` (#2305).

How FastMCP builds the tool descriptions and instructions snippets
registered here, and how clients read them, is in
``docs/design/reference/mcp-model-facing-text.md``.
"""

from __future__ import annotations

import base64
import functools
import logging
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from mcp.types import Icon, ToolAnnotations

from markdown_vault_mcp._instructions_builder import InstructionRole, instructions_for
from markdown_vault_mcp._job_store import (
    JOB_POLL_TOOL_NAME,
    JobLimitExceededError,
    JobNotFoundError,
)
from markdown_vault_mcp._tool_boundary import tool_boundary

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

    from markdown_vault_mcp._jobs import Jobs

# Lucide "loader" icon (MIT): raw SVG, base64-encoded once at import time
# into a data URI, so the polling tool carries an icon with no file-system
# or network dependency.
_POLL_SVG = (
    '<svg width="24" height="24" xmlns="http://www.w3.org/2000/svg"'
    ' viewBox="0 0 24 24">'
    '<g fill="none" stroke="currentColor" stroke-linecap="round"'
    ' stroke-linejoin="round" stroke-width="2">'
    '<path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83'
    'M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83"/>'
    "</g></svg>"
)
_POLL_ICON = Icon(
    src="data:image/svg+xml;base64," + base64.b64encode(_POLL_SVG.encode()).decode(),
    mime_type="image/svg+xml",
)

_POLL_DESCRIPTION = (
    "Get the status of a background job started by a long-running tool on "
    "this server; returns status (working, completed or failed) with the "
    "result or error once it finishes. Job records expire, so fetch a "
    "finished job's result promptly."
)

_POLL_WORKFLOW = (
    "A long-running tool returns a job id when this client cannot run it "
    f"as a task; poll {JOB_POLL_TOOL_NAME} with that id until the status "
    "is completed or failed, honouring retry_after_s, instead of invoking "
    "the tool again."
)


def register_long_running_tool(
    mcp: FastMCP,
    jobs: Jobs,
    **tool_kwargs: Any,
) -> Callable[[Callable[..., Any]], Any]:
    """Register a long-running domain coroutine as a dual-mode tool.

    Use as a decorator factory, like ``FastMCP.tool``. The decorated
    coroutine returns a JSON-serialisable value and never branches on how
    it runs. A call behaves as:

    - task-augmented request: native background-task execution; Docket
      owns lifecycle and results.
    - plain request that finishes within the soft deadline: the
      coroutine's own result, inline.
    - plain request past the deadline: the work continues in the
      background and the caller gets a job handle to poll with
      ``get_job_result``. A failure after promotion is reported through
      polling, not raised.

    Both the coroutine and the registered tool carry
    :func:`~markdown_vault_mcp._tool_boundary.tool_boundary`, so a failure
    ends the same way on every path: a ``ToolError`` the coroutine raises
    reaches the caller (or the poller) with its own message and
    ``log_level``, and any other exception becomes the boundary's fault
    message, logged once with its traceback. A caller at its live-job cap
    when the call would be promoted gets a ``ToolError`` at INFO saying to
    retry later.

    Args:
        mcp: The server to register on.
        jobs: The server's shared ``Jobs`` mechanics (from ``build_jobs``).
        **tool_kwargs: Passed through to ``FastMCP.tool`` (name,
            description, annotations, icons, tags).

    Returns:
        The decorator to apply to the domain coroutine.

    Raises:
        ValueError: If ``task`` is passed; the tool is always registered
            with ``TaskConfig(mode="optional")``, which makes it dual-mode.
    """
    if "task" in tool_kwargs:
        raise ValueError(
            "register_long_running_tool owns the task registration mode "
            "(TaskConfig(mode='optional')); do not pass 'task'. A tool "
            "that must never run as a task is a plain @mcp.tool."
        )
    from fastmcp.utilities.tasks import TaskConfig

    def decorator(fn: Callable[..., Any]) -> Any:
        tool_name = tool_kwargs.get("name") or fn.__name__
        # The domain coroutine gets its own boundary so a fault is classified
        # the same way on every path it can end on: inline, native task, and
        # after promotion, where only the jobs manager sees it and stores the
        # boundary's message instead of the raw exception text. The outer
        # boundary covers the wrapper's own code around it.
        work = tool_boundary(fn)

        @tool_boundary
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await jobs.run_with_deadline(
                    work(*args, **kwargs), tool=tool_name
                )
            except JobLimitExceededError:
                # The caller is at its cap of live job records; they count
                # until they expire, so waiting is the only way out.
                raise ToolError(
                    f"{tool_name} ran past its foreground time limit, and this "
                    "caller already has the maximum number of background jobs, "
                    "so it was stopped. Retry it later, once older jobs have "
                    "expired.",
                    log_level=logging.INFO,
                ) from None

        return mcp.tool(task=TaskConfig(mode="optional"), **tool_kwargs)(wrapper)

    return decorator


def register_job_tools(
    mcp: FastMCP,
    jobs: Jobs,
    *,
    note: str | None = None,
) -> None:
    """Register the ``get_job_result`` polling tool and its workflow snippet.

    Call once per server, with the same ``Jobs`` the long-running tools run
    on. The tool returns ``Jobs.poll``'s payload: ``job_id``, ``status``
    (working, completed or failed), ``result`` and ``error``, plus
    ``running_for_s``, ``retry_after_s`` and ``message`` while working.

    Lookups are scoped to the calling subject: another subject's job id
    answers exactly like an unknown one, so ids are not probeable across
    callers. Records expire ``result_ttl_s`` after creation.

    Args:
        mcp: The server to register on.
        jobs: The server's shared ``Jobs`` mechanics.
        note: Optional sentence appended to the tool description.
    """
    # Model-facing text (the ``writing-model-facing-text`` skill): the tool
    # description says what the call returns; the sequence that spans the
    # long-running tool and this one lives once, in the WORKFLOWS snippet.
    description = f"{_POLL_DESCRIPTION} {note}" if note else _POLL_DESCRIPTION

    @mcp.tool(
        name=JOB_POLL_TOOL_NAME,
        description=description,
        icons=[_POLL_ICON],
        # group:jobs keeps the generated reference page at tools/jobs.md.
        tags={"jobs", "group:jobs"},
        annotations=ToolAnnotations(
            title="Get Job Result",
            read_only_hint=True,
            destructive_hint=False,
            # The same job_id yields "working" then a terminal result as
            # the background work lands: not idempotent across time.
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    @tool_boundary
    async def get_job_result(job_id: str) -> dict[str, Any]:
        """Poll one job.

        Args:
            job_id: The job_id a long-running tool returned when it continued
                in the background.
        """
        try:
            return await jobs.poll(job_id)
        except JobNotFoundError:
            # A request the model must change (designing-tool-outcomes,
            # outcome 2). The message says the same for another caller's id
            # as for an unknown one, so ids are not probeable across callers.
            raise ToolError(
                f"No job {job_id!r} for this caller: the id is unknown, has "
                "expired, or belongs to another caller. Run the original tool "
                "again if you still need its result.",
                log_level=logging.INFO,
            ) from None

    instructions_for(mcp).add(
        _POLL_WORKFLOW,
        role=InstructionRole.WORKFLOWS,
        requires_tools={JOB_POLL_TOOL_NAME},
    )
