"""Result types and reason constants for git pull/push operations."""

from __future__ import annotations

from dataclasses import dataclass, field

# Reason codes returned in :class:`PullResult.reason` and
# :class:`PushResult.reason`.  Defined as module-level constants so callers
# (and tests) can refer to them by name rather than re-typing string literals.
PULL_REASON_FETCH_FAILED = "fetch_failed"
PULL_REASON_NO_REMOTE = "no_remote"
PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS = "non_fast_forward_with_conflicts"
PULL_REASON_REBASED = "rebased"
PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS = "conflicts_resolved_with_siblings"
PULL_REASON_CONFLICT_RESOLUTION_FAILED = "conflict_resolution_failed"

PUSH_REASON_DRY_RUN_UNSUPPORTED = "dry_run_unsupported"
PUSH_REASON_NO_REMOTE = "no_remote"
PUSH_REASON_NON_FAST_FORWARD = "non_fast_forward"
PUSH_REASON_PUSH_FAILED = "push_failed"


@dataclass(frozen=True)
class PullResult:
    """Result of a :meth:`GitWriteStrategy.force_pull` invocation.

    Attributes:
        applied: ``True`` when the pull was actually executed and HEAD was
            moved (or was already up-to-date and required no work).
            Also ``True`` when divergent history was resolved via the
            Syncthing-style sibling write — HEAD advanced to the remote
            and the local MCP versions were preserved as
            ``.conflict-mcp-*`` siblings (see ``conflict_files``).
            ``False`` for ``dry_run`` calls and for failures that left
            HEAD unchanged.
        fast_forward: ``True`` when the pull was (or would have been) a
            clean fast-forward.  ``False`` when divergent history
            required rebase + sibling writes, or when the operation
            failed.  Inspect ``reason`` and ``conflict_files`` to
            distinguish "applied via conflict resolution" from outright
            failure.
        commits_pulled: Count of commits brought in.  Reliable on the
            fast-forward path (``reason is None`` and ``fast_forward=True``).
            On ``"rebased"`` and ``"conflicts_resolved_with_siblings"`` this
            is ``0`` even when HEAD advanced — the rebase replays local
            commits *on top of* the upstream rather than fast-forwarding,
            so the linear-history "commits pulled" count is not meaningful.
            Inspect ``from_sha != to_sha`` to detect that HEAD actually
            moved on those paths.  In ``dry_run`` mode this is the count
            that *would* have been pulled.
        from_sha: HEAD SHA before the pull.
        to_sha: HEAD SHA after the pull.  In ``dry_run`` mode this is the
            SHA HEAD would have moved to.  When the pull failed and HEAD
            did not move this equals ``from_sha``.
        reason: Diagnostic code describing the outcome.  ``None`` for
            clean fast-forward pulls and dry-runs.  Otherwise one of:

            * ``"fetch_failed"`` — ``git fetch origin`` exited non-zero
              (network error, auth failure, etc.); HEAD did not move.
            * ``"no_remote"`` — neither ``@{upstream}`` nor
              ``origin/HEAD`` could be resolved on the local clone.
            * ``"non_fast_forward_with_conflicts"`` — local and remote
              histories diverged and the conflict-resolution path
              failed to produce a usable result; HEAD did not move.
            * ``"rebased"`` — local and remote histories diverged but
              ``git rebase @{upstream}`` replayed local commits cleanly
              on top of the upstream with no manual intervention.
              ``applied`` is ``True``; ``conflict_files`` is empty.
            * ``"conflicts_resolved_with_siblings"`` — local and remote
              histories diverged AND rebase hit real conflicts, which
              were resolved by accepting upstream and saving the local
              MCP versions as ``.conflict-mcp-*`` siblings (see #232).
              HEAD advanced; ``applied`` is ``True`` and
              ``conflict_files`` is populated.
            * ``"conflict_resolution_failed"`` — the conflict-resolution
              path could not produce a usable result.  Two variants:
              (a) the rebase was aborted before completing — HEAD did not
              move (``from_sha == to_sha``); (b) the rebase completed and
              HEAD advanced, but the sibling-files commit failed — HEAD
              has moved (``from_sha != to_sha``, ``applied=False``).

            See module-level ``PULL_REASON_*`` constants for the
            string values.
        conflict_files: Vault-relative paths of Syncthing-style
            ``.conflict-mcp-*`` siblings written when the pull resolved
            divergent history (see #232 and the
            ``"conflicts_resolved_with_siblings"`` reason above).
            Empty for clean fast-forwards, dry-runs, and failure paths
            that did not write any siblings.
    """

    applied: bool
    fast_forward: bool
    commits_pulled: int
    from_sha: str
    to_sha: str
    reason: str | None = None
    conflict_files: tuple[str, ...] = field(default=())

    @classmethod
    def head_unchanged_failure(cls, from_sha: str, reason: str) -> PullResult:
        """Construct a ``PullResult`` for a failure path where HEAD did not move.

        The failure paths in :meth:`GitWriteStrategy.force_pull` and its
        helpers that use this factory (``no_remote``, ``fetch_failed``,
        ``conflict_resolution_failed`` when the rebase was aborted before
        completing, and ``non_fast_forward_with_conflicts``) all share the
        same shape: ``applied=False``, ``fast_forward=False``,
        ``commits_pulled=0``, and ``to_sha == from_sha`` (HEAD unchanged).
        This factory reduces the repetition.

        Args:
            from_sha: HEAD SHA before the failed operation.  Used for both
                ``from_sha`` and ``to_sha`` since HEAD did not move.
            reason: One of the ``PULL_REASON_*`` constants.

        Returns:
            A ``PullResult`` with the failure-shape fields set and the
            provided ``reason``.
        """
        return cls(
            applied=False,
            fast_forward=False,
            commits_pulled=0,
            from_sha=from_sha,
            to_sha=from_sha,
            reason=reason,
        )


