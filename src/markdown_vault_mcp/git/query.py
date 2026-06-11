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

from markdown_vault_mcp.git._run import cleanup_git_env, git_env
from markdown_vault_mcp.types import CommitDiff, HistoryEntry

logger = logging.getLogger(__name__)


def _diff_is_binary(
    git_root: Path, diff_args: list[str], env: dict[str, str] | None
) -> bool:
    """True if git reports the diff target as binary.

    *diff_args* is the ref/path portion of a ``git diff`` invocation — either
    ``[f"{ref}..HEAD", "--", path_str]`` (no rename) or
    ``[f"{ref}:{old_path}", f"HEAD:{cur_rel}"]`` (rename-recovered). ``git diff
    --numstat`` prints ``-\\t-\\t<path>`` for binary and real counts for text;
    empty output (no change) → non-binary.
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
) -> list[HistoryEntry]:
    """Return commits that touched *path* (or the whole vault).

    Args:
        git_root: Pre-resolved git repository root, or ``None`` if the vault
            is not inside a git repository (returns ``[]`` immediately).
        repo_path: Absolute path of the vault root.  Used to compute the
            vault-relative prefix when the git root is a parent of the vault.
        path: Absolute path of the file to filter on, or ``None`` for the
            entire vault.
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

    # Compute vault-relative prefix for normalising --name-only output.
    # When the git root is a parent of repo_path, git reports paths
    # relative to the git root (e.g. "vault/note.md").  We strip the
    # leading prefix so callers always receive vault-relative paths.
    # Resolve repo_path to handle symlinks: git rev-parse --show-toplevel
    # always returns the real (resolved) path, so we must match it.
    try:
        vault_rel = repo_path.resolve().relative_to(git_root)
    except ValueError:
        vault_rel = Path()
    vault_prefix = "" if vault_rel == Path() else vault_rel.as_posix() + "/"

    # \x1e (ASCII Record Separator) is the sentinel used to split commit
    # blocks in the output — it cannot appear in filenames or commit messages.
    _SENTINEL = "\x1e"
    cmd = [
        "git",
        "-C",
        str(git_root),
        "log",
        f"--format={_SENTINEL}%H%x00%h%x00%aI%x00%aN <%aE>%x00%s",
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
    else:
        cmd += ["--follow", "--", str(path)]

    env = git_env(token, username)
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
    finally:
        cleanup_git_env(env)

    entries: list[HistoryEntry] = []
    raw = result.stdout
    if not raw.strip():
        return []
    # Split on the sentinel we embedded at the start of each format line.
    # The first element will be empty (output starts with sentinel), so we
    # skip it.  Each remaining block is: header_line\nfile1\nfile2\n
    blocks = raw.split(_SENTINEL)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if not lines:
            continue
        header = lines[0]
        parts = header.split("\x00")
        if len(parts) < 5:
            continue
        sha, short_sha, timestamp, author, message = parts[:5]
        paths_changed: list[str] = []
        if path is None and len(lines) > 1:
            # vault-wide query: strip vault prefix to get vault-relative paths
            for ln in lines[1:]:
                ln = ln.strip()
                if not ln:
                    continue
                if vault_prefix and ln.startswith(vault_prefix):
                    ln = ln[len(vault_prefix) :]
                paths_changed.append(ln)
        entries.append(
            HistoryEntry(
                sha=sha,
                short_sha=short_sha,
                timestamp=timestamp,
                author=author,
                message=message,
                paths_changed=paths_changed,
            )
        )
    return entries


def resolve_path_at_ref(
    git_root: Path,
    ref: str,
    cur_rel: str,
    env: dict[str, str] | None,
) -> str | None:
    """Return the path *cur_rel* had at *ref* via rename detection, else None."""
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
                "HEAD",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError:
        return None
    # Stream: <status>\0<path>\0  (R*/C* add a second path before the closing NUL).
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
    _DIFF_MAX_BYTES = 50 * 1024  # 50 KB

    if git_root is None:
        if per_commit:
            return []
        return ""

    path_str = str(path)
    env = git_env(token, username)
    try:
        if since_timestamp is not None:
            # Resolve the ISO timestamp to the most recent commit before it.
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
            ref = rev_result.stdout.strip()
            if not ref:
                return [] if per_commit else ""

        if ref is None:
            raise ValueError("Either 'ref' or 'since_timestamp' must be provided")

        if not per_commit:
            # Resolve the path-at-ref once so renames are handled uniformly for
            # binary detection, the --stat summary, and the full diff.
            try:
                cur_rel = path.resolve().relative_to(git_root).as_posix()
            except ValueError:
                cur_rel = None
            old_path = (
                resolve_path_at_ref(git_root, ref, cur_rel, env)
                if cur_rel is not None
                else None
            )
            if old_path is None or cur_rel is None or old_path == cur_rel:
                diff_args = [f"{ref}..HEAD", "--", path_str]
            else:
                diff_args = [f"{ref}:{old_path}", f"HEAD:{cur_rel}"]

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
            diff = result.stdout
            if len(diff.encode()) > _DIFF_MAX_BYTES:
                omitted = len(diff.encode()) - _DIFF_MAX_BYTES
                diff = diff.encode()[:_DIFF_MAX_BYTES].decode(errors="replace")
                diff += f"\n[diff truncated: {omitted} bytes omitted]"
            return diff

        # Binary attachments: per-commit patches are equally meaningless, so
        # detect binariness once up front (rename-aware, mirroring the
        # non-per-commit branch) and emit a --stat per commit below.
        try:
            cur_rel = path.resolve().relative_to(git_root).as_posix()
        except ValueError:
            cur_rel = None
        old_path = (
            resolve_path_at_ref(git_root, ref, cur_rel, env)
            if cur_rel is not None
            else None
        )
        if old_path is None or cur_rel is None or old_path == cur_rel:
            detect_args = [f"{ref}..HEAD", "--", path_str]
        else:
            detect_args = [f"{ref}:{old_path}", f"HEAD:{cur_rel}"]
        binary = summarize_binary and _diff_is_binary(git_root, detect_args, env)

        # per_commit=True: enumerate commits in range then show each.
        # Use --name-only with a sentinel so we can recover the path the
        # file had at each commit — critical for correct diffs across
        # renames (git show sha -- new.md returns nothing for pre-rename
        # commits; we must pass the old filename instead).
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

        diffs: list[CommitDiff] = []
        for block in log_result.stdout.split(_PC_SENTINEL):
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            if not lines:
                continue
            parts = lines[0].split("\x00")
            if len(parts) < 4:
                continue
            sha, short_sha, timestamp, message = parts[:4]
            # Recover the path the file had at this specific commit.
            # With --follow, this will be the old name for pre-rename commits.
            commit_path = next((ln.strip() for ln in lines[1:] if ln.strip()), path_str)
            # NOTE: for a renamed binary the per-commit `git show --stat
            # -- commit_path` pathspec defeats git's rename pairing and renders a
            # text-style stat instead of `Bin … bytes` — known limitation, see #683.
            # The non-per-commit path is rename-aware.
            show_cmd = [
                "git",
                "-C",
                str(git_root),
                "show",
                "--format=",
                "--stat" if binary else "-p",
                sha,
                "--",
                commit_path,
            ]
            try:
                show_result = subprocess.run(
                    show_cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                )
            except subprocess.CalledProcessError as exc:
                raise ValueError(f"Could not retrieve diff for commit {sha!r}") from exc
            commit_diff = show_result.stdout.lstrip("\n")
            if len(commit_diff.encode()) > _DIFF_MAX_BYTES:
                omitted = len(commit_diff.encode()) - _DIFF_MAX_BYTES
                commit_diff = commit_diff.encode()[:_DIFF_MAX_BYTES].decode(
                    errors="replace"
                )
                commit_diff += f"\n[diff truncated: {omitted} bytes omitted]"
            diffs.append(
                CommitDiff(
                    sha=sha,
                    short_sha=short_sha,
                    timestamp=timestamp,
                    message=message,
                    diff=commit_diff,
                )
            )
        return diffs
    finally:
        cleanup_git_env(env)
