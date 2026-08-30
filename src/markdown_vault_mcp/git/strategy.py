"""Git write strategy for auto-commit and push on write operations.

Provides :class:`GitWriteStrategy`, a stateful callback that commits
per-write and defers pushes to a background timer.  Also retains the
legacy :func:`git_write_strategy` factory for backward compatibility.

Two collaborators are composed in (#893): repository discovery, managed
cloning, and startup validation live in
:class:`~markdown_vault_mcp.git.bootstrap.RepoBootstrap`; the deferred-push
timer, pending-flag, and push execution live in
:class:`~markdown_vault_mcp.git.push_scheduler.PushScheduler`.  Both share
the strategy-wide lock — the SAME object — so the lock-ordering contracts
documented on :meth:`GitWriteStrategy._force_pull_rebase_fallback` and
:meth:`GitWriteStrategy._quiesce_writes` are unchanged.
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from markdown_vault_mcp._identity import Principal
    from markdown_vault_mcp.types import CommitDiff, HistoryEntry, WriteOperation

from markdown_vault_mcp.git import conflict, query
from markdown_vault_mcp.git._run import (
    cleanup_git_env,
    git_env,
    redact,
    resolve_tracking_ref,
    run_git,
    run_git_capturing,
)
from markdown_vault_mcp.git.bootstrap import RepoBootstrap
from markdown_vault_mcp.git.push_scheduler import PushScheduler
from markdown_vault_mcp.git.types import (
    PULL_REASON_CONFLICT_RESOLUTION_FAILED,
    PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS,
    PULL_REASON_FETCH_FAILED,
    PULL_REASON_NO_REMOTE,
    PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS,
    PULL_REASON_PULL_DISABLED,
    PULL_REASON_REBASED,
    PUSH_REASON_DRY_RUN_UNSUPPORTED,
    PUSH_REASON_NO_REMOTE,
    PUSH_REASON_NON_FAST_FORWARD,
    PUSH_REASON_PUSH_FAILED,
    PullResult,
    PushResult,
)

logger = logging.getLogger(__name__)


class GitWriteStrategy:
    """Stateful git strategy: commit per write, deferred push.

    On each callback invocation:

    1. Stages the changed file (``git add`` or ``git add -u`` for deletes).
    2. Commits with an auto-generated message (``"operation: path"``).
    3. Resets the push timer — push fires after ``push_delay_s`` of idle.

    Push is deferred to a background ``threading.Timer`` that resets on
    each write.  When the timer fires (no writes for ``push_delay_s``),
    all accumulated local commits are pushed in a single ``git push``.

    On startup, any unpushed local commits (from a previous crash) are
    pushed immediately.

    Args:
        token: PAT for HTTPS push via ``GIT_ASKPASS``.  ``None`` uses
            SSH or pre-configured credentials.
        username: Username used with token auth. Defaults to
            ``"x-access-token"`` (GitHub-compatible).
        repo_url: Remote URL expected in managed mode.
        managed: When ``True``, ensure the repo exists under ``repo_path``:
            clone into an empty directory or validate ``origin`` on existing repos.
        enable_pull: Enable fetch + ff-only sync methods.
        enable_push: Enable deferred push behavior.
        push_delay_s: Seconds of idle before pushing.  ``0`` disables
            the timer (push only on :meth:`close`).
        commit_name: Git committer name; defaults to
            :attr:`DEFAULT_COMMIT_NAME`.
        commit_email: Git committer email; defaults to
            :attr:`DEFAULT_COMMIT_EMAIL`.
        commit_name_claim: OIDC claim key configured for the author name.
        commit_email_claim: OIDC claim key configured for the author email.

            .. deprecated::
                The two claim kwargs no longer drive claim extraction — the
                strategy runs on the write-callback dispatcher thread, where
                no request token exists (#1218). Claims are now resolved at
                the MCP tool edge into the ``principal`` passed per
                invocation (register the keys via
                :func:`markdown_vault_mcp._identity.configure_identity_claims`).
                They remain accepted, and still inform the startup
                identity warning (:meth:`_check_identity`).
        git_lfs: When ``True`` (default), run ``git lfs pull`` during
            lazy initialisation so LFS pointers are resolved before the
            first write is committed.  Requires ``git-lfs`` to be on
            ``PATH``; failures are logged at ERROR and never propagated.
        repo_path: Optional repository path used for startup validation.
            When set together with ``token``, startup raises
            :class:`~markdown_vault_mcp.exceptions.ConfigurationError`
            if ``origin`` uses SSH transport instead of HTTPS.

    Example::

        strategy = GitWriteStrategy(token="ghp_...", push_delay_s=30)
        vault = Vault(on_write=strategy, ...)
        # ... writes happen, push deferred ...
        strategy.close()  # final flush
    """

    #: Default committer name used when none is set in git config or env.
    DEFAULT_COMMIT_NAME = "markdown-vault-mcp"
    #: Default committer email used when none is set in git config or env.
    DEFAULT_COMMIT_EMAIL = "noreply@markdown-vault-mcp"

    def __init__(
        self,
        token: str | None = None,
        username: str = "x-access-token",
        repo_url: str | None = None,
        managed: bool = False,
        enable_pull: bool = True,
        enable_push: bool = True,
        push_delay_s: float = 30.0,
        commit_name: str | None = None,
        commit_email: str | None = None,
        commit_name_claim: str | None = None,
        commit_email_claim: str | None = None,
        git_lfs: bool = True,
        repo_path: Path | None = None,
    ) -> None:
        # Token is retained for GIT_ASKPASS credential forwarding in subprocesses.
        # This pattern is intentionally accepted and suppressed in CodeQL config.
        self._token = token
        self._username = username
        self._repo_url = repo_url
        self._managed = managed
        self._enable_pull = enable_pull
        self._enable_push = enable_push
        self._push_delay_s = push_delay_s
        self._commit_name = commit_name or self.DEFAULT_COMMIT_NAME
        self._commit_email = commit_email or self.DEFAULT_COMMIT_EMAIL
        self._commit_name_claim = commit_name_claim
        self._commit_email_claim = commit_email_claim
        self._git_lfs = git_lfs
        # Retain the configured repo_path so methods invoked after construction
        # (e.g. force_pull / force_push) can reach the working tree without
        # the caller re-passing it.  Distinct from ``_pull_repo_path`` which
        # is only set when the periodic pull loop is started via ``start()``.
        self._repo_path: Path | None = repo_path
        self._write_init_done = False
        # ONE strategy-wide lock, shared with both collaborators below.
        self._lock = threading.Lock()
        self._bootstrap = RepoBootstrap(
            lock=self._lock,
            token=token,
            username=username,
            repo_url=repo_url,
        )
        self._push_scheduler = PushScheduler(
            lock=self._lock,
            bootstrap=self._bootstrap,
            enable_push=enable_push,
            push_delay_s=push_delay_s,
        )
        self._closed = False
        self._pull_stop = threading.Event()
        self._pull_thread: threading.Thread | None = None
        self._pull_interval_s: int = 0
        self._pull_repo_path: Path | None = None
        self._pause_writes: (
            Callable[[], contextlib.AbstractContextManager[None]] | None
        ) = None
        self._drain_writes: Callable[[], bool] | None = None
        self._on_pull: Callable[[], object] | None = None
        if repo_path is not None:
            if self._managed:
                self._bootstrap.ensure_managed_repo(repo_path)
            else:
                self.validate_startup(repo_path)

    def _git_env(self) -> dict[str, str] | None:
        """Build environment for git subprocess calls.

        When a token is set, reuse the existing GIT_ASKPASS mechanism to avoid
        prompting interactively. This mirrors the push path and keeps the token
        out of command-line arguments.
        """
        return git_env(self._token, self._username)

    def _cleanup_git_env(self, env: dict[str, str] | None) -> None:
        cleanup_git_env(env)

    def _redact(self, text: str) -> str:
        """Replace the configured PAT with ``***`` so it never reaches logs/responses.

        Args:
            text: Raw stderr / message text that may contain ``self._token``.

        Returns:
            The same text with every occurrence of ``self._token`` replaced by
            ``"***"``.  Returns ``text`` unchanged when no token is configured
            or the text doesn't contain it (cheap no-op for the common case).
        """
        return redact(text, self._token)

    @property
    def _git_root(self) -> Path | None:
        """The memoised working-tree root, owned by :class:`RepoBootstrap`."""
        return self._bootstrap.git_root

    @_git_root.setter
    def _git_root(self, value: Path | None) -> None:
        self._bootstrap.git_root = value

    @property
    def _push_pending(self) -> bool:
        """The pending-push flag, owned by :class:`PushScheduler`."""
        return self._push_scheduler._push_pending

    @_push_pending.setter
    def _push_pending(self, value: bool) -> None:
        self._push_scheduler._push_pending = value

    @property
    def _timer(self) -> threading.Timer | None:
        """The idle push timer, owned by :class:`PushScheduler`."""
        return self._push_scheduler._timer

    def _ensure_git_root(self, repo_path: Path) -> Path | None:
        """Discover and memoise the git root via :class:`RepoBootstrap`."""
        return self._bootstrap.ensure_git_root(repo_path)

    def validate_startup(self, repo_path: Path) -> None:
        """Validate startup git settings for token-authenticated workflows."""
        self._bootstrap.validate_startup(repo_path)

    def _ensure_write_init(self) -> None:
        """One-time initialisation for the write path (identity/push/LFS)."""
        if self._write_init_done or self._git_root is None:
            return
        with self._lock:
            if self._write_init_done or self._git_root is None:
                return
            self._bootstrap.check_remote_protocol(self._git_root)
            self._check_identity()
            if self._enable_push:
                self._push_scheduler.push_if_unpushed()
            # LFS pull runs under the git lock to avoid overlapping git ops.
            # Forward auth credentials so token-protected LFS backends
            # authenticate with the same GIT_ASKPASS mechanism used for push.
            if self._enable_pull or self._enable_push:
                env = self._git_env()
                try:
                    self._lfs_pull(env=env)
                finally:
                    self._cleanup_git_env(env)
            self._write_init_done = True

    #: Opt into the dispatcher's ``old_path=`` keyword on renames (#894), so
    #: the rename commit stages exactly the two paths the rename touched.
    #: See :data:`~markdown_vault_mcp.types.ACCEPTS_OLD_PATH_ATTR`.
    accepts_old_path: bool = True

    #: Opt into the dispatcher's ``principal=`` keyword (#1160), so the commit
    #: author reflects the identity resolved at the MCP tool edge instead of a
    #: request-context read that is always empty on the dispatcher thread
    #: (#1218). See :data:`~markdown_vault_mcp.types.ACCEPTS_PRINCIPAL_ATTR`.
    accepts_principal: bool = True

    def __call__(
        self,
        path: Path,
        content: str,  # noqa: ARG002
        operation: WriteOperation,
        *,
        old_path: Path | None = None,
        principal: Principal | None = None,
    ) -> None:
        """WriteCallback interface: stage + commit, then schedule push.

        Args:
            path: Absolute path of the file the operation landed on. For a
                rename this is the *new* path.
            content: File content at write time; unused here, since staging
                reads the working tree.
            operation: The kind of write that occurred.
            old_path: For a rename, the absolute path the file moved from,
                so staging can be scoped to it and *path* (#894).
            principal: The identity performing the write, resolved at the MCP
                tool edge and snapshotted through the dispatcher queue
                (#1160). Its display name / email become the commit's
                ``--author``; ``None`` fields (or no principal) fall back to
                the static committer identity.
        """
        if self._closed:
            return

        self._ensure_git_root(path)
        if self._git_root is None:
            logger.debug(
                "No git repository found for %s; git operations disabled", path
            )
            return

        self._ensure_write_init()

        if self._git_root is None:
            return

        try:
            principal_name = principal.display_name if principal is not None else None
            principal_email = principal.email if principal is not None else None
            effective_name = principal_name or self._commit_name
            effective_email = principal_email or self._commit_email
            logger.debug(
                "git_identity_resolved name=%s email=%s name_from_principal=%s email_from_principal=%s",
                effective_name,
                effective_email,
                principal_name is not None,
                principal_email is not None,
            )
            with self._lock:
                _stage_and_commit(
                    self._git_root,
                    path,
                    operation,
                    old_path=old_path,
                    commit_name=self._commit_name,
                    commit_email=self._commit_email,
                    author_name=effective_name
                    if effective_name != self._commit_name
                    else None,
                    author_email=effective_email
                    if effective_email != self._commit_email
                    else None,
                )
            if self._enable_push:
                self._push_scheduler.schedule_push()
        except subprocess.CalledProcessError as exc:
            sanitized_stderr = self._redact(exc.stderr or "")
            logger.error(
                "Git operation failed for %s (%s): command %s returned %d\n%s",
                path,
                operation,
                exc.cmd,
                exc.returncode,
                sanitized_stderr,
            )
        except Exception:
            logger.error(
                "Git operation failed for %s (%s)",
                path,
                operation,
                exc_info=True,
            )

    def _check_identity(self) -> None:
        """Warn once at startup if no git committer identity is configured.

        Runs ``git config user.email`` against the repo.  If it returns
        nothing the repo (and global) git config have no identity set, so
        commits will use the identity supplied to this strategy instance.
        """
        if self._git_root is None:
            return
        try:
            result = subprocess.run(
                ["git", "-C", str(self._git_root), "config", "user.email"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return
        if not result.stdout.strip() and (
            self._commit_name == self.DEFAULT_COMMIT_NAME
            and self._commit_email == self.DEFAULT_COMMIT_EMAIL
            and not self._commit_name_claim
            and not self._commit_email_claim
        ):
            logger.warning(
                "Git: no user.email in git config — commits will use "
                "committer identity '%s <%s>'. Set MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME "
                "and MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL to override.",
                self._commit_name,
                self._commit_email,
            )

    def _lfs_pull(self, env: dict[str, str] | None = None) -> None:
        """Run ``git lfs pull`` to resolve LFS pointers, if LFS is enabled.

        Called during lazy init and after successful ff-only pull ticks
        (:meth:`sync_once`) so LFS pointer files are resolved before reads,
        indexing, and git commits.
        Failures are logged at ERROR and never propagated to the caller.
        """
        if not self._git_lfs or self._git_root is None:
            return
        try:
            result = subprocess.run(
                ["git", "-C", str(self._git_root), "lfs", "pull"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            logger.info("Git LFS: pulled from remote")
            if result.stdout.strip():
                logger.debug("Git LFS pull output: %s", result.stdout.strip())
        except subprocess.CalledProcessError as exc:
            logger.error(
                "Git LFS pull failed: command %s returned %d\n%s",
                exc.cmd,
                exc.returncode,
                exc.stderr or "",
            )
        except FileNotFoundError:
            logger.error(
                "Git LFS pull failed: git not found on PATH. "
                "Install git or set MARKDOWN_VAULT_MCP_GIT_LFS=false to suppress this error."
            )

    def _build_pull_result_advanced(
        self,
        git_root: Path,
        env: dict[str, str] | None,
        *,
        from_sha: str,
        reason: str,
        conflict_files: tuple[str, ...] = (),
    ) -> PullResult:
        """Build a successful PullResult after HEAD has advanced.

        Captures the post-pull HEAD SHA and runs ``git lfs pull`` so any
        LFS pointers brought in by the merge/rebase are resolved before
        callers continue.  Used by both the plain-rebase success path and
        the conflicts-resolved-with-siblings success path in
        :meth:`force_pull` / :meth:`_force_pull_rebase_fallback`.

        Args:
            git_root: Working-tree root.
            env: Optional GIT_ASKPASS environment.
            from_sha: HEAD SHA captured before the operation.
            reason: One of the ``PULL_REASON_*`` constants for the success
                shape (typically ``REBASED`` or
                ``CONFLICTS_RESOLVED_WITH_SIBLINGS``).
            conflict_files: Tuple of Syncthing-style sibling paths written
                during conflict resolution.  Empty for the plain-rebase
                path.

        Returns:
            A ``PullResult`` with ``applied=True``, ``fast_forward=False``,
            ``commits_pulled=0``, ``to_sha`` set to the post-operation
            HEAD, and the provided ``reason`` / ``conflict_files``.
        """
        new_head = self.head_sha(git_root)
        self._lfs_pull(env=env)
        return PullResult(
            applied=True,
            fast_forward=False,
            commits_pulled=0,
            from_sha=from_sha,
            to_sha=new_head,
            reason=reason,
            conflict_files=conflict_files,
        )

    # ------------------------------------------------------------------
    # Synchronous force-trigger helpers (used by the ``git_sync`` MCP tool)
    # ------------------------------------------------------------------

    @property
    def is_managed(self) -> bool:
        """Whether this strategy owns a managed clone of a remote repository.

        Part of the :class:`~markdown_vault_mcp.git.interfaces.Syncer` seam
        (#1229): the ``git_sync`` tool gates on it, and did so by reading the
        private attribute before the promotion.
        """
        return self._managed

    def resolve_force_repo(self) -> Path:
        """Return the working tree path used by ``force_*`` methods.

        Returns:
            The configured working tree.

        Raises:
            RuntimeError: When no ``repo_path`` was configured at
                construction time.  The ``force_*`` methods require an
                explicit working tree because they cannot infer one from
                a per-write callback path.
        """
        if self._repo_path is None:
            raise RuntimeError(
                "GitWriteStrategy.force_* requires repo_path to be set at "
                "construction time."
            )
        return self._repo_path

    def head_sha(self, git_root: Path) -> str:
        """Return the current HEAD SHA of *git_root*.

        Args:
            git_root: The working tree to read.

        Returns:
            The full HEAD SHA.
        """
        return self._git(git_root, "rev-parse", "HEAD").strip()

    def branch_name(self, git_root: Path) -> str:
        """Return the checked-out branch name of *git_root*.

        A detached HEAD yields ``"HEAD"`` from git itself.  Failures to invoke
        git at all propagate; the caller decides whether a fallback is
        appropriate.

        Args:
            git_root: The working tree to read.

        Returns:
            The branch name.
        """
        return self._git(git_root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def _git(
        self,
        git_root: Path,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> str:
        """Run ``git -C <git_root> <args>`` and return stdout.

        Thin wrapper used by the ``force_*`` helpers — keeps subprocess
        boilerplate (capture, text mode, check=True) in one place.

        Raises:
            subprocess.CalledProcessError: If git exits non-zero.  Callers
                handle this for branches that can legitimately fail
                (e.g. ``merge --ff-only``).
        """
        return run_git(git_root, *args, env=env)

    def _run_git_capturing(
        self,
        git_root: Path,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command and return the completed process without raising.

        Sister of :meth:`_git` for paths that need to inspect ``returncode``
        and ``stderr`` instead of letting :class:`subprocess.CalledProcessError`
        propagate.  Used in :meth:`_force_pull_rebase_fallback` where we need
        to make recovery decisions based on whether ``git rebase --abort``
        succeeded, whether the working tree has an in-progress rebase, etc.

        Args:
            git_root: Working-tree root used for ``git -C``.
            *args: Git subcommand and arguments (without the leading ``git``).
            env: Optional environment, typically ``self._git_env()`` for
                token-bearing operations.  ``None`` inherits the parent process
                environment.

        Returns:
            :class:`subprocess.CompletedProcess` with ``returncode``, ``stdout``,
            and ``stderr`` populated.  Stderr will need to be passed through
            :meth:`_redact` before logging or surfacing to callers.
        """
        return run_git_capturing(git_root, *args, env=env)

    def _tracking_ref(
        self, git_root: Path, env: dict[str, str] | None = None
    ) -> str | None:
        """Resolve the remote-tracking ref to sync against (``origin/<branch>``).

        Thin instance wrapper over :func:`resolve_tracking_ref`.  Returns the
        verified ref name (e.g. ``"origin/main"``), falling back to
        ``origin/HEAD`` for detached / non-tracking checkouts, or ``None`` when
        neither resolves.  Used in place of ``@{upstream}`` so sync does not
        depend on branch tracking being configured.
        """
        return resolve_tracking_ref(git_root, env)

    def force_pull(self, *, dry_run: bool = False) -> PullResult:
        """Pull from ``origin`` synchronously and return a structured result.

        The remote-tracking branch is resolved as ``origin/<current-branch>``
        (see :meth:`_tracking_ref`) so this method works even when branch
        tracking (``@{upstream}``) was never configured on the local clone —
        falling back to ``origin/HEAD`` for a detached checkout.

        Acquires :attr:`_lock` for the duration so the periodic pull loop
        and the per-write commit path cannot race against the fetch /
        merge / rebase pipeline.  This blocks writes for the network
        round-trip; that is acceptable for the interactive ``git_sync``
        tool and mirrors what :meth:`sync_once` already does.

        Before the merge it self-quiesces via :meth:`_quiesce_writes`: new
        writes are paused and the deferred-commit queue is drained (best-effort,
        time-bounded) so a write that landed just before the pull is committed
        first and the merge runs on a clean tree (#571). Skipped under
        ``dry_run`` (which only fetches and never touches the working tree).

        On ``ff-only`` failure (divergent history) the implementation
        falls through to the same rebase + Syncthing-style sibling write
        path used by :meth:`sync_once` (see
        :func:`~markdown_vault_mcp.git.conflict.resolve_rebase_conflicts` and
        :func:`~markdown_vault_mcp.git.conflict.write_conflict_files`).  When
        the conflict-resolution
        path produces sibling files HEAD has advanced to the remote and
        :attr:`PullResult.applied` is ``True`` with
        :attr:`PullResult.reason` set to
        ``"conflicts_resolved_with_siblings"``.

        After a successful HEAD advance — fast-forward or sibling
        resolution — :meth:`_lfs_pull` runs so any LFS pointers in the
        new commits are materialised before the caller sees the working
        tree.

        A strategy built without remote sync — unmanaged / commit-only
        mode, where ``enable_pull`` is ``False`` — has no remote to pull
        from, so this returns ``applied=False`` with reason
        ``"pull_disabled"`` **before running any git command** (#1128).
        Without that gate the pipeline ran ``git fetch origin`` on a
        remoteless checkout (answering ``"fetch_failed"``, which reads as
        retryable) and raised ``CalledProcessError`` out of ``head_sha``
        on a vault that is not a git repository at all. ``enable_pull``
        gated only the periodic loop before; it now gates every pull.

        Args:
            dry_run: When ``True``, runs ``git fetch`` and computes the
                would-be pull without modifying HEAD.  Returns
                ``applied=False`` with ``commits_pulled`` set to the count
                that *would* have been pulled.

        Returns:
            :class:`PullResult` describing the operation.  See the
            ``reason`` field for the full enumeration of outcomes.

        Raises:
            RuntimeError: When the strategy was constructed without
                ``repo_path``.
        """
        if not self._enable_pull:
            logger.info(
                "Git force_pull: pull is disabled for this strategy "
                "(no managed remote); skipping git entirely"
            )
            return PullResult(
                applied=False,
                fast_forward=False,
                commits_pulled=0,
                from_sha="",
                to_sha="",
                reason=PULL_REASON_PULL_DISABLED,
            )
        git_root = self.resolve_force_repo()
        return self._pull_pipeline(git_root, dry_run=dry_run)

    def _pull_pipeline(
        self,
        git_root: Path,
        *,
        dry_run: bool = False,
        log_prefix: str = "Git force_pull",
    ) -> PullResult:
        """Run the shared fetch → ff-only → rebase → sibling pipeline.

        The single implementation behind :meth:`force_pull` (interactive
        ``git_sync`` tool) and :meth:`sync_once` (periodic pull loop) —
        #879: the loop previously carried a diverging re-implementation
        whose post-abort upstream restore had no failure handling.

        Args:
            git_root: Resolved working-tree root.
            dry_run: Fetch and compute the would-be pull without touching
                HEAD.
            log_prefix: Message prefix identifying the calling entry point
                (``"Git force_pull"`` / ``"Git pull"``).

        Returns:
            :class:`PullResult`; see :meth:`force_pull` for the outcome
            enumeration.
        """
        env = self._git_env()
        try:
            with self._quiesce_writes(skip=dry_run), self._lock:
                from_sha = self.head_sha(git_root)

                # Always fetch first — both dry-run and real-pull need the
                # remote-tracking ref refreshed before comparing SHAs.
                try:
                    self._git(git_root, "fetch", "origin", env=env)
                except subprocess.CalledProcessError as exc:
                    # Sanitise the token before logging — fetch is the
                    # network-touching subprocess in this method, and git
                    # error messages can echo the URL with credentials
                    # back at the user.  Mirrors the redaction pattern
                    # already used in ``PushScheduler.do_push_safe`` and
                    # ``force_push``.
                    stderr = self._redact((exc.stderr or "").strip())
                    logger.warning(
                        "%s: fetch failed: %s",
                        log_prefix,
                        stderr,
                    )
                    return PullResult.head_unchanged_failure(
                        from_sha, PULL_REASON_FETCH_FAILED
                    )

                # Resolve the remote-tracking ref (``origin/<branch>``,
                # falling back to ``origin/HEAD`` for a non-tracking or
                # detached checkout) and read its SHA.  An unresolvable or
                # unparseable ref is one outcome: no usable remote.
                ref = self._tracking_ref(git_root, env)
                remote_sha: str | None = None
                if ref is not None:
                    try:
                        remote_sha = self._git(
                            git_root, "rev-parse", ref, env=env
                        ).strip()
                    except subprocess.CalledProcessError:
                        remote_sha = None
                if ref is None or remote_sha is None:
                    return PullResult.head_unchanged_failure(
                        from_sha, PULL_REASON_NO_REMOTE
                    )

                if remote_sha == from_sha:
                    # Already up to date — successful no-op (applied=True even on dry_run).
                    return PullResult(
                        applied=True,
                        fast_forward=True,
                        commits_pulled=0,
                        from_sha=from_sha,
                        to_sha=from_sha,
                    )

                # Count commits between local and remote.  When the local
                # branch is behind the remote this is the number of commits
                # ``merge --ff-only`` would apply.
                commits_ahead = self._git(
                    git_root,
                    "rev-list",
                    "--count",
                    f"{from_sha}..{remote_sha}",
                    env=env,
                ).strip()
                try:
                    commits_pulled = int(commits_ahead)
                except ValueError:
                    # ``rev-list --count`` is documented to print a single
                    # integer; if parsing fails, the underlying git call is
                    # broken in a way we should surface rather than silently
                    # report 0 commits.  Fall back to 0 but log loudly.
                    logger.warning(
                        "%s: could not parse commit count %r "
                        "from `git rev-list --count %s..%s`",
                        log_prefix,
                        commits_ahead,
                        from_sha,
                        remote_sha,
                    )
                    commits_pulled = 0

                if dry_run:
                    # Heuristic: assume fast-forward.  Actual ff-ness is
                    # only known after attempting the merge; the conflict
                    # path below corrects this for non-dry-run calls.
                    return PullResult(
                        applied=False,
                        fast_forward=True,
                        commits_pulled=commits_pulled,
                        from_sha=from_sha,
                        to_sha=remote_sha,
                    )

                # Attempt fast-forward merge first.  On divergence fall
                # through to rebase + Syncthing-style sibling resolution,
                # mirroring :meth:`sync_once`.
                try:
                    self._git(git_root, "merge", "--ff-only", remote_sha, env=env)
                except subprocess.CalledProcessError as ff_exc:
                    logger.debug(
                        "%s: ff-only merge failed, attempting rebase: %s",
                        log_prefix,
                        (ff_exc.stderr or "").strip(),
                    )
                    return self._force_pull_rebase_fallback(
                        git_root=git_root,
                        env=env,
                        from_sha=from_sha,
                        ref=ref,
                        log_prefix=log_prefix,
                    )

                # Fast-forward succeeded.  ``remote_sha`` is the new HEAD —
                # no need to re-read it via ``head_sha``.
                self._lfs_pull(env=env)
                return PullResult(
                    applied=True,
                    fast_forward=True,
                    commits_pulled=commits_pulled,
                    from_sha=from_sha,
                    to_sha=remote_sha,
                )
        finally:
            self._cleanup_git_env(env)

    def _force_pull_rebase_fallback(
        self,
        *,
        git_root: Path,
        env: dict[str, str] | None,
        from_sha: str,
        ref: str,
        log_prefix: str = "Git force_pull",
    ) -> PullResult:
        """Attempt rebase + Syncthing-style sibling resolution.

        Called by :meth:`_pull_pipeline` when ``merge --ff-only`` failed
        because local and remote histories diverged.  Returns a
        structured :class:`PullResult`; :meth:`sync_once` adapts it back
        to its historical bool (#879).

        Must be called with :attr:`_lock` already held — it issues
        further git commands against the same working tree.

        Args:
            git_root: Working-tree root used for git ``-C``.
            env: Optional GIT_ASKPASS environment for token auth.
            from_sha: HEAD SHA captured before the fetch.
            ref: Remote-tracking ref (``origin/<branch>``) already resolved
                and verified non-``None`` by :meth:`_pull_pipeline` before
                delegating here.  Used as the rebase target and the
                post-abort upstream-restore ref.
            log_prefix: Message prefix identifying the calling entry point
                (``"Git force_pull"`` / ``"Git pull"``).

        Returns:
            :class:`PullResult` whose ``reason`` is one of
            ``"rebased"`` (plain rebase succeeded, HEAD advanced,
            ``applied=True``),
            ``"conflicts_resolved_with_siblings"`` (HEAD advanced,
            siblings written, ``applied=True``),
            ``"conflict_resolution_failed"`` (HEAD unchanged,
            ``applied=False``), or
            ``"non_fast_forward_with_conflicts"`` (rebase started but
            could not be cleanly resolved or aborted, ``applied=False``).
        """
        # First try a plain rebase — this handles the common case where
        # local commits touch *different* files than the upstream commits
        # and replay cleanly with no manual intervention.
        try:
            self._git(git_root, "rebase", ref, env=env)
        except subprocess.CalledProcessError:
            # Real conflicts during rebase — resolve by accepting upstream
            # and saving the local MCP versions as Syncthing-style siblings.
            #
            # ``conflict.resolve_rebase_conflicts`` runs ``git checkout --ours``
            # and ``git add`` with ``check=True``; if either raises mid-loop the
            # repository is left in a half-rebased state.
            # ``conflict.resolve_conflicts_safely`` wraps that in a defensive
            # abort so a subsequent ``force_pull`` (or any per-write commit
            # path) does not trip over the leftover ``rebase-merge`` directory.
            saved, early_exit = conflict.resolve_conflicts_safely(
                git_root, env, from_sha, token=self._token
            )
            if early_exit is not None:
                return early_exit
            # ``resolve_conflicts_safely`` returns ``(saved, None)`` on
            # success and ``(None, PullResult)`` on failure — exactly one
            # is non-None.  After the early-exit guard above, ``saved`` is
            # guaranteed non-None; assert so mypy can narrow.
            assert saved is not None

            # If a rebase is still in progress (loop hit its iteration
            # limit, or exited via ``break`` without completing), abort
            # cleanly so the working tree is consistent before we write
            # conflict files.
            rebase_in_progress = conflict.rebase_in_progress(
                git_root, env, token=self._token
            )

            if rebase_in_progress:
                if not conflict.abort_in_progress_rebase(
                    git_root, env, token=self._token
                ):
                    return PullResult.head_unchanged_failure(
                        from_sha, PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS
                    )
                saved = conflict.restore_upstream_paths(
                    git_root, env, saved, ref, token=self._token
                )

            if not saved:
                logger.warning(
                    "%s: conflict resolution failed, leaving HEAD unchanged",
                    log_prefix,
                )
                return PullResult.head_unchanged_failure(
                    from_sha, PULL_REASON_CONFLICT_RESOLUTION_FAILED
                )

            # Rebase has already completed via ``git rebase --continue`` — HEAD
            # has advanced even if the sibling-files commit below fails.
            actual_head = self.head_sha(git_root)
            written = conflict.write_conflict_files(
                git_root,
                saved,
                env,
                commit_name=self._commit_name,
                commit_email=self._commit_email,
                token=self._token,
            )
            if written is None:
                logger.warning("%s: conflict commit failed, skipping", log_prefix)
                return PullResult(
                    applied=False,
                    fast_forward=False,
                    commits_pulled=0,
                    from_sha=from_sha,
                    to_sha=actual_head,
                    reason=PULL_REASON_CONFLICT_RESOLUTION_FAILED,
                )
            for cf in written:
                logger.warning(
                    "%s: conflict resolved, saved MCP version as %s",
                    log_prefix,
                    cf,
                )
            logger.info(
                "%s: rebase completed with %d conflict file(s)",
                log_prefix,
                len(written),
            )
            # HEAD has advanced past the upstream because conflict
            # resolution itself produced a new commit on top.  The helper
            # captures the actual new HEAD rather than ``remote_sha``.
            return self._build_pull_result_advanced(
                git_root,
                env,
                from_sha=from_sha,
                reason=PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS,
                conflict_files=tuple(written),
            )

        # Plain rebase succeeded — local commits replayed cleanly on top
        # of the upstream.  HEAD has advanced.
        logger.info(
            "%s: ff-only not possible, rebased local commits onto upstream",
            log_prefix,
        )
        return self._build_pull_result_advanced(
            git_root,
            env,
            from_sha=from_sha,
            reason=PULL_REASON_REBASED,
        )

    def force_push(self, *, dry_run: bool = False) -> PushResult:
        """Push local commits to ``origin`` synchronously.

        Never force-pushes — the underlying ``git push origin`` is a plain
        fast-forward push.  When the remote has commits the local clone
        has not seen, the push is rejected and the returned
        :class:`PushResult` carries ``reason="non_fast_forward"`` plus a
        hint pointing at ``git_sync(direction='pull')``.  The caller is
        expected to reconcile via the pull path and then retry.

        Acquires :attr:`_lock` for the duration so the periodic pull loop
        and the per-write commit + deferred-push pipeline cannot race
        against the synchronous push.  This blocks writes for the network
        round-trip; that is acceptable for the interactive ``git_sync``
        tool and mirrors :meth:`force_pull`.

        ``dry_run`` is a no-op.  Git has no safe local probe for "would
        this push be accepted by the remote": the only authoritative
        check is to actually attempt the push.  Rather than silently
        substitute a misleading approximation, we surface this with
        ``reason="dry_run_unsupported"`` so callers can document the
        limitation.

        Args:
            dry_run: When ``True``, returns immediately without contacting
                the remote.  See above for the rationale.

        Returns:
            :class:`PushResult` describing the operation.  See the
            ``reason`` field for the full enumeration of outcomes.

        Raises:
            RuntimeError: When the strategy was constructed without
                ``repo_path``.
        """
        git_root = self.resolve_force_repo()

        with self._lock:
            local_head = self.head_sha(git_root)

            # Resolve the remote-tracking SHA before the push.  Mirrors
            # :meth:`force_pull` — derive ``origin/<branch>`` from the current
            # branch (see :meth:`_tracking_ref`), falling back to
            # ``origin/HEAD`` for a detached checkout, so the lookup does not
            # depend on ``@{upstream}`` tracking being configured.
            ref = self._tracking_ref(git_root)
            try:
                if ref is None:
                    raise subprocess.CalledProcessError(1, "git rev-parse")
                remote_sha_before = self._git(git_root, "rev-parse", ref).strip()
            except subprocess.CalledProcessError:
                return PushResult(
                    applied=False,
                    commits_pushed=0,
                    remote_sha_before="",
                    remote_sha_after="",
                    reason=PUSH_REASON_NO_REMOTE,
                    hint=(
                        "No remote-tracking branch (origin/<branch>) could be "
                        "resolved for the current branch.  Push the branch to "
                        "origin once (`git push -u origin <branch>`) so the "
                        "remote-tracking ref exists."
                    ),
                )

            if dry_run:
                # Document the limitation rather than fake a result.
                return PushResult(
                    applied=False,
                    commits_pushed=0,
                    remote_sha_before=remote_sha_before,
                    remote_sha_after=remote_sha_before,
                    reason=PUSH_REASON_DRY_RUN_UNSUPPORTED,
                    hint=(
                        "force_push has no dry_run mode: git provides no safe "
                        "local probe for whether the remote will accept a push. "
                        "Re-invoke with dry_run=False to actually push."
                    ),
                )

            # No-op: local already matches the remote-tracking SHA.  We
            # could let `git push` short-circuit on its own, but returning
            # early avoids a subprocess + reads more clearly in logs.
            if remote_sha_before == local_head:
                return PushResult(
                    applied=True,
                    commits_pushed=0,
                    remote_sha_before=remote_sha_before,
                    remote_sha_after=remote_sha_before,
                )

            # Count commits between remote and local.  When the local
            # branch is strictly ahead this is the count `git push` will
            # send; when histories diverge `git push` would reject the
            # push as non-fast-forward and the count is best-effort.
            commits_ahead_str = self._git(
                git_root,
                "rev-list",
                "--count",
                f"{remote_sha_before}..{local_head}",
            ).strip()
            try:
                commits_pushed = int(commits_ahead_str)
            except ValueError:
                logger.warning(
                    "Git force_push: could not parse commit count %r "
                    "from `git rev-list --count %s..%s`",
                    commits_ahead_str,
                    remote_sha_before,
                    local_head,
                )
                commits_pushed = 0

            # Use the strategy's git env so token-based HTTPS auth works
            # through the same GIT_ASKPASS mechanism the deferred-push
            # path uses.  Cleaned up in the finally block below.
            env = self._git_env()
            try:
                try:
                    self._git(git_root, "push", "origin", env=env)
                except subprocess.CalledProcessError as exc:
                    # Redact token if it leaked into stderr.  Mirrors the
                    # sanitisation in :meth:`_do_push_safe`.
                    stderr = self._redact((exc.stderr or "").strip())

                    # Detect the specific non-fast-forward case so the
                    # caller can route to git_sync(direction='pull').
                    # Git's wording is stable: "non-fast-forward" appears
                    # both in the rejection line and the hint paragraph.
                    if "non-fast-forward" in stderr or "fetch first" in stderr:
                        logger.warning(
                            "Git force_push: rejected as non-fast-forward "
                            "(local %s vs remote %s)",
                            local_head,
                            remote_sha_before,
                        )
                        return PushResult(
                            applied=False,
                            commits_pushed=0,
                            remote_sha_before=remote_sha_before,
                            remote_sha_after=remote_sha_before,
                            reason=PUSH_REASON_NON_FAST_FORWARD,
                            hint=(
                                "Remote has commits the local clone has not "
                                "seen.  Run git_sync(direction='pull') to "
                                "reconcile (fast-forward when possible, "
                                "Syncthing-style siblings on real conflict), "
                                "then retry git_sync(direction='push')."
                            ),
                        )

                    logger.error(
                        "Git force_push: push failed: %s",
                        stderr,
                    )
                    truncated = stderr[:200]
                    return PushResult(
                        applied=False,
                        commits_pushed=0,
                        remote_sha_before=remote_sha_before,
                        remote_sha_after=remote_sha_before,
                        reason=PUSH_REASON_PUSH_FAILED,
                        hint=truncated or "git push exited non-zero",
                    )
            finally:
                self._cleanup_git_env(env)

            # Push succeeded — remote now matches local HEAD.
            return PushResult(
                applied=True,
                commits_pushed=commits_pushed,
                remote_sha_before=remote_sha_before,
                remote_sha_after=local_head,
            )

    def sync_once(self, repo_path: Path) -> bool:
        """Fetch and update once, returning True if HEAD advanced.

        Thin adapter over :meth:`_pull_pipeline` (#879) — the periodic
        pull loop and the interactive ``git_sync`` tool now share one
        fetch → ff-only → rebase → sibling implementation, so the loop
        gets the pipeline's safe conflict handling: defensive rebase
        abort and an upstream restore that drops paths whose restore
        failed instead of committing stale local content over them.

        The pipeline self-quiesces before the merge via
        :meth:`_quiesce_writes` (pause new writes + drain the
        deferred-commit queue, best-effort/time-bounded) so a write
        racing the periodic pull is committed first and the merge runs
        on a clean tree (#571). The pause is held for the whole fetch +
        merge — including the network round-trip — so MCP writes block
        for the pull's duration; acceptable for a periodic background
        pull (default every 600 s) and a fast fetch.
        """
        if self._closed or not self._enable_pull:
            return False

        git_root = self._ensure_git_root(repo_path)
        if git_root is None:
            return False

        try:
            # Pre-check the remote-tracking ref (local ref inspection, no
            # network) so a remoteless checkout skips at INFO level rather
            # than surfacing the pipeline's fetch-failed WARNING on every
            # loop tick.
            if self._tracking_ref(git_root, None) is None:
                logger.info(
                    "Git pull: no remote-tracking ref resolvable; skipping fetch"
                )
                return False
            result = self._pull_pipeline(git_root, log_prefix="Git pull")
        except FileNotFoundError:
            logger.info("Git pull: git not found on PATH; pull loop disabled")
            return False
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Git pull: git command failed, skipping: %s",
                (exc.stderr or "").strip(),
            )
            return False

        return result.applied and result.to_sha != result.from_sha

    def set_write_quiescer(
        self,
        pause_writes: Callable[[], contextlib.AbstractContextManager[None]],
        drain_writes: Callable[[], bool],
    ) -> None:
        """Wire the write-quiescing callables used before a pull (#571).

        Called once by the owner (``Vault``) after the write-callback
        dispatcher exists, so both the interactive ``force_pull`` and the
        periodic ``sync_once`` can pause new writes and drain pending commits
        before the merge — independent of whether the periodic pull loop is
        started.

        Args:
            pause_writes: Context manager that blocks new file mutations while
                held (acquires the shared file-write lock).
            drain_writes: Blocks until all already-queued write callbacks have
                been committed; returns ``True`` when the queue drained (or
                there was nothing to drain), ``False`` if it did not finish or
                the dispatcher worker has died.
        """
        self._pause_writes = pause_writes
        self._drain_writes = drain_writes

    @contextlib.contextmanager
    def _quiesce_writes(self, *, skip: bool = False) -> Iterator[None]:
        """Pause new writes and drain pending commits for the duration.

        Enters ``pause_writes`` (blocking new writes + their callback enqueues),
        drains the queued commits, then yields so the caller's merge runs on a
        clean working tree. If the drain does not complete, logs a WARNING and
        proceeds anyway — a stalled drain must never block the pull; the worst
        case is the pre-fix dirty-tree churn for the still-pending commit. No-op
        when ``skip`` is set (e.g. a dry-run pull) or when no quiescer was wired
        (standalone / tests).

        DEADLOCK INVARIANT: the drain runs *before* the caller acquires
        ``self._lock`` (callers use ``with self._quiesce_writes(...), self._lock:``,
        which enters this context manager first). The drain blocks on the
        dispatcher worker, whose commit path acquires ``self._lock`` — so the
        caller must NOT already hold ``self._lock`` here, and the write callback
        must NEVER acquire the file-write lock that ``pause_writes`` holds.
        Reverse either and a pull with a pending commit deadlocks.
        """
        if skip or self._pause_writes is None or self._drain_writes is None:
            yield
            return
        with self._pause_writes():
            if not self._drain_writes():
                logger.warning(
                    "Git pull: write-callback queue did not fully drain before "
                    "the merge; proceeding anyway. A still-pending commit may "
                    "cause the pre-fix dirty-tree churn for this one merge. If "
                    "this recurs on every pull, the dispatcher worker may be "
                    "dead (see the prior 'dead worker' ERROR) and pending "
                    "commits will never land."
                )
            yield

    def start(
        self,
        *,
        repo_path: Path,
        pull_interval_s: int,
        on_pull: Callable[[], object] | None = None,
    ) -> None:
        """Start a periodic fetch + ff-only update loop in a daemon thread."""
        if self._closed or not self._enable_pull or pull_interval_s <= 0:
            return

        git_root = self._ensure_git_root(repo_path)
        if git_root is None:
            return

        # Guard: do not start the loop if no remote-tracking ref resolves.
        # This check is intentionally independent of the sync_once() call in
        # sync_from_remote_before_index() — start() may be called even when
        # the startup sync was skipped (pull_interval_s changed at runtime,
        # or Vault.start() called directly by library users).  The double
        # check is harmless (costs one git subprocess) and avoids noisy
        # "no remote ref" logs on every tick.
        env = None
        try:
            env = self._git_env()
            if self._tracking_ref(git_root, env) is None:
                logger.info(
                    "Git pull: no remote-tracking ref resolvable; pull loop disabled"
                )
                return
        except FileNotFoundError:
            logger.info("Git pull: git not found on PATH; pull loop disabled")
            return
        finally:
            self._cleanup_git_env(env)

        with self._lock:
            if self._pull_thread is not None and self._pull_thread.is_alive():
                return
            self._pull_repo_path = repo_path
            self._pull_interval_s = pull_interval_s
            self._on_pull = on_pull
            self._pull_stop.clear()
            self._pull_thread = threading.Thread(
                target=self._pull_loop, name="GitPullLoop", daemon=True
            )
            self._pull_thread.start()

    def _pull_loop(self) -> None:
        repo_path = self._pull_repo_path
        if repo_path is None:
            return

        while not self._pull_stop.is_set():
            try:
                did_advance = self.sync_once(repo_path)
                if did_advance and self._on_pull is not None:
                    pause = self._pause_writes
                    if pause is None:
                        self._on_pull()
                    else:
                        with pause():
                            self._on_pull()
                # Retry a pending push after the pull reconciled any
                # non-fast-forward divergence via its rebase step (#957).
                # PushScheduler.do_push's guard makes this a no-op when
                # nothing is pending.
                if self._enable_push:
                    self._push_scheduler.do_push_safe()
            except Exception:
                logger.exception("Git pull loop tick failed")
            # Wait until the next interval, or stop early.
            if self._pull_stop.wait(timeout=self._pull_interval_s):
                break

    def stop(self) -> None:
        """Stop the pull loop thread if it is running."""
        with self._lock:
            thread = self._pull_thread
            if thread is None:
                return
            self._pull_stop.set()
        # Do not block indefinitely on shutdown.
        thread.join(timeout=5.0)
        with self._lock:
            if self._pull_thread is thread:
                self._pull_thread = None

    def flush(self) -> None:
        """Block until any pending push completes.

        Cancels the idle timer and pushes immediately if there are
        pending local commits.  Thin delegation to
        :meth:`PushScheduler.flush`, which owns the timer/pending-flag
        mechanics (#893).
        """
        self._push_scheduler.flush()

    def close(self) -> None:
        """Cancel timer, flush pending push, mark strategy as closed.

        Sequencing: mark closed first (new writes become no-ops), stop the
        periodic pull thread, then flush the push scheduler so the final
        push happens with no pull tick racing it.
        """
        self._closed = True
        self.stop()
        self.flush()

    # ------------------------------------------------------------------
    # Read-only git history query methods
    # ------------------------------------------------------------------

    def get_file_history(
        self,
        repo_path: Path,
        path: Path | None,
        since: str | None,
        limit: int,
        until: str | None = None,
        *,
        is_dir: bool = False,
    ) -> list[HistoryEntry]:
        """Return commits that touched *path* (or the whole vault).

        Args:
            repo_path: Path inside the git repository (used to locate the root).
            path: Absolute path of the file (or directory, when *is_dir*) to
                filter on, or ``None`` for the entire vault.
            is_dir: When ``True``, scope history to *path*'s subtree instead of
                treating it as a single file (see :func:`query.get_file_history`).
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

        Returns:
            List of :class:`HistoryEntry` ordered from newest to oldest.

        Raises:
            ValueError: If ``git log`` exits non-zero (e.g. an invalid
                ``since`` / ``until`` expression).
        """
        return query.get_file_history(
            self._ensure_git_root(repo_path),
            repo_path,
            path,
            since,
            limit,
            until,
            token=self._token,
            username=self._username,
            is_dir=is_dir,
        )

    @staticmethod
    def _resolve_path_at_ref(
        git_root: Path,
        ref: str,
        cur_rel: str,
        env: dict[str, str] | None,
    ) -> str | None:
        """Return the path *cur_rel* had at *ref* via rename detection, else None."""
        return query.resolve_path_at_ref(git_root, ref, cur_rel, env)

    def get_file_diff(
        self,
        repo_path: Path,
        path: Path,
        ref: str | None,
        per_commit: bool,
        since_timestamp: str | None = None,
        limit: int | None = None,
        *,
        summarize_binary: bool = False,
    ) -> str | list[CommitDiff]:
        """Return a unified diff of *path* from *ref* to HEAD.

        Exactly one of *ref* or *since_timestamp* must be supplied.  When
        *since_timestamp* is given, it is resolved via
        ``git rev-list --before=<ts> -1 HEAD`` to the most recent commit at
        or before that instant.  Boundary is **inclusive**: a commit whose
        committer date equals *since_timestamp* IS the resolved ref.

        Args:
            repo_path: Path inside the git repository.
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
            summarize_binary: When True and the file is binary, return a
                ``--stat`` summary instead of a patch (#342).

        Returns:
            A unified diff string when *per_commit* is ``False``, or a list of
            :class:`CommitDiff` when *per_commit* is ``True``.

        Raises:
            ValueError: If *ref* is not found in history, *since_timestamp*
                cannot be resolved, or a git subprocess exits non-zero.
        """
        return query.get_file_diff(
            self._ensure_git_root(repo_path),
            path,
            ref,
            per_commit,
            since_timestamp,
            limit,
            token=self._token,
            username=self._username,
            summarize_binary=summarize_binary,
        )


def git_write_strategy(
    token: str | None = None,
    push_delay_s: float = 0,
    git_lfs: bool = True,
) -> GitWriteStrategy:
    """Create a :class:`GitWriteStrategy` callback.

    Convenience wrapper around :class:`GitWriteStrategy`.  With the
    default ``push_delay_s=0``, commits happen per-write but push only
    fires when :meth:`~GitWriteStrategy.close` or
    :meth:`~GitWriteStrategy.flush` is called.

    When used via :class:`~markdown_vault_mcp.vault.Vault`,
    ``Vault.close()`` automatically calls the strategy's
    ``close()``, so pushes flush on shutdown.  Callers using this
    as a bare ``WriteCallback`` must retain a reference and call
    ``close()`` explicitly.

    .. deprecated::
        Prefer :class:`GitWriteStrategy` directly for access to
        :meth:`~GitWriteStrategy.flush` and :meth:`~GitWriteStrategy.close`.

    .. note::
        The default ``push_delay_s=0`` here differs from
        :class:`GitWriteStrategy`'s default of ``30.0``.  This preserves
        backward compatibility (push on close/flush only).

    Args:
        token: PAT for HTTPS push.
        push_delay_s: Push delay in seconds (default 0 = push on close only).
        git_lfs: When ``True`` (default), run ``git lfs pull`` during init.

    Returns:
        A :class:`GitWriteStrategy` instance (also satisfies
        :data:`~markdown_vault_mcp.types.WriteCallback`).
    """
    return GitWriteStrategy(token=token, push_delay_s=push_delay_s, git_lfs=git_lfs)


_GIT_IDENTITY_UNSAFE = re.compile(r"[\r\n<>]")


def _sanitize_git_identity(value: str) -> str:
    """Strip characters that break git commit-object header parsing.

    Git commit objects use line-based headers; a newline or carriage return in
    an author/committer field can inject additional header lines.  Angle
    brackets break the ``Name <email>`` format git expects.  OIDC claim values
    are user-influenceable at the IdP, so sanitization is required before
    interpolating them into the ``--author`` string.
    """
    return _GIT_IDENTITY_UNSAFE.sub("", value)


def _is_tracked(root: str, path: Path) -> bool:
    """Return whether git tracks *path* in the repository at *root*.

    ``git ls-files`` lists the index, so a file staged or committed earlier
    answers ``True`` even once it has been removed from the working tree —
    which is exactly the state the old side of a rename is in by the time
    staging runs.

    Args:
        root: Git repository root, as a string.
        path: Absolute path to probe.

    Returns:
        ``True`` when the path is in the index.
    """
    result = subprocess.run(
        ["git", "-C", root, "ls-files", "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _stage_rename(root: str, path: Path, old_path: Path | None) -> None:
    """Stage both sides of a rename without touching anything else (#894).

    ``git add -A`` over an explicit pathspec records the old path's
    disappearance and the new path's arrival while leaving every other
    working-tree change alone, so a concurrent edit elsewhere in the vault is
    not swept into this commit. ``-A`` rather than ``-u`` because ``-u``
    would record no deletion for an old path git never tracked.

    An untracked old path is omitted from the pathspec rather than passed and
    tolerated: ``git add`` fails the *whole* invocation on a pathspec that
    matches nothing, which would leave the new path unstaged too.

    Args:
        root: Git repository root, as a string.
        path: Absolute path the file was renamed *to*.
        old_path: Absolute path the file was renamed *from*, or ``None`` from
            a caller predating #894 — which falls back to the historical
            repository-wide staging, imprecise in exactly the two ways this
            function fixes.
    """
    if old_path is None:
        logger.debug("git_stage_rename_unscoped path=%s", path)
        subprocess.run(
            ["git", "-C", root, "add", "-u"],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", root, "add", "--", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return

    pathspec = [str(path)]
    if _is_tracked(root, old_path):
        pathspec.insert(0, str(old_path))
    else:
        logger.debug("git_stage_rename_old_untracked old_path=%s", old_path)
    subprocess.run(
        ["git", "-C", root, "add", "-A", "--", *pathspec],
        capture_output=True,
        text=True,
        check=True,
    )


def _stage_and_commit(
    git_root: Path,
    path: Path,
    operation: WriteOperation,
    *,
    old_path: Path | None = None,
    commit_name: str = GitWriteStrategy.DEFAULT_COMMIT_NAME,
    commit_email: str = GitWriteStrategy.DEFAULT_COMMIT_EMAIL,
    author_name: str | None = None,
    author_email: str | None = None,
) -> None:
    """Stage and commit a single file change (no push).

    Args:
        git_root: Git repository root.
        path: Absolute path to the changed file.  For a rename, the *new*
            path.
        operation: The write operation type.
        old_path: For a rename, the absolute path the file moved from.
            Staging is then scoped to exactly *old_path* and *path* (#894).
            ``None`` falls back to a repository-wide ``git add -u``, which
            sweeps unrelated tracked modifications into the commit — see the
            comment on that branch.
        commit_name: Git committer name (overrides git config).
        commit_email: Git committer email (overrides git config).
        author_name: Git author name override.  Falls back to *commit_name*
            when ``None``.  Evaluated independently of *author_email*: when
            only one field is provided, the other is taken from the committer
            identity, producing a mixed ``--author`` string.
        author_email: Git author e-mail override.  Falls back to *commit_email*
            when ``None``.  Both values are sanitized (``\\r``, ``\\n``,
            ``<``, ``>`` stripped) before being interpolated into the
            ``--author`` string to prevent commit-object header injection.
    """
    root = str(git_root)

    # Stage the change.
    if operation == "delete":
        # File already removed from disk; stage the deletion.
        subprocess.run(
            ["git", "-C", root, "add", "-u", "--", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
    elif operation == "rename":
        _stage_rename(root, path, old_path)
    else:
        subprocess.run(
            ["git", "-C", root, "add", "--", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )

    # Generate commit message from operation and relative path.
    try:
        rel_path = path.relative_to(git_root)
    except ValueError:
        rel_path = path

    # Skip commit if staging produced no diff (e.g. writing identical content).
    check_result = subprocess.run(
        ["git", "-C", root, "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if check_result.returncode == 0:
        logger.debug(
            "Git: nothing staged for %s (%s), skipping commit", rel_path, operation
        )
        return

    commit_msg = f"{operation}: {rel_path}"

    # Build author string when per-request identity differs from committer.
    # Sanitize both sides of the comparison so a commit_name that itself
    # contains stripped chars (e.g. angle brackets) doesn't trigger a
    # spurious --author when no OIDC author is configured, and so a claim
    # that sanitizes to the same value as the committer is treated as equal.
    eff_author_name = _sanitize_git_identity(
        author_name if author_name is not None else commit_name
    )
    eff_author_email = _sanitize_git_identity(
        author_email if author_email is not None else commit_email
    )
    san_commit_name = _sanitize_git_identity(commit_name)
    san_commit_email = _sanitize_git_identity(commit_email)
    if author_name is not None and eff_author_name != author_name:
        logger.debug(
            "git_identity_sanitized field=author_name original=%s sanitized=%s",
            author_name,
            eff_author_name,
        )
    if author_email is not None and eff_author_email != author_email:
        logger.debug(
            "git_identity_sanitized field=author_email original=%s sanitized=%s",
            author_email,
            eff_author_email,
        )
    author_args: list[str] = []
    if eff_author_name != san_commit_name or eff_author_email != san_commit_email:
        author_args = ["--author", f"{eff_author_name} <{eff_author_email}>"]

    subprocess.run(
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
            commit_msg,
            *author_args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    logger.info("Git: committed %s (%s)", rel_path, operation)
