"""Bounded preview and application of a scoped textual replacement."""

from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import regex as regex_engine  # type: ignore[import-untyped]

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentNotFoundError,
    InvalidRequestError,
)
from markdown_vault_mcp.hashing import compute_etag

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp.managers.document import DocumentManager

MAX_NOTES = 500
MAX_NOTE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_MATCHES = 10_000
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_DIFF_CHARS = 100_000
REGEX_NOTE_SECONDS = 0.25
REGEX_TOTAL_SECONDS = 2.0
NOTICE = (
    "Applying performs ordinary write side effects: in an OKF-active vault, "
    "generated is stamped, verified is cleared, and folder log.md and index.md "
    "are updated. Diffs show requested text edits only."
)


@dataclass(frozen=True)
class ReplaceRequest:
    """Request a scoped preview or application of a replacement."""

    pattern: str
    replacement: str = ""
    paths: tuple[str, ...] = ()
    folders: tuple[str, ...] = ()
    regex: bool = False
    dry_run: bool = True
    plan: str | None = None


@dataclass
class NoteReplacement:
    """A changed note and its replacement count and preview diff."""

    path: str
    replacements: int
    diff: str | None


@dataclass
class ReplaceResult:
    """A complete preview or application result."""

    dry_run: bool
    plan: str
    notes: list[NoteReplacement]
    replacements: int
    scanned: int
    notice: str | None


class ReplaceInterruptedError(RuntimeError):
    """A write failed after some replacements may have been applied."""