@dataclass(frozen=True)
class PushResult:
    """Result of a :meth:`GitWriteStrategy.force_push` invocation.

    Attributes:
        applied: ``True`` when the push succeeded (or was a no-op because
            the remote already had every local commit).  ``False`` for
            ``dry_run`` calls and for push attempts that were rejected or
            failed.
        commits_pushed: Count of commits sent to the remote.  ``0`` when
            there was nothing to push.
        remote_sha_before: Remote ref SHA before the push.  Equals
            ``remote_sha_after`` on no-op and on failure.
        remote_sha_after: Remote ref SHA after the push.  Equals the local
            HEAD on success and ``remote_sha_before`` on failure.
        reason: Diagnostic code describing the outcome.  ``None`` for
            successful pushes (including the no-op already-up-to-date
            case).  Otherwise one of:

            * ``"dry_run_unsupported"`` — the caller passed
              ``dry_run=True``; git has no safe local probe for "would
              this push be accepted by the remote", so the call is a
              no-op that sets this code.  HEAD and the remote are not
              touched.
            * ``"no_remote"`` — the upstream tracking branch could not
              be resolved (no ``@{upstream}`` and no ``origin/HEAD``);
              the push was not attempted.
            * ``"non_fast_forward"`` — the remote rejected the push
              because the local branch is not a strict descendant of
              the remote tip.  ``hint`` points the caller at
              ``git_sync(direction='pull')`` to reconcile first.
            * ``"push_failed"`` — ``git push origin`` exited non-zero
              for any other reason (network error, auth failure, hook
              rejection).  ``hint`` carries the truncated stderr.

            See module-level ``PUSH_REASON_*`` constants for the
            string values.
        hint: Operator-facing remediation suggestion when ``applied=False``.
            Surfaced verbatim in the MCP tool response so the caller can
            see exactly what to do next.
    """

    applied: bool
    commits_pushed: int
    remote_sha_before: str
    remote_sha_after: str
    reason: str | None = None
    hint: str | None = None
