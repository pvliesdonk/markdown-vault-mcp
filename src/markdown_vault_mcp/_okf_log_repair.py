"""Exact-byte proposals for the legacy stacked log frontmatter defect (#1403).

Matching bytes identify a candidate, never its origin. Only an explicit
caller assertion authorizes the reviewed removal; no ordinary write uses this.
"""

from __future__ import annotations

import base64
import json
import re
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.hashing import compute_etag

if TYPE_CHECKING:
    from pathlib import Path

_UNIT = re.compile(rb"---(?P<nl>\r?\n)(?P<yaml>.*?)(?P=nl)---(?P=nl)(?P=nl)", re.DOTALL)
_REVIEW = (
    "Identical adjacent blocks are a candidate, not proof of generated origin. "
    "Have the operator review the exact removal and retained opening block. "
    "Only after authorization, pass approval to okf_apply_log_repair with "
    "operator_reviewed=true; leave authored or uncertain content unchanged."
)


class LogRepairApproval(BaseModel):
    """Caller assertion bound to one snapshot and its exact proposed removal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    path: str
    etag: str
    new_etag: str
    removal_start: int
    removal_end: int
    removal_base64: str
    binding: str
    operator_reviewed: bool = False


class LogRepairPreview(BaseModel):
    """Read-only candidate evidence, including an unapproved apply request."""

    path: str
    etag: str
    status: Literal["no_candidate", "review_required"] = "no_candidate"
    explanation: str
    new_etag: str | None = None
    removal_start: int = 0
    removal_end: int = 0
    removed_blocks: int = 0
    removal_text: str = ""
    removal_base64: str = ""
    approval: LogRepairApproval | None = None


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
            f"Log repair stopped; {state} Tell the operator to inspect the target "
            f"and recovery artifact {recovery_path!r} before any retry; the artifact "
            "may be complete, partial, or absent. Existing artifacts are never reused."
        )


class LogRepairCollision(InvalidRequestError):
    """An existing recovery entry must be inspected before retrying."""


def _unit_length(data: bytes) -> int:
    """Recognize one historical YAML mapping plus exactly one blank line."""
    match = _UNIT.match(data)
    if match is None:
        return 0
    try:
        mapping = yaml.safe_load(match["yaml"].decode("utf-8"))
    except yaml.YAMLError:
        return 0
    return match.end() if isinstance(mapping, dict) else 0


def preview_log_repair(path: str, original: bytes) -> LogRepairPreview:
    """Propose removing only an immediate run of identical historical units.

    Args:
        path: Exact vault-relative log.md path to bind to the proposal.
        original: Complete UTF-8 snapshot, without newline normalization.

    Returns:
        Candidate evidence with an unapproved assertion, or a review explanation.
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
    encoded = base64.b64encode(removal).decode("ascii")
    new_etag = compute_etag(original[:start] + original[end:])
    binding = compute_etag(
        json.dumps(
            [path, etag, new_etag, start, end, encoded],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
    )
    approval = LogRepairApproval(
        path=path,
        etag=etag,
        new_etag=new_etag,
        removal_start=start,
        removal_end=end,
        removal_base64=encoded,
        binding=binding,
    )
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
        removal_base64=encoded,
        approval=approval,
    )


def validate_approval(preview: LogRepairPreview, approval: LogRepairApproval) -> None:
    """Require an explicit review assertion matching this exact proposal.

    Args:
        preview: Fresh proposal from the current bytes under the write lock.
        approval: Operator-reviewed assertion supplied by the caller.

    Raises:
        InvalidRequestError: If approval is absent, stale, or for another removal.
    """
    if (
        not isinstance(approval, LogRepairApproval)
        or approval.operator_reviewed is not True
    ):
        raise InvalidRequestError(
            "approval.operator_reviewed must be true only after the operator "
            "reviews and authorizes the exact removal from okf_preview_log_repair."
        )
    expected = preview.approval
    if expected is None or approval.model_dump(
        exclude={"operator_reviewed"}
    ) != expected.model_dump(exclude={"operator_reviewed"}):
        raise InvalidRequestError(
            f"approval does not match the current removal for path {preview.path!r}. "
            "Call okf_preview_log_repair with path again and obtain fresh operator "
            "authorization before calling okf_apply_log_repair; do not replay approval."
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
            f"The recovery artifact {recovery_path!r} already exists. "
            "Ask the operator to inspect and preserve it before retrying "
            "okf_apply_log_repair; this call never reuses a recovery artifact."
        ) from exc
    with stream:
        recovery.chmod(0o600)
        stream.write(original)
    if recovery.read_bytes() != original:
        raise OSError("Recovery copy did not match the complete original")
