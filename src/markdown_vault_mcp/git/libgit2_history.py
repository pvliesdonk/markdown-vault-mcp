"""Native local Git history; see docs/design/reference/git-libgit2.md."""

from __future__ import annotations

import datetime as dt
import re
import time
from collections import defaultdict
from itertools import islice, takewhile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pygit2

from markdown_vault_mcp.exceptions import ConfigurationError, InvalidRequestError
from markdown_vault_mcp.git.libgit2 import _clean, _fresh, _index_path, _tree_entry
from markdown_vault_mcp.git.libgit2 import _rel as _literal_rel
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


def _entry(commit: Any, path: str) -> Any | None:
    return _tree_entry(commit.tree, path)


def _diff(parent: Any | None, commit: Any) -> Any:
    if parent is None:
        return commit.tree.diff_to_tree(swap=True)
    return parent.tree.diff_to_tree(commit.tree)


def _unsupported(shape: str) -> InvalidRequestError:
    return InvalidRequestError(
        f"Native Git cannot resolve {shape}; use GitWriteStrategy (the Git CLI)."
    )


def _resolved(diff: Any, path: str) -> tuple[Any, str | None]:
    added = [d for d in diff.deltas if d.status == pygit2.enums.DeltaStatus.ADDED]
    deleted = [d for d in diff.deltas if d.status == pygit2.enums.DeltaStatus.DELETED]
    new = next((d.new_file for d in added if d.new_file.path == path), None)
    if new and deleted:
        exact = [d for d in deleted if d.old_file.id == new.id]
        twins = [d for d in added if d.new_file.id == new.id]
        if len(exact) != 1 or len(twins) != 1:
            raise _unsupported("inexact rename resolution")
        old = exact[0].old_file
        return diff, old.path if old.mode >> 12 == new.mode >> 12 else None
    return diff, None


def _same(a: Any | None, b: Any | None) -> bool:
    if a is None or b is None:
        return a is b
    return bool(a.id == b.id and a.filemode == b.filemode)


def _walk(  # noqa: C901, RUF100 -- bounded lineage must handle each parent.
    repo: Any, path: str, until: Any = None, *, follow: bool = True
) -> Iterator[tuple[Any, str, bool, bool]]:
    """Carry each name through all parents, stopping each branch at birth."""
    if repo.head_is_unborn:
        return
    names: dict[Any, set[str]] = defaultdict(set, {repo.head.target: {path}})
    walker = repo.walk(repo.head.target, _SORT)
    if until is not None:
        for parent in until.parents:
            walker.hide(parent.id)
    for commit in walker:
        if not names:
            return
        for name in names.pop(commit.id, set()):
            if until is not None and commit.id == until.id:
                yield commit, name, False, False
                continue
            current = _entry(commit, name)
            differences = []
            born = current is not None
            for parent in commit.parents:
                old = name
                prev = _entry(parent, name)
                if follow and current is not None and prev is None:
                    old = _resolved(_diff(parent, commit), name)[1] or ""
                    prev = _entry(parent, old) if old else None
                born = born and prev is None
                differences.append(not _same(current, prev) or old != name)
                if old and (prev is not None or current is None):
                    names[parent.id].add(old)
            changed = all(differences) if differences else current is not None
            if follow and changed and len(commit.parents) > 1:
                raise _unsupported("file lineage across merge commits")
            yield commit, name, changed, born


_SORT = pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME
_AGO = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?")


def _date(value: str | None) -> float | None:  # noqa: C901, PLR0911, RUF100 -- bounded date grammar.
    now = dt.datetime.now().astimezone()
    if not value:
        return None if value is None else now.timestamp()
    try:
        if not re.match(r"\d{4}-\d\d-\d\d", value):
            raise ValueError
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = parsed.replace(hour=now.hour, minute=now.minute, second=now.second)
        return parsed.timestamp()
    except ValueError:
        pass
    try:
        text = re.sub(r"[._]", " ", value.lower()).strip()
        if text in {"today", "midnight", "noon"}:
            hour = 12 if text == "noon" else 0
            day = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            return (day - dt.timedelta(days=day > now)).timestamp()
        if text == "yesterday":
            return (now - dt.timedelta(days=1)).timestamp()
        weekdays = "monday tuesday wednesday thursday friday saturday sunday".split()  # noqa: SIM905
        if text.startswith("last ") and text[5:] in weekdays:
            days = (now.weekday() - weekdays.index(text[5:])) % 7 or 7
            return (now - dt.timedelta(days=days)).timestamp()
        pairs = _AGO.findall(text)
        if pairs and _AGO.sub("", text).strip() == "ago":
            local = list(time.localtime(now.timestamp()))
            months = sum(int(n) * {"month": 1, "year": 12}.get(u, 0) for n, u in pairs)
            year, month = divmod(local[0] * 12 + local[1] - 1 - months, 12)
            local[0], local[1] = year, month + 1
            return time.mktime(time.struct_time(local)) - sum(
                dt.timedelta(**{f"{unit}s": int(n)}).total_seconds()
                for n, unit in pairs
                if unit not in {"month", "year"}
            )
    except (OverflowError, OSError, ValueError):
        pass
    raise InvalidRequestError(
        f"Unsupported date {value!r}; use ISO 8601 or N units ago, or GitWriteStrategy for other Git date expressions."
    )


