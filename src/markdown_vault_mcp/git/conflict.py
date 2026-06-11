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
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter

from markdown_vault_mcp.git._run import redact, run_git_capturing
from markdown_vault_mcp.git.types import (
    PULL_REASON_CONFLICT_RESOLUTION_FAILED,
    PullResult,
)

if TYPE_CHECKING:
    import collections.abc

logger = logging.getLogger(__name__)


def resolve_rebase_conflicts(
    git_root: Path,
    env: dict[str, str] | None,
) -> list[tuple[str, str]]:
    """Resolve rebase conflicts by accepting theirs and saving ours.

    Called when ``git rebase @{upstream}`` has stopped at a conflict.
    For each conflicting file, saves the MCP version from
    ``REBASE_HEAD``, then accepts the upstream version via
    ``git checkout --ours``.  Continues the rebase, looping if
    multiple commits conflict.

    Returns:
        A list of ``(relative_path, saved_content)`` tuples for the
        files that had conflicts.  May be partial (not all commits
        resolved) if the iteration limit is hit; the caller is
        responsible for aborting any in-progress rebase before
        writing the conflict files.
    """
    root = str(git_root)
    saved: dict[str, str] = {}
    max_iterations = 50  # safety limit

    for _ in range(max_iterations):
        # Identify conflicting files.
        result = subprocess.run(
            ["git", "-C", root, "diff", "--name-only", "--diff-filter=U"],
            capture_output=True,
            text=True,
            env=env,
        )
        conflicting = [f for f in result.stdout.strip().splitlines() if f]
        if not conflicting:
            # No conflicts — rebase may have stopped for another reason.
            break

        for rel_path in conflicting:
            # Save the MCP version (the commit being rebased).
            show_result = subprocess.run(
                ["git", "-C", root, "show", f"REBASE_HEAD:{rel_path}"],
                capture_output=True,
                text=True,
                env=env,
            )
            if show_result.returncode == 0:
                saved[rel_path] = show_result.stdout
            else:
                logger.warning(
                    "Git pull: could not read MCP version of %s, skipping conflict file",
                    rel_path,
                )

            # Accept upstream's version.  During rebase, --ours is the
            # branch being rebased onto (upstream), --theirs is the
            # commit being replayed (our local MCP commit).
            subprocess.run(
                ["git", "-C", root, "checkout", "--ours", "--", rel_path],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            subprocess.run(
                ["git", "-C", root, "add", "--", rel_path],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )

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
            return list(saved.items())
        # returncode != 0 means the next commit also has conflicts —
        # loop around and resolve again.

    # Exhausted iterations or no conflicting files after non-zero continue.
    logger.error(
        "Git pull: conflict resolution loop exceeded %d iterations", max_iterations
    )
    return list(saved.items())


def resolve_conflicts_safely(
    git_root: Path,
    env: dict[str, str] | None,
    from_sha: str,
    *,
    token: str | None,
    resolve_fn: collections.abc.Callable[
        [Path, dict[str, str] | None], list[tuple[str, str]]
    ]
    | None = None,
) -> tuple[list[tuple[str, str]] | None, PullResult | None]:
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
        resolve_fn: Optional override for the conflict-resolution
            callable.  Defaults to :func:`resolve_rebase_conflicts`.
            ``GitWriteStrategy`` passes ``self._resolve_rebase_conflicts``
            so that tests which monkeypatch ``_resolve_rebase_conflicts`` on
            the strategy instance are still honoured.

    Returns:
        ``(saved, None)`` on success — ``saved`` is the list of
        ``(rel_path, mcp_content)`` tuples from
        :func:`resolve_rebase_conflicts`.

        ``(None, PullResult)`` on failure — caller should return the
        ``PullResult`` immediately.
    """
    _resolve = resolve_fn if resolve_fn is not None else resolve_rebase_conflicts
    try:
        saved = _resolve(git_root, env)
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
        return None, PullResult.head_unchanged_failure(
            from_sha, PULL_REASON_CONFLICT_RESOLUTION_FAILED
        )
    return saved, None


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
    *,
    token: str | None,
) -> list[tuple[str, str]]:
    """Restore upstream content for each conflict path after rebase abort.

    After ``git rebase --abort`` the working tree reverts to the
    pre-rebase MCP state — every file in ``saved`` again contains the
    MCP version, not the upstream version.  For each path we run
    ``git checkout @{upstream} -- <path>`` to bring back the upstream
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
            "@{upstream}",
            "--",
            rel_path,
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
    root = str(git_root)
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

        conflict_abs = git_root / conflict_rel
        conflict_abs.parent.mkdir(parents=True, exist_ok=True)
        conflict_abs.write_text(frontmatter.dumps(post), encoding="utf-8")

        # --- Update original file with conflict_with frontmatter ---
        original_abs = git_root / rel_path
        if original_abs.exists():
            try:
                # Read once and reuse this content for the parse-failure
                # fallback below (the prior version re-read the file there). The
                # read_text and write_text both sit inside this try, so if the
                # original was removed/became inaccessible after the exists()
                # check (TOCTOU) or is not valid UTF-8, the error is caught below
                # and skips just this original's update instead of crashing the
                # whole pull.
                content = original_abs.read_text(encoding="utf-8")
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
        ["git", "-C", root, "add", "--", *paths_to_add],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    n = len(written)
    file_list = ", ".join(written)
    # Conflict resolution runs on the pull background thread, not inside a
    # request context, so _extract_claim would return None.  Use the static
    # server identity directly — per-user attribution does not apply here.
    # NOTE: this commit is pathspec-less — it commits the whole staged index,
    # not just ``paths_to_add``. That is intentional: it also captures upstream
    # content already staged during conflict resolution (by the rebase's merge,
    # or by the ``checkout @{upstream}`` restore on the rebase-abort path). It
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
        logger.error(
            "Git pull: conflict commit failed (rc=%d): %s",
            commit_result.returncode,
            redact(
                (commit_result.stderr or commit_result.stdout or "").strip(),
                token,
            ),
        )
        return None

    return written
