"""Read-only git history and diff queries.

Pure functions -- lock-free, no mutation of repository state. Subprocess calls are
module-qualified (``subprocess.run``) so the test suite's global monkeypatch still
intercepts them.

The ``git_root`` parameter is pre-resolved by the caller (typically via
:meth:`GitWriteStrategy._ensure_git_root`, which memoises the result).  Passing
``None`` to the two query entry points (:func:`get_file_history` /
:func:`get_file_diff`) is a no-op: they return an empty result immediately.
(:func:`resolve_path_at_ref` is an internal helper and requires a resolved
``git_root``.)  This keeps git-root discovery out of these functions so that tests
can prime the cache on the
strategy instance and then patch ``subprocess.run`` without the patch also
interfering with the discovery call.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp.git._run import cleanup_git_env, git_env
from markdown_vault_mcp.types import CommitDiff, HistoryEntry, RevisionContent

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_vault_mcp.git.types import RevisionQuery

logger = logging.getLogger(__name__)

# Git's well-known empty-tree SHA: a real object in every repository.
# Used to diff a parent-less (root/orphan) commit against "nothing".
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Diff payloads larger than this are truncated with a byte-count marker.
_DIFF_MAX_BYTES = 50 * 1024  # 50 KB

# \x1e (ASCII Record Separator) is the sentinel used to split commit
# blocks in ``git log`` output — it cannot appear in filenames or commit
# messages.
_HISTORY_SENTINEL = "\x1e"


def _truncate_diff(diff: str) -> str:
    """Cap *diff* at :data:`_DIFF_MAX_BYTES`, appending an omission marker.

    Truncation is byte-based (UTF-8), decoding the kept prefix with
    ``errors="replace"`` so a multi-byte character split at the boundary
    cannot raise.
    """
    if len(diff.encode()) <= _DIFF_MAX_BYTES:
        return diff
    omitted = len(diff.encode()) - _DIFF_MAX_BYTES
    diff = diff.encode()[:_DIFF_MAX_BYTES].decode(errors="replace")
    return diff + f"\n[diff truncated: {omitted} bytes omitted]"


def _resolve_since_timestamp(
    git_root: Path, since_timestamp: str, env: dict[str, str] | None
) -> str | None:
    """Resolve *since_timestamp* to the most recent commit at or before it.

    Returns:
        The commit SHA, or ``None`` when no commit exists at or before the
        timestamp (inclusive boundary: a commit whose committer date equals
        the timestamp is returned).

    Raises:
        ValueError: If ``git rev-list`` exits non-zero.
    """
    try:
        rev_result = subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "rev-list",
                f"--before={since_timestamp}",
                "-1",
                "HEAD",
                # No path filter: git rev-list has no --follow, so
                # filtering by current path silently misses pre-rename
                # commits.  Resolve the timestamp globally and let the
                # subsequent diff (which uses -- path_str) handle scope.
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"Could not resolve timestamp {since_timestamp!r}: "
            f"{(exc.stderr or '').strip()}"
        ) from exc
    return rev_result.stdout.strip() or None


def _rename_aware_diff_args(
    git_root: Path,
    from_ref: str,
    to_ref: str,
    cur_rel: str | None,
    pathspec: str,
    env: dict[str, str] | None,
) -> tuple[list[str], str | None]:
    """Build diff args for ``from_ref..to_ref``, resolving renames.

    Resolves the path *cur_rel* had at *from_ref*; when it differs from the
    current name, the diff targets the two blob specs so git pairs the
    rename instead of reporting delete+add.

    Returns:
        Tuple ``(diff_args, old_path)`` where *diff_args* is the ref/path
        portion of the ``git diff`` invocation and *old_path* is the
        resolved historical path (``None`` when no rename was resolved).
    """
    old_path = (
        resolve_path_at_ref(git_root, from_ref, cur_rel, env, to_ref=to_ref)
        if cur_rel is not None
        else None
    )
    if old_path is None or cur_rel is None or old_path == cur_rel:
        return [f"{from_ref}..{to_ref}", "--", pathspec], old_path
    return [f"{from_ref}:{old_path}", f"{to_ref}:{cur_rel}"], old_path


def _diff_is_binary(
    git_root: Path, diff_args: list[str], env: dict[str, str] | None
) -> bool:
    """True if git reports the diff target as binary.

    *diff_args* is the ref/path portion of a ``git diff`` invocation — such as
    a range/path form (``["<from>..<to>", "--", path_str]``), a two-endpoint
    blob/tree-spec form (``[f"{a}:{old}", f"{b}:{new}"]``), or two revs followed
    by a pathspec (e.g. the empty-tree SHA and a commit SHA followed by
    ``["--", commit_path]``, used for parent-less commits).  The endpoints may
    be refs, commit SHAs, blob specs, or the empty-tree constant.
    ``git diff --numstat`` prints ``-\\t-\\t<path>`` for binary and real counts
    for text; empty output (no change) → non-binary.
    A non-zero git exit (e.g. a bad ref) yields empty stdout, so it is classified
    non-binary; the error is then surfaced by the subsequent checked ``git diff``
    call. This is a detection probe, not the final word.
    """
    result = subprocess.run(
        ["git", "-C", str(git_root), "diff", "--numstat", *diff_args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    first = result.stdout.strip().split("\n", 1)[0]
    return first.startswith("-\t-")


def _vault_prefix(git_root: Path, repo_path: Path) -> str:
    """Return the prefix to strip from ``--name-only`` output paths.

    When the git root is a parent of *repo_path*, git reports paths relative
    to the git root (e.g. ``"vault/note.md"``); the returned prefix (e.g.
    ``"vault/"``) is stripped so callers always receive vault-relative paths.
    *repo_path* is resolved to handle symlinks: ``git rev-parse
    --show-toplevel`` always returns the real (resolved) path, so it must be
    matched.

    Returns:
        The vault prefix ending in ``"/"``, or ``""`` when the git root is
        the vault root itself (or *repo_path* is not under *git_root*).
    """
    try:
        vault_rel = repo_path.resolve().relative_to(git_root)
    except ValueError:
        return ""
    return "" if vault_rel == Path() else vault_rel.as_posix() + "/"


def _history_log_cmd(
    git_root: Path,
    repo_path: Path,
    path: Path | None,
    since: str | None,
    until: str | None,
    limit: int,
    *,
    is_dir: bool,
) -> list[str]:
    """Assemble the ``git log`` argv for a history query.

    Args:
        git_root: Pre-resolved git repository root.
        repo_path: Absolute path of the vault root (the vault-wide pathspec).
        path: Absolute path of the file (or directory, when *is_dir*) to
            filter on, or ``None`` for the entire vault.
        since: ``--since`` filter, or ``None`` to disable it.
        until: ``--until`` filter, or ``None`` to disable it.
        limit: Maximum number of commits (already clamped by the caller).
        is_dir: When ``True``, *path* is a directory subtree.

    Returns:
        The full ``git log`` argv, formatted with :data:`_HISTORY_SENTINEL`
        block markers and NUL-separated header fields.
    """
    cmd = [
        "git",
        "-C",
        str(git_root),
        "log",
        f"--format={_HISTORY_SENTINEL}%H%x00%h%x00%aI%x00%aN <%aE>%x00%s",
        f"-n{limit}",
    ]
    if since:
        cmd.append(f"--since={since}")
    if until:
        cmd.append(f"--until={until}")
    if path is None:
        # vault-wide: scope to the resolved real path so symlinked SOURCE_DIR
        # values work correctly (git compares against the real toplevel).
        cmd += ["--name-only", "--", str(repo_path.resolve())]
    elif is_dir:
        # directory scope: no --follow (git rejects it for anything but a
        # single file); --name-only so paths_changed carries the subtree files
        # each commit touched. git already filters the name-only output to the
        # <dir> pathspec, so no sibling paths leak in and no extra filtering is
        # needed here.
        cmd += ["--name-only", "--", str(path)]
    else:
        cmd += ["--follow", "--", str(path)]
    return cmd


def _history_log_output(cmd: list[str], env: dict[str, str] | None) -> str:
    """Run the assembled ``git log`` command and return its raw stdout.

    Raises:
        ValueError: If ``git log`` exits non-zero (e.g. an invalid
            ``--since`` / ``--until`` expression).
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"git log failed: {(exc.stderr or '').strip()}") from exc
    return result.stdout


