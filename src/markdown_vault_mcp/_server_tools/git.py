from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from markdown_vault_mcp.git import GitWriteStrategy, PullResult, PushResult
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_deps import get_vault

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# git_sync helpers
# ---------------------------------------------------------------------------


def _resolve_managed_strategy(vault: Vault) -> GitWriteStrategy:
    """Resolve and validate the managed-mode git strategy from a Vault.

    Returns:
        The Vault's :class:`GitWriteStrategy` if it's in managed mode.

    Raises:
        ValueError: If the deployment isn't wired with a managed git
            strategy (no ``MARKDOWN_VAULT_MCP_GIT_REPO_URL`` env var).
    """
    # The MCP layer is a trusted consumer of Vault internals — adding
    # a public accessor for this single tool would be scope creep.
    strategy = vault._git_strategy
    if not isinstance(strategy, GitWriteStrategy) or not strategy._managed:
        raise ValueError(
            "git_sync requires a managed git deployment.  Set "
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL to enable it."
        )
    return strategy


async def _get_branch_name(strategy: GitWriteStrategy, git_root: Path) -> str:
    """Return the current branch name, falling back to ``"HEAD"`` on failure.

    Used by :func:`git_sync` to populate the ``branch`` field of the
    response.  Detached-HEAD checkouts produce a clean ``"HEAD"`` from
    git itself; this helper's fallback covers the rarer cases where
    git invocation fails entirely (binary missing, transient FS error).
    """

    def _read_branch() -> str:
        return strategy._git(git_root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    try:
        return await asyncio.to_thread(_read_branch)
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Narrow the catch per CLAUDE.md's logging standard so a real bug
        # (e.g. AttributeError) still propagates.
        logger.warning(
            "git_sync: failed to read branch name, using 'HEAD' fallback",
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
        # SHA comparison: handles force_pull's up-to-date early-return cleanly.
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


async def _reindex_after_pull(vault: Vault, pull_dict: dict[str, Any]) -> None:
    """Refresh the FTS index after a pull that moved HEAD.

    ``force_pull`` only mutates the working tree; without this call,
    ``search`` / ``list_documents`` / ``get_context`` would serve stale
    data until the next write.  Mirrors the periodic pull loop's
    ``on_pull`` callback in :meth:`GitWriteStrategy._pull_loop` —
    same ``pause_writes()`` + ``reindex()`` pattern.

    On reindex failure: the pull side-effect already happened (HEAD
    moved, files on disk), so failing the whole tool would hide the
    successful pull from the caller.  Surfaces ``reindex_failed=True``
    + ``reindex_hint`` on the pull payload instead so the agent knows
    the index is stale and can decide whether to retry via the
    ``reindex`` tool.

    Mutates ``pull_dict`` in place on failure.
    """

    def _pause_and_reindex() -> None:
        with vault.pause_writes():
            vault.index.reindex()

    try:
        await asyncio.to_thread(_pause_and_reindex)
    except Exception:
        logger.exception(
            "git_sync: reindex after pull failed — FTS index "
            "is stale until the next reindex / write tick"
        )
        pull_dict["reindex_failed"] = True
        pull_dict["reindex_hint"] = (
            "Pull succeeded but the FTS index could not be "
            "refreshed.  search / list_documents / get_context "
            "will serve stale data until the next call to the "
            "reindex tool or the next write."
        )


async def _run_pull_leg(
    strategy: GitWriteStrategy,
    vault: Vault,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Run the pull leg of ``git_sync`` and return its response dict.

    Calls :meth:`GitWriteStrategy.force_pull`, projects the result,
    and triggers a reindex when HEAD actually moved (skipped on
    dry-run and on failure).  The reindex's own failure is surfaced
    on the returned dict, not raised.

    Args:
        strategy: Resolved managed-mode strategy.
        vault: Vault used for the post-pull reindex.
        dry_run: Forwarded to ``force_pull`` and to
            :func:`_format_pull_dict`.

    Returns:
        The pull-leg payload dict (caller assigns to
        ``result["pull"]``).
    """
    pull_result = await asyncio.to_thread(strategy.force_pull, dry_run=dry_run)
    pull_dict = _format_pull_dict(pull_result, dry_run)
    if (
        not dry_run
        and pull_result.applied
        and pull_result.from_sha != pull_result.to_sha
    ):
        await _reindex_after_pull(vault, pull_dict)
    return pull_dict


async def _run_push_leg(strategy: GitWriteStrategy, *, dry_run: bool) -> dict[str, Any]:
    """Run the push leg of ``git_sync`` and return its response dict."""
    push_result = await asyncio.to_thread(strategy.force_push, dry_run=dry_run)
    return _format_push_dict(push_result)


def register(mcp: FastMCP) -> None:
    """Register git history/sync tools on *mcp*."""

    @mcp.tool(
        icons=_TOOL_ICONS["get_history"],
        annotations={
            "title": "Note History",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_history(
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """List commits that touched a note or the whole vault.

        Only available for git-backed vaults. Use 'stats' to check
        whether git is configured, or call this and handle the error.

        Args:
            path: Vault-relative path of the note or attachment to filter on
                (e.g. "notes/alpha.md" or "assets/diagram.png"). May be a
                `.md` note or a configured attachment extension (png, pdf,
                svg, …). Omit (or pass null) for vault-wide commit history.
            since: ISO 8601 datetime string ("2026-04-01T00:00:00") or a git
                date expression ("1 week ago"). Passed as --since to git log.
                Omit for full history.
            until: ISO 8601 datetime string or git date expression, passed as
                --until to git log. Both 'since' and 'until' boundaries are
                inclusive: a commit whose committer date equals either
                endpoint is included in the result. Omit to disable the upper
                bound.
            limit: Maximum number of commits to return. Default 20, max 100.

        Returns:
            Envelope dict with the following fields:

            - commits (list[dict]): Commit entries, newest-first. Each entry
              contains:
                - sha (str): Full 40-character commit SHA.
                - short_sha (str): 7-character abbreviated SHA.
                - timestamp (str): ISO 8601 author timestamp.
                - author (str): Author name and email, e.g. "Name <email>".
                - message (str): First line of the commit message.
                - paths_changed (list[str]): Files touched by the commit.
                  Populated for vault-wide queries (path=None). Always empty
                  for single-note queries, since the path is already
                  determined by the query arguments — callers know which
                  file the commit touched without needing it echoed back.
            - total (int): Count of entries in `commits` (always equals
              `len(commits)`; does NOT indicate how many commits exist
              beyond the `limit` cap).

        Raises:
            ToolError: If the path is invalid or uses an unsupported extension.
        """
        try:
            results = await asyncio.to_thread(
                vault.reader.get_history,
                path,
                since=since,
                until=until,
                limit=limit,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        commits = [asdict(r) for r in results]
        return {"commits": commits, "total": len(commits)}

    @mcp.tool(
        icons=_TOOL_ICONS["get_diff"],
        annotations={
            "title": "Note Diff",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_diff(
        path: str,
        since_sha: str | None = None,
        since_timestamp: str | None = None,
        per_commit: bool = False,
        limit: int | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return the diff of a note between a reference point and HEAD.

        Only available for git-backed vaults. Exactly one of 'since_sha' or
        'since_timestamp' must be provided. Use 'get_history' first to find
        commit SHAs.

        Args:
            path: Vault-relative path of the note or attachment to diff (e.g.
                "notes/alpha.md" or "assets/diagram.png"). May be a `.md`
                note or a configured attachment extension (png, pdf, svg, …).
                A binary attachment returns a `--stat` size/rename summary
                instead of a full unified patch; a text attachment (e.g.
                `.svg`, `.csv`) returns a full unified diff. `.md` notes are
                unchanged. An unsupported extension is rejected.
            since_sha: A commit SHA (full or abbreviated, at least 4 hex digits)
                to diff from. Mutually exclusive with since_timestamp.
            since_timestamp: ISO 8601 datetime string, resolved via
                `git rev-list --before=<ts> -1 HEAD` to the most recent
                commit at or before that instant. Boundary is
                **inclusive**: a commit whose committer date equals
                since_timestamp IS the resolved ref. Mutually exclusive
                with since_sha.
            per_commit: When False (default), return a single unified diff from
                the reference point to HEAD. When True, return one diff per
                intervening commit.
            limit: When per_commit=True, cap the number of intervening commits
                returned to the `limit` most recent ones. Clamped to [1, 100].
                Defaults to null (unbounded — still bounded by the underlying
                since..HEAD range). Ignored when per_commit=False. Useful for
                keeping per-commit responses within context budgets when
                auditing long histories.

        Returns:
            Envelope dict whose shape depends on `per_commit`:

            - When per_commit=False:
                - diff (str): Unified diff from the reference to HEAD. Empty
                  string when there are no changes. May include a truncation
                  notice if the diff exceeds 50 KB.
            - When per_commit=True:
                - commits (list[dict]): Per-commit entries, newest-first.
                  Each contains:
                    - sha (str): Full commit SHA.
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
        try:
            result = await asyncio.to_thread(
                vault.reader.get_diff,
                path,
                since_sha=since_sha,
                since_timestamp=since_timestamp,
                per_commit=per_commit,
                limit=limit,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if isinstance(result, list):
            commits = [asdict(r) for r in result]
            return {"commits": commits, "total": len(commits)}
        return {"diff": result}

    @mcp.tool(
        tags={"write", "git-managed"},
        icons=_TOOL_ICONS["git_sync"],
        annotations={
            "title": "Sync with Git",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def git_sync(
        direction: Literal["pull", "push", "both"] = "both",
        dry_run: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Synchronously reconcile the local clone with ``origin``.

        Composes :meth:`GitWriteStrategy.force_pull` and
        :meth:`GitWriteStrategy.force_push` behind a single tool call so
        an operator can request "pull then push" with one round-trip.
        Only available on managed git deployments
        (``MARKDOWN_VAULT_MCP_GIT_REPO_URL`` set).

        ``direction='pull'`` runs only the pull leg; ``'push'`` only the
        push leg; ``'both'`` runs pull first, then push.  When pull fails
        in ``'both'`` mode the push leg is skipped — the failure surfaces
        in the ``pull`` payload and the caller is expected to inspect
        ``pull.reason`` (and ``pull.conflict_files`` for conflict
        resolution) before retrying.

        ``dry_run=True`` projects the would-be pull without moving HEAD.
        Push has no safe dry-run (git provides no local probe for
        "would the remote accept this"), so the push leg returns
        ``applied=False`` with ``reason='dry_run_unsupported'``.

        Args:
            direction: ``"pull"``, ``"push"``, or ``"both"`` (default).
            dry_run: When ``True``, projects pull without moving HEAD.
                See :meth:`GitWriteStrategy.force_push` for why this is
                a no-op on the push leg.

        Returns:
            Dict with the following fields:

            - direction (str): The requested direction, echoed back.
            - head_sha (str): Local HEAD SHA after the operation.  May
              differ from the pre-call HEAD when the pull leg advanced
              the branch.
            - branch (str): Current branch name (from
              ``git rev-parse --abbrev-ref HEAD``).
            - pull (dict | None): Payload from the pull leg, or ``None``
              when ``direction='push'``.  Contains ``applied``,
              ``fast_forward``, ``commits_pulled``, ``from_sha``,
              ``to_sha``, optional ``reason`` and ``conflict_files``.
              In ``dry_run`` mode also includes
              ``would_apply: bool``.
            - push (dict | None): Payload from the push leg, or ``None``
              when ``direction='pull'`` or when the pull leg failed in
              ``direction='both'``.  Contains ``applied``,
              ``commits_pushed``, ``remote_sha_before``,
              ``remote_sha_after``, optional ``reason`` and ``hint``.
            - dry_run (bool): Only present when ``dry_run=True`` was
              passed.

        Raises:
            ValueError: When the underlying strategy is not a managed
                :class:`GitWriteStrategy` (i.e. the deployment is not
                wired with ``MARKDOWN_VAULT_MCP_GIT_REPO_URL``).
        """
        strategy = _resolve_managed_strategy(vault)
        git_root = strategy._resolve_force_repo()

        result: dict[str, Any] = {
            "direction": direction,
            "head_sha": await asyncio.to_thread(strategy._head_sha, git_root),
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
            # Excludes ``dry_run``: in dry-run mode ``applied`` is always
            # ``False`` even on a healthy projection, so falling through is
            # correct — the push leg's ``dry_run_unsupported`` reason then
            # surfaces in the response, distinguishing a preview from a
            # real-pull-failed-push-skipped result.
            if direction == "both" and not pull_dict["applied"] and not dry_run:
                # Refresh head_sha defensively (today's force_pull leaves
                # HEAD in place on failure, but allowed to move).
                result["head_sha"] = await asyncio.to_thread(
                    strategy._head_sha, git_root
                )
                return result

        if direction in ("push", "both"):
            result["push"] = await _run_push_leg(strategy, dry_run=dry_run)

        # HEAD may have moved (pull leg advanced it).  Refresh once at the
        # end so the caller sees the post-sync state regardless of which
        # legs ran.
        result["head_sha"] = await asyncio.to_thread(strategy._head_sha, git_root)
        return result
