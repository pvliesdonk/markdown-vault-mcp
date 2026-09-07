"""Rebase-conflict resolution mechanics.

Contract: every function here assumes the CALLER already holds
``GitWriteStrategy._lock`` and that a rebase is in progress (or being driven) on
``git_root``. These functions take no lock of their own -- serialization is the
strategy's responsibility. Subprocess calls are module-qualified
(``subprocess.run``, or ``run_git_capturing`` from ``git/_run.py``) so the test
suite's global ``subprocess.run`` monkeypatch still intercepts them.
"""

from __future__ import annotations

import datetime
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter

from markdown_vault_mcp.git._run import literal_pathspec, redact, run_git_capturing
from markdown_vault_mcp.git.types import (
    PULL_REASON_CONFLICT_RESOLUTION_FAILED,
    PullResult,
)
from markdown_vault_mcp.utils.text import read_text_utf8

if TYPE_CHECKING:
    import collections.abc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectionRules:
    """Conflict handling for files that are projections of vault state (#1395).

    A note's two versions need a human, so a conflicting note keeps upstream
    and saves the local version as a ``.conflict-mcp-*`` sibling. A file the
    server regenerates from vault state — an OKF ``index.md`` / ``log.md``
    under ``OKF_WRITE`` — has no such reader, and the sibling policy turned
    every collision into a stray concept file plus ``conflict_with`` keys the
    spec forbids on an index. Such a path is instead resolved *in place*:
    upstream wins, :attr:`merge` may fold the local side back in, and no
    sibling or marker is written.

    The rules are injected by the owner (the vault) so this package keeps no
    domain knowledge; ``None`` keeps every path on the sibling policy.

    Attributes:
        is_projection: ``(repo_root, rel_path)`` → whether that path is a
            projection. The repository *toplevel* travels with the path
            because conflict paths are relative to it while a vault may be a
            subdirectory of the repository, so the owner can answer from
            neither alone.
        merge: ``(rel_path, upstream_text, local_text)`` → the text to keep,
            or ``None`` to keep upstream verbatim.
    """

    is_projection: collections.abc.Callable[[Path, str], bool]
    merge: collections.abc.Callable[[str, str, str], str | None]


@dataclass(frozen=True)
class ConflictResolution:
    """What one resolution pass did, split by how each path was handled.

    Attributes:
        saved: ``(relative_path, mcp_content)`` for the paths owed a
            ``.conflict-mcp-*`` sibling.
        projections: Paths resolved in place instead (:class:`ProjectionRules`).
            The caller needs these separately because an abort undoes them:
            the merged ``log.md`` and the upstream ``index.md`` go back to
            their pre-pull local content, and committing the notes' siblings
            on top of that would report a partial resolution as a success.
    """

    saved: list[tuple[str, str]]
    projections: tuple[str, ...]


def repo_root(git_root: Path, env: dict[str, str] | None) -> Path:
    """Return the repository toplevel that git's own paths are relative to.

    ``git diff --name-only`` reports repository-relative paths whatever
    directory it was run from, while the working tree handed to this package
    may be a subdirectory of the repository — the ``force_*`` entry points
    pass the configured vault, not the toplevel. Joining a repository-relative
    path onto the wrong root silently answers "inside the vault" for every
    path (#1395). Falls back to *git_root* when the toplevel cannot be read,
    which is the pre-existing assumption.
    """
    proc = run_git_capturing(git_root, "rev-parse", "--show-toplevel", env=env)
    if proc.returncode != 0:
        logger.debug("git_toplevel_unresolved path=%s", git_root)
        return git_root
    return Path(proc.stdout.strip() or git_root)


