"""Git history, diff, and revision-read query manager.

Handles read-only git queries (commit history, diffs, a note's content at a
revision) with dependency injection — receives a
:class:`~markdown_vault_mcp.git.HistorySource`
(or ``None`` when the vault is not a git repository) and the ``source_dir``,
with no back-reference to :class:`Vault`. Sibling to
:class:`~markdown_vault_mcp.managers.link.LinkManager`. Extracted from
``Vault`` (#610) so the read facet stays thin.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import TYPE_CHECKING

import yaml

from markdown_vault_mcp.git import RevisionQuery, RevisionReader
from markdown_vault_mcp.scanner import extract_section, list_section_headings
from markdown_vault_mcp.utils import (
    effective_attachment_extensions,
    is_note,
    validate_history_dir,
    validate_history_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from markdown_vault_mcp.git import HistorySource
    from markdown_vault_mcp.types import CommitDiff, HistoryEntry, RevisionContent

logger = logging.getLogger(__name__)


#: Accepted shape of a caller-supplied commit SHA: a full or abbreviated object
#: ID, in either hash algorithm git supports.  The upper bound is 64 rather than
#: 40 because a repository created with ``git init --object-format=sha256``
#: yields 64-hex commit IDs, which is what ``get_history`` hands the caller
#: there (#1284).  The check exists to keep caller input out of the git argv,
#: not to prove the object exists — git reports an unknown ref itself.
_SHA_RE = r"[0-9a-f]{4,64}"


class GitQueryManager:
    """Read-only git history, diff, and revision queries.

    Holds a ``HistorySource`` for history and diff, and gates the revision
    surface on a separate ``isinstance`` check against ``RevisionReader``
    (#1229, #1137) — so it stays coupled to neither the commit nor the sync
    surface, and a backend offering only history is still a usable one.
    ``GitWriteStrategy`` is the implementation the git-backed vault supplies.

    Args:
        git_strategy: The history source to query, or ``None`` when the vault's
            source directory is not inside a git repository.  History and diff
            queries then return empty results; :meth:`read_at_revision` raises
            instead, for the reason given there.
        source_dir: Absolute path to the vault root directory.
        attachment_extensions: Allowed attachment file extensions, in any
            case and with or without leading dots (e.g. ``["png", "pdf"]``).
            ``None`` uses the default set from
            :data:`~markdown_vault_mcp.types.DEFAULT_ATTACHMENT_EXTENSIONS`.
            Passed to :func:`~markdown_vault_mcp.utils.validate_history_path`
            so that history/diff queries accept attachments as well as notes.
        max_note_read_bytes: Read cap applied to a note fetched at a revision,
            matching the cap :class:`~markdown_vault_mcp.managers.document.DocumentManager`
            applies on disk.  ``0`` disables it.
    """

    def __init__(
        self,
        git_strategy: HistorySource | None,
        source_dir: Path,
        attachment_extensions: Sequence[str] | None = None,
        max_note_read_bytes: int = 0,
    ) -> None:
        self._git_strategy = git_strategy
        self._source_dir = source_dir
        self._attachment_extensions = effective_attachment_extensions(
            attachment_extensions
        )
        self._max_note_read_bytes = max_note_read_bytes

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

        if since_sha is not None and not re.fullmatch(_SHA_RE, since_sha):
            raise ValueError(
                f"Invalid SHA {since_sha!r}: must be 4-64 lowercase hex digits"
            )

        return self._git_strategy.get_file_diff(
            self._source_dir,
            abs_path,
            ref=since_sha,
            per_commit=per_commit,
            since_timestamp=since_timestamp,
            limit=limit if per_commit else None,
            # True for any non-.md path; get_file_diff only emits --stat if git also reports it binary — text attachments fall through to a full diff.
            summarize_binary=not is_note(path),
        )

    def _revision_reader(self) -> RevisionReader:
        """Return the store's revision-read facet, or explain its absence.

        Unlike :meth:`get_history` and :meth:`get_diff`, which degrade to an
        empty result without git, this raises: an empty string is
        indistinguishable from "the note was empty at that revision", and a
        caller about to restore content must not have to guess which it got.

        Raises:
            ValueError: When the vault has no git backing, or its store cannot
                serve revision reads.
        """
        if not isinstance(self._git_strategy, RevisionReader):
            raise ValueError(
                "Reading a note at a revision requires a git-backed vault; this "
                "vault's source directory is not inside a git repository."
            )
        return self._git_strategy

    def read_at_revision(
        self, path: str, revision: str, *, section: str | None = None
    ) -> RevisionContent:
        """Return a note's content as it stood at *revision*.

        Resolution is by note, not by path: pass the path the note has today
        and git's own add/rename records are walked back.  Where they do not
        connect the note to that revision — a name since reused by a different
        note, a delete-and-recreate, a rename git cannot detect — this raises
        rather than returning content belonging to another note.

        Args:
            path: Vault-relative path of the note as it is named today.
                A note that no longer exists on disk is still readable, which
                is how a deleted note is recovered.
            revision: Commit SHA, as returned by :meth:`get_history` or by an
                overwriting write's ``previous_revision``.
            section: When provided, return only that section of the historical
                content, matched exactly as
                :meth:`~markdown_vault_mcp.managers.document.DocumentManager.read`
                matches it on disk.

        Returns:
            A :class:`~markdown_vault_mcp.types.RevisionContent`.

        Raises:
            ValueError: If the vault is not git-backed, *revision* is not a
                SHA or is not an ancestor of HEAD, *path* is not a ``.md``
                note or escapes the vault, the note's identity cannot be
                traced to that revision, or its content there is unreadable.
        """
        reader = self._revision_reader()
        if not is_note(path):
            raise ValueError(
                f"Revision reads are for markdown notes; {path!r} is an "
                "attachment, whose content at a revision is binary. Use "
                "'get_history' and 'get_diff' to inspect its history."
            )
        if not re.fullmatch(_SHA_RE, revision):
            raise ValueError(
                f"Invalid revision {revision!r}: must be 4-64 lowercase hex "
                "digits. Pass a SHA from 'get_history' or from a write "
                "result's 'previous_revision'."
            )
        abs_path = validate_history_path(
            path, self._source_dir, self._attachment_extensions
        )
        content = reader.get_file_at_ref(
            RevisionQuery(
                repo_path=self._source_dir,
                path=abs_path,
                ref=revision,
                max_bytes=self._max_note_read_bytes,
            )
        )
        if section is None:
            return content
        content.content = _section_of(content, section)
        return content

    def committed_revision(self, path: str) -> str | None:
        """Return the revision holding what is on disk for *path* right now.

        The breadcrumb an overwriting write hands back, read *before* the
        write lands.  Returns ``None`` rather than raising for every reason it
        might not be answerable — no git, no commit for the note yet, a
        working copy that has moved on, or git itself failing — because a
        write must never fail over a breadcrumb it could not compute.

        Args:
            path: Vault-relative path of the note.

        Returns:
            The commit SHA, or ``None`` when no revision provably holds the
            content currently on disk.
        """
        if not isinstance(self._git_strategy, RevisionReader):
            return None
        try:
            abs_path = validate_history_path(
                path, self._source_dir, self._attachment_extensions
            )
            # A note not on disk is being created, not overwritten: nothing is
            # replaced, and probing would walk the whole history to learn that
            # a never-seen path has no commit.
            if not abs_path.is_file():
                return None
            return self._git_strategy.committed_revision(self._source_dir, abs_path)
        except (ValueError, OSError, subprocess.SubprocessError):
            logger.debug("previous_revision_unavailable path=%s", path, exc_info=True)
            return None


def _section_of(content: RevisionContent, section: str) -> str:
    """Return one section of a historical note, or explain why it is not there.

    Frontmatter is parsed to find the body, so a revision whose frontmatter
    was malformed surfaces as a caller-visible error rather than a YAML
    exception escaping the read.

    Raises:
        ValueError: If *section* is empty, the historical frontmatter cannot be
            parsed, or no heading matches.
    """
    if not section.strip():
        raise ValueError("section must be a non-empty heading")
    try:
        body = extract_section(content.content, section.strip())
        headings = list_section_headings(content.content)
    except yaml.YAMLError as exc:
        raise ValueError(
            f"{content.historical_path!r} had malformed frontmatter at revision "
            f"{content.revision!r}, so its sections cannot be resolved. Read the "
            "whole note at that revision instead."
        ) from exc
    if body is None:
        suggestions = ", ".join(repr(h) for h in headings[:10]) or "none"
        raise ValueError(
            f"Section {section!r} not found at revision {content.revision!r}. "
            f"Headings there: {suggestions}"
        )
    return body
