"""LLM-backed note summarization manager.

Resolves a note, a set of notes, or a folder subtree into note bodies, builds
prompts, and delegates generation to a provider-neutral
:class:`~markdown_vault_mcp.summarizer.Summarizer`.  Reuses
:meth:`DocumentManager.get_toc` for subtree expansion and
:meth:`DocumentManager.read` for bodies, so it depends only on the document
manager and the summarizer.

Large inputs are handled map-reduce style (#922): notes are packed into
batches of at most ``max_input_chars`` characters, each batch is summarized
independently (the map phase, with bounded parallelism), and in synthesis
mode the batch summaries are combined by a final reduce call (recursively,
should the summaries themselves exceed one request budget).  A single batch
takes the direct one-call path, identical to the pre-#922 behavior.  Total
coverage is governed by ``max_notes``; ``max_input_chars`` bounds each
individual model request.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
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

# Ceiling used when expanding subtrees so the pre-cap match count is known
# (get_toc caps listings itself); the configured max_notes still governs how
# many notes are actually summarized.
_RESOLVE_CAP = 10_000

# Concurrent map-phase backend calls. Bounded and small: the library is sync
# (the MCP layer wraps it in a thread), so this is a nested, short-lived pool.
_MAP_CONCURRENCY = 4

# Block separator in user prompts; accounted for when packing batches.
_SEPARATOR = "\n\n---\n\n"

# The response is a terminal tool result, not a conversation turn: offers of
# further help ("If you want I can...") are noise nobody can answer (#921).
_NO_META = (
    " Output only the summary itself: no offers of further help, no "
    "follow-up questions, and no meta-commentary about what you could do next."
)

_SYSTEM_SYNTHESIS = (
    "You summarize notes from a markdown vault. Produce a single cohesive "
    "summary that synthesizes across all the provided notes. When a point "
    "comes from a specific note, reference that note by its path (e.g. "
    "`folder/note.md`) so the reader can trace each claim back to its source. "
    "Prefer prose over bullet dumps; be faithful to the notes and do not "
    "invent details." + _NO_META
)

_SYSTEM_PER_NOTE = (
    "You summarize notes from a markdown vault. Produce a separate concise "
    "summary for each note provided. Head each summary with the note's path "
    "exactly as given. Do not merge notes together; keep one summary per note, "
    "in the order provided. Be faithful to the notes and do not invent "
    "details." + _NO_META
)

# Map phase over one batch of a larger set: keep enough specifics (and the
# path references) for the reduce phase to combine partial summaries.
# SYNC: the map/reduce prose here is mirrored — adapted for client subagents —
# in static/prompts/summarize-subtree.md (#1035); tune the two together.
_SYSTEM_MAP = (
    "You summarize notes from a markdown vault. The notes provided are one "
    "part of a larger collection; other parts are summarized separately. "
    "Produce a detailed partial summary of these notes that preserves "
    "concrete specifics, and reference each note by its path (e.g. "
    "`folder/note.md`) so the partial summaries can be combined without "
    "losing attribution. Be faithful to the notes and do not invent "
    "details." + _NO_META
)

# Reduce phase: combine partial summaries into the final synthesis.
_SYSTEM_REDUCE = (
    "You combine partial summaries, each covering a different subset of "
    "notes from one markdown vault, into a single cohesive summary that "
    "reads as one text. Merge overlapping points, keep the note-path "
    "references intact, prefer prose over bullet dumps, and do not invent "
    "details beyond the partial summaries." + _NO_META
)


def _with_focus(system: str, focus: str | None) -> str:
    """Fold an optional focus instruction into a system prompt."""
    if focus and focus.strip():
        return f"{system} Focus specifically on: {focus.strip()}"
    return system


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
            max_notes: Cap on notes summarised per call (the coverage and
                cost lever).
            max_input_chars: Character budget of a single model request; a
                larger input is split into batches and combined map-reduce
                style (#922).
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
        max_notes: int | None = None,
    ) -> SummaryResult:
        """Summarize one or more notes and/or folder subtrees.

        Args:
            paths: Note paths (``"a/b.md"``) and/or folder prefixes (``"a/b"``).
                Folders expand to their notes via the subtree table-of-contents.
            focus: Optional free-text steer folded into the prompt (e.g.
                ``"extract action items"``). ``None`` yields a general summary.
            mode: ``"synthesis"`` (default) for one cross-note summary that
                references sources, or ``"per_note"`` for one summary per note.
            max_notes: Optional per-call note limit. Clamped to the server's
                configured cap, which stays the operator's per-call cost and
                latency ceiling (#925). ``None`` uses the server cap.

        Returns:
            A :class:`~markdown_vault_mcp.types.SummaryResult`.

        Raises:
            ValueError: If *mode* is invalid, *paths* is empty, *max_notes*
                is below 1, or no readable notes were found for the given
                paths.
            RuntimeError: If a summarization backend call fails.
        """
        if mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")
        if not paths:
            raise ValueError("paths must contain at least one note or folder path.")
        if max_notes is not None and max_notes < 1:
            raise ValueError(f"max_notes must be >= 1, got {max_notes!r}")

        limit = (
            min(max_notes, self._max_notes)
            if max_notes is not None
            else self._max_notes
        )
        resolved, matched = self._resolve_paths(paths, limit)
        if not resolved:
            raise ValueError("No notes found for the given paths.")

        notes, note_clipped = self._gather_notes(resolved)
        if not notes:
            raise ValueError("No readable notes found for the given paths.")

        batches = self._pack_batches(notes)
        summary, reduce_clipped = self._summarize_batches(
            batches, focus=focus, mode=mode
        )

        omitted = matched - len(notes)
        return SummaryResult(
            summary=summary,
            sources=[SummarySource(path=p, title=t) for p, t, _ in notes],
            mode=mode,
            truncated=omitted > 0 or note_clipped or reduce_clipped,
            notes_included=len(notes),
            notes_omitted=omitted,
            notes_limit=limit,
            hint=self._coverage_hint(len(notes), matched, limit),
        )

    @staticmethod
    def _coverage_hint(included: int, matched: int, limit: int) -> str | None:
        """Build the caller-facing recovery hint when notes were omitted.

        Guidance placed in the result reaches the calling model at the
        moment it matters; schema docs alone are often ignored (#925).
        """
        if matched <= included:
            return None
        return (
            f"Only {included} of {matched} matched notes were summarized "
            f"(note limit {limit}). For full coverage, summarize subfolders "
            "or smaller sets of paths in separate calls."
        )

    def _resolve_paths(self, paths: list[str], limit: int) -> tuple[list[str], int]:
        """Expand folders to notes and dedupe; cap at *limit*.

        Returns the ordered, de-duplicated note paths (capped at *limit*)
        and the total number of matched notes before the cap, so callers can
        report how many were omitted (#922).
        """
        resolved: list[str] = []
        seen: set[str] = set()
        for path in paths:
            for candidate in self._expand_path(path):
                if candidate not in seen:
                    seen.add(candidate)
                    resolved.append(candidate)
        matched = len(resolved)
        return resolved[:limit], matched

    def _expand_path(self, path: str) -> list[str]:
        """Resolve one input path to note paths (single note or subtree)."""
        if path.endswith(".md"):
            return [path]
        # Expand generously so the pre-cap match count is known; the
        # note limit is applied by _resolve_paths afterwards.
        cap = max(_RESOLVE_CAP, self._max_notes)
        toc = self._doc_mgr.get_toc(path, max_notes=cap)
        if isinstance(toc, SubtreeToc):
            return [note.path for note in toc.notes]
        return []

    def _gather_notes(
        self, resolved: list[str]
    ) -> tuple[list[tuple[str, str, str]], bool]:
        """Read note bodies, clipping any single body to one request budget.

        Returns ``(path, title, body)`` triples in order and whether any body
        was cut. Missing or unreadable notes are skipped. There is no
        aggregate cap here: oversize totals are handled by batching (#922).
        """
        notes: list[tuple[str, str, str]] = []
        clipped = False
        for path in resolved:
            note = self._read_note(path)
            if note is None:
                continue
            body = note.content
            # A block must fit one request on its own; leave room for the
            # header and separator the prompt adds around the body.
            overhead = len(self._format_block(path, note.title, "")) + len(_SEPARATOR)
            budget = max(1, self._max_input_chars - overhead)
            if len(body) > budget:
                body = body[:budget]
                clipped = True
            notes.append((path, note.title, body))
        return notes, clipped

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
    def _format_block(path: str, title: str, body: str) -> str:
        """Format one note as a prompt block."""
        return f"### {path}\n(title: {title})\n\n{body}"

    def _pack_batches(
        self, notes: list[tuple[str, str, str]]
    ) -> list[list[tuple[str, str, str]]]:
        """Greedily pack notes, in order, into per-request character budgets."""
        batches: list[list[tuple[str, str, str]]] = []
        current: list[tuple[str, str, str]] = []
        used = 0
        for path, title, body in notes:
            size = len(self._format_block(path, title, body)) + len(_SEPARATOR)
            if current and used + size > self._max_input_chars:
                batches.append(current)
                current = []
                used = 0
            current.append((path, title, body))
            used += size
        if current:
            batches.append(current)
        return batches

    def _summarize_batches(
        self,
        batches: list[list[tuple[str, str, str]]],
        *,
        focus: str | None,
        mode: str,
    ) -> tuple[str, bool]:
        """Run the map phase over *batches* and reduce to one summary.

        Returns the summary and whether any partial summary was clipped to
        fit a request budget during the reduce phase.
        """
        if len(batches) == 1:
            # Single request: identical to the pre-#922 direct path.
            system = _SYSTEM_SYNTHESIS if mode == "synthesis" else _SYSTEM_PER_NOTE
            summary = self._summarizer.summarize(
                _with_focus(system, focus), self._join_blocks(batches[0])
            )
            return summary, False

        logger.info(
            "summarize_map_reduce batches=%d notes=%d mode=%s",
            len(batches),
            sum(len(b) for b in batches),
            mode,
        )
        map_system = _with_focus(
            _SYSTEM_MAP if mode == "synthesis" else _SYSTEM_PER_NOTE, focus
        )
        partials = self._run_parallel(
            [self._join_blocks(batch) for batch in batches], map_system
        )
        if mode == "per_note":
            # Per-note output is already in note order; no reduce needed.
            return "\n\n".join(partials), False
        return self._reduce(partials, focus=focus)

    def _join_blocks(self, batch: list[tuple[str, str, str]]) -> str:
        """Join a batch of notes into one user prompt."""
        return _SEPARATOR.join(
            self._format_block(path, title, body) for path, title, body in batch
        )

    def _run_parallel(self, users: list[str], system: str) -> list[str]:
        """Summarize *users* with bounded parallelism, preserving order."""
        if len(users) == 1:
            return [self._summarizer.summarize(system, users[0])]
        workers = min(_MAP_CONCURRENCY, len(users))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(
                pool.map(lambda user: self._summarizer.summarize(system, user), users)
            )

    def _reduce(self, partials: list[str], *, focus: str | None) -> tuple[str, bool]:
        """Combine partial summaries, recursively if they exceed one budget.

        Returns the combined summary and whether any partial was clipped to
        fit a request budget along the way.
        """
        system = _with_focus(_SYSTEM_REDUCE, focus)
        clipped = False
        while True:
            if len(partials) == 1:
                # A previous round already collapsed everything into one
                # summary; re-summarizing it would be a wasted, lossy call.
                return partials[0], clipped
            groups, group_clipped = self._pack_texts(partials)
            clipped = clipped or group_clipped
            if len(groups) == 1:
                return self._summarizer.summarize(system, groups[0]), clipped
            if len(groups) >= len(partials):
                # Every group is a singleton (each partial nearly fills the
                # budget): force pairwise merges, clipped to the budget, so
                # each round strictly shrinks the list and terminates.
                merged = [
                    "\n\n".join(partials[i : i + 2]) for i in range(0, len(partials), 2)
                ]
                clipped = clipped or any(len(m) > self._max_input_chars for m in merged)
                groups = [m[: self._max_input_chars] for m in merged]
            partials = self._run_parallel(groups, system)

    def _pack_texts(self, texts: list[str]) -> tuple[list[str], bool]:
        """Greedily join texts, in order, into per-request character budgets.

        Returns the groups and whether any single text had to be clipped to
        fit one budget on its own.
        """
        groups: list[str] = []
        current: list[str] = []
        used = 0
        any_clipped = False
        for text in texts:
            clipped = text[: self._max_input_chars]
            any_clipped = any_clipped or len(clipped) < len(text)
            size = len(clipped) + 2  # joined with a blank line
            if current and used + size > self._max_input_chars:
                groups.append("\n\n".join(current))
                current = []
                used = 0
            current.append(clipped)
            used += size
        if current:
            groups.append("\n\n".join(current))
        return groups, any_clipped
