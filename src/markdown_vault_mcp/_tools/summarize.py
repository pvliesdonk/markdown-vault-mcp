"""The LLM-backed ``summarize`` tool, registered as a dual-mode job (#1033).

Registration goes through pvl-core's ``register_long_running_tool`` rather
than a bare ``@mcp.tool``: a client that speaks MCP tasks (SEP-2663) gets
native background-task execution, and any other client runs in the
foreground up to the jobs subsystem's soft deadline, after which the
still-running work is promoted to a background job and the caller receives
a handle to poll via the generic ``get_job_result`` tool. This replaces the
former hand-rolled ``SummaryJobStore`` + ``get_summary`` pair (#937).

Unlike the other tool groups, registration needs the server's ``Jobs``
mechanics, which are built from the loaded config — so ``register`` is
called from ``_server_wiring``, which ``make_server``'s DOMAIN-WIRING block
calls (the same already-loaded-config pattern as ``register_domain_prompts``, #609), not from the
config-free ``register_tools`` entry point.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.dependencies import Depends
from fastmcp_pvl_core import register_long_running_tool

from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_queryable import needs_queryable
from ..domain import get_vault

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp_pvl_core import Jobs

logger = logging.getLogger(__name__)


def register(mcp: FastMCP, jobs: Jobs) -> None:
    """Register the summarize tool on *mcp* as a dual-mode long-running tool.

    Args:
        mcp: The server to register on.
        jobs: The server's shared jobs mechanics (``build_jobs`` result);
            promotion handles from this tool resolve through the generic
            ``get_job_result`` tool registered on the same object.
    """

    @register_long_running_tool(
        mcp,
        jobs,
        icons=_TOOL_ICONS["summarize"],
        tags={"summarize"},
        annotations={
            "title": "Summarize Notes",
            "read_only_hint": True,
            "destructive_hint": False,
            # LLM output varies run to run — not idempotent.
            "idempotent_hint": False,
        },
    )
    @library_outcomes
    @needs_queryable()
    async def summarize(
        paths: list[str],
        focus: str | None = None,
        mode: Literal["synthesis", "per_note"] = "synthesis",
        max_notes: int | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Summarize notes or folders with a language model; returns one summary that
        cites its source notes by path, or one summary per note.

        It covers at most {max_notes} notes per call; split a larger folder into
        subfolders, found with get_toc, and combine the results. The notes are sent to a
        language model this server's operator chose.

        Args:
            paths: Note paths, such as `"notes/topic.md"`, and folders, such as `"notes/project"`,
                which stand for every note under them.
            focus: What the summary should concentrate on, such as `"extract action items"`;
                omit for a general summary.
            mode: `"synthesis"` (default) for one summary across all the notes, or
                `"per_note"` for one per note.
            max_notes: A smaller note limit for this call, to narrow the work; omit for
                the server's limit.

        Returns:
            When the summary completes within the soft deadline, a dict with
            `"status": "completed"` plus:

            - summary (str): The generated summary text.
            - sources (list[dict]): The notes that were summarised, each with
              `path` and `title`: always populated so individual notes are
              attributable even when the prose does not name every one.
            - mode (str): The mode used (`"synthesis"` or `"per_note"`).
            - truncated (bool): True when content was lost (notes omitted at
              the note limit, notes skipped, or content cut to fit a request
              budget).
            - notes_included (int): Notes whose content reached the model.
            - notes_omitted (int): Matched notes dropped by the note limit.
              When non-zero, the summary does not cover the whole selection;
              surface that to the reader.
            - notes_limit (int): The note limit in effect for this call.
            - hint (str | None): Recovery guidance when notes were omitted
              or skipped, one step per cause; follow it for full coverage.
              None when fully covered.
            - skipped (list[dict]): Matched notes left out for a reason other
              than the note limit, each with `path` and `reason`
              (`not_found`, `invalid_path`, `over_read_limit`,
              `unreadable`).

            When the work is promoted to a background job, a dict with
            `"status": "working"`, a `"job_id"` string, and a
            `"message"`; call `get_job_result` with the `job_id` to
            fetch the result.

        Raises:
            InvalidRequestError: If `paths` is empty, `mode` is invalid,
                `max_notes` is below 1, or the paths hold no note that exists
                and is within the read limit.
            ValueError: If every note found exists but cannot be read.
            RuntimeError: If the summarization backend call fails within the
                soft deadline. A backend failure that happens after promotion
                is reported through `get_job_result` instead.
        """
        result = await asyncio.to_thread(
            vault.summarizer.summarize,
            paths,
            focus=focus,
            mode=mode,
            max_notes=max_notes,
        )
        return {**asdict(result), "status": "completed"}


def apply_summarize_limits(mcp: FastMCP, *, max_notes: int) -> None:
    """Substitute the live note limit into the summarize tool description.

    A calling model can plan folder splits before its first call only when
    the real configured number is visible; the docstring above carries a
    ``{max_notes}`` placeholder for that purpose (#925). Only the description
    carries it: the limit is said once, where the tool is chosen (#1599), so
    no parameter description needs patching.
    Called from ``_server_wiring``, which ``make_server``'s DOMAIN-WIRING
    block calls, where the loaded
    config is available (registration itself is config-free by template
    contract). Uses the same ``local_provider._components`` access as
    ``fastmcp_pvl_core.register_tool_icons``, with the same guard.

    Args:
        mcp: The FastMCP instance the summarize tool was registered on.
        max_notes: The configured per-call note limit to surface.

    Raises:
        RuntimeError: If FastMCP's internal component API changed.
    """
    from fastmcp.tools.base import Tool

    try:
        components = mcp.local_provider._components
    except AttributeError as exc:  # pragma: no cover - fastmcp API drift
        raise RuntimeError(
            "FastMCP internal API changed: cannot enumerate tools via "
            "local_provider._components."
        ) from exc
    for component in components.values():
        if not (isinstance(component, Tool) and component.name == "summarize"):
            continue
        if component.description:
            component.description = component.description.replace(
                "{max_notes}", str(max_notes)
            )
