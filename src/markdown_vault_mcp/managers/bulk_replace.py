"""Bounded preview and application of a scoped textual replacement.

Regex timeout semantics are recorded in docs/design/reference/regex-timeout.md.
"""

from __future__ import annotations

import difflib
import importlib
import json
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import regex as regex_engine  # type: ignore[import-untyped]

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentNotFoundError,
    InvalidRequestError,
)
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.okf import OKF_RESERVED_FILENAMES

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
        pattern = self._compile(request)
        if request.dry_run:
            return self._prepare(
                request, pattern, reject_reserved=maintain is not None
            )[0]
        self._doc_mgr.ensure_writable()
        written: list[str] = []
        uncertain: list[str] = []
        try:
            with self._doc_mgr.write_scope():
                try:
                    result, changes = self._prepare(
                        request, pattern, reject_reserved=maintain is not None
                    )
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
                        if self._possibly_changed(path, etag, exc):
                            uncertain.append(path)
                        remaining = [item[0] for item in changes[index + 1 :]]
                        raise ReplaceInterruptedError(
                            f"Replacement interrupted. Written: {written}; failed path may already be written: {path}; unwritten: {remaining}. Inspect the notes before retrying."
                        ) from exc
                    written.append(path)
        finally:
            if maintain is not None:
                for path in written + uncertain:
                    maintain(path)
        result.dry_run = False
        for note in result.notes:
            note.diff = None
        return result

    def _possibly_changed(self, path: str, etag: str, exc: Exception) -> bool:
        if isinstance(exc, ConcurrentModificationError):
            return False
        try:
            _, current_etag, _ = self._doc_mgr.read_for_replace(path, MAX_NOTE_BYTES)
        except Exception:
            return True
        return current_etag != etag

    @staticmethod
    def _compile(request: ReplaceRequest) -> regex_engine.Pattern:
        if not request.pattern or not (request.paths or request.folders):
            raise InvalidRequestError(
                "Provide a nonempty pattern and paths or folders."
            )
        try:
            pattern_bytes = request.pattern.encode()
            replacement_bytes = request.replacement.encode()
        except UnicodeEncodeError as exc:
            raise InvalidRequestError(
                "Pattern and replacement must be valid UTF-8 text."
            ) from exc
        if len(pattern_bytes) > 4096 or len(replacement_bytes) > 65536:
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
        except (regex_engine.error, RecursionError) as exc:
            raise InvalidRequestError(
                f"Invalid pattern: {exc}; correct the pattern."
            ) from exc
        return pattern

    def _snapshots(
        self, request: ReplaceRequest, *, reject_reserved: bool
    ) -> list[tuple[str, str, str]]:
        candidates = self._doc_mgr.replace_candidates(
            request.paths, request.folders, MAX_NOTES
        )
        if reject_reserved and any(
            PurePosixPath(path).name in OKF_RESERVED_FILENAMES
            for path in (*request.paths, *(path for path, _ in candidates))
        ):
            raise InvalidRequestError(
                "reserved index.md and log.md cannot be selected with OKF maintenance; choose other notes."
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
        return snapshots

    @staticmethod
    def _replacement_template(
        pattern: regex_engine.Pattern, replacement: str
    ) -> list[str | int]:
        """Use regex's compiler so the bound follows its exact escape grammar."""
        module = importlib.import_module(regex_engine.compile.__module__)
        return module._compile_replacement_helper(pattern, replacement)  # type: ignore[no-any-return]

    def _request_template(
        self, request: ReplaceRequest, pattern: regex_engine.Pattern
    ) -> list[str | int]:
        try:
            return (
                self._replacement_template(pattern, request.replacement)
                if request.regex
                else [request.replacement]
            )
        except (regex_engine.error, IndexError, ValueError, OverflowError) as exc:
            raise InvalidRequestError(
                f"Invalid replacement: {exc}; correct the replacement."
            ) from exc

    @staticmethod
    def _diff(path: str, text: str, new: str, total_diff: int) -> tuple[str, int]:
        diff = "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
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
        return diff, total_diff

    @staticmethod
    def _bound_expansion(
        match: regex_engine.Match, text: str, template: list[str | int], remaining: int
    ) -> int:
        budget = remaining
        sizes: dict[int, int] = {}
        for part in template:
            if isinstance(part, str):
                remaining -= len(part.encode())
            else:
                if part not in sizes:
                    start, end = match.span(part)
                    sizes[part] = len(text[start:end].encode()) if start >= 0 else 0
                remaining -= sizes[part]
            if remaining < 0:
                raise InvalidRequestError(
                    "Replacement output too large; narrow the request."
                )
        return budget - remaining

    def _prepare(
        self,
        request: ReplaceRequest,
        pattern: regex_engine.Pattern,
        *,
        reject_reserved: bool,
    ) -> tuple[ReplaceResult, list[tuple[str, str, str]]]:
        snapshots = self._snapshots(request, reject_reserved=reject_reserved)
        template = self._request_template(request, pattern)
        deadline = time.monotonic() + REGEX_TOTAL_SECONDS
        notes: list[NoteReplacement] = []
        changes: list[tuple[str, str, str]] = []
        total_matches = total_output = total_diff = added = 0
        for path, text, etag in snapshots:

            def replacement(
                match: regex_engine.Match,
                text: str = text,
            ) -> str:
                nonlocal added, total_matches
                total_matches += 1
                if total_matches > MAX_MATCHES:
                    raise InvalidRequestError(
                        "Too many matches; narrow paths or folders."
                    )
                added += self._bound_expansion(
                    match, text, template, MAX_OUTPUT_BYTES - added
                )
                return (
                    match.expand(request.replacement)
                    if request.regex
                    else request.replacement
                )

            timeout = min(REGEX_NOTE_SECONDS, deadline - time.monotonic())
            if timeout <= 0:
                raise InvalidRequestError("Regex timed out; simplify the pattern.")
            try:
                new, count = pattern.subn(replacement, text, timeout=timeout)
            except TimeoutError as exc:
                raise InvalidRequestError(
                    "Regex timed out; simplify the pattern."
                ) from exc
            total_output += len(new.encode())
            if total_output > MAX_OUTPUT_BYTES:
                raise InvalidRequestError(
                    "Replacement output too large; narrow the request."
                )
            if new == text:
                continue
            self._doc_mgr.validate_for_replace(path, new)
            diff, total_diff = self._diff(path, text, new, total_diff)
            notes.append(NoteReplacement(path, count, diff))
            changes.append((path, new, etag))
        identity = [
            (path, etag, compute_etag(text.encode())) for path, text, etag in changes
        ]
        plan = compute_etag(
            json.dumps(
                [request.pattern, request.replacement, request.regex, identity],
                ensure_ascii=False,
            ).encode()
        )
        return ReplaceResult(
            True,
            plan,
            notes,
            sum(n.replacements for n in notes),
            len(snapshots),
            NOTICE,
        ), changes
