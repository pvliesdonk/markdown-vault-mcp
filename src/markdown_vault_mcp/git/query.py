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

# Git's well-known empty-tree SHA: a real object in every repository.
# Used to diff a parent-less (root/orphan) commit against "nothing".
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Diff payloads larger than this are truncated with a byte-count marker.
_DIFF_MAX_BYTES = 50 * 1024  # 50 KB


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
    elif is_dir:
        # directory scope: no --follow (git rejects it for anything but a
        # single file); --name-only so paths_changed carries the subtree files
        # each commit touched. git already filters the name-only output to the
        # <dir> pathspec, so no sibling paths leak in and no extra filtering is
        # needed here.
        cmd += ["--name-only", "--", str(path)]
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
        if (path is None or is_dir) and len(lines) > 1:
            # vault-wide or directory query: strip the vault prefix to get
            # vault-relative paths. git already scoped a directory query's
            # --name-only output to the folder pathspec.
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
