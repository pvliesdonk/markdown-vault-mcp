from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_queryable import needs_queryable
from ..domain import get_vault


def register(mcp: FastMCP) -> None:
    """Register the LLM-backed summarize tool on *mcp*."""

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
        at the server's note limit (50 unless the operator changed it); the
        response reports the effective limit as ``notes_limit``. When
        ``notes_omitted`` is non-zero, the summary does NOT cover the whole
        selection: for full coverage, summarize subfolders or smaller sets of
        paths in separate calls and combine the results yourself.

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
                configured cap are clamped to it (the cap bounds per-call cost
                and latency); values below it narrow the work. Omit to use the
                server cap.

        Returns:
            A dict with:

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

        Raises:
            ValueError: If ``paths`` is empty, ``mode`` is invalid,
                ``max_notes`` is below 1, or no readable notes were found for
                the given paths.
            RuntimeError: If the summarization backend call fails.
        """
        result = await asyncio.to_thread(
            vault.summarizer.summarize,
            paths,
            focus=focus,
            mode=mode,
            max_notes=max_notes,
        )
        return asdict(result)