def _timestamp(commit: Any) -> str:
    zone = dt.timezone(dt.timedelta(minutes=commit.author.offset))
    return dt.datetime.fromtimestamp(commit.author.time, zone).isoformat()


def _short_id(repo: Any, commit: Any) -> str:
    if not str(_tree_entry(repo.config, "core.abbrev")).isdigit():
        raise _unsupported("unset, auto or non-numeric core.abbrev; set a number")
    return str(commit.short_id)


def _history_entry(repo: Any, commit: Any, paths: list[str]) -> HistoryEntry:
    author = pygit2.Mailmap.from_repository(repo).resolve_signature(commit.author)
    return HistoryEntry(
        str(commit.id),
        _short_id(repo, commit),
        _timestamp(commit).replace("+00:00", "Z"),
        f"{author.name} <{author.email}>",
        " ".join(_clean(commit.message or "").split("\n\n", 1)[0].split("\n")),
        paths,
    )


def _changed(commit: Any, scope: str, prefix: str) -> list[str]:
    diff = _diff(commit.parents[0] if commit.parents else None, commit)
    inside = [d for d in diff.deltas if d.new_file.path.startswith(scope)]
    deleted = any(d.status_char() == "D" for d in inside)
    added = [d.new_file.path for d in inside if d.status_char() == "A"]
    moved = {_resolved(diff, path)[1] for path in added if deleted}
    return [
        d.new_file.path[len(prefix) :] for d in inside if d.old_file.path not in moved
    ]


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
    lower, upper = _date(since or None), _date(until or None)
    if repo is None or repo.head_is_unborn:
        return []
    root = Path(repo.workdir).resolve()
    prefix = _vault_prefix(root, vault)
    rows: Iterator[tuple[Any, list[str]]]
    if path is not None and not is_dir:
        rows = (
            (commit, [])
            for commit, _, changed, _ in takewhile(
                lambda walked: lower is None or walked[0].commit_time >= lower,
                _walk(repo, _literal_rel(repo, path)),
            )
            if changed
        )
    else:
        scope = _vault_prefix(root, path) if path is not None else prefix

        def scoped() -> Iterator[tuple[Any, list[str]]]:
            for commit in repo.walk(repo.head.target, _SORT):
                if lower is not None and commit.commit_time < lower:
                    return
                if upper is not None and commit.commit_time > upper:
                    continue
                if len(commit.parents) > 1:
                    raise _unsupported("vault or directory history across merges")
                if paths := _changed(commit, scope, prefix):
                    yield commit, paths

        rows = scoped()
    rows = ((c, p) for c, p in rows if upper is None or c.commit_time <= upper)
    return [
        _history_entry(repo, c, p) for c, p in islice(rows, min(max(1, limit), 100))
    ]


def _commit(repo: Any, ref: str, *, ancestral: bool = True) -> Any:
    try:
        obj = repo.revparse_single(ref).peel(pygit2.Commit)
        if ancestral and repo.merge_base(repo.head.target, obj.id) != obj.id:
            raise InvalidRequestError(
                f"Revision {ref!r} is not an ancestor of the current HEAD (it may be on history that was rebased away), so this note's path at that revision cannot be established."
            )
        return obj
    except (KeyError, ValueError, pygit2.GitError) as exc:
        raise InvalidRequestError(
            f"{ref!r} is not a commit in this repository: unknown, not a unique commit prefix, or another kind of object. Pass a SHA from 'get_history'."
        ) from exc


def _at_ref(repo: Any, path: str, target: Any) -> tuple[str | None, bool]:
    for commit, name, _, born in _walk(repo, path, target):
        if commit.id == target.id:
            return (name if _entry(commit, name) is not None else None), True
        if len(commit.parents) > 1:
            raise _unsupported("file lineage across merge commits")
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
    path = _repo_relative(Path(repo.workdir).resolve(), query.path)
    repo = _fresh(repo.path, pygit2, policy=False)
    if path_exists(query.path) and path not in repo.index:
        raise InvalidRequestError(f"{path!r} is not tracked by Git.")
    prefix = _vault_prefix(Path(repo.workdir).resolve(), query.repo_path)
    historical, _ = _at_ref(repo, path, target)
    if historical is None:
        raise InvalidRequestError(
            f"{path!r} was created after revision {query.ref!r}, so the note now at that path did not exist at that revision. Anything stored under that name then belongs to a different note; use 'get_history' to find a revision this note actually has."
        )
    _require_inside_vault(historical, prefix, query.ref)
    entry = _entry(target, historical)
    if entry is None or entry.filemode >> 12 != 8:
        raise InvalidRequestError("The historical path is not a regular file.")
    _, size = repo.odb.read_header(entry.id)
    if query.max_bytes and size > query.max_bytes:
        raise InvalidRequestError(
            f"The historical note is {size} bytes, exceeding the {query.max_bytes}-byte limit; use section= to read a section."
        )
    relative = path[len(prefix) :]
    old_relative = historical[len(prefix) :]
    content = _note_text(
        repo[entry.id].data, relative, f"{old_relative!r} at revision {query.ref!r}"
    )
    return RevisionContent(relative, old_relative, query.ref, content)


