"""Git history/diff query manager.

Handles read-only git queries (commit history, diffs) with dependency
injection — receives a :class:`~markdown_vault_mcp.git.GitWriteStrategy`
(or ``None`` when the vault is not a git repository) and the ``source_dir``,
with no back-reference to :class:`Collection`. Sibling to
:class:`~markdown_vault_mcp.managers.link.LinkManager`. Extracted from
``Collection`` (#610) so the read facet stays thin.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from markdown_vault_mcp.utils import validate_path

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.types import CommitDiff, HistoryEntry


class GitQueryManager:
    """Read-only git history/diff queries, backed by a ``GitWriteStrategy``.

    Args:
        git_strategy: The git strategy to query, or ``None`` when the vault's
            source directory is not inside a git repository (queries then
            return empty results rather than raising).
        source_dir: Absolute path to the vault root directory.
    """

    def __init__(self, git_strategy: GitWriteStrategy | None, source_dir: Path) -> None:
        self._git_strategy = git_strategy
        self._source_dir = source_dir

    def get_history(
        self,
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
    ) -> list[HistoryEntry]:
        """Return commits that touched a note or the whole vault.

        When *path* is ``None``, queries the full vault history.  Returns an
        empty list for vaults whose source directory is not inside a git
        repository.

        Args:
            path: Vault-relative path of the note to filter on (e.g.
                ``"notes/alpha.md"``).  Must end with ``.md``.  ``None``
                returns vault-wide history.
            since: ISO 8601 datetime string or git date expression (e.g.
                ``"1 week ago"``).  Passed as ``--since`` to ``git log``.
                ``None`` disables the filter.
            until: ISO 8601 datetime string or git date expression, passed as
                ``--until`` to ``git log``.  ``None`` disables the filter.
                Both ``since`` and ``until`` boundaries are **inclusive**: a
                commit whose committer date equals either endpoint is included
                in the result.
            limit: Maximum number of commits to return.  Clamped to
                ``[1, 100]``.  Defaults to ``20``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.HistoryEntry` ordered
            newest-first.  Empty list when the vault has no git history or
            the note has no commits in the given range.  The
            ``paths_changed`` field on each entry is populated for vault-wide
            queries (``path=None``); it is always empty for single-note
            queries, since the path is already determined by the query
            arguments — callers know which file the commit touched without
            needing it echoed back.

        Raises:
            ValueError: If *path* is provided but fails path validation.
        """
        if self._git_strategy is None:
            return []
        abs_path: Path | None = None
        if path is not None:
            abs_path = validate_path(path, self._source_dir)
        return self._git_strategy.get_file_history(
            self._source_dir, abs_path, since, limit, until=until
        )

    def get_diff(
        self,
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
    ) -> str | list[CommitDiff]:
        """Return the diff of a note between a reference point and HEAD.

        Exactly one of *since_sha* or *since_timestamp* must be supplied.

        Args:
            path: Vault-relative path of the note to diff.  Must end with
                ``.md``.
            since_sha: A commit SHA (full or abbreviated, at least 4 hex
                digits) to diff from.  Mutually exclusive with
                *since_timestamp*.
            since_timestamp: ISO 8601 datetime string, resolved via
                ``git rev-list --before=<ts> -1 HEAD`` to the most recent
                commit at or before that instant.  Boundary is
                **inclusive**: a commit whose committer date equals
                *since_timestamp* IS the resolved ref.  Mutually exclusive
                with *since_sha*.
            per_commit: When ``False`` (default), return a single unified diff
                string from the reference point to HEAD.  When ``True``,
                return one :class:`~markdown_vault_mcp.types.CommitDiff` per
                intervening commit.
            limit: When *per_commit* is ``True``, cap the number of
                intervening commits returned to the *limit* most recent ones.
                Clamped to ``[1, 100]``.  ``None`` (the default) means
                unbounded (still bounded by the underlying ``since..HEAD``
                range).  Silently ignored when *per_commit* is ``False``.

        Returns:
            A unified diff string when *per_commit* is ``False``, or a list of
            :class:`~markdown_vault_mcp.types.CommitDiff` when *per_commit* is
            ``True``.  Returns an empty string / empty list when the note has
            no changes in the given range.

        Raises:
            ValueError: If exactly one of *since_sha* / *since_timestamp* is
                not supplied, *since_sha* contains invalid characters, or the
                resolved ref is not found in history.
        """
        if self._git_strategy is None:
            return [] if per_commit else ""

        if (since_sha is None) == (since_timestamp is None):
            raise ValueError(
                "Exactly one of 'since_sha' or 'since_timestamp' must be provided"
            )

        abs_path = validate_path(path, self._source_dir)

        if since_sha is not None and not re.fullmatch(r"[0-9a-f]{4,40}", since_sha):
            raise ValueError(
                f"Invalid SHA {since_sha!r}: must be 4-40 lowercase hex digits"
            )

        return self._git_strategy.get_file_diff(
            self._source_dir,
            abs_path,
            ref=since_sha,
            per_commit=per_commit,
            since_timestamp=since_timestamp,
            limit=limit if per_commit else None,
        )
