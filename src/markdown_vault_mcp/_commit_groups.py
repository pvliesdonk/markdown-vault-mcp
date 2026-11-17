"""Internal state for explicitly grouped write callbacks."""

from __future__ import annotations

import secrets
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from markdown_vault_mcp._commit_scope import CommitScope, new_commit_scope

if TYPE_CHECKING:
    from collections.abc import Callable

    from markdown_vault_mcp._identity import Principal

COMMIT_GROUP_ATTR = "accepts_group_commit"


@dataclass(frozen=True, slots=True)
class GroupCommitResult:
    """Result of flushing a group; files counts paths in its commits."""

    sha: str | None
    files: int
    status: Literal["committed", "no_changes", "failed"]


@dataclass(slots=True)
class GroupCommitRequest:
    """Explicit subject and result sink for an opted-in callback."""

    subject: str
    report: Callable[[GroupCommitResult], None]
    stage_failed: bool = False


class CommitGroupError(ValueError):
    """A group handle or operation is invalid."""


@dataclass(slots=True)
class CommitGroup:
    """A persistent scope and its accumulated flush results."""

    handle: str
    description: str
    owner: tuple[str, str | None]
    scope: CommitScope
    result: GroupCommitResult = field(
        default_factory=lambda: GroupCommitResult(None, 0, "no_changes")
    )
    active: int = 0
    closed: bool = False

    def record(self, result: GroupCommitResult) -> None:
        """Accumulate a flush result, preserving any failure."""
        previous = self.result
        status = result.status
        if previous.status == "failed" or status == "failed":
            status = "failed"
        elif previous.status == "committed" and status == "no_changes":
            status = "committed"
        self.result = GroupCommitResult(
            result.sha or previous.sha, previous.files + result.files, status
        )


def owner_key(principal: Principal | None) -> tuple[str, str | None]:
    """Identify a caller by stable kind and subject; absent means local."""
    if principal is None or principal.kind == "local":
        return ("local", None)
    return (principal.kind, principal.subject)


def make_group(description: str, owner: Principal | None) -> CommitGroup:
    """Create a group with a server-minted handle and unique scope.

    The subject is a single line of at most 4096 UTF-8 bytes, leaving room in
    Git's command-line argument limit for the remainder of the invocation.
    """
    try:
        size = len(description.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise CommitGroupError("Description must be valid UTF-8") from exc
    if (
        not description.strip()
        or description != description.strip()
        or size > 4096
        or any(
            unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for char in description
        )
    ):
        raise CommitGroupError(
            "Description must be a single line of at most 4096 UTF-8 bytes without leading or trailing whitespace or controls"
        )
    return CommitGroup(
        secrets.token_urlsafe(32),
        description,
        owner_key(owner),
        new_commit_scope(description),
    )
