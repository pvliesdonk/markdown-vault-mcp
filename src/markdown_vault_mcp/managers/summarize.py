"""LLM-backed note summarization manager.

Resolves a note, a set of notes, or a folder subtree into note bodies, builds
a prompt, and delegates generation to a provider-neutral
:class:`~markdown_vault_mcp.summarizer.Summarizer`.  Reuses
:meth:`DocumentManager.get_toc` for subtree expansion and
:meth:`DocumentManager.read` for bodies, so it depends only on the document
manager and the summarizer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from markdown_vault_mcp.types import (
    SubtreeToc,
    SummaryResult,
    SummarySource,
)

if TYPE_CHECKING:
    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.summarizer import Summarizer
    from markdown_vault_mcp.types import NoteContent

logger = logging.getLogger(__name__)

_VALID_MODES = ("synthesis", "per_note")

_SYSTEM_SYNTHESIS = (
    "You summarize notes from a markdown vault. Produce a single cohesive "
    "summary that synthesizes across all the provided notes. When a point "
    "comes from a specific note, reference that note by its path (e.g. "
    "`folder/note.md`) so the reader can trace each claim back to its source. "
    "Prefer prose over bullet dumps; be faithful to the notes and do not "
    "invent details."
)

_SYSTEM_PER_NOTE = (
    "You summarize notes from a markdown vault. Produce a separate concise "
    "summary for each note provided. Head each summary with the note's path "
    "exactly as given. Do not merge notes together; keep one summary per note, "
    "in the order provided. Be faithful to the notes and do not invent details."
)


class SummarizeManager:
    """Turn a set of note/subtree paths into an LLM-generated summary."""

    def __init__(
        self,
        *,
        doc_mgr: DocumentManager,
        summarizer: Summarizer,
        max_notes: int,
        max_input_chars: int,
    ) -> None:
        """Hold the collaborators and input caps.

        Args:
            doc_mgr: Document reads + subtree table-of-contents.
            summarizer: The generation backend.
            max_notes: Cap on notes summarised per call.
            max_input_chars: Aggregate cap on note characters sent to the model.
        """
        self._doc_mgr = doc_mgr
        self._summarizer = summarizer
        self._max_notes = max_notes
        self._max_input_chars = max_input_chars

    def summarize(
        self,
        paths: list[str],
        *,
        focus: str | None = None,
        mode: str = "synthesis",
    ) -> SummaryResult:
        """Summarize one or more notes and/or folder subtrees.

        Args:
            paths: Note paths (``"a/b.md"``) and/or folder prefixes (``"a/b"``).
                Folders expand to their notes via the subtree table-of-contents.
            focus: Optional free-text steer folded into the prompt (e.g.
                ``"extract action items"``). ``None`` yields a general summary.
            mode: ``"synthesis"`` (default) for one cross-note summary that
                references sources, or ``"per_note"`` for one summary per note.

        Returns:
            A :class:`~markdown_vault_mcp.types.SummaryResult`.

        Raises:
            ValueError: If *mode* is invalid, *paths* is empty, or no readable
                notes were found for the given paths.
            RuntimeError: If the summarization backend call fails.
        """
        if mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")
        if not paths:
            raise ValueError("paths must contain at least one note or folder path.")

        resolved, truncated_paths = self._resolve_paths(paths)
        if not resolved:
            raise ValueError("No notes found for the given paths.")

        notes, truncated_input = self._gather_notes(resolved)
        if not notes:
            raise ValueError("No readable notes found for the given paths.")

        system, user = self._build_prompt(notes, focus=focus, mode=mode)
        summary = self._summarizer.summarize(system, user)

        return SummaryResult(
            summary=summary,
            sources=[SummarySource(path=p, title=t) for p, t, _ in notes],
            mode=mode,
            truncated=truncated_paths or truncated_input,
        )

    def _resolve_paths(self, paths: list[str]) -> tuple[list[str], bool]:
        """Expand folders to notes and dedupe; cap at ``max_notes``.

        Returns the ordered, de-duplicated note paths and whether the set was
        truncated (a subtree exceeded its own cap, or the total exceeded
        ``max_notes``).
        """
        resolved: list[str] = []
        seen: set[str] = set()
        truncated = False
        for path in paths:
            candidates, sub_truncated = self._expand_path(path)
            truncated = truncated or sub_truncated
            for candidate in candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    resolved.append(candidate)
        if len(resolved) > self._max_notes:
            resolved = resolved[: self._max_notes]
            truncated = True
        return resolved, truncated

    def _expand_path(self, path: str) -> tuple[list[str], bool]:
        """Resolve one input path to note paths (single note or subtree)."""
        if path.endswith(".md"):
            return [path], False
        # Pass the configured cap through: get_toc defaults max_notes to 200,
        # which would silently override a SUMMARIZE_MAX_NOTES set above it (the
        # post-hoc cap in _resolve_paths only masks this when max_notes < 200).
        toc = self._doc_mgr.get_toc(path, max_notes=self._max_notes)
        if isinstance(toc, SubtreeToc):
            return [note.path for note in toc.notes], toc.truncated
        return [], False

    def _gather_notes(
        self, resolved: list[str]
    ) -> tuple[list[tuple[str, str, str]], bool]:
        """Read note bodies, enforcing the aggregate character cap.

        Returns ``(path, title, body)`` triples in order and whether the input
        was truncated (a body was cut or later notes were dropped at the cap).
        Missing or unreadable notes are skipped.
        """
        notes: list[tuple[str, str, str]] = []
        total = 0
        truncated = False
        for path in resolved:
            note = self._read_note(path)
            if note is None:
                continue
            remaining = self._max_input_chars - total
            if remaining <= 0:
                truncated = True
                break
            body = note.content
            if len(body) > remaining:
                body = body[:remaining]
                truncated = True
            total += len(body)
            notes.append((path, note.title, body))
            if truncated:
                break
        return notes, truncated

    def _read_note(self, path: str) -> NoteContent | None:
        """Read a note, skipping missing/oversized/unparseable ones.

        Two skip paths converge on ``None``: ``read`` returns ``None`` for a
        missing or unparseable file, and raises ``ValueError`` only for an
        oversized note (``MAX_NOTE_READ_BYTES``), which we catch here so one
        large note does not abort a whole subtree summary.
        """
        try:
            return self._doc_mgr.read(path)
        except ValueError as exc:
            logger.debug("summarize_skip_note path=%s reason=%s", path, exc)
            return None

    @staticmethod
    def _build_prompt(
        notes: list[tuple[str, str, str]],
        *,
        focus: str | None,
        mode: str,
    ) -> tuple[str, str]:
        """Build the (system, user) prompt for the given notes."""
        system = _SYSTEM_SYNTHESIS if mode == "synthesis" else _SYSTEM_PER_NOTE
        if focus and focus.strip():
            system = f"{system} Focus specifically on: {focus.strip()}"
        blocks = [
            f"### {path}\n(title: {title})\n\n{body}" for path, title, body in notes
        ]
        user = "\n\n---\n\n".join(blocks)
        return system, user
