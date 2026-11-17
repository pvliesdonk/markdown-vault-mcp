"""Native local Git history; see docs/design/reference/git-libgit2.md."""

from __future__ import annotations

import datetime as dt
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pygit2

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.git.query import (
    _note_text,
    _require_inside_vault,
    _truncate_diff,
)
from markdown_vault_mcp.types import CommitDiff, HistoryEntry, RevisionContent
from markdown_vault_mcp.utils.fs import path_exists

if TYPE_CHECKING:
    from markdown_vault_mcp.git.types import RevisionQuery


def _rel(repo: Any, path: Path) -> str:
    return path.relative_to(Path(repo.workdir)).as_posix()


def _prefix(repo: Any, vault: Path) -> str:
    rel = _rel(repo, vault)
    return "" if rel == "." else rel.rstrip("/") + "/"


def _entry(commit: Any, path: str) -> Any | None:
    try:
        return commit.tree[path]
    except KeyError:
        return None


def _diff(parent: Any | None, commit: Any) -> Any:
    if parent is None:
        return commit.tree.diff_to_tree(swap=True)
    return parent.tree.diff_to_tree(commit.tree)


def _old_path(parent: Any, commit: Any, path: str) -> str | None:
    diff = _diff(parent, commit)
    diff.find_similar(
        flags=pygit2.enums.DiffFind.FIND_RENAMES, rename_threshold=30, rename_limit=2000
    )
    for delta in diff.deltas:
        if (
            delta.status == pygit2.enums.DeltaStatus.RENAMED
            and delta.new_file.path == path
        ):
            return str(delta.old_file.path)
    return None


def _same(a: Any | None, b: Any | None) -> bool:
    if a is None or b is None:
        return a is b
    return bool(a.id == b.id and a.filemode == b.filemode)


def _walk(repo: Any, path: str) -> list[tuple[Any, str, bool]]:
    """Carry each name through all parents, stopping each branch at birth."""
    if repo.head_is_unborn:
        return []
    names: dict[Any, set[str]] = defaultdict(set)
    names[repo.head.target].add(path)
    result = []
    for commit in repo.walk(
        repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME
    ):
        current_names = names.pop(commit.id, set())
        for name in current_names:
            current = _entry(commit, name)
            differences = []
            for parent in commit.parents:
                previous_name = name
                previous = _entry(parent, name)
                if current is not None and previous is None:
                    previous_name = _old_path(parent, commit, name) or ""
                    previous = _entry(parent, previous_name) if previous_name else None
                differences.append(
                    not _same(current, previous) or previous_name != name
                )
                if previous_name and (previous is not None or current is None):
                    names[parent.id].add(previous_name)
            changed = all(differences) if differences else current is not None
            result.append((commit, name, changed))
    return result


