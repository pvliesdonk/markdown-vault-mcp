"""Summarize facet: the LLM-backed note-summarization surface.

A thin view over :class:`~markdown_vault_mcp.managers.summarize.SummarizeManager`,
gating on the index-readiness callback first (subtree expansion needs a built
index). Reached via the ``Vault.summarizer`` accessor; present only when a
summarization backend is configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.managers.summarize import SummarizeManager
    from markdown_vault_mcp.types import SummaryResult


class SummarizeFacet:
    """LLM-backed summarization over the shared document manager."""

    def __init__(
        self,
        *,
        summarize_mgr: SummarizeManager,
        require_built: Callable[[], None],
    ) -> None:
        """Hold the manager the summarize method delegates to.

        Args:
            summarize_mgr: Path resolution, body reads, prompt build, and
                backend call.
            require_built: Index-readiness gate (subtree expansion reads the
                built index).
        """
        self._summarize_mgr = summarize_mgr
        self._require_built = require_built

    def summarize(
        self,
        paths: list[str],
        *,
        focus: str | None = None,
        mode: str = "synthesis",
    ) -> SummaryResult:
        """Summarize a note, a set of notes, or a folder subtree.

        Args:
            paths: Note paths and/or folder prefixes; folders expand to their
                notes via the subtree table-of-contents.
            focus: Optional free-text steer folded into the prompt.
            mode: ``"synthesis"`` (default) or ``"per_note"``.

        Returns:
            A :class:`~markdown_vault_mcp.types.SummaryResult`.

        Raises:
            ValueError: On invalid input or when no readable notes were found.
            RuntimeError: If the summarization backend call fails.
        """
        self._require_built()
        return self._summarize_mgr.summarize(paths, focus=focus, mode=mode)