def _vault_relative_paths(lines: list[str], vault_prefix: str) -> list[str]:
    """Normalise ``--name-only`` path lines to vault-relative paths.

    Blank lines are dropped; *vault_prefix* (when non-empty) is stripped from
    each remaining line so callers always receive vault-relative paths.
    """
    paths: list[str] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if vault_prefix and ln.startswith(vault_prefix):
            ln = ln[len(vault_prefix) :]
        paths.append(ln)
    return paths


def _parse_history_block(
    block: str, vault_prefix: str, *, collect_paths: bool
) -> HistoryEntry | None:
    """Parse one sentinel-delimited ``git log`` block into a history entry.

    Args:
        block: One commit block: ``header_line\\nfile1\\nfile2\\n`` where the
            header carries five NUL-separated fields.
        vault_prefix: Prefix stripped from path lines (see
            :func:`_vault_prefix`).
        collect_paths: When ``True`` (vault-wide or directory queries),
            populate ``paths_changed`` from the block's ``--name-only``
            lines; when ``False`` (single-file queries) leave it empty.

    Returns:
        The parsed :class:`HistoryEntry`, or ``None`` for an empty or
        malformed block.
    """
    block = block.strip()
    if not block:
        return None
    # A stripped non-empty block always has a first line.
    lines = block.splitlines()
    parts = lines[0].split("\x00")
    if len(parts) < 5:
        return None
    sha, short_sha, timestamp, author, message = parts[:5]
    paths_changed: list[str] = []
    if collect_paths:
        paths_changed = _vault_relative_paths(lines[1:], vault_prefix)
    return HistoryEntry(
        sha=sha,
        short_sha=short_sha,
        timestamp=timestamp,
        author=author,
        message=message,
        paths_changed=paths_changed,
    )