class BulkReplacer:
    """Preview or apply scoped replacements through a document manager."""

    def __init__(self, doc_mgr: DocumentManager) -> None:
        self._doc_mgr = doc_mgr

    def replace(
        self, request: ReplaceRequest, *, maintain: Callable[[str], None] | None = None
    ) -> ReplaceResult:
        """Preview or apply a request, refusing stale previews before writing.

        Args:
            request: Selectors, text and preview plan.
            maintain: Optional post-write convention maintenance callback.

        Returns:
            Complete replacement counts and diffs for a preview.
        """
        if not request.pattern or not (request.paths or request.folders):
            raise InvalidRequestError(
                "Provide a nonempty pattern and paths or folders."
            )
        if (
            len(request.pattern.encode()) > 4096
            or len(request.replacement.encode()) > 65536
        ):
            raise InvalidRequestError(
                "Pattern or replacement too large; shorten the request."
            )
        if not request.dry_run and not request.plan:
            raise InvalidRequestError("Preview first, then pass the returned plan.")
        try:
            pattern = regex_engine.compile(
                request.pattern
                if request.regex
                else regex_engine.escape(request.pattern),
                regex_engine.V0,
            )
        except regex_engine.error as exc:
            raise InvalidRequestError(
                f"Invalid pattern: {exc}; correct the pattern."
            ) from exc
        if request.dry_run:
            return self._prepare(request, pattern)[0]
        self._doc_mgr.ensure_writable()
        written: list[str] = []
        try:
            with self._doc_mgr.write_scope():
                try:
                    result, changes = self._prepare(request, pattern)
                except (DocumentNotFoundError, FileNotFoundError) as exc:
                    raise ConcurrentModificationError(
                        "selection", request.plan or "", "missing"
                    ) from exc
                if result.plan != request.plan:
                    raise ConcurrentModificationError(
                        "selection", request.plan or "", result.plan
                    )
                for index, (path, text, etag) in enumerate(changes):
                    try:
                        self._doc_mgr.write(path, text, if_match=etag)
                    except Exception as exc:
                        remaining = [item[0] for item in changes[index + 1 :]]
                        raise ReplaceInterruptedError(
                            f"Replacement interrupted. Written: {written}; failed: {path}; unwritten: {remaining}. Inspect the notes before retrying."
                        ) from exc
                    written.append(path)
        finally:
            if maintain is not None:
                for path in written:
                    maintain(path)
        result.dry_run = False
        for note in result.notes:
            note.diff = None
        return result

    def _prepare(
        self, request: ReplaceRequest, pattern: regex_engine.Pattern
    ) -> tuple[ReplaceResult, list[tuple[str, str, str]]]:
        candidates = self._doc_mgr.replace_candidates(
            request.paths, request.folders, MAX_NOTES
        )
        if (
            any(size > MAX_NOTE_BYTES for _, size in candidates)
            or sum(size for _, size in candidates) > MAX_TOTAL_BYTES
        ):
            raise InvalidRequestError(
                "Selection exceeds the size limit; narrow paths or folders."
            )
        snapshots = []
        remaining = MAX_TOTAL_BYTES
        for path, _ in candidates:
            text, etag, size = self._doc_mgr.read_for_replace(
                path, min(MAX_NOTE_BYTES, remaining)
            )
            remaining -= size
            snapshots.append((path, text, etag))
        identity = [(path, etag) for path, _, etag in snapshots]
        plan = compute_etag(
            json.dumps(
                [request.pattern, request.replacement, request.regex, identity],
                ensure_ascii=False,
            ).encode()
        )
        deadline = time.monotonic() + REGEX_TOTAL_SECONDS
        notes: list[NoteReplacement] = []
        changes: list[tuple[str, str, str]] = []
        total_matches = total_output = total_diff = 0
        for path, text, etag in snapshots:
            last = 0
            generated = 0

            def replacement(
                match: regex_engine.Match,
                text: str = text,
                total_output: int = total_output,
            ) -> str:
                nonlocal last, generated, total_matches
                total_matches += 1
                if total_matches > MAX_MATCHES:
                    raise InvalidRequestError(
                        "Too many matches; narrow paths or folders."
                    )
                generated += len(text[last : match.start()].encode())
                # Upper bound expansion before the engine allocates it.
                if (
                    request.regex
                    and len(request.replacement.encode()) * max(1, len(text.encode()))
                    > MAX_OUTPUT_BYTES - total_output - generated
                ):
                    raise InvalidRequestError(
                        "Replacement may be too large; narrow the request."
                    )
                value = (
                    match.expand(request.replacement)
                    if request.regex
                    else request.replacement
                )
                generated += len(value.encode())
                last = match.end()
                if total_output + generated > MAX_OUTPUT_BYTES:
                    raise InvalidRequestError(
                        "Replacement output too large; narrow the request."
                    )
                return value

            timeout = min(REGEX_NOTE_SECONDS, deadline - time.monotonic())
            if timeout <= 0:
                raise InvalidRequestError("Regex timed out; simplify the pattern.")
            try:
                new, count = pattern.subn(replacement, text, timeout=timeout)
            except TimeoutError as exc:
                raise InvalidRequestError(
                    "Regex timed out; simplify the pattern."
                ) from exc
            except (regex_engine.error, IndexError) as exc:
                raise InvalidRequestError(
                    f"Invalid replacement: {exc}; correct the replacement."
                ) from exc
            generated += len(text[last:].encode())
            total_output += generated
            if total_output > MAX_OUTPUT_BYTES:
                raise InvalidRequestError(
                    "Replacement output too large; narrow the request."
                )
            if new == text:
                continue
            diff = "".join(
                line
                if line.endswith("\n")
                else line + "\n\\ No newline at end of file\n"
                for line in difflib.unified_diff(
                    text.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    fromfile=path,
                    tofile=path,
                )
            )
            total_diff += len(diff)
            if total_diff > MAX_DIFF_CHARS:
                raise InvalidRequestError("Diff too large; narrow paths or folders.")
            notes.append(NoteReplacement(path, count, diff))
            changes.append((path, new, etag))
        return ReplaceResult(
            True,
            plan,
            notes,
            sum(n.replacements for n in notes),
            len(snapshots),
            NOTICE,
        ), changes
