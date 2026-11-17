"""Native local Git history; see docs/design/reference/git-libgit2.md."""

from __future__ import annotations

import datetime as dt
import re
import time
from collections import defaultdict
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pygit2

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.git.libgit2 import _rel as _literal_rel
from markdown_vault_mcp.git.libgit2 import _tree_entry
from markdown_vault_mcp.git.query import (
    _note_text,
    _repo_relative,
    _require_inside_vault,
    _truncate_diff,
    _vault_prefix,
)
from markdown_vault_mcp.types import CommitDiff, HistoryEntry, RevisionContent
from markdown_vault_mcp.utils.fs import path_exists

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_vault_mcp.git.types import RevisionQuery


def _rel(repo: Any, path: Path) -> str:
    return _repo_relative(Path(repo.workdir).resolve(), path)


def _entry(commit: Any, path: str) -> Any | None:
    return _tree_entry(commit.tree, path)


def _diff(parent: Any | None, commit: Any) -> Any:
    if parent is None:
        return commit.tree.diff_to_tree(swap=True)
    return parent.tree.diff_to_tree(commit.tree)


def _similar(parent: Any | None, commit: Any) -> Any:
    diff = _diff(parent, commit)
    diff.find_similar(
        flags=pygit2.enums.DiffFind.FIND_RENAMES, rename_threshold=30, rename_limit=2000
    )
    return diff


def _old_path(diff: Any, path: str) -> str | None:
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


def _walk(repo: Any, path: str) -> Iterator[tuple[Any, str, bool, bool]]:
    """Carry each name through all parents, stopping each branch at birth."""
    if repo.head_is_unborn:
        return
    names: dict[Any, set[str]] = defaultdict(set)
    names[repo.head.target].add(path)
    for commit in repo.walk(
        repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME
    ):
        if not names:
            return
        current_names = names.pop(commit.id, set())
        for name in current_names:
            current = _entry(commit, name)
            differences = []
            born = current is not None
            for parent in commit.parents:
                previous_name = name
                previous = _entry(parent, name)
                if current is not None and previous is None:
                    previous_name = _old_path(_similar(parent, commit), name) or ""
                    previous = _entry(parent, previous_name) if previous_name else None
                born = born and previous is None
                differences.append(
                    not _same(current, previous) or previous_name != name
                )
                if previous_name and (previous is not None or current is None):
                    names[parent.id].add(previous_name)
            changed = all(differences) if differences else current is not None
            yield commit, name, changed, born


_AGO = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?")


