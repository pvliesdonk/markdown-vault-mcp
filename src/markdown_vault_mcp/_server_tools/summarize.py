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
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Summarize a note, a set of notes, or a folder subtree with an LLM.

        Sends the referenced notes to a language model and returns a generated
        summary. In the default "synthesis" mode the summary is a single
        cohesive text that synthesizes across all the notes and references the
        individual source notes by path, so each point can be traced back to
        its origin. In "per_note" mode it returns one summary per note instead.

        Only available when a summarization backend is configured (an
        OPENAI_API_KEY or an OpenAI-compatible base URL). Note content is
        sent to the external model provider; do not summarize notes whose
        content must not leave your environment.

        Args:
            paths: One or more note paths (e.g. "notes/topic.md") and/or folder
                prefixes (e.g. "notes/project"). Folders expand to every note
                in the subtree (capped by the server's summarize limits). Mix
                freely; duplicates are de-duplicated.
            focus: Optional free-text instruction that steers the summary, e.g.
                "extract action items" or "focus on decisions and their
                rationale". Omit for a general-purpose summary.
            mode: "synthesis" (default) for one cross-note summary that
                references sources, or "per_note" for a separate summary per
                note.

        Returns:
            A dict with:

            - summary (str): The generated summary text.
            - sources (list[dict]): The notes that were summarised, each with
              ``path`` and ``title`` — always populated so individual notes are
              attributable even when the prose does not name every one.
            - mode (str): The mode used ("synthesis" or "per_note").
            - truncated (bool): True when the input was capped (a subtree had
              more notes than the server's limit, or the aggregate note text
              exceeded the character budget and was cut).

        Raises:
            ValueError: If ``paths`` is empty, ``mode`` is invalid, or no
                readable notes were found for the given paths.
            RuntimeError: If the summarization backend call fails.
        """
        result = await asyncio.to_thread(
            vault.summarizer.summarize,
            paths,
            focus=focus,
            mode=mode,
        )
        return asdict(result)
