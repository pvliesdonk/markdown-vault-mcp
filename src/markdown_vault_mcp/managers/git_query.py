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
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.git import RevisionQuery, RevisionReader
from markdown_vault_mcp.scanner import extract_section, list_section_headings
from markdown_vault_mcp.utils import (
    effective_attachment_extensions,
    is_note,
    resolve_inside,
    validate_history_dir,
    validate_history_path,
)
from markdown_vault_mcp.utils.fs import (
    is_directory,
    is_regular_file,
    open_directory_nofollow,
    path_exists,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

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


class GitSnapshotUnavailableError(ValueError):
    """The selected Git snapshot cannot be exported as vault content."""


@dataclass(frozen=True)
class GitExportSnapshot:
    """Immutable Git tree selected for a vault export."""

    commit: str
    entries: dict[str, tuple[str, str]]
    changed: set[str]
    root: Path
    sizes: dict[str, int]
    flagged: dict[str, bool] = field(default_factory=dict)
    children: dict[str, tuple[Path, str]] = field(default_factory=dict)
    repos: dict[str, Path] = field(default_factory=dict)
    _cache: dict[str, bytes] = field(default_factory=dict, repr=False, compare=False)
    _process: dict[Path, subprocess.Popen[bytes]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def read(self, path: str) -> bytes:
        """Read a selected tree blob without interpreting its path as a revision."""
        oid = self.entries[path][1]
        raw = self._cache.get(oid)
        if raw is None:
            root = self.repos.get(path, self.root)
            try:
                raw = self._read_blob(oid, root)
            except (OSError, ValueError) as exc:
                if root != self.root:
                    raise GitSnapshotUnavailableError(
                        "Git submodule object is unavailable; run git submodule update --init and retry."
                    ) from exc
                raise
            if len(raw) <= 1024 * 1024:
                self._cache[oid] = raw
        return raw

    def _read_blob(self, oid: str, root: Path) -> bytes:
        if root not in self._process:
            # Only Git-generated object IDs are sent over the batch protocol.
            self._process[root] = subprocess.Popen(  # noqa: S603, RUF100
                ["git", "-C", str(root), "cat-file", "--batch"],  # noqa: S607, RUF100
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
            )
        process = self._process[root]
        if process.stdin is None or process.stdout is None:
            raise OSError("Git batch stream unavailable")
        process.stdin.write(oid.encode("ascii") + b"\n")
        process.stdin.flush()
        header = process.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError("Git snapshot blob unavailable")
        size = int(header[2])
        raw = process.stdout.read(size)
        if len(raw) != size or process.stdout.read(1) != b"\n":
            raise OSError("Git snapshot blob stream ended early")
        return raw

    def close(self) -> None:
        """Close the batch reader, if started."""
        while self._process:
            _, process = self._process.popitem()
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            process.wait()


def _export_git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    # The executable is the configured Git CLI and arguments are never interpreted by a shell.
    return subprocess.run(  # noqa: S603, RUF100
        ["git", "-C", str(root), *args],  # noqa: S607, RUF100
        capture_output=True,
        check=check,
        env={
            **os.environ,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "LC_ALL": "C",
        },
    )


# Discovery distinguishes absent Git, unsafe worktrees, and unavailable commits.
def git_export_snapshot(source: Path) -> GitExportSnapshot | None:  # noqa: C901, RUF100
    """Pin a vault's Git commit and enumerate its blobs.

    Args:
        source: Vault root, which may be nested within a repository.

    Returns:
        Commit and vault-relative tree entries, or None outside a Git worktree.

    Raises:
        ValueError: If a repository exists but its snapshot cannot be read.
    """
    try:
        probe = _export_git(source, "rev-parse", "--show-toplevel", check=False)
    except FileNotFoundError as exc:
        if any(path_exists(parent / ".git") for parent in (source, *source.parents)):
            raise ValueError("Git is required to export a Git snapshot") from exc
        return None
    if probe.returncode:
        if b"not a git repository" in probe.stderr:
            return None
        reason = probe.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Cannot locate Git worktree for export: {reason}")
    root = Path(os.fsdecode(probe.stdout.removesuffix(b"\n")))
    prefix = source.resolve().relative_to(root.resolve()).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    head = _export_git(
        root, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False
    )
    if head.returncode == 1:
        raise GitSnapshotUnavailableError(
            "Git snapshot has no readable commit; if the repository has no commits, create one with git commit."
        )
    if head.returncode:
        reason = head.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Cannot resolve Git snapshot HEAD for export: {reason}")
    try:
        commit = head.stdout.decode("ascii").strip()
        tree = _export_git(
            root, "ls-tree", "--full-tree", "-r", "-l", "-z", commit
        ).stdout
        entries: dict[str, tuple[str, str]] = {}
        sizes: dict[str, int] = {}
        for record in tree.split(b"\0"):
            if not record:
                continue
            info, name = record.split(b"\t", 1)
            mode, kind, oid, size = info.split()
            path = os.fsdecode(name)
            if path.startswith(prefix) and kind in {b"blob", b"commit"}:
                entries[path[len(prefix) :]] = (
                    mode.decode("ascii"),
                    oid.decode("ascii"),
                )
                sizes[path[len(prefix) :]] = int(size) if size != b"-" else 0
        snapshot = GitExportSnapshot(commit, entries, set(), root, sizes)
        _expand_gitlinks(source, snapshot)
        return snapshot
    except (subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise ValueError("Cannot read Git snapshot for export") from exc


def _expand_gitlinks(source: Path, snapshot: GitExportSnapshot) -> None:
    pending = [path for path, (mode, _) in snapshot.entries.items() if mode == "160000"]
    while pending:
        path = pending.pop()
        oid = snapshot.entries[path][1]
        parts = tuple(path.split("/"))
        if len(parts) > 32:
            continue
        child = source.joinpath(*parts)
        try:
            with open_directory_nofollow(source, parts, source_resolved=True):
                pass
            root = _export_git(child, "rev-parse", "--show-toplevel").stdout.rstrip(
                b"\n"
            )
            if Path(os.fsdecode(root)).resolve() != child.resolve():
                continue
            _export_git(child, "cat-file", "-e", f"{oid}^{{commit}}")
            tree = _export_git(
                child, "ls-tree", "--full-tree", "-r", "-l", "-z", oid
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        del snapshot.entries[path]
        snapshot.sizes.pop(path)
        snapshot.children[path] = (child, oid)
        for record in tree.split(b"\0"):
            if not record:
                continue
            info, name = record.split(b"\t", 1)
            mode, kind, blob, size = info.split()
            if kind not in {b"blob", b"commit"}:
                continue
            entry_path = path + "/" + os.fsdecode(name)
            snapshot.entries[entry_path] = (mode.decode("ascii"), blob.decode("ascii"))
            snapshot.sizes[entry_path] = int(size) if size != b"-" else 0
            snapshot.repos[entry_path] = child
            if mode == b"160000":
                pending.append(entry_path)


def git_snapshot_changes(
    source: Path, commit: str
) -> tuple[set[str], dict[str, bool], set[str]]:
    """Observe changed and index-flagged names against a pinned commit."""
    try:
        options = ("--no-ext-diff", "--no-renames", "--relative", "--name-only", "-z")
        diff = _export_git(source, "diff", *options, commit, "--", ".").stdout
        staged = _export_git(
            source, "diff", "--cached", *options, commit, "--", "."
        ).stdout
        untracked = _export_git(source, "ls-files", "--others", "-z", "--", ".").stdout
        flagged_raw = _export_git(source, "ls-files", "-v", "-z", "--", ".").stdout
        changed = {
            os.fsdecode(name)
            for name in (diff + staged + untracked).split(b"\0")
            if name
        }
        flagged = {
            os.fsdecode(row[2:]): row[:1] in {b"S", b"s"}
            for row in flagged_raw.split(b"\0")
            if row[:1] in {b"h", b"S", b"s"}
        }
        staged_names = {os.fsdecode(name) for name in staged.split(b"\0") if name}
        return changed, flagged, staged_names
    except subprocess.SubprocessError as exc:
        raise ValueError("Cannot read Git snapshot for export") from exc


def _reject_nul_date(name: str, value: str | None) -> None:
    """Refuse a date argument holding a NUL byte before it reaches git's argv.

    A NUL in a subprocess argument raises a plain ``ValueError``, which the
    tool layer would report as a server fault (#1636).

    Raises:
        InvalidRequestError: If *value* contains ``"\\x00"``.
    """
    if value is not None and "\x00" in value:
        raise InvalidRequestError(
            f"{name} {value!r} contains a NUL byte; pass the date without it."
        )


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
        attachment scopes to that single file, following its renames back and
        stopping where it was created — a name that was freed and reused does
        not carry its previous occupant's commits (#1285); ``None`` returns
        vault-wide history.

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
            InvalidRequestError: If *path* is provided but fails path
                validation (a file with an unknown extension, path traversal,
                a NUL byte), or *since* / *until* holds a NUL byte.
        """
        if self._git_strategy is None:
            return []
        _reject_nul_date("since", since)
        _reject_nul_date("until", until)
        abs_path: Path | None = None
        is_dir = False
        if path is not None:
            # A path pointing at a real directory scopes to that subtree; only
            # non-directories are held to the note/attachment extension rule.
            # Validate (NUL, containment) before the stat, which would
            # otherwise fail on such a path as a fault (#1636, #1625).
            if is_directory(resolve_inside(path, self._source_dir)):
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
            The diff describes the note the path names today: where that name
            was held by a different note earlier, a reference older than this
            note's creation renders as a creation rather than pairing the two
            notes' content (#1285).

        Raises:
            InvalidRequestError: If not exactly one of *since_sha* /
                *since_timestamp* is supplied, *since_sha* is not 4-64
                lowercase hex digits or names no commit, or *path* has an
                extension that is neither ``.md`` nor a configured attachment
                type, or *since_timestamp* holds a NUL byte.
            ValueError: If a git subprocess fails for any other reason.
        """
        if self._git_strategy is None:
            return [] if per_commit else ""

        if (since_sha is None) == (since_timestamp is None):
            raise InvalidRequestError(
                "Exactly one of 'since_sha' or 'since_timestamp' must be provided"
            )
        _reject_nul_date("since_timestamp", since_timestamp)

        abs_path = validate_history_path(
            path, self._source_dir, self._attachment_extensions
        )

        if since_sha is not None and not re.fullmatch(_SHA_RE, since_sha):
            raise InvalidRequestError(
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
            InvalidRequestError: When the vault has no git backing, or its
                store cannot serve revision reads: an operator's choice, not a
                fault (#1608).
        """
        if not isinstance(self._git_strategy, RevisionReader):
            raise InvalidRequestError(
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
            InvalidRequestError: If the vault is not git-backed, *revision* is
                not a SHA or is not an ancestor of HEAD, *path* is not a
                ``.md`` note or escapes the vault, the note's identity cannot
                be traced to that revision, the content is over the read cap,
                or *section* is empty or not found there.
            DocumentUnreadableError: If the content at that revision is not
                valid UTF-8, or is a Git LFS pointer.
            ValueError: If git itself fails.
        """
        reader = self._revision_reader()
        if not is_note(path):
            raise InvalidRequestError(
                f"Revision reads are for markdown notes; {path!r} is an "
                "attachment, whose content at a revision is binary. Use "
                "'get_history' and 'get_diff' to inspect its history."
            )
        if not re.fullmatch(_SHA_RE, revision):
            raise InvalidRequestError(
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
                # A section read is exempt from the whole-note cap, matching
                # DocumentManager.read, which returns the section before it
                # ever stats the file. The cap exists to bound what reaches
                # the model, and the escape hatch it points callers at is
                # precisely `section=`.
                max_bytes=0 if section is not None else self._max_note_read_bytes,
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
            if not is_regular_file(abs_path):
                return None
            return self._git_strategy.committed_revision(self._source_dir, abs_path)
        except (ValueError, OSError, subprocess.SubprocessError):
            logger.debug("previous_revision_unavailable path=%s", path, exc_info=True)
            return None


def _section_of(content: RevisionContent, section: str) -> str:
    """Return one section of a historical note, or explain why it is not there.

    Frontmatter is parsed to find the body, so a revision whose frontmatter
    was malformed surfaces as a caller-visible error rather than a YAML
    exception escaping the read.  Unlike the on-disk read, that is the
    caller's to change: the whole note at that revision is still readable.

    Raises:
        InvalidRequestError: If *section* is empty, the historical frontmatter
            cannot be parsed, or no heading matches (#1608).
    """
    if not section.strip():
        raise InvalidRequestError("section must be a non-empty heading")
    try:
        body = extract_section(content.content, section.strip())
        headings = list_section_headings(content.content)
    except yaml.YAMLError as exc:
        raise InvalidRequestError(
            f"{content.historical_path!r} had malformed frontmatter at revision "
            f"{content.revision!r}, so its sections cannot be resolved. Read the "
            "whole note at that revision instead."
        ) from exc
    if body is None:
        suggestions = ", ".join(repr(h) for h in headings[:10]) or "none"
        raise InvalidRequestError(
            f"Section {section!r} not found at revision {content.revision!r}. "
            f"Headings there: {suggestions}"
        )
    return body