def _local_version(root: str, rel_path: str, env: dict[str, str] | None) -> str | None:
    """Return the MCP version of a conflicting path, or ``None`` if unreadable.

    Read from ``REBASE_HEAD`` — the commit being replayed — before the
    working tree takes upstream's side. ``None`` means nothing can be saved
    for this path, which is logged and then left to the caller.
    """
    show_result = subprocess.run(
        ["git", "-C", root, "show", f"REBASE_HEAD:{rel_path}"],
        capture_output=True,
        text=True,
        env=env,
    )
    if show_result.returncode == 0:
        return str(show_result.stdout)
    logger.warning(
        "Git pull: could not read MCP version of %s, skipping conflict file",
        rel_path,
    )
    return None


def _accept_upstream(root: str, rel_path: str, env: dict[str, str] | None) -> None:
    """Take the upstream side of *rel_path* and stage it.

    During a rebase ``--ours`` is the branch being rebased onto (upstream)
    and ``--theirs`` the commit being replayed (the local MCP commit).
    """
    subprocess.run(
        ["git", "-C", root, "checkout", "--ours", "--", literal_pathspec(rel_path)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    subprocess.run(
        ["git", "-C", root, "add", "--", literal_pathspec(rel_path)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def _resolve_projection(
    top: Path,
    rel_path: str,
    local_text: str | None,
    rules: ProjectionRules,
    env: dict[str, str] | None,
) -> None:
    """Resolve a projection in place: upstream already staged, maybe merged.

    Called after :func:`_accept_upstream`, so the working tree holds the
    upstream version. *top* is the repository toplevel, which the
    repository-relative *rel_path* is resolved against. When the rules produce a merge that differs from it,
    the merged text replaces it and is staged; either way nothing is saved
    for a sibling.
    """
    rewritten = False
    if local_text is not None:
        upstream_text = read_text_utf8(top / rel_path)
        merged = rules.merge(rel_path, upstream_text, local_text)
        if merged is not None and merged != upstream_text:
            (top / rel_path).write_text(merged, encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(top), "add", "--", literal_pathspec(rel_path)],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            rewritten = True
    # ``merged`` reports what happened to the file, not whether the rules
    # returned something: the log merge always returns text, and returns it
    # unchanged when the local side added nothing.
    logger.info("conflict_projection_resolved path=%s merged=%s", rel_path, rewritten)


def resolve_rebase_conflicts(
    git_root: Path,
    env: dict[str, str] | None,
    projection: ProjectionRules | None = None,
) -> ConflictResolution:
    """Resolve rebase conflicts by accepting theirs and saving ours.

    Called when ``git rebase <ref>`` (the resolved ``origin/<branch>``
    remote-tracking ref) has stopped at a conflict.
    For each conflicting file, saves the MCP version from
    ``REBASE_HEAD``, then accepts the upstream version via
    ``git checkout --ours``.  Continues the rebase, looping if
    multiple commits conflict.

    A path *projection* recognises is resolved in place instead
    (:class:`ProjectionRules`): upstream is kept, possibly merged with the
    local side, and nothing is saved for it.

    Returns:
        A :class:`ConflictResolution`: the paths owed a sibling, and the
        paths resolved in place.  Either may be partial (not all commits
        resolved) if the iteration limit is hit; the caller is responsible
        for aborting any in-progress rebase before writing the conflict
        files, and for the fact that an abort undoes the in-place
        resolutions.
    """
    # Every path below is repository-relative, whichever directory git ran
    # from, while the caller's working tree may sit under the repository:
    # addressing them from anywhere else builds ``vault/vault/log.md``.
    top = repo_root(git_root, env)
    root = str(top)
    saved: dict[str, str] = {}
    projections: list[str] = []
    max_iterations = 50  # safety limit

    for _ in range(max_iterations):
        # Identify conflicting files.  ``-z`` frames the paths by NUL: under
        # git's default ``core.quotePath`` a non-ASCII name comes back
        # octal-escaped inside double quotes, and every command below is then
        # handed a path no longer naming the conflicting file.
        result = subprocess.run(
            ["git", "-C", root, "diff", "--name-only", "-z", "--diff-filter=U"],
            capture_output=True,
            text=True,
            env=env,
        )
        conflicting = [f for f in result.stdout.split("\0") if f]
        if not conflicting:
            # No unmerged paths: the rebase stopped for a reason this
            # resolver cannot fix (missing committer identity, a hook, a
            # lock).  Say so — the old silent ``break`` fell through to the
            # "loop exceeded" line and sent operators hunting for a merge
            # conflict that did not exist (#1362).  DEBUG for the reason
            # given at the loop-cap line below: this repeats every pull
            # cycle while the divergence stands; the rebase's own stderr
            # reaches the one-time transition line as its ``cause=``.
            logger.debug(
                "Git pull: rebase stopped with no unmerged paths; not a "
                "content conflict"
            )
            break

        for rel_path in conflicting:
            local_text = _local_version(root, rel_path, env)
            _accept_upstream(root, rel_path, env)
            if projection is not None and projection.is_projection(top, rel_path):
                _resolve_projection(top, rel_path, local_text, projection, env)
                if rel_path not in projections:
                    projections.append(rel_path)
            elif local_text is not None:
                saved[rel_path] = local_text

        # Continue the rebase.  If another commit conflicts, the loop
        # iterates again.  ``--no-edit`` avoids opening an editor for
        # any automatically generated merge messages.
        cont = subprocess.run(
            ["git", "-C", root, "rebase", "--continue"],
            capture_output=True,
            text=True,
            env={**(env or {}), "GIT_EDITOR": "true"},
        )
        if cont.returncode == 0:
            # Rebase completed successfully.
            return ConflictResolution(list(saved.items()), tuple(projections))
        # returncode != 0 means the next commit also has conflicts —
        # loop around and resolve again.

    # Exhausted iterations or no conflicting files after non-zero continue.
    # DEBUG, not ERROR (#1287): a clone stuck on the same divergence hits this
    # cap on every pull cycle, and that repetition is what let the reported
    # incident scroll past unnoticed.  The transition into the unsynced state
    # is logged once, loudly, by SyncHealthTracker; this line keeps the detail
    # for whoever is diagnosing it.
    logger.debug(
        "Git pull: conflict resolution loop exceeded %d iterations", max_iterations
    )
    return ConflictResolution(list(saved.items()), tuple(projections))


def resolve_conflicts_safely(
    git_root: Path,
    env: dict[str, str] | None,
    from_sha: str,
    *,
    token: str | None,
    projection: ProjectionRules | None = None,
    resolve_fn: collections.abc.Callable[
        [Path, dict[str, str] | None, ProjectionRules | None],
        ConflictResolution,
    ]
    | None = None,
) -> ConflictResolution | PullResult:
    """Defensive wrapper around :func:`resolve_rebase_conflicts`.

    Catches the case where conflict resolution itself raises mid-loop,
    leaving the repository in a half-rebased state.  On exception:
    logs at ERROR with traceback, runs ``git rebase --abort`` defensively
    (logging WARNING if the abort itself fails — but not failing the
    overall recovery), and returns an early-exit ``PullResult`` so the
    caller can surface ``conflict_resolution_failed`` without a leftover
    ``rebase-merge`` directory.

    Args:
        git_root: Working-tree root.
        env: Optional GIT_ASKPASS environment.
        from_sha: HEAD SHA captured by the caller before the rebase
            attempt; reused on the failure path so the returned result
            has consistent ``from_sha == to_sha`` semantics.
        token: PAT used for redacting sensitive text in log messages.
        projection: Rules for paths resolved in place rather than saved
            for a sibling (:class:`ProjectionRules`), or ``None``.
        resolve_fn: Optional override for the conflict-resolution
            callable.  Defaults to :func:`resolve_rebase_conflicts`
            (resolved from this module's globals at call time, so tests
            monkeypatch ``conflict.resolve_rebase_conflicts`` directly;
            #893 removed the strategy-level delegation shims).

    Returns:
        The :class:`ConflictResolution` from :func:`resolve_rebase_conflicts`
        on success, or a :class:`PullResult` the caller should return
        immediately on failure. One value rather than a pair of optionals,
        so the caller narrows by type instead of asserting which half is
        populated.
    """
    _resolve = resolve_fn if resolve_fn is not None else resolve_rebase_conflicts
    try:
        resolution = _resolve(git_root, env, projection)
    except Exception:
        logger.error(
            "Git force_pull: conflict resolution raised — aborting rebase",
            exc_info=True,
        )
        abort_proc = run_git_capturing(git_root, "rebase", "--abort", env=env)
        if abort_proc.returncode != 0:
            logger.warning(
                "Git force_pull: defensive `git rebase --abort` "
                "after conflict-resolution failure also failed: %s",
                redact((abort_proc.stderr or "").strip(), token),
            )
        return PullResult.head_unchanged_failure(
            from_sha, PULL_REASON_CONFLICT_RESOLUTION_FAILED
        )
    return resolution


def rebase_in_progress(
    git_root: Path,
    env: dict[str, str] | None,
    *,
    token: str | None,
) -> bool:
    """Return True if a rebase is in progress in this working tree.

    Reliable signal: the existence of ``.git/rebase-merge`` or
    ``.git/rebase-apply`` directories.  ``REBASE_HEAD`` ref is NOT
    reliable — git keeps it around after a successful ``rebase
    --continue`` for use as a backup reference, so its mere existence
    does not mean a rebase is in flight.  Resolves ``GIT_DIR`` via
    ``rev-parse`` so this works inside worktrees and submodules where
    the directory is not the repo's literal ``.git``.

    On ``rev-parse --git-dir`` failure (which means we genuinely cannot
    tell), this conservatively returns ``True`` so the caller's abort
    runs — abort on a clean tree fails loudly (and is logged), but
    failing to abort a real in-progress rebase would leave the repo
    wedged for every subsequent ``force_pull``.  The underlying
    rev-parse failure is logged at ERROR with token-redacted stderr.

    Args:
        git_root: Working-tree root.
        env: Optional GIT_ASKPASS environment.
        token: PAT used for redacting sensitive text in log messages.

    Returns:
        ``True`` if a rebase appears to be in progress (or if we cannot
        tell); ``False`` only when ``rev-parse --git-dir`` succeeded
        AND no ``rebase-merge`` / ``rebase-apply`` directory exists.
    """
    git_dir_proc = run_git_capturing(git_root, "rev-parse", "--git-dir", env=env)
    if git_dir_proc.returncode != 0:
        logger.error(
            "Git force_pull: `git rev-parse --git-dir` failed; "
            "conservatively assuming rebase is in progress: %s",
            redact((git_dir_proc.stderr or "").strip(), token),
        )
        return True

    git_dir = Path(git_dir_proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = git_root / git_dir
    return (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir()


def abort_in_progress_rebase(
    git_root: Path,
    env: dict[str, str] | None,
    *,
    token: str | None,
) -> bool:
    """Run ``git rebase --abort`` synchronously.

    Args:
        git_root: Working-tree root.
        env: Optional GIT_ASKPASS environment.
        token: PAT used for redacting sensitive text in log messages.

    Returns:
        ``True`` if abort succeeded; ``False`` if abort itself failed
        (in which case the caller should bail out — the working tree
        may be inconsistent).  Failure is logged at ERROR with
        token-redacted stderr.
    """
    abort_proc = run_git_capturing(git_root, "rebase", "--abort", env=env)
    if abort_proc.returncode != 0:
        logger.error(
            "Git force_pull: failed to abort rebase: %s",
            redact((abort_proc.stderr or "").strip(), token),
        )
        return False
    return True


def restore_upstream_paths(
    git_root: Path,
    env: dict[str, str] | None,
    saved: list[tuple[str, str]],
    ref: str,
    *,
    token: str | None,
) -> list[tuple[str, str]]:
    """Restore upstream content for each conflict path after rebase abort.

    After ``git rebase --abort`` the working tree reverts to the
    pre-rebase MCP state — every file in ``saved`` again contains the
    MCP version, not the upstream version.  For each path we run
    ``git checkout <ref> -- <path>`` to bring back the upstream
    bytes so :func:`write_conflict_files` reads the right side
    (canonical = upstream, sibling = MCP).

    On per-path checkout failure (file deleted upstream, permission
    error, etc.), the path is DROPPED from the returned list — writing
    a sibling that contains the same MCP bytes as the canonical path
    would defeat the "remote wins, local preserved" invariant.  Each
    drop is logged at ERROR with the path name and token-redacted
    stderr so an operator can investigate.

    Args:
        git_root: Working-tree root.
        env: Optional GIT_ASKPASS environment.
        saved: List of ``(rel_path, mcp_content)`` tuples returned by
            :func:`resolve_conflicts_safely`.
        ref: Remote-tracking ref to restore from (``origin/<branch>``).
        token: PAT used for redacting sensitive text in log messages.

    Returns:
        Subset of ``saved`` whose upstream restore succeeded.  May be
        empty if every checkout failed.
    """
    restored: list[tuple[str, str]] = []
    for rel_path, mcp_content in saved:
        checkout_proc = run_git_capturing(
            git_root,
            "checkout",
            ref,
            "--",
            literal_pathspec(rel_path),
            env=env,
        )
        if checkout_proc.returncode != 0:
            logger.error(
                "Git force_pull: failed to restore upstream "
                "version of %r after rebase abort; dropping it "
                "from conflict siblings to avoid duplicate MCP "
                "content: %s",
                rel_path,
                redact((checkout_proc.stderr or "").strip(), token),
            )
            continue
        restored.append((rel_path, mcp_content))
    return restored


def write_conflict_files(
    git_root: Path,
    saved: list[tuple[str, str]],
    env: dict[str, str] | None,
    *,
    commit_name: str,
    commit_email: str,
    token: str | None,
) -> list[str] | None:
    """Write conflict files and add ``conflict_with`` frontmatter to both sides.

    For each ``(relative_path, content)`` in *saved*:

    1. Write the MCP version as ``<stem>.conflict-mcp-<timestamp><ext>``
       with ``conflict_with`` and ``conflict_date`` frontmatter.
    2. Merge ``conflict_with`` and ``conflict_date`` into the original
       file's existing frontmatter.  If the original cannot be read or
       rewritten (removed/inaccessible after the existence check, or not
       valid UTF-8), its update is skipped with a logged warning — the
       conflict sibling is still written and counted.

    Returns:
        List of conflict file relative paths that were written and
        committed.  Returns ``None`` when the final ``git commit`` step
        failed (nothing-to-commit, hook failure, signing failure, etc.)
        so callers can surface ``conflict_resolution_failed`` instead of
        implying a successful conflict-resolution commit.  Returns an
        empty list when *saved* is empty (nothing to do).
    """
    # Repository-relative paths again (see ``resolve_rebase_conflicts``): the
    # sibling belongs beside the conflicting file, not beside a repeat of its
    # path under the working tree.
    top = repo_root(git_root, env)
    root = str(top)
    now = datetime.datetime.now(tz=datetime.UTC)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    conflict_date = now.isoformat(timespec="seconds")
    written: list[str] = []
    # Originals we actually rewrote with conflict_with frontmatter. Only these
    # get staged into the conflict commit — an original whose update was skipped
    # (#662 OSError/UnicodeDecodeError guard) must NOT be re-staged here (#675).
    updated_originals: list[str] = []

    for rel_path, mcp_content in saved:
        original = Path(rel_path)
        conflict_name = f"{original.stem}.conflict-mcp-{timestamp}{original.suffix}"
        conflict_rel = str(original.parent / conflict_name)

        # --- Write conflict file (MCP version) ---
        try:
            post = frontmatter.loads(mcp_content)
        except Exception:
            # If frontmatter parsing fails, treat as plain content.
            logger.warning(
                "Git pull: failed to parse frontmatter for conflict file %s; treating as plain content",
                conflict_rel,
                exc_info=True,
            )
            post = frontmatter.Post(mcp_content)

        post.metadata["conflict_with"] = rel_path
        post.metadata["conflict_date"] = conflict_date

        conflict_abs = top / conflict_rel
        conflict_abs.parent.mkdir(parents=True, exist_ok=True)
        conflict_abs.write_text(frontmatter.dumps(post), encoding="utf-8")

        # --- Update original file with conflict_with frontmatter ---
        original_abs = top / rel_path
        if original_abs.exists():
            try:
                # Read once and reuse this content for the parse-failure
                # fallback below (the prior version re-read the file there). The
                # read_text and write_text both sit inside this try, so if the
                # original was removed/became inaccessible after the exists()
                # check (TOCTOU) or is not valid UTF-8, the error is caught below
                # and skips just this original's update instead of crashing the
                # whole pull.
                content = read_text_utf8(original_abs)
                try:
                    orig_post = frontmatter.loads(content)
                except Exception:
                    logger.warning(
                        "Git pull: failed to parse frontmatter for original file %s; treating as plain content",
                        rel_path,
                        exc_info=True,
                    )
                    orig_post = frontmatter.Post(content)
                orig_post.metadata["conflict_with"] = conflict_rel
                orig_post.metadata["conflict_date"] = conflict_date
                original_abs.write_text(frontmatter.dumps(orig_post), encoding="utf-8")
                updated_originals.append(rel_path)
            except (OSError, UnicodeDecodeError):
                # OSError: removed/inaccessible after exists() (TOCTOU), permission,
                # or a write-back failure. UnicodeDecodeError (a ValueError, not an
                # OSError): the original is not valid UTF-8, so we cannot read it as
                # text to merge frontmatter. Either way, skip just this original's
                # update; the conflict sibling is already written.
                logger.warning(
                    "Git pull: could not read or update original file %s with "
                    "conflict frontmatter (inaccessible, removed, or not UTF-8); "
                    "skipping its update",
                    rel_path,
                    exc_info=True,
                )

        written.append(conflict_rel)

    if not written:
        return written

    # Stage only the files we actually touched: originals we rewrote with
    # conflict_with frontmatter (NOT ones whose update was skipped — #675;
    # re-staging a skipped original here would needlessly re-add an untouched
    # file, or stage a *deletion* for a TOCTOU-removed one) plus the new
    # conflict siblings. A skipped original keeps whatever the rebase already
    # staged for it (the upstream version), which the pathspec-less commit
    # below still captures.
    paths_to_add = updated_originals + written
    subprocess.run(
        ["git", "-C", root, "add", "--", *map(literal_pathspec, paths_to_add)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    n = len(written)
    file_list = ", ".join(written)
    # Conflict resolution runs on the pull background thread, with no acting
    # Principal (#1160) — per-user attribution does not apply here.  Use the
    # static server identity directly.
    # NOTE: this commit is pathspec-less — it commits the whole staged index,
    # not just ``paths_to_add``. That is intentional: it also captures upstream
    # content already staged during conflict resolution (by the rebase's merge,
    # or by the ``checkout <ref>`` (``origin/<branch>``) restore on the
    # rebase-abort path). It
    # relies on the caller holding ``_lock`` so no unrelated change is staged.
    commit_result = subprocess.run(
        [
            "git",
            "-C",
            root,
            "-c",
            f"user.name={commit_name}",
            "-c",
            f"user.email={commit_email}",
            "commit",
            "-m",
            f"conflict: saved {n} MCP version(s) for manual reconciliation\n\n{file_list}",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if commit_result.returncode != 0:
        # DEBUG for the reason given at the loop-cap line above (#1287): this
        # outcome marks the clone unsynced, and the tracker logs that once.
        logger.debug(
            "Git pull: conflict commit failed (rc=%d): %s",
            commit_result.returncode,
            redact(
                (commit_result.stderr or commit_result.stdout or "").strip(),
                token,
            ),
        )
        return None

    return written