def committed_revision(repo: Any, path: Path) -> str | None:
    """Find the newest note commit when its content matches the working tree."""
    if repo is None or repo.head_is_unborn:
        return None
    rel = _repo_relative(Path(repo.workdir).resolve(), path)
    try:
        repo.index.read(force=True)
        if rel not in repo.index:
            return None
        for commit, name, changed, _ in _walk(repo, rel, follow=False):
            if changed:
                index = pygit2.Index()
                index.read_tree(commit.tree)
                before = index[name]
                _index_path(repo, index, name, pygit2)
                return str(commit.id) if before == index[name] else None
    except (OSError, ValueError, KeyError, pygit2.GitError, ConfigurationError):
        return None
    return None


def _spaced(text: str) -> str:
    head, at, body = text.partition("\n@@")
    return re.sub(r"(?m)^([-+]{3} \"?[ab]/.* .*)$", "\\1\t", head) + at + body


def _patches(diff: Any, names: set[str], summarize_binary: bool) -> str:
    chunks = []
    for patch in diff:
        paths = {patch.delta.old_file.path, patch.delta.new_file.path}
        if paths & names:
            if not all(map(str.isascii, paths)):
                raise _unsupported("non-ASCII diff paths")
            if summarize_binary and patch.delta.is_binary:
                raise _unsupported("binary diff summaries")
            chunks.append(_spaced(patch.text or ""))
    return _truncate_diff(re.sub(r"\r\n?", "\n", "".join(chunks)))


def _probe_diff(repo: Any) -> None:
    cfg = repo.config
    if (
        ("diff.mnemonicprefix" in cfg and cfg.get_bool("diff.mnemonicprefix"))
        or ("diff.context" in cfg and cfg.get_int("diff.context") != 3)
        or ("diff.interhunkcontext" in cfg and cfg.get_int("diff.interhunkcontext"))
    ):
        raise _unsupported("diff.context, diff.interHunkContext or diff.mnemonicPrefix")


def get_file_diff(  # noqa: C901, PLR0912, PLR0913, RUF100 -- protocol and lineage capabilities.
    repo: Any,
    path: Path,
    ref: str | None,
    per_commit: bool,
    since_timestamp: str | None = None,
    limit: int | None = None,
    summarize_binary: bool = False,
) -> str | list[CommitDiff]:
    """Return the note's aggregate or per-commit patch."""
    if repo is None or repo.head_is_unborn:
        return [] if per_commit else ""
    _probe_diff(repo)
    if ref is None and since_timestamp is None:
        raise ValueError("Either 'ref' or 'since_timestamp' must be provided")
    target = (
        _commit(repo, ref, ancestral=False) if ref and since_timestamp is None else None
    )
    cutoff = _date(since_timestamp)
    current = _literal_rel(repo, path)
    if target is None and cutoff is not None:
        target = next(
            (c for c in repo.walk(repo.head.target, _SORT) if c.commit_time <= cutoff),
            None,
        )
        if target is None:
            return [] if per_commit else ""
    if not per_commit:
        if target is None:
            return ""
        head = repo.head.peel(pygit2.Commit)
        historical, reached = _at_ref(repo, current, target)
        if reached and historical is None:
            old_tree = repo[repo.TreeBuilder().write()]
        else:
            old_tree = target.tree
        diff, old = _resolved(old_tree.diff_to_tree(head.tree), current)
        if old:
            if target.tree[old].filemode != head.tree[current].filemode:
                raise _unsupported("rename mode changes")
            blob_patch = pygit2.Patch.create_from(
                repo[target.tree[old].id], repo[head.tree[current].id], old, current
            )
            return _patches([blob_patch], {old, current}, summarize_binary)
        return _patches(old_tree.diff_to_tree(head.tree), {current}, summarize_binary)
    selected = (
        (commit, name)
        for commit, name, changed, _ in _walk(repo, current, target)
        if changed
    )
    output = []
    for commit, name in islice(
        selected, None if limit is None else min(max(1, limit), 100)
    ):
        row = _history_entry(repo, commit, [])
        parent = commit.parents[0] if commit.parents else None
        diff, old = _resolved(_diff(parent, commit), name)
        if old:
            if not _same(_entry(parent, old), _entry(commit, name)):
                raise _unsupported("rename mode changes")
            patch = f"{old} => {name} (renamed, no content change)\n"
        else:
            patch = _patches(diff, {name}, summarize_binary)
        output.append(
            CommitDiff(row.sha, row.short_sha, row.timestamp, row.message, patch)
        )
    return output
