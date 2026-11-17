"""Internal state for explicitly grouped write callbacks."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from markdown_vault_mcp._commit_scope import CommitScope, new_commit_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from markdown_vault_mcp._identity import Principal
    from markdown_vault_mcp.types import WriteBatchItem

COMMIT_GROUP_ATTR = "accepts_group_commit"


@dataclass(frozen=True, slots=True)
class GroupCommitResult:
    """Result of flushing a group; files counts paths in its commits."""

    sha: str | None
    files: int
    status: Literal["committed", "no_changes", "failed"]


@dataclass(frozen=True, slots=True)
class GroupCommitRequest:
    """Explicit subject and result sink for an opted-in callback."""

    subject: str
    report: Callable[[GroupCommitResult], None]


class CommitGroupError(ValueError):
    """A group handle or operation is invalid."""


@dataclass(slots=True)
class CommitGroup:
    """A persistent scope and its accumulated flush results."""

    handle: str
    description: str
    owner: Principal | None
    scope: CommitScope
    result: GroupCommitResult = field(
        default_factory=lambda: GroupCommitResult(None, 0, "no_changes")
    )
    active: int = 0
    closed: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    def record(self, result: GroupCommitResult) -> None:
        """Accumulate a flush result, preserving any failure."""
        with self.lock:
            previous = self.result
            status = result.status
            if previous.status == "failed" or status == "failed":
                status = "failed"
            elif previous.status == "committed" and status == "no_changes":
                status = "committed"
            self.result = GroupCommitResult(
                result.sha or previous.sha, previous.files + result.files, status
            )


def make_group(description: str, owner: Principal | None) -> CommitGroup:
    """Create a group with a server-minted handle and unique scope."""
    if (
        not description.strip()
        or description != description.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in description)
    ):
        raise CommitGroupError("Description must be a nonempty single line")
    return CommitGroup(
        secrets.token_urlsafe(32), description, owner, new_commit_scope(description)
    )


class GroupAwareCallback(Protocol):
    """Batch callback that accepts an explicit group request."""

    def on_write_batch(
        self,
        items: Sequence[WriteBatchItem],
        tool_name: str,
        *,
        primary_paths: frozenset[Path],
        group: GroupCommitRequest,
    ) -> None:
        """Commit the batch and report its result."""
        ...