def _date(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:
        text = re.sub(r"[._]", " ", value.lower()).strip()
        now = dt.datetime.now().astimezone()
        if text.startswith("noon "):
            now, text = (
                now.replace(hour=12, minute=0, second=0, microsecond=0),
                text[5:],
            )
        if text == "yesterday":
            return (now - dt.timedelta(days=1)).timestamp()
        weekdays = (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
        if text.startswith("last ") and text[5:] in weekdays:
            days = (now.weekday() - weekdays.index(text[5:])) % 7 or 7
            return (now - dt.timedelta(days=days)).timestamp()
        pairs = _AGO.findall(text)
        if pairs and _AGO.sub("", text).strip() == "ago":
            local = list(time.localtime(now.timestamp()))
            months = sum(
                int(n) * (12 if unit == "year" else 1)
                for n, unit in pairs
                if unit in {"month", "year"}
            )
            year, month = divmod(local[0] * 12 + local[1] - 1 - months, 12)
            local[0], local[1] = year, month + 1
            seconds = {
                "second": 1,
                "minute": 60,
                "hour": 3600,
                "day": 86400,
                "week": 604800,
            }
            return time.mktime(time.struct_time(local)) - sum(
                int(n) * seconds[unit] for n, unit in pairs if unit in seconds
            )
    except (OverflowError, OSError, ValueError):
        pass
    raise InvalidRequestError(
        f"Invalid or unsupported date {value!r}; use GitWriteStrategy (the Git CLI) for other Git date expressions."
    )


def _timestamp(commit: Any) -> str:
    zone = dt.timezone(dt.timedelta(minutes=commit.author.offset))
    return dt.datetime.fromtimestamp(commit.author.time, zone).isoformat()


def _subject(commit: Any) -> str:
    return " ".join((commit.message or "").strip().split("\n\n", 1)[0].splitlines())


def _history_entry(commit: Any, paths: list[str]) -> HistoryEntry:
    sha = str(commit.id)
    return HistoryEntry(
        sha,
        sha[:7],
        _timestamp(commit),
        f"{commit.author.name} <{commit.author.email}>",
        _subject(commit),
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
    root = Path(repo.workdir).resolve()
    prefix = _vault_prefix(root, vault)
    rows: Iterator[tuple[Any, list[str]]]
    if path is not None and not is_dir:
        rows = (
            (commit, [])
            for commit, _, changed, _ in _walk(repo, _literal_rel(repo, path))
            if changed
        )
    else:
        scope = _vault_prefix(root, path) if path is not None else prefix

        def scoped() -> Iterator[tuple[Any, list[str]]]:
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
                    yield commit, [] if len(commit.parents) > 1 else paths

        rows = scoped()
    return list(
        islice(
            (
                _history_entry(c, paths)
                for c, paths in rows
                if (lower is None or c.commit_time >= lower)
                and (upper is None or c.commit_time <= upper)
            ),
            min(max(1, limit), 100),
        )
    )


def _commit(repo: Any, ref: str, *, ancestral: bool = True) -> Any:
    try:
        obj = repo.revparse_single(ref).peel(pygit2.Commit)
        if (
            ancestral
            and obj.id != repo.head.target
            and not repo.descendant_of(repo.head.target, obj.id)
        ):
            raise InvalidRequestError(f"Revision {ref!r} is not an ancestor of HEAD.")
        return obj
    except (KeyError, ValueError, pygit2.GitError) as exc:
        raise InvalidRequestError(
            f"Revision {ref!r} is not a valid ancestral commit."
        ) from exc


def _at_ref(repo: Any, path: str, target: Any) -> tuple[str | None, bool]:
    for commit, name, _, born in _walk(repo, path):
        if commit.id == target.id:
            return (name if _entry(commit, name) is not None else None), True
        if born and not repo.descendant_of(target.id, commit.id):
            return None, True
    return None, False


def get_file_at_ref(repo: Any, query: RevisionQuery) -> RevisionContent:
    """Read an ancestral note after verifying lineage, type, and size."""
    if repo is None or repo.head_is_unborn:
        raise InvalidRequestError(
            "Reading a revision requires a git-backed vault with commits."
        )
    target = _commit(repo, query.ref)
    path = _rel(repo, query.path)
    repo.index.read(force=True)
    if path_exists(query.path) and path not in repo.index:
        raise InvalidRequestError(f"{path!r} is not tracked by Git.")
    prefix = _vault_prefix(Path(repo.workdir).resolve(), query.repo_path)
    historical, _ = _at_ref(repo, path, target)
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
        raise InvalidRequestError(
            f"The historical note is {size} bytes, exceeding the {query.max_bytes}-byte limit; use section= to read a section."
        )
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
    rel = _rel(repo, path)
    try:
        repo.index.read(force=True)
        if rel not in repo.index:
            return None
        for commit, name, changed, _ in _walk(repo, rel):
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


def get_file_diff(  # noqa: C901, PLR0913, RUF100 -- protocol and lineage range handling.
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
    target = (
        _commit(repo, ref, ancestral=False) if ref and since_timestamp is None else None
    )
    cutoff = _date(since_timestamp)
    current = _literal_rel(repo, path)
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
    if not per_commit:
        head = repo.head.peel(pygit2.Commit)
        historical, reached = _at_ref(repo, current, target) if target else (None, True)
        if not reached and _entry(target, current):
            historical = current
        old_tree = _single_tree(repo, target, historical)
        new_tree = _single_tree(repo, head, current)
        diff = old_tree.diff_to_tree(new_tree)
        diff.find_similar(flags=pygit2.enums.DiffFind.FIND_RENAMES, rename_threshold=30)
        return _patches(
            diff,
            {current, *([historical] if historical else [])},
            summarize_binary,
        )
    selected = (
        (commit, name)
        for commit, name, changed, _ in _walk(repo, current)
        if changed
        and not (
            target is not None
            and (commit.id == target.id or repo.descendant_of(target.id, commit.id))
        )
    )
    output = []
    for commit, name in islice(
        selected, None if limit is None else min(max(1, limit), 100)
    ):
        parent = commit.parents[0] if commit.parents else None
        diff = _similar(parent, commit)
        old = _old_path(diff, name)
        patch = _patches(diff, {name, *([old] if old else [])}, summarize_binary)
        if old and _same(_entry(parent, old), _entry(commit, name)):
            patch = f"{old} => {name} (renamed, no content change)\n"
        sha = str(commit.id)
        output.append(
            CommitDiff(
                sha,
                sha[:7],
                _timestamp(commit),
                _subject(commit),
                patch,
            )
        )
    return output
