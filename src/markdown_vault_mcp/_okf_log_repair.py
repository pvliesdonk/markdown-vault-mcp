"""Exact-byte proposals for the legacy stacked log frontmatter defect (#1403).

Matching bytes identify a candidate, never its origin. Only an operator who
reviewed the preview authorizes the removal, by applying it for the etag the
preview reported; no ordinary write uses this.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel

from markdown_vault_mcp._yaml12 import load_yaml
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    InvalidRequestError,
)
from markdown_vault_mcp.hashing import compute_etag

if TYPE_CHECKING:
    from pathlib import Path

_UNIT = re.compile(rb"---(?P<nl>\r?\n)(?P<yaml>.*?)(?P=nl)---(?P=nl)(?P=nl)", re.DOTALL)
_REVIEW = (
    "Identical adjacent blocks are a candidate, not proof of generated origin. "
    "Review the exact removal and the retained opening block, and apply it "
    "only if nothing removed was written on purpose."
)


class LogRepairPreview(BaseModel):
    """Read-only candidate evidence for an operator to review."""

    path: str
    etag: str
    status: Literal["no_candidate", "review_required"] = "no_candidate"
    explanation: str
    new_etag: str | None = None
    removal_start: int = 0
    removal_end: int = 0
    removed_blocks: int = 0
    removal_text: str = ""


class LogRepairResult(BaseModel):
    """Completed splice and the location of its retained complete original."""

    path: str
    etag: str
    recovery_path: str
    original_etag: str
    removed_blocks: int


class LogRepairInterrupted(RuntimeError):
    """A repair stopped after attempting retention; inspection precedes retry."""

    def __init__(self, recovery_path: str, *, target_may_have_changed: bool) -> None:
        """Describe the retained evidence without claiming rollback.

        Args:
            recovery_path: Vault-relative recovery artifact, possibly partial or absent.
            target_may_have_changed: Whether replacement was attempted.
        """
        self.recovery_path = recovery_path
        self.target_may_have_changed = target_may_have_changed
        state = (
            "The target may already have changed."
            if target_may_have_changed
            else "This repair did not replace the target."
        )
        super().__init__(
            f"Log repair stopped; {state} Inspect the target and the recovery "
            f"artifact {recovery_path!r} before any retry; the artifact may be "
            "complete, partial, or absent. Existing artifacts are never reused."
        )


class LogRepairCollision(InvalidRequestError):
    """An existing recovery entry must be inspected before retrying."""


def _unit_length(data: bytes) -> int:
    """Recognize one historical YAML mapping plus exactly one blank line."""
    match = _UNIT.match(data)
    if match is None:
        return 0
    try:
        mapping = load_yaml(match["yaml"].decode("utf-8"))
    except (yaml.YAMLError, ValueError):
        # The core-schema loader raises YAMLError on bad YAML or an explicit
        # tag it rejects; ValueError stays caught for a standard tag such as
        # !!timestamp on an impossible date. Load the block directly here to
        # reject a non-mapping explicitly; frontmatter's Post is always a dict.
        return 0
    return match.end() if isinstance(mapping, dict) else 0


def preview_log_repair(path: str, original: bytes) -> LogRepairPreview:
    """Propose removing only an immediate run of identical historical units.

    Args:
        path: Exact vault-relative log.md path to bind to the proposal.
        original: Complete UTF-8 snapshot, without newline normalization.

    Returns:
        Candidate evidence to review, or an explanation that there is none.
    """
    etag = compute_etag(original)
    absent = LogRepairPreview(
        path=path,
        etag=etag,
        explanation="No supported adjacent duplicate run. Keep the file unchanged; "
        "have the operator review differing, malformed, or interleaved blocks manually.",
    )
    opening = 3 if original.startswith(b"\xef\xbb\xbf") else 0
    length = _unit_length(original[opening:])
    if not length:
        return absent
    unit = original[opening : opening + length]
    start = end = opening + length
    while original.startswith(unit, end):
        end += length
    # A different or interleaved block is outside this narrow transform.
    suffix = original[end:].lstrip(b" \t\r\n")
    if end == start or suffix.startswith((b"---\n", b"---\r\n")):
        return absent
    removal = original[start:end]
    new_etag = compute_etag(original[:start] + original[end:])
    return LogRepairPreview(
        path=path,
        etag=etag,
        status="review_required",
        explanation=_REVIEW,
        new_etag=new_etag,
        removal_start=start,
        removal_end=end,
        removed_blocks=(end - start) // length,
        removal_text=removal.decode("utf-8"),
    )


def require_reviewed(preview: LogRepairPreview, reviewed_etag: str) -> None:
    """Require that the operator reviewed this exact proposal.

    The proposal is a pure function of the path and the snapshot bytes, so the
    snapshot etag the operator reviewed binds the whole removal.

    Args:
        preview: Fresh proposal from the current bytes under the write lock.
        reviewed_etag: The etag the reviewed preview reported.

    Raises:
        ConcurrentModificationError: The log changed since that preview.
        InvalidRequestError: The current log holds no candidate to remove.
    """
    if reviewed_etag != preview.etag:
        raise ConcurrentModificationError(
            preview.path, expected=reviewed_etag, actual=preview.etag
        )
    if preview.status != "review_required":
        raise InvalidRequestError(
            f"Log {preview.path!r} holds no supported repeated frontmatter to "
            "remove; nothing was changed."
        )


def retain_original(recovery: Path, original: bytes, *, recovery_path: str) -> None:
    """Exclusively create and verify a complete original before replacement.

    Args:
        recovery: Fixed adjacent recovery path selected by DocumentManager.
        original: Complete original bytes to retain.
        recovery_path: Vault-relative artifact path to identify in a collision.

    Raises:
        LogRepairCollision: Every existing entry is a collision, including symlinks.
        OSError: The copy or verification failed; partial evidence remains in place.
    """
    try:
        stream = recovery.open("xb")
    except FileExistsError as exc:
        raise LogRepairCollision(
            f"The recovery artifact {recovery_path!r} already exists. Inspect it "
            "and preserve it elsewhere before retrying; a repair never reuses a "
            "recovery artifact."
        ) from exc
    with stream:
        recovery.chmod(0o600)
        stream.write(original)
    if recovery.read_bytes() != original:
        raise OSError("Recovery copy did not match the complete original")
