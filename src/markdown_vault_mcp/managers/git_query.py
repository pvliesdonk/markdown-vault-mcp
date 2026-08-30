"""Git history/diff query manager.

Handles read-only git queries (commit history, diffs) with dependency
injection — receives a :class:`~markdown_vault_mcp.git.HistorySource`
(or ``None`` when the vault is not a git repository) and the ``source_dir``,
with no back-reference to :class:`Vault`. Sibling to
:class:`~markdown_vault_mcp.managers.link.LinkManager`. Extracted from
``Vault`` (#610) so the read facet stays thin.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from markdown_vault_mcp.utils import (
    effective_attachment_extensions,
    validate_history_dir,
    validate_history_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from markdown_vault_mcp.git import HistorySource
    from markdown_vault_mcp.types import CommitDiff, HistoryEntry


class GitQueryManager:
    """Read-only git history/diff queries, backed by a ``HistorySource``.

    Depends on the narrowest of the three versioning facets (#1229): this
    manager calls ``get_file_history`` and ``get_file_diff`` and nothing else,
    so it is coupled to neither the commit nor the sync surface.
    ``GitWriteStrategy`` is the implementation the git-backed vault supplies.

    Args:
        git_strategy: The history source to query, or ``None`` when the vault's
            source directory is not inside a git repository (queries then
            return empty results rather than raising).
        source_dir: Absolute path to the vault root directory.
        attachment_extensions: Allowed attachment file extensions (lowercase,
            without leading dot, e.g. ``["png", "pdf"]``).  ``None`` uses the
            default set from :data:`~markdown_vault_mcp.types.DEFAULT_ATTACHMENT_EXTENSIONS`.
            Passed to :func:`~markdown_vault_mcp.utils.validate_history_path`
            so that history/diff queries accept attachments as well as notes.
    """

    def __init__(
        self,
        git_strategy: HistorySource | None,
        source_dir: Path,
        attachment_extensions: Sequence[str] | None = None,
    ) -> None:
        self._git_strategy = git_strategy
        self._source_dir = source_dir
        self._attachment_extensions = effective_attachment_extensions(
            attachment_extensions
        )

    def get_history(
        self,
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
    ) -> list[HistoryEntry]:
        """Return commits that touched a note, attachment, folder, or the whole vault.

        When *path* is ``None``, queries the full vault history.  Returns an
        empty list for vaults whose source directory is not inside a git
        repository.

        A *path* that resolves to an existing directory scopes history to that
        subtree (``git log -- <dir>``); a ``.md`` note or a configured
        attachment scopes to that single file (rename-following); ``None``
        returns vault-wide history.

        Args:
            path: Vault-relative path to filter on. An existing directory
                (e.g. ``"guides"``) scopes to its subtree; a ``.md`` note or a
                configured attachment (e.g. ``"notes/alpha.md"``,
                ``"assets/x.png"``) scopes to that file. ``None`` returns
                vault-wide history.
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
            queries (``path=None``) and directory queries (the subtree files
            the commit touched); it is always empty for single-note queries,
            since the path is already determined by the query arguments —
            callers know which file the commit touched without needing it
            echoed back.

        Raises:
            ValueError: If *path* is provided but fails path validation
                (a file with an unknown extension, or path traversal).
        """
        if self._git_strategy is None:
            return []
        abs_path: Path | None = None
        is_dir = False
        if path is not None:
            # A path pointing at a real directory scopes to that subtree; only
            # non-directories are held to the note/attachment extension rule.
            if (self._source_dir / path).is_dir():
                abs_path = validate_history_dir(path, self._source_dir)
                is_dir = True
            else:
                abs_path = validate_history_path(
                    path, self._source_dir, self._attachment_extensions
                )
        return self._git_strategy.get_file_history(
            self._source_dir, abs_path, since, limit, until=until, is_dir=is_dir
        )

    def get_diff(
        self,
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
    ) -> str | list[CommitDiff]:
        """Return the diff of a note or attachment between a reference point and HEAD.

        Exactly one of *since_sha* or *since_timestamp* must be supplied.

        Args:
            path: Vault-relative path of the note or attachment to diff.
                A ``.md`` note or a configured attachment (e.g.
                ``assets/x.png``).  Unknown extensions raise ``ValueError``.
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
            ``True``.  For an attachment that git reports as binary, a
            ``--stat`` summary is returned (per-commit: ``--stat`` lines per
            commit) instead of a unified patch; a text attachment returns a
            full unified diff like a ``.md`` note.
            Returns an empty string / empty list when the file has no changes
            in the given range, or when the vault's source directory is not
            inside a git repository.  Per-commit (``per_commit=True``)
            attachment diffs are rename-aware (a copied file renders as an add).

        Raises:
            ValueError: If exactly one of *since_sha* / *since_timestamp* is
                not supplied, *since_sha* contains invalid characters, the
                resolved ref is not found in history, or *path* has an
                extension that is neither ``.md`` nor a configured attachment
                type.
        """
        if self._git_strategy is None:
            return [] if per_commit else ""

        if (since_sha is None) == (since_timestamp is None):
            raise ValueError(
                "Exactly one of 'since_sha' or 'since_timestamp' must be provided"
            )

        abs_path = validate_history_path(
            path, self._source_dir, self._attachment_extensions
        )

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
            # True for any non-.md path; get_file_diff only emits --stat if git also reports it binary — text attachments fall through to a full diff.
            summarize_binary=not path.endswith(".md"),
        )