def get_file_history(
    git_root: Path | None,
    repo_path: Path,
    path: Path | None,
    since: str | None,
    limit: int,
    until: str | None = None,
    *,
    token: str | None,
    username: str,
    is_dir: bool = False,
) -> list[HistoryEntry]:
    """Return commits that touched *path* (or the whole vault).

    Args:
        git_root: Pre-resolved git repository root, or ``None`` if the vault
            is not inside a git repository (returns ``[]`` immediately).
        repo_path: Absolute path of the vault root.  Used to compute the
            vault-relative prefix when the git root is a parent of the vault.
        path: Absolute path of the file (or directory, when *is_dir*) to
            filter on, or ``None`` for the entire vault.
        is_dir: When ``True``, *path* is a directory: history is scoped to its
            subtree (``git log -- <dir>``, without the single-file
            ``--follow``), and ``paths_changed`` is populated with the
            subtree files each commit touched. When ``False`` (a single file),
            ``--follow`` tracks renames and ``paths_changed`` stays empty.
        since: Passed as ``--since`` to ``git log`` (ISO 8601 or git date
            expression such as ``"1 week ago"``).  ``None`` disables the
            filter.
        limit: Maximum number of commits to return (capped at 100).
        until: Passed as ``--until`` to ``git log`` (same format as
            *since*).  ``None`` disables the filter.  When both *since*
            and *until* are given the window is bounded on both sides,
            inclusive at both endpoints (git's ``--since`` / ``--until``
            semantics: a commit whose committer date equals either
            boundary is included).
        token: Personal access token for authenticated git operations, or
            ``None`` for unauthenticated access.
        username: Git username for authenticated operations.

    Returns:
        List of :class:`HistoryEntry` ordered from newest to oldest.

    Raises:
        ValueError: If ``git log`` exits non-zero (e.g. an invalid
            ``since`` / ``until`` expression).
    """
    if git_root is None:
        return []

    limit = min(max(1, limit), 100)
    cmd = _history_log_cmd(
        git_root, repo_path, path, since, until, limit, is_dir=is_dir
    )

    env = git_env(token, username)
    try:
        raw = _history_log_output(cmd, env)
    finally:
        cleanup_git_env(env)

    if not raw.strip():
        return []

    vault_prefix = _vault_prefix(git_root, repo_path)
    # paths_changed is populated for vault-wide and directory queries only;
    # single-file queries (--follow) leave it empty.
    collect_paths = path is None or is_dir
    # Split on the sentinel embedded at the start of each format line.  The
    # first element will be empty (output starts with the sentinel); the
    # parser rejects it along with any other empty/malformed block.
    entries: list[HistoryEntry] = []
    for block in raw.split(_HISTORY_SENTINEL):
        entry = _parse_history_block(block, vault_prefix, collect_paths=collect_paths)
        if entry is not None:
            entries.append(entry)
    return entries


