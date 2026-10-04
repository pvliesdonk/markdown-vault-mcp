from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp_pvl_core import tool_boundary

from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.git import PullResult, PushResult, Syncer
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from ..domain import get_vault

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# git_sync helpers
# ---------------------------------------------------------------------------


def _resolve_managed_strategy(vault: Vault) -> Syncer:
    """Resolve and validate the managed-mode git syncer from a Vault.

    Gates on the :class:`~markdown_vault_mcp.git.Syncer` seam rather than the
    concrete strategy class (#1229), so the check is about what the store can
    do, not which class implements it.

    Returns:
        The Vault's syncer if it is in managed mode.

    Raises:
        InvalidRequestError: If the deployment isn't wired with a managed git
            strategy (no ``MARKDOWN_VAULT_MCP_GIT_REPO_URL`` env var): an
            opt-in left off, so a change of request, not a fault (#1608).
    """
    # The MCP layer is a trusted consumer of Vault internals — adding
    # a public accessor for this single tool would be scope creep.
    strategy = vault._git_strategy
    if not isinstance(strategy, Syncer) or not strategy.is_managed:
        raise InvalidRequestError(
            "This vault is not synced with a remote, so git_sync has nothing to do."
        )
    return strategy


async def _get_branch_name(strategy: Syncer, git_root: Path) -> str:
    """Return the current branch name, falling back to ``"HEAD"`` on failure.

    Used by :func:`git_sync` to populate the ``branch`` field of the
    response.  Detached-HEAD checkouts produce a clean ``"HEAD"`` from
    git itself; this helper's fallback covers the rarer cases where
    git invocation fails entirely (binary missing, transient FS error).
    """
    try:
        return await asyncio.to_thread(strategy.branch_name, git_root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Narrow the catch per CLAUDE.md's logging standard so a real bug
        # (e.g. AttributeError) still propagates.
        logger.warning(
            "git_sync_branch_read_failed fallback=HEAD",
            exc_info=True,
        )
        return "HEAD"


def _format_pull_dict(result: PullResult, dry_run: bool) -> dict[str, Any]:
    """Project a :class:`PullResult` into the response dict shape.

    Pure transformation — no side effects.  Adds optional ``reason``,
    ``conflict_files``, and (in dry-run) ``would_apply`` fields when
    relevant.
    """
    pull_dict: dict[str, Any] = {
        "applied": result.applied,
        "fast_forward": result.fast_forward,
        "commits_pulled": result.commits_pulled,
        "from_sha": result.from_sha,
        "to_sha": result.to_sha,
    }
    if result.reason is not None:
        pull_dict["reason"] = result.reason
    if result.conflict_files:
        pull_dict["conflict_files"] = list(result.conflict_files)
    if dry_run:
        # SHA comparison: handles the pull pipeline's nothing-to-pull return
        # (up-to-date, or a clone that is only ahead) cleanly — both report
        # ``to_sha == from_sha``.  On a ``diverged`` projection this is
        # ``True`` because the real pull would do work, though the work is a
        # rebase that mints its own SHAs rather than a move to ``to_sha``.
        pull_dict["would_apply"] = result.from_sha != result.to_sha
    return pull_dict


def _format_push_dict(result: PushResult) -> dict[str, Any]:
    """Project a :class:`PushResult` into the response dict shape.

    Pure transformation — no side effects.  Adds optional ``reason``
    and ``hint`` fields when present.
    """
    push_dict: dict[str, Any] = {
        "applied": result.applied,
        "commits_pushed": result.commits_pushed,
        "remote_sha_before": result.remote_sha_before,
        "remote_sha_after": result.remote_sha_after,
    }
    if result.reason is not None:
        push_dict["reason"] = result.reason
    if result.hint is not None:
        push_dict["hint"] = result.hint
    return push_dict


async def _reconcile_after_pull(vault: Vault, pull_dict: dict[str, Any]) -> None:
    """Reindex when the index does not yet reflect the pulled HEAD (#1532).

    ``force_pull`` only mutates the working tree; without this call,
    ``search`` / ``list_documents`` / ``get_context`` would serve stale
    data.  Level-triggered through
    :meth:`~markdown_vault_mcp.vault.Vault.reconcile_index_with_head`, the
    same step the store's periodic pull loop runs on every tick: it compares
    HEAD with the head the index last reflected, so it also retries a
    reindex an earlier pull lost, and runs after a pull reported as not
    applied that still moved HEAD.

    When the index could not be brought up to date the pull side-effect has
    still happened, so failing the whole tool would hide it from the caller.
    Surfaces ``reindex_failed=True`` + ``reindex_hint`` on the pull payload
    instead so the agent knows the index is stale.

    Mutates ``pull_dict`` in place on failure.
    """
    try:
        outcome = await asyncio.to_thread(
            vault.reconcile_index_with_head, source="git_sync"
        )
    except Exception:
        logger.exception("reindex_after_pull_failed source=git_sync")
        outcome = "failed"
    # ``deferred`` means the index is still building: that build (and the
    # boot reindex behind it) covers the pulled tree, so it is not stale.
    if outcome == "failed":
        pull_dict["reindex_failed"] = True
        pull_dict["reindex_hint"] = (
            "The FTS index could not be refreshed after the pull.  "
            "search / list_documents / get_context will serve stale data "
            "until the next reconcile (the periodic pull, a webhook "
            "delivery, another git_sync) or a call to the reindex tool."
        )


async def _run_pull_leg(
    strategy: Syncer,
    vault: Vault,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Run the pull leg of ``git_sync`` and return its response dict.

    Calls :meth:`~markdown_vault_mcp.git.Syncer.force_pull`, projects the result,
    and reconciles the index with HEAD (skipped on dry-run).  The
    reconcile's own failure is surfaced on the returned dict, not raised.

    Args:
        strategy: Resolved managed-mode strategy.
        vault: Vault whose index is reconciled after the pull.
        dry_run: Forwarded to ``force_pull`` and to
            :func:`_format_pull_dict`.

    Returns:
        The pull-leg payload dict (caller assigns to
        ``result["pull"]``).
    """
    pull_result = await asyncio.to_thread(strategy.force_pull, dry_run=dry_run)
    pull_dict = _format_pull_dict(pull_result, dry_run)
    if not dry_run:
        await _reconcile_after_pull(vault, pull_dict)
    return pull_dict


async def _run_push_leg(strategy: Syncer, *, dry_run: bool) -> dict[str, Any]:
    """Run the push leg of ``git_sync`` and return its response dict."""
    push_result = await asyncio.to_thread(strategy.force_push, dry_run=dry_run)
    return _format_push_dict(push_result)


def register(mcp: FastMCP) -> None:
    """Register git history/sync tools on *mcp*."""

    @mcp.tool(
        icons=_TOOL_ICONS["get_history"],
        annotations={
            "title": "Note History",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def get_history(
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """List the commits that touched a note, an attachment, a folder or the whole
        vault, newest first.

        A vault without git history returns no commits.

        Args:
            path: A note or attachment path, such as `"notes/alpha.md"`, or a folder, such as
                `"guides"`; omit for the whole vault. Give a note's current name:
                history from before a rename is included.
            since: Earliest commit date, inclusive: an ISO 8601 time such as
                `"2026-04-01T00:00:00"` or a relative date such as `"1 week ago"`; omit for
                all history.
            until: Latest commit date, inclusive, in the same forms as since; omit for
                no upper bound.
            limit: Maximum commits to return (default 20, at most 100).

        Returns:
            Envelope dict with the following fields:

            - commits (list[dict]): Commit entries, newest-first. Each entry
              contains:
                - `sha` (str): Full commit SHA (40 hex digits, or 64 in a
                  SHA-256 repository).
                - short_sha (str): 7-character abbreviated SHA.
                - timestamp (str): ISO 8601 author timestamp.
                - author (str): Author name and email, such as `"Name <email>"`.
                - message (str): First line of the commit message.
                - paths_changed (list[str]): Files touched by the commit.
                  Populated for vault-wide queries (path=None) and folder
                  queries (the subtree files the commit touched). Always empty
                  for single-note queries, since the path is already
                  determined by the query arguments; callers know which
                  file the commit touched without needing it echoed back.
            - total (int): Count of entries in `commits` (always equals
              `len(commits)`; does NOT indicate how many commits exist
              beyond the `limit` cap).

        Raises:
            ToolError: If the path is invalid or uses an unsupported extension.
        """
        results = await asyncio.to_thread(
            vault.reader.get_history,
            path,
            since=since,
            until=until,
            limit=limit,
        )
        commits = [asdict(r) for r in results]
        return {"commits": commits, "total": len(commits)}

    @mcp.tool(
        icons=_TOOL_ICONS["get_diff"],
        annotations={
            "title": "Note Diff",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def get_diff(
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return how a note or attachment changed from an earlier commit to now, as one
        unified diff or one per commit.

        Pass exactly one of since_sha and since_timestamp; find commits with
        get_history. A vault without git history returns an empty diff.

        Args:
            path: A note or attachment path, such as `"notes/alpha.md"`; a binary attachment
                returns a summary of size and renames instead of a patch.
            since_sha: A commit SHA to diff from, such as from get_history; at least 4 hex
                digits.
            since_timestamp: An ISO 8601 time; diffs from the latest commit at or before
                it.
            per_commit: True for one diff per commit since the starting point, newest
                first; default false for a single diff.
            limit: With per_commit, how many of the most recent commits to include, 1 to
                100; omit for all.

        Returns:
            Envelope dict whose shape depends on `per_commit`:

            - When per_commit=False:
                - diff (str): Unified diff from the reference to HEAD. Empty
                  string when there are no changes. May include a truncation
                  notice if the diff exceeds 50 KB.
            - When per_commit=True:
                - commits (list[dict]): Per-commit entries, newest-first.
                  Each contains:
                    - `sha` (str): Full commit SHA.
                    - short_sha (str): Abbreviated SHA.
                    - timestamp (str): ISO 8601 author timestamp.
                    - message (str): First line of commit message.
                    - diff (str): Unified diff for this commit.
                - total (int): Count of entries in `commits` (always equals
                  `len(commits)`; does NOT indicate how many commits exist
                  beyond the `limit` cap).

        Raises:
            ToolError: If neither or both reference parameters are supplied,
                the SHA is invalid, the reference commit is not found, or the
                path uses an unsupported extension.
        """
        result = await asyncio.to_thread(
            vault.reader.get_diff,
            path,
            since_sha=since_sha,
            since_timestamp=since_timestamp,
            per_commit=per_commit,
            limit=limit,
        )
        if isinstance(result, list):
            commits = [asdict(r) for r in result]
            return {"commits": commits, "total": len(commits)}
        return {"diff": result}

    @mcp.tool(
        tags={"write", "git-managed"},
        icons=_TOOL_ICONS["git_sync"],
        annotations={
            "title": "Sync with Git",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def git_sync(
        direction: Literal["pull", "push", "both"] = "both",
        dry_run: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Pull from and push to the vault's git remote now; returns what each leg did.

        With direction "both" the pull runs first, and a failed pull skips the push.

        Args:
            direction: `pull`, `push` or `both` (default).
            dry_run: Report what a pull would do without changing anything; the push
                leg reports applied false without contacting the remote.

        Returns:
            Dict with the following fields:

            - direction (str): The requested direction, echoed back.
            - head_sha (str): Local HEAD SHA after the operation. May
              differ from the pre-call HEAD when the pull leg advanced
              the branch.
            - branch (str): Current branch name, or `"HEAD"` when the
              checkout is detached.
            - pull (dict | None): Payload from the pull leg, or `None`
              when direction is `"push"`. Contains:

              - applied (bool): true when the pull ran or there was nothing
                to bring in.
              - fast_forward (bool): true for a clean fast-forward.
              - commits_pulled (int): commits brought in. It is 0 when the
                pull rebased or resolved conflicts with sibling files, even
                though HEAD moved; compare from_sha and to_sha to see that.
              - from_sha (str), to_sha (str): HEAD before and after the pull.
              - reason (str, optional): `"fetch_failed"`, `"no_remote"`,
                `"non_fast_forward_with_conflicts"`, `"rebased"`,
                `"conflicts_resolved_with_siblings"` (conflict_files lists the
                sibling files), `"conflict_resolution_failed"` (either HEAD
                did not move, or the rebase completed and HEAD moved but
                committing the sibling files failed), or, on a dry run only,
                `"diverged"`.
              - conflict_files (list[str], optional): the `.conflict-mcp-*`
                sibling files written when the pull resolved conflicts.
              - would_apply (bool, dry run only): true when a real pull
                would change HEAD.
              - reindex_failed (bool, optional) and reindex_hint (str,
                optional): present when the search index could not be
                refreshed after the pull, so search serves stale results
                until the next reindex.

            - push (dict | None): Payload from the push leg, or `None`
              when direction is `"pull"` or when the pull leg failed with
              direction `"both"`. Contains applied, commits_pushed,
              remote_sha_before, remote_sha_after, and an optional reason
              (`"dry_run_unsupported"`, `"no_remote"`, `"non_fast_forward"` or
              `"push_failed"`) and hint.
            - dry_run (bool): Only present when dry_run was true.

        Raises:
            InvalidRequestError: If the vault is not synced with a remote, so
                git_sync has nothing to do.
        """
        strategy = _resolve_managed_strategy(vault)
        git_root = strategy.resolve_force_repo()

        result: dict[str, Any] = {
            "direction": direction,
            "head_sha": await asyncio.to_thread(strategy.head_sha, git_root),
            "branch": await _get_branch_name(strategy, git_root),
            "pull": None,
            "push": None,
        }
        if dry_run:
            result["dry_run"] = True

        if direction in ("pull", "both"):
            pull_dict = await _run_pull_leg(strategy, vault, dry_run=dry_run)
            result["pull"] = pull_dict

            # Short-circuit the push leg when the pull failed in 'both' mode
            # so we don't push on top of an unreconciled local clone.
            # Excludes ``dry_run``: a projection reports ``applied=False``
            # whenever it predicts work, so falling through is correct — the
            # push leg's ``dry_run_unsupported`` reason then surfaces in the
            # response, distinguishing a preview from a
            # real-pull-failed-push-skipped result.
            if direction == "both" and not pull_dict["applied"] and not dry_run:
                # Refresh head_sha defensively (today's force_pull leaves
                # HEAD in place on failure, but allowed to move).
                result["head_sha"] = await asyncio.to_thread(
                    strategy.head_sha, git_root
                )
                return result

        if direction in ("push", "both"):
            result["push"] = await _run_push_leg(strategy, dry_run=dry_run)

        # HEAD may have moved (pull leg advanced it).  Refresh once at the
        # end so the caller sees the post-sync state regardless of which
        # legs ran.
        result["head_sha"] = await asyncio.to_thread(strategy.head_sha, git_root)
        return result
