from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_queryable import needs_queryable
from ..domain import get_vault

if TYPE_CHECKING:
    from markdown_vault_mcp.summary_jobs import SummaryJobStore

logger = logging.getLogger(__name__)

# Strong references to promoted background summarize tasks so the event loop
# does not garbage-collect a task before it finishes (the documented asyncio
# footgun); each task removes itself on completion.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _record_job(store: SummaryJobStore, job_id: str, task: asyncio.Task[Any]) -> None:
    """Store a finished background summary's result or failure for polling.

    Runs as a task done-callback on the event-loop thread. Any exception
    the summarize raised (invalid input, a backend error, or the #937
    timeout) is recorded as the job's failure so ``get_summary`` can report
    it, rather than being lost as an unretrieved task exception.
    """
    try:
        result = task.result()
    except Exception as exc:
        store.fail(job_id, str(exc) or type(exc).__name__)
    else:
        store.complete(job_id, result)


def register(mcp: FastMCP) -> None:
    """Register the summarize + get_summary tools on *mcp*."""

    @mcp.tool(
        icons=_TOOL_ICONS["summarize"],
        tags={"summarize"},
        annotations={
            "title": "Summarize Notes",
            "readOnlyHint": True,
            "destructiveHint": False,
            # LLM output varies run to run — not idempotent.
            "idempotentHint": False,
        },
    )
    @needs_queryable()
    async def summarize(
        paths: list[str],
        focus: str | None = None,
        mode: Literal["synthesis", "per_note"] = "synthesis",
        max_notes: int | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Summarize a note, a set of notes, or a folder subtree with an LLM.

        Sends the referenced notes to a language model and returns a generated
        summary. In the default "synthesis" mode the summary is a single
        cohesive text that synthesizes across all the notes and references the
        individual source notes by path, so each point can be traced back to
        its origin. In "per_note" mode it returns one summary per note instead.

        Inputs larger than one model request are handled automatically: notes
        are split into batches, each batch is summarized, and the partial
        summaries are combined into the final result (several model calls, so
        large folders take proportionally longer). Coverage per call is capped
        at this server's note limit of {max_notes} notes; the response reports
        the effective limit as ``notes_limit``. Plan ahead: when a folder
        holds more notes than the limit (check with ``get_toc`` or
        ``list_documents``), call this tool once per subfolder or per smaller
        set of paths and combine the results yourself. When ``notes_omitted``
        in a response is non-zero, the summary did NOT cover the whole
        selection.

        Slow summaries do not block: if the work is still running after the
        server's inline deadline, this returns ``{"status": "in_progress",
        "job_id": ...}`` immediately and keeps generating in the background —
        retrieve the finished summary by calling ``get_summary`` with that
        ``job_id`` (poll every few seconds). A summary that finishes within
        the deadline returns inline with ``"status": "completed"`` and the
        fields below.

        Only available when a summarization backend is configured (an
        OPENAI_API_KEY or an OpenAI-compatible base URL). Note content is
        sent to the external model provider; do not summarize notes whose
        content must not leave your environment.

        Args:
            paths: One or more note paths (e.g. "notes/topic.md") and/or folder
                prefixes (e.g. "notes/project"). Folders expand to every note
                in the subtree (capped by the note limit). Mix freely;
                duplicates are de-duplicated.
            focus: Optional free-text instruction that steers the summary, e.g.
                "extract action items" or "focus on decisions and their
                rationale". Omit for a general-purpose summary.
            mode: "synthesis" (default) for one cross-note summary that
                references sources, or "per_note" for a separate summary per
                note.
            max_notes: Optional per-call note limit. Values above the server's
                cap of {max_notes} are clamped to it (the cap bounds per-call
                cost and latency); values below it narrow the work. Omit to
                use the server cap.

        Returns:
            When the summary completes within the inline deadline, a dict with
            ``"status": "completed"`` plus:

            - summary (str): The generated summary text.
            - sources (list[dict]): The notes that were summarised, each with
              ``path`` and ``title`` — always populated so individual notes are
              attributable even when the prose does not name every one.
            - mode (str): The mode used ("synthesis" or "per_note").
            - truncated (bool): True when content was lost to a cap (notes
              omitted at the note limit, or content cut to fit a request
              budget).
            - notes_included (int): Notes whose content reached the model.
            - notes_omitted (int): Matched notes dropped by the note limit.
              When non-zero, the summary does not cover the whole selection —
              surface that to the reader.
            - notes_limit (int): The note limit in effect for this call.
            - hint (str | None): Recovery guidance when notes were omitted;
              follow it for full coverage. None when fully covered.

            When the work is promoted to the background, a dict with
            ``"status": "in_progress"``, a ``"job_id"`` string, and a
            ``"message"`` — call ``get_summary`` with the ``job_id`` to fetch
            the result.

        Raises:
            ValueError: If ``paths`` is empty, ``mode`` is invalid,
                ``max_notes`` is below 1, or no readable notes were found for
                the given paths.
            RuntimeError: If the summarization backend call fails within the
                inline deadline. A backend failure that happens after
                promotion is reported through ``get_summary`` instead.
        """
        task: asyncio.Task[Any] = asyncio.ensure_future(
            asyncio.to_thread(
                vault.summarizer.summarize,
                paths,
                focus=focus,
                mode=mode,
                max_notes=max_notes,
            )
        )
        inline_timeout = vault.summarize_inline_timeout
        done, _pending = await asyncio.wait({task}, timeout=inline_timeout)
        if task in done:
            # Finished inside the deadline: return inline (re-raising a fast
            # ValueError/RuntimeError exactly as before promotion existed).
            result = task.result()
            return {**asdict(result), "status": "completed"}

        # Still running: promote to a background job and return a handle. The
        # task keeps running; its done-callback records the outcome (#937).
        job_id = vault.summary_jobs.create()
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        task.add_done_callback(lambda t: _record_job(vault.summary_jobs, job_id, t))
        logger.info(
            "summarize_promoted_to_job job_id=%s inline_timeout=%ss",
            job_id,
            inline_timeout,
        )
        return {
            "status": "in_progress",
            "job_id": job_id,
            "poll_with": "get_summary",
            "message": (
                f"Summary is still generating after {inline_timeout:g}s and is "
                "now running in the background. Call get_summary with "
                f"job_id={job_id!r} to retrieve it (poll every few seconds)."
            ),
        }

    @mcp.tool(
        icons=_TOOL_ICONS["get_summary"],
        tags={"summarize"},
        annotations={
            "title": "Get Summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            # The same job_id yields "in_progress" then a terminal result as
            # the background work lands — not idempotent across time.
            "idempotentHint": False,
        },
    )
    async def get_summary(
        job_id: str,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Retrieve a background summary started by ``summarize``.

        When a ``summarize`` call runs past the server's inline deadline it
        returns ``{"status": "in_progress", "job_id": ...}`` and finishes in
        the background. Call this tool with that ``job_id`` to fetch the
        result. Poll every few seconds while it is still running.

        Job records are held in memory and are lost on server restart; a
        finished job is retained for a while, then evicted — fetch it soon
        after it completes.

        Args:
            job_id: The ``job_id`` returned by a promoted ``summarize`` call.

        Returns:
            A dict whose ``status`` is one of:

            - "completed": the summary is ready — the dict also carries the
              same fields as a completed ``summarize`` result (``summary``,
              ``sources``, ``mode``, ``truncated``, ``notes_included``,
              ``notes_omitted``, ``notes_limit``, ``hint``).
            - "in_progress": still generating — poll again shortly.
            - "failed": generation failed — see ``error`` for the reason
              (e.g. the backend timed out; narrow the request and retry).
            - "not_found": no such job — it was never created, already
              evicted, or the id is wrong.
        """
        job = vault.summary_jobs.get(job_id)
        if job is None:
            return {
                "status": "not_found",
                "job_id": job_id,
                "message": (
                    "No summary job with that id. It may have expired, the "
                    "server may have restarted, or the id is wrong. Re-run "
                    "summarize."
                ),
            }
        if job.status == "completed" and job.result is not None:
            return {**asdict(job.result), "status": "completed", "job_id": job_id}
        if job.status == "failed":
            return {"status": "failed", "job_id": job_id, "error": job.error}
        return {
            "status": "in_progress",
            "job_id": job_id,
            "message": "Still generating; poll get_summary again in a few seconds.",
        }


def apply_summarize_limits(mcp: FastMCP, *, max_notes: int) -> None:
    """Substitute the live note limit into the summarize tool schema.

    A calling model can plan folder splits before its first call only when
    the real configured number is visible in the tool schema; the docstring
    above carries ``{max_notes}`` placeholders for that purpose (#925).
    FastMCP splits the docstring at decoration time: the free text becomes
    ``Tool.description`` while each ``Args:`` entry lands in the JSON
    schema's per-parameter ``description`` — so both must be patched.
    Called from ``make_server``'s DOMAIN-WIRING block, where the loaded
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
        for prop in component.parameters.get("properties", {}).values():
            description = prop.get("description")
            if description and "{max_notes}" in description:
                prop["description"] = description.replace("{max_notes}", str(max_notes))