def _date(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(
        r"\s*(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago\s*", value, re.I
    )
    if match:
        unit = match[2].lower()
        count = int(match[1])
        if unit in {"month", "year"}:
            local = list(time.localtime())
            months = count if unit == "month" else count * 12
            year, month = divmod(local[0] * 12 + local[1] - 1 - months, 12)
            local[0], local[1] = year, month + 1
            return time.mktime(time.struct_time(local))
        seconds = {
            "second": 1,
            "minute": 60,
            "hour": 3600,
            "day": 86400,
            "week": 604800,
        }[unit]
        return int(time.time()) - count * seconds
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise InvalidRequestError(
            f"Invalid date {value!r}; use ISO 8601 or 'N days ago'."
        ) from exc


def _timestamp(commit: Any) -> str:
    zone = dt.timezone(dt.timedelta(minutes=commit.author.offset))
    return dt.datetime.fromtimestamp(commit.author.time, zone).isoformat()


def _history_entry(commit: Any, paths: list[str]) -> HistoryEntry:
    sha = str(commit.id)
    return HistoryEntry(
        sha,
        sha[:7],
        _timestamp(commit),
        f"{commit.author.name} <{commit.author.email}>",
        (commit.message or "").split("\n", 1)[0],
        paths,
    )


def get_file_history(  # noqa: PLR0913, RUF100 -- mirrors the HistorySource protocol.
    repo: Any,
    vault: Path,
    path: Path | None,
    since: str | None,
    limit: int,
    until: str | None = None,
    *,
    is_dir: bool = False,
) -> list[HistoryEntry]:
    """Return commits touching a file, directory, or vault."""
    lower, upper = _date(since), _date(until)
    if repo is None or repo.head_is_unborn:
        return []
    prefix = _prefix(repo, vault)
    rows: list[tuple[Any, list[str]]]
    if path is not None and not is_dir:
        rows = [
            (commit, [])
            for commit, _, changed in _walk(repo, _rel(repo, path))
            if changed
        ]
    else:
        scope = _rel(repo, path).rstrip("/") + "/" if path is not None else prefix
        rows = []
        for commit in repo.walk(
            repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME
        ):
            diff = _diff(commit.parents[0] if commit.parents else None, commit)
            paths = [
                d.new_file.path[len(prefix) :]
                for d in diff.deltas
                if d.new_file.path.startswith(scope)
            ]
            if paths:
                rows.append((commit, paths))
    return [
        _history_entry(c, paths)
        for c, paths in rows
        if (lower is None or c.commit_time >= lower)
        and (upper is None or c.commit_time <= upper)
    ][:limit]


def _commit(repo: Any, ref: str) -> Any:
    try:
        obj = repo.revparse_single(ref).peel(pygit2.Commit)
        if obj.id != repo.head.target and not repo.descendant_of(
            repo.head.target, obj.id
        ):
            raise InvalidRequestError(f"Revision {ref!r} is not an ancestor of HEAD.")
        return obj
    except (KeyError, ValueError, pygit2.GitError) as exc:
        raise InvalidRequestError(
            f"Revision {ref!r} is not a valid ancestral commit."
        ) from exc


def _at_ref(repo: Any, path: str, target: Any) -> str | None:
    for commit, name, _ in _walk(repo, path):
        if commit.id == target.id and _entry(commit, name) is not None:
            return name
    return None


def get_file_at_ref(repo: Any, query: RevisionQuery) -> RevisionContent:
    """Read an ancestral note after verifying lineage, type, and size."""
    if repo is None or repo.head_is_unborn:
        raise InvalidRequestError(
            "Reading a revision requires a git-backed vault with commits."
        )
    target = _commit(repo, query.ref)
    path = _rel(repo, query.path)
    if path_exists(query.path) and path not in repo.index:
        raise InvalidRequestError(f"{path!r} is not tracked by Git.")
    prefix = _prefix(repo, query.repo_path)
    historical = _at_ref(repo, path, target)
    if historical is None:
        raise InvalidRequestError(f"The note did not exist at revision {query.ref!r}.")
    _require_inside_vault(historical, prefix, query.ref)
    entry = _entry(target, historical)
    if entry is None or entry.filemode not in (
        pygit2.GIT_FILEMODE_BLOB,
        pygit2.GIT_FILEMODE_BLOB_EXECUTABLE,
    ):
        raise InvalidRequestError("The historical path is not a regular file.")
    _, size = repo.odb.read_header(entry.id)
    if query.max_bytes and size > query.max_bytes:
        raise InvalidRequestError("The historical note exceeds the read limit.")
    blob = repo[entry.id]
    relative = path[len(prefix) :]
    old_relative = historical[len(prefix) :]
    content = _note_text(
        blob.data, relative, f"{old_relative!r} at revision {query.ref!r}"
    )
    return RevisionContent(relative, old_relative, query.ref, content)


def committed_revision(repo: Any, path: Path) -> str | None:
    """Find the newest note commit when its content matches the working tree."""
    if repo is None or repo.head_is_unborn:
        return None
    try:
        rel = _rel(repo, path)
        if rel not in repo.index:
            return None
        for commit, name, changed in _walk(repo, rel):
            if changed:
                entry = _entry(commit, name)
                return (
                    str(commit.id)
                    if entry is not None
                    and repo.create_blob_fromworkdir(rel) == entry.id
                    else None
                )
    except (OSError, ValueError, KeyError, pygit2.GitError):
        return None
    return None


def _patches(diff: Any, names: set[str], summarize_binary: bool) -> str:
    chunks = []
    for patch in diff:
        delta = patch.delta
        if delta.old_file.path not in names and delta.new_file.path not in names:
            continue
        if summarize_binary and delta.is_binary:
            chunks.append(
                f"Binary files a/{delta.old_file.path} and b/{delta.new_file.path} differ\n"
            )
        else:
            chunks.append(patch.text or "")
    return _truncate_diff("".join(chunks))


def _single_tree(repo: Any, commit: Any | None, name: str | None) -> Any:
    index = pygit2.Index()
    entry = _entry(commit, name) if commit is not None and name else None
    if entry is not None and name is not None:
        index.add(pygit2.IndexEntry(name, entry.id, entry.filemode))
    return repo[index.write_tree(repo)]


def get_file_diff(  # noqa: PLR0913, RUF100 -- mirrors the HistorySource protocol.
    repo: Any,
    vault: Path,
    path: Path,
    ref: str | None,
    per_commit: bool,
    since_timestamp: str | None = None,
    limit: int | None = None,
    *,
    summarize_binary: bool = False,
) -> str | list[CommitDiff]:
    """Return the note's aggregate or per-commit patch."""
    del vault
    if repo is None or repo.head_is_unborn:
        return [] if per_commit else ""
    if ref is None and since_timestamp is None:
        raise ValueError("Either 'ref' or 'since_timestamp' must be provided")
    target = _commit(repo, ref) if ref and since_timestamp is None else None
    cutoff = _date(since_timestamp)
    rows = _walk(repo, _rel(repo, path))
    if target is None and cutoff is not None:
        target = next(
            (
                c
                for c in repo.walk(
                    repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME
                )
                if c.commit_time <= cutoff
            ),
            None,
        )
        if target is None:
            return [] if per_commit else ""
    selected = []
    for commit, name, changed in rows:
        if target is not None and commit.id == target.id:
            break
        if changed:
            selected.append((commit, name))
    if not per_commit:
        head = repo.head.peel(pygit2.Commit)
        current_name = _rel(repo, path)
        historical = _at_ref(repo, current_name, target) if target else None
        old_tree = _single_tree(repo, target, historical)
        new_tree = _single_tree(repo, head, current_name)
        diff = old_tree.diff_to_tree(new_tree)
        diff.find_similar(flags=pygit2.enums.DiffFind.FIND_RENAMES, rename_threshold=30)
        return _patches(
            diff,
            {current_name, *([historical] if historical else [])},
            summarize_binary,
        )
    output = []
    for commit, name in selected[:limit]:
        parent = commit.parents[0] if commit.parents else None
        old = _old_path(parent, commit, name) if parent else None
        patch = _patches(
            _diff(parent, commit),
            {name, *([old] if old else [])},
            summarize_binary,
        )
        sha = str(commit.id)
        output.append(
            CommitDiff(
                sha,
                sha[:7],
                _timestamp(commit),
                (commit.message or "").split("\n", 1)[0],
                patch,
            )
        )
    return output