def resolve_path_at_ref(
    git_root: Path,
    ref: str,
    cur_rel: str,
    env: dict[str, str] | None,
    *,
    to_ref: str = "HEAD",
) -> str | None:
    """Return the path *cur_rel* had at *ref* via rename detection, else None.

    Diffs ``ref..to_ref`` (``to_ref`` defaults to ``HEAD``).  Pass a specific
    commit as *to_ref* to resolve a rename a single commit introduced.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "diff",
                "--name-status",
                # 30% threshold: catch rename-with-edits per #338, avoid template false-positives.
                "--find-renames=30",
                # -z: NUL-terminated fields, tolerates tabs/newlines in paths.
                "-z",
                ref,
                to_ref,
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError:
        return None
    # Stream: <status>\0<path>\0 — R*/C* records add a second path
    # (\0<old>\0<new>\0).  Copies (C*) are skipped, not resolved (a copy is
    # an add, not a rename); the branch keeps the parser in sync if git ever
    # emits one (e.g. if ``--find-copies`` were added in future).  Without
    # ``--find-copies`` / ``--find-copies-harder`` git does not emit C* records,
    # so this branch is defensive dead-code today.
    items = result.stdout.split("\0")[:-1]
    i = 0
    while i < len(items):
        status = items[i]
        if status.startswith("R"):
            if i + 2 >= len(items):
                break
            if items[i + 2] == cur_rel:
                return items[i + 1]
            i += 3
        elif status.startswith("C"):
            if i + 2 >= len(items):
                break
            i += 3
        else:
            if i + 1 >= len(items):
                break
            i += 2
    return None


def get_file_diff(
    git_root: Path | None,
    path: Path,
    ref: str | None,
    per_commit: bool,
    since_timestamp: str | None = None,
    limit: int | None = None,
    *,
    token: str | None,
    username: str,
    summarize_binary: bool = False,
) -> str | list[CommitDiff]:
    """Return a unified diff of *path* from *ref* to HEAD.

    Exactly one of *ref* or *since_timestamp* must be supplied.  When
    *since_timestamp* is given, it is resolved via
    ``git rev-list --before=<ts> -1 HEAD`` to the most recent commit at
    or before that instant.  Boundary is **inclusive**: a commit whose
    committer date equals *since_timestamp* IS the resolved ref.

    Args:
        git_root: Pre-resolved git repository root, or ``None`` if the vault
            is not inside a git repository (returns ``""`` / ``[]``
            immediately).
        path: Absolute path of the file to diff.
        ref: The git ref (SHA or expression) to diff from.  Mutually
            exclusive with *since_timestamp*.
        per_commit: When ``False``, return a single unified diff string.
            When ``True``, return one :class:`CommitDiff` per intervening
            commit.
        since_timestamp: ISO 8601 datetime string resolved to a commit SHA
            via ``git rev-list --before``.  Mutually exclusive with *ref*.
        limit: When *per_commit* is ``True``, cap the number of commits
            walked to the *limit* most recent ones (clamped to
            ``[1, 100]``).  Ignored when *per_commit* is ``False``.
            ``None`` means unbounded (still capped by the underlying
            ``ref..HEAD`` range).
        token: Personal access token for authenticated git operations, or
            ``None`` for unauthenticated access.
        username: Git username for authenticated operations.
        summarize_binary: When ``True`` and git reports the file as a binary
            change over the range, return a ``git diff --stat`` summary
            instead of a (meaningless) binary patch.  Text files -- and every
            note, since the default is ``False`` -- fall through to the normal
            full unified diff (#342).

    Returns:
        A unified diff string when *per_commit* is ``False``, or a list of
        :class:`CommitDiff` when *per_commit* is ``True``.

    Raises:
        ValueError: If *ref* is not found in history, *since_timestamp*
            cannot be resolved, or a git subprocess exits non-zero.
    """
    if git_root is None:
        return [] if per_commit else ""

    env = git_env(token, username)
    try:
        if since_timestamp is not None:
            ref = _resolve_since_timestamp(git_root, since_timestamp, env)
            if ref is None:
                return [] if per_commit else ""

        if ref is None:
            raise ValueError("Either 'ref' or 'since_timestamp' must be provided")

        if not per_commit:
            return _range_diff(
                git_root, path, ref, env, summarize_binary=summarize_binary
            )
        return _per_commit_diffs(
            git_root, path, ref, limit, env, summarize_binary=summarize_binary
        )
    finally:
        cleanup_git_env(env)


def _range_diff(
    git_root: Path,
    path: Path,
    ref: str,
    env: dict[str, str] | None,
    *,
    summarize_binary: bool,
) -> str:
    """Return the single unified diff of *path* from *ref* to HEAD.

    Handles rename resolution, the binary ``--stat`` summary (#342), and
    truncation.  Raises :exc:`ValueError` on an invalid ref or a path not
    present at that revision.
    """
    path_str = str(path)
    # Resolve the path-at-ref once so renames are handled uniformly for
    # binary detection, the --stat summary, and the full diff.
    try:
        cur_rel = path.resolve().relative_to(git_root).as_posix()
    except ValueError:
        cur_rel = None
    diff_args, _old_path = _rename_aware_diff_args(
        git_root, ref, "HEAD", cur_rel, path_str, env
    )

    # Binary attachments: a unified patch is meaningless, so emit a
    # --stat summary instead.  Text attachments (and notes, since the
    # default is summarize_binary=False) fall through to the full diff.
    if summarize_binary and _diff_is_binary(git_root, diff_args, env):
        try:
            stat = subprocess.run(
                ["git", "-C", str(git_root), "diff", "--stat", *diff_args],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(
                f"Could not compute diff summary against {ref!r}: invalid ref "
                "or path not present at that revision"
            ) from exc
        return stat.stdout

    diff_cmd = ["git", "-C", str(git_root), "diff", *diff_args]
    try:
        result = subprocess.run(
            diff_cmd,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"Could not compute diff against {ref!r}: invalid ref or "
            f"path not present at that revision"
        ) from exc
    return _truncate_diff(result.stdout)


def _root_commit_diff(
    git_root: Path,
    sha: str,
    commit_path: str,
    env: dict[str, str] | None,
    *,
    summarize_binary: bool,
) -> str:
    """Render the add-form diff of a parent-less (root/orphan) commit.

    Classifies binariness against the empty tree (so a root binary still
    gets ``--stat``) and falls back to ``git show``.  Raises
    :exc:`ValueError` when even the fallback fails.
    """
    # Resolve the empty-tree object for this repo's hash algorithm
    # (the hardcoded SHA-1 constant is invalid in SHA-256 repos);
    # fall back to the SHA-1 constant if resolution fails.
    empty_tree = _EMPTY_TREE_SHA
    try:
        res = subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "hash-object",
                "-t",
                "tree",
                "--stdin",
            ],
            input="",
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        empty_tree = res.stdout.strip() or _EMPTY_TREE_SHA
    except subprocess.CalledProcessError:
        pass
    root_binary = summarize_binary and _diff_is_binary(
        git_root, [empty_tree, sha, "--", commit_path], env
    )
    fallback_cmd = [
        "git",
        "-C",
        str(git_root),
        "show",
        "--format=",
        "--stat" if root_binary else "-p",
        sha,
        "--",
        commit_path,
    ]
    try:
        show_result = subprocess.run(
            fallback_cmd,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Could not retrieve diff for commit {sha!r}") from exc
    return show_result.stdout


def _parse_log_block(block: str, rel_fallback: str) -> tuple[str, ...] | None:
    """Parse one sentinel-delimited ``git log --name-only`` block.

    Returns:
        ``(sha, short_sha, timestamp, message, commit_path)``, or ``None``
        for an empty or malformed block.  *commit_path* is the path the
        file had at that commit (the old name for pre-rename commits,
        thanks to ``--follow``), falling back to *rel_fallback* when the
        block carries no path line.
    """
    block = block.strip()
    if not block:
        return None
    lines = block.splitlines()
    if not lines:
        return None
    parts = lines[0].split("\x00")
    if len(parts) < 4:
        return None
    sha, short_sha, timestamp, message = parts[:4]
    commit_path = next((ln.strip() for ln in lines[1:] if ln.strip()), rel_fallback)
    return sha, short_sha, timestamp, message, commit_path


def _per_commit_diffs(
    git_root: Path,
    path: Path,
    ref: str,
    limit: int | None,
    env: dict[str, str] | None,
    *,
    summarize_binary: bool,
) -> list[CommitDiff]:
    """Return one :class:`CommitDiff` per commit in ``ref..HEAD``.

    Enumerates commits with ``git log --follow --name-only`` (with a
    sentinel) so the path the file had at each commit can be recovered —
    critical for correct diffs across renames (``git show sha -- new.md``
    returns nothing for pre-rename commits; the old filename must be
    passed instead).  Raises :exc:`ValueError` when *ref* is not found or
    a per-commit diff fails for a commit that does have a parent.
    """
    path_str = str(path)
    _PC_SENTINEL = "\x1e"
    log_cmd = [
        "git",
        "-C",
        str(git_root),
        "log",
        "--follow",
        f"--format={_PC_SENTINEL}%H%x00%h%x00%aI%x00%s",
        "--name-only",
    ]
    if limit is not None:
        clamped_limit = min(max(1, limit), 100)
        log_cmd.append(f"-n{clamped_limit}")
    log_cmd += [f"{ref}..HEAD", "--", path_str]
    try:
        log_result = subprocess.run(
            log_cmd,
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Commit {ref!r} not found in history") from exc

    # Repo-relative posix path — the correct fallback when a --name-only
    # block has no path line (git returns posix-relative paths, so the
    # absolute platform-native path_str would break rename resolution on
    # Windows).
    try:
        rel_fallback = path.resolve().relative_to(git_root).as_posix()
    except ValueError:
        rel_fallback = path_str

    diffs: list[CommitDiff] = []
    for block in log_result.stdout.split(_PC_SENTINEL):
        parsed = _parse_log_block(block, rel_fallback)
        if parsed is None:
            continue
        sha, short_sha, timestamp, message, commit_path = parsed

        # Build a rename-aware diff target for THIS commit vs its parent,
        # mirroring the single-range branch, so a renamed binary pairs into
        # `{old => new} | Bin OLD -> NEW` instead of an add/text stat (#683).
        parent = f"{sha}^"
        commit_args, old_at_parent = _rename_aware_diff_args(
            git_root, parent, sha, commit_path, commit_path, env
        )
        # Classify binariness for THIS commit (not the whole range).
        commit_binary = summarize_binary and _diff_is_binary(git_root, commit_args, env)
        diff_cmd = [
            "git",
            "-C",
            str(git_root),
            "diff",
            "--stat" if commit_binary else "-p",
            *commit_args,
        ]
        try:
            show_result = subprocess.run(
                diff_cmd, capture_output=True, text=True, check=True, env=env
            )
            commit_diff_raw = show_result.stdout
        except subprocess.CalledProcessError as exc:
            # A failure here is expected ONLY for a parent-less (root/orphan)
            # commit, where `{sha}^` can't resolve. Any other failure is real
            # and must surface rather than silently degrade to an add-form,
            # rename-unaware diff.
            parent_exists = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(git_root),
                        "rev-parse",
                        "--verify",
                        "--quiet",
                        f"{sha}^",
                    ],
                    capture_output=True,
                    env=env,
                ).returncode
                == 0
            )
            if parent_exists:
                raise ValueError(f"Could not retrieve diff for commit {sha!r}") from exc
            commit_diff_raw = _root_commit_diff(
                git_root, sha, commit_path, env, summarize_binary=summarize_binary
            )
        commit_diff = commit_diff_raw.lstrip("\n")
        if (
            not commit_diff
            and old_at_parent is not None
            and old_at_parent != commit_path
        ):
            # Pure rename with byte-identical content: the two-blob diff
            # of identical blobs is empty, so the rename would otherwise be
            # invisible. Synthesize a marker rather than emit an empty diff (#683).
            commit_diff = (
                f"{old_at_parent} => {commit_path} (renamed, no content change)\n"
            )
        diffs.append(
            CommitDiff(
                sha=sha,
                short_sha=short_sha,
                timestamp=timestamp,
                message=message,
                diff=_truncate_diff(commit_diff),
            )
        )
    return diffs


# ---------------------------------------------------------------------------
# Revision reads (#1137)
# ---------------------------------------------------------------------------

# Git's file mode for a symlink.  The blob behind one holds the link *target*
# as text, not the linked note's content, so a revision read refuses rather
# than handing back a path string as though it were a note.
_SYMLINK_MODE = "120000"

# Rename-detection threshold for the revision walk.  Matches
# ``resolve_path_at_ref``'s 30% (#338): at git's 50% default, a rename that
# also rewrote half the note reports as an unrelated add plus delete, which
# this walk is obliged to refuse.
_RENAME_THRESHOLD = "--find-renames=30"

# Rename-detection is capped per commit; a commit renaming more files than the
# cap degrades every rename to add+delete, which the walk refuses.  Pinning the
# value keeps that boundary a property of this code rather than of whatever the
# operator has in their git config.
_RENAME_LIMIT = "diff.renameLimit=2000"


def _revision_walk_output(
    git_root: Path, ref: str, cur_rel: str, env: dict[str, str] | None
) -> str:
    """Return git's ``--name-status`` record stream for *cur_rel* over ``ref..HEAD``.

    ``-m --first-parent`` is load-bearing, not decoration: a rename performed
    *while resolving a merge* appears in no parent's diff, so a plain walk
    returns no records at all and the caller would fall through to trusting
    today's path at an old revision.  Restricting to the first parent and
    asking for its diff surfaces that rename as an ordinary ``R`` record.

    ``-z`` frames paths by NUL, so a path containing a space, a quote or a
    newline survives intact and no ``core.quotePath`` unescaping is needed.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                _RENAME_LIMIT,
                "-C",
                str(git_root),
                "log",
                "-m",
                "--first-parent",
                "--follow",
                "--name-status",
                "-z",
                _RENAME_THRESHOLD,
                f"--format={_HISTORY_SENTINEL}%H",
                f"{ref}..HEAD",
                "--",
                cur_rel,
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"Could not read history for revision {ref!r}: {(exc.stderr or '').strip()}"
        ) from exc
    return result.stdout


def _iter_name_status(raw: str) -> Iterator[tuple[str, list[str]]]:
    """Yield ``(status, paths)`` from a ``-z --name-status`` stream, newest first.

    The stream interleaves ``\\x1e<sha>`` commit markers with records; a record
    is a status token followed by one path, or two for ``R``/``C``.  Only the
    status token is stripped of the newline git emits after a commit header —
    path tokens are yielded byte-for-byte, since a leading newline is legal in
    a filename.
    """
    tokens = raw.split("\0")
    i = 0
    while i < len(tokens):
        status = tokens[i].lstrip("\n")
        if not status or status.startswith(_HISTORY_SENTINEL):
            i += 1
            continue
        wanted = 2 if status[0] in ("R", "C") else 1
        paths = tokens[i + 1 : i + 1 + wanted]
        if len(paths) < wanted or not all(paths):
            # A short record, or one whose path slot is the stream's trailing
            # empty token: either way the record is incomplete, and half a
            # rename is not something to reason about.
            return
        yield status, paths
        i += 1 + wanted


def _path_at_ref(raw: str, cur_rel: str, ref: str) -> str:
    """Resolve the path *cur_rel* had at *ref*, or refuse.

    Walks git's own records newest-first, carrying the path currently being
    tracked.  Every outcome maps to something git recorded:

    * ``R old new`` where *new* is tracked — the note was renamed; keep going
      from *old*.
    * ``R old new`` where *new* is tracked — the note was renamed; keep going
      from *old*.
    * ``A`` on the tracked path — git is saying the file there was created
      within the range, so the note the caller named did not exist at *ref*.
    * ``C source new`` where *new* is tracked — git found the note's content
      copied from somewhere else rather than carried forward, which is a birth
      too, not a lineage.
    * ``D`` / ``M`` / ``T`` — the note changed or was removed but its identity
      is intact; keep going.
    * Anything else — not a record class this walk understands, and an
      unrecognised record is not evidence of continuity.

    Raises:
        ValueError: When the records do not connect the note to *ref*.
    """
    tracked = cur_rel
    for status, paths in _iter_name_status(raw):
        if status.startswith("R") and paths[-1] == tracked:
            tracked = paths[0]
        elif status.startswith("A") and paths[0] == tracked:
            raise ValueError(
                f"{tracked!r} was created after revision {ref!r}, so the note now "
                "at that path did not exist at that revision. Anything stored "
                "under that name then belongs to a different note; use "
                "'get_history' to find a revision this note actually has."
            )
        elif status.startswith("C") and paths[-1] == tracked:
            raise ValueError(
                f"{tracked!r} appears as a copy of {paths[0]!r} rather than as a "
                "continuation of it, so the note now at that path did not exist "
                f"at that revision. Read {paths[0]!r} at {ref!r} instead if the "
                "content you want is the one it was copied from."
            )
        elif status[0] not in ("D", "M", "T") or paths[0] != tracked:
            raise ValueError(
                f"Cannot follow {cur_rel!r} back to revision {ref!r}: git reports "
                f"{status!r} for {paths[0]!r}, which does not establish that the "
                "note is the same one. Use 'get_history' and 'get_diff' to inspect "
                "the range directly."
            )
    return tracked


def _require_ancestor(git_root: Path, ref: str, env: dict[str, str] | None) -> None:
    """Refuse a revision that is not an ancestor of HEAD.

    ``ref..HEAD`` enumerates what is reachable from HEAD but not from *ref*.
    When *ref* sits on a discarded branch — after a rebase, a reset, or a SHA
    copied from elsewhere — that set is not "the commits since *ref*", and the
    walk's reasoning about creations and renames does not hold.
    """
    result = subprocess.run(
        ["git", "-C", str(git_root), "merge-base", "--is-ancestor", ref, "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise ValueError(
            f"Revision {ref!r} is not an ancestor of the current HEAD (it may be "
            "unknown, or on history that was rebased away), so this note's path "
            "at that revision cannot be established."
        )


def _require_tracked(
    git_root: Path, path: Path, cur_rel: str, env: dict[str, str] | None
) -> None:
    """Refuse when the note on disk is not the one git has been recording.

    A note committed, deleted, and then recreated **untracked** leaves the walk
    with a ``D`` record and no ``A``: nothing marks the current file's birth,
    because git never saw it. Without this check the walk would return the
    deleted note's content under the new note's name.  A path absent from disk
    is the recover-a-deleted-note case and is left to the walk.
    """
    if not path.exists():
        return
    result = subprocess.run(
        ["git", "-C", str(git_root), "ls-files", "--", cur_rel],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if not result.stdout.strip():
        raise ValueError(
            f"{cur_rel!r} is not tracked by git, so it has no history: content "
            "stored under that name at an earlier revision belongs to a "
            "different file."
        )


def _blob_text(
    git_root: Path, spec: str, max_bytes: int, env: dict[str, str] | None
) -> str:
    """Return the text of the blob named by *spec* (``<ref>:<path>``).

    Checks the object's type and size before materialising it, the revision
    analogue of the ``stat()`` :meth:`DocumentManager.read` does — so an
    oversized historical note is refused rather than buffered whole.  The blob
    is read as bytes and decoded explicitly: a note that is not valid UTF-8 at
    that revision is a caller-visible ``ValueError``, not a decode error
    escaping from the subprocess layer.

    Raises:
        ValueError: If the object is missing, is a symlink, exceeds
            *max_bytes*, or does not decode as UTF-8.
    """
    probe = subprocess.run(
        ["git", "-C", str(git_root), "cat-file", "-s", spec],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if probe.returncode != 0:
        raise ValueError(
            f"Not present at that revision: {spec!r} ({(probe.stderr or '').strip()})"
        )
    size = int(probe.stdout.strip() or 0)
    if 0 < max_bytes < size:
        raise ValueError(
            f"Note is {size} bytes at that revision, over the "
            f"{max_bytes}-byte MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES limit."
        )
    blob = subprocess.run(
        ["git", "-C", str(git_root), "cat-file", "blob", spec],
        capture_output=True,
        check=False,
        env=env,
    )
    if blob.returncode != 0:
        raise ValueError(f"Could not read {spec!r} from git history.")
    try:
        return blob.stdout.decode()
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Content at {spec!r} is not valid UTF-8, so it cannot be returned "
            "as a note."
        ) from exc


def _require_regular_file(
    git_root: Path, ref: str, repo_rel: str, env: dict[str, str] | None
) -> None:
    """Refuse a path that git stored as a symlink at *ref*.

    Git keeps a symlink's *target path* in its blob, so reading one would hand
    back a filename where the caller expects note text; a read on disk would
    have followed the link instead.
    """
    result = subprocess.run(
        ["git", "-C", str(git_root), "ls-tree", ref, "--", repo_rel],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.stdout.startswith(_SYMLINK_MODE):
        raise ValueError(
            f"{repo_rel!r} was a symlink at that revision; git stores its target "
            "path rather than note content, so there is nothing to return."
        )


def get_file_at_ref(
    git_root: Path | None,
    query: RevisionQuery,
    *,
    token: str | None,
    username: str,
) -> RevisionContent:
    """Return a note's content as it stood at a revision, rename-aware.

    Resolution is by *note*, not by path: the caller passes the path the note
    has today and git's own add/rename records are walked back to *ref*.
    Where those records do not connect the two — a name reused by a different
    note, a delete-and-recreate, a rename git cannot detect — this raises
    rather than returning content that belongs to some other note.  A caller
    about to write the result back must never be handed a plausible wrong
    answer.

    Args:
        git_root: Pre-resolved git repository root; ``None`` raises, since an
            empty result is indistinguishable from "the note was empty then".
        query: The note, revision, and read cap being asked for.
        token: Git credential token, forwarded to the subprocess environment.
        username: Git username for credential forwarding.

    Returns:
        A :class:`~markdown_vault_mcp.types.RevisionContent` carrying the
        content and the vault-relative path the note had at that revision.

    Raises:
        ValueError: When the vault is not git-backed, the revision is unusable,
            or the note's identity cannot be traced to it.
    """
    if git_root is None:
        raise ValueError(
            "Reading a note at a revision requires a git-backed vault; this "
            "vault's source directory is not inside a git repository."
        )
    cur_rel = _repo_relative(git_root, query.path)
    env = git_env(token, username)
    try:
        _require_ancestor(git_root, query.ref, env)
        _require_tracked(git_root, query.path, cur_rel, env)
        historical = _path_at_ref(
            _revision_walk_output(git_root, query.ref, cur_rel, env),
            cur_rel,
            query.ref,
        )
        _require_regular_file(git_root, query.ref, historical, env)
        content = _blob_text(
            git_root, f"{query.ref}:{historical}", query.max_bytes, env
        )
    finally:
        cleanup_git_env(env)

    prefix = _vault_prefix(git_root, query.repo_path)
    return RevisionContent(
        path=_strip_prefix(cur_rel, prefix),
        historical_path=_strip_prefix(historical, prefix),
        revision=query.ref,
        content=content,
    )


def committed_revision(
    git_root: Path | None,
    path: Path,
    *,
    token: str | None,
    username: str,
) -> str | None:
    """Return the newest commit whose blob for *path* is what is on disk now.

    The breadcrumb an overwrite hands back has to name a commit that actually
    holds the content being replaced, so both halves are checked: the note's
    latest commit, and that the working tree still matches it.  The comparison
    is against that **named commit** rather than ``HEAD`` — the commit worker
    runs on its own thread and does not hold the vault's write lock, so a
    ``HEAD``-relative check can answer about a commit this function never
    named.

    Returns:
        The commit SHA, or ``None`` when the note has no commit yet, when the
        working tree has moved on from its newest one, or when git cannot be
        consulted.  ``None`` means "no breadcrumb", never "no such commit".
    """
    if git_root is None:
        return None
    env = git_env(token, username)
    try:
        rel = _repo_relative(git_root, path)
        found = subprocess.run(
            ["git", "-C", str(git_root), "log", "-1", "--format=%H", "--", rel],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        sha = found.stdout.strip()
        if found.returncode != 0 or not sha:
            return None
        tracked = subprocess.run(
            ["git", "-C", str(git_root), "ls-files", "--", rel],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if not tracked.stdout.strip():
            # git diff is blind to an untracked file, so without this a note
            # recreated after a committed delete would "match" the commit that
            # deleted it — a breadcrumb pointing at content that is not there.
            return None
        clean = subprocess.run(
            ["git", "-C", str(git_root), "diff", "--quiet", sha, "--", rel],
            capture_output=True,
            check=False,
            env=env,
        )
        return sha if clean.returncode == 0 else None
    finally:
        cleanup_git_env(env)


def _repo_relative(git_root: Path, path: Path) -> str:
    """Return *path* as git sees it: relative to the repository root, posix.

    Git reports and accepts repository-root-relative paths, while the vault's
    own paths are relative to the vault root — which may sit below the git
    root.  Comparing the two forms directly is the defect this function
    exists to prevent: every rename and creation test in the walk would
    silently never match.

    Resolving also follows symlinks, deliberately: an on-disk read of a
    symlinked note returns the target's content, so a revision read of one
    reports the target's history rather than a second, divergent answer.

    Raises:
        ValueError: If *path* resolves outside the repository.
    """
    try:
        return path.resolve().relative_to(git_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"{path.name!r} resolves outside the git repository, so it has no "
            "history there."
        ) from exc


def _strip_prefix(repo_rel: str, prefix: str) -> str:
    """Convert a repository-relative path back to a vault-relative one."""
    return (
        repo_rel[len(prefix) :] if prefix and repo_rel.startswith(prefix) else repo_rel
    )
