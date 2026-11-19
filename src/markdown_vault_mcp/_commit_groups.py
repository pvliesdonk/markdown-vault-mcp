"""Internal state for explicitly grouped write callbacks."""

from __future__ import annotations

import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from markdown_vault_mcp._commit_scope import CommitScope, new_commit_scope

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp._identity import Principal

COMMIT_GROUP_ATTR = "accepts_group_commit"


@dataclass(frozen=True, slots=True)
class GroupCommitResult:
    """Accumulated group result.

    ``committed`` means a commit was made; ``no_changes`` means none was
    needed; ``failed`` means staging, commit, or reporting failed. Failure is
    sticky across flushes. An unknown path count is reported as zero.
    """

    sha: str | None
    files: int
    status: Literal["committed", "no_changes", "failed"]


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
    principal: Principal | None = None
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    active: int = 0
    closed: bool = False
    closing: bool = False
    paths: set[Path] = field(default_factory=set)

    def record(self, result: GroupCommitResult) -> None:
        """Accumulate a flush result, preserving any failure."""
        previous = self.result
        status = max(
            previous.status,
            result.status,
            key=("no_changes", "committed", "failed").index,
        )
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
    if (
        not description.strip()
        or description != description.strip()
        or any(
            unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"}
            for char in description
        )
        # After the Cs test, so a lone surrogate never reaches the encoder.
        or len(description.encode("utf-8")) > 4096
    ):
        raise CommitGroupError(
            "Description must be a single line of at most 4096 UTF-8 bytes without leading or trailing whitespace or controls"
        )
    return CommitGroup(
        secrets.token_urlsafe(32),
        description,
        owner_key(owner),
        new_commit_scope(description),
        principal=owner,
    )
