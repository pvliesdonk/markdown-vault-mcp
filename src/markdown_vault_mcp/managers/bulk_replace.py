"""Bounded preview and application of a scoped textual replacement.

Regex timeout semantics are recorded in docs/design/reference/regex-timeout.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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
MAX_DIFF_CHARS = 1024 * 1024
REPLACE_SECONDS = 3.0


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


class ReplaceInterruptedError(RuntimeError):
    """A write failed after some replacements may have been applied."""


class BulkReplacer:
    """Preview or apply scoped replacements through a document manager."""

    def __init__(self, doc_mgr: DocumentManager) -> None:
        self._doc_mgr = doc_mgr

    def replace(
        self,
        request: ReplaceRequest,
        *,
        maintain: Callable[[str], None] | None = None,
        skip_reserved: bool | None = None,
    ) -> ReplaceResult:
        """Preview or apply a request, refusing stale previews before writing.

        Args:
            request: Selectors, text and preview plan.
            maintain: Optional post-write convention maintenance callback.
            skip_reserved: Whether maintenance can update reserved notes.

        Returns:
            Complete replacement counts and diffs for a preview.
        """
        self._validate_request(request)
        reject_reserved = (
            maintain is not None if skip_reserved is None else skip_reserved
        )
        if request.dry_run:
            return self._prepare(request, reject_reserved=reject_reserved)[0]
        self._doc_mgr.ensure_writable()
        written: list[str] = []
        uncertain: list[str] = []
        try:
            with self._doc_mgr.write_scope():
                try:
                    result, changes = self._prepare(
                        request, reject_reserved=reject_reserved
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
    def _validate_request(request: ReplaceRequest) -> None:
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

    def _snapshots(
        self, request: ReplaceRequest, *, reject_reserved: bool
    ) -> list[tuple[str, str, str]]:
        candidates = self._doc_mgr.replace_candidates(
            request.paths, request.folders, MAX_NOTES, skip_reserved=reject_reserved
        )
        snapshots = []
        remaining = MAX_TOTAL_BYTES
        for path in candidates:
            text, etag, size = self._doc_mgr.read_for_replace(
                path, min(MAX_NOTE_BYTES, remaining)
            )
            remaining -= size
            snapshots.append((path, text, etag))
        return snapshots

    def _prepare(
        self, request: ReplaceRequest, *, reject_reserved: bool
    ) -> tuple[ReplaceResult, list[tuple[str, str, str]]]:
        snapshots = self._snapshots(request, reject_reserved=reject_reserved)
        payload = {
            "pattern": request.pattern,
            "replacement": request.replacement,
            "regex": request.regex,
            "snapshots": snapshots,
            "want_diff": request.dry_run,
            "max_matches": MAX_MATCHES,
            "max_output": MAX_OUTPUT_BYTES,
            "max_diff": MAX_DIFF_CHARS,
        }
        try:
            result = subprocess.run(  # noqa: S603, RUF100  # Fixed Python executable and worker; data uses stdin.
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).with_name("_replace_worker.py")),
                ],
                input=json.dumps(payload, ensure_ascii=True),
                text=True,
                capture_output=True,
                timeout=REPLACE_SECONDS,
                check=True,
            )
            response = json.loads(result.stdout)
        except subprocess.TimeoutExpired as exc:
            raise InvalidRequestError(
                "Replacement worker timed out; narrow the selection or simplify the pattern."
            ) from exc
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise RuntimeError("Replacement worker failed to complete.") from exc
        if "error" in response:
            raise InvalidRequestError(
                f"Invalid pattern or replacement: {response['error']}"
            )
        if "limit" in response:
            raise InvalidRequestError(response["limit"])
        etags = {path: etag for path, _, etag in snapshots}
        notes = []
        changes = []
        for path, new, count, diff in response["proposals"]:
            self._doc_mgr.validate_for_replace(path, new)
            notes.append(NoteReplacement(path, count, diff))
            changes.append((path, new, etags[path]))
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
        ), changes
