"""Git write strategy for auto-commit and push on write operations.

Provides :class:`GitWriteStrategy`, a stateful callback that commits
per-write and defers pushes to a background timer.  Also retains the
legacy :func:`git_write_strategy` factory for backward compatibility.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import os
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import frontmatter

if TYPE_CHECKING:
    from collections.abc import Callable

from fastmcp.server.dependencies import get_access_token as _get_access_token

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.types import CommitDiff, HistoryEntry

logger = logging.getLogger(__name__)

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


def _is_ssh_remote(url: str) -> bool:
    return url.startswith("git@") or url.startswith("ssh://")


def _normalize_remote(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[: -len(".git")]
    return normalized


def _build_askpass_env(token: str, username: str) -> dict[str, str]:
    fd, script_path_str = tempfile.mkstemp(suffix=".sh", prefix="git_askpass_")
    script_path = Path(script_path_str)
    os.close(fd)
    script_path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  *sername*) printf '%s\\n' \"${MVMCP_GIT_USERNAME:-}\" ;;\n"
        "  *) printf '%s\\n' \"${MVMCP_GIT_TOKEN:-}\" ;;\n"
        "esac\n"
    )
    script_path.chmod(stat.S_IRWXU)
    return {
        **os.environ,
        "GIT_ASKPASS": script_path_str,
        "GIT_TERMINAL_PROMPT": "0",
        "MVMCP_GIT_USERNAME": username,
        "MVMCP_GIT_TOKEN": token,
    }


def _find_git_root(path: Path) -> Path | None:
    """Find the git repository root containing *path*.

    Args:
        path: Absolute path to search from.

    Returns:
        The git repository root, or ``None`` if not inside a repo.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path if path.is_dir() else path.parent),
                "rev-parse",
                "--show-toplevel",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


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
            * ``"conflict_resolution_failed"`` — rebase produced no
              recoverable saved files and the working tree was
              restored; HEAD did not move.

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
    conflict_files: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def head_unchanged_failure(cls, from_sha: str, reason: str) -> PullResult:
        """Construct a ``PullResult`` for a failure path where HEAD did not move.

        The 5 failure paths in :meth:`GitWriteStrategy.force_pull` and its
        helpers (``no_remote``, ``non_fast_forward``,
        ``conflict_resolution_failed``, ``non_fast_forward_with_conflicts``,
        ``fetch_failed``) all share the same shape: ``applied=False``,
        ``fast_forward=False``, ``commits_pulled=0``, and
        ``to_sha == from_sha`` (HEAD unchanged).  This factory reduces the
        repetition.

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


def _extract_claim(claim_key: str | None) -> str | None:
    """Return the string value of an OIDC claim from the current request token.

    Returns ``None`` when *claim_key* is ``None``, no access token is present,
    the key is absent from the token's claims, or the value is not a non-empty
    string.
    """
    if claim_key is None:
        return None
    token = _get_access_token()
    if token is None:
        return None
    claims = getattr(token, "claims", None)
    if not isinstance(claims, dict):
        return None
    value = claims.get(claim_key)
    return value if isinstance(value, str) and value else None


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
        collection = Collection(on_write=strategy, ...)
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
        self._git_root: Path | None = None
        self._git_root_checked = False
        self._write_init_done = False
        self._push_pending = False
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._pull_stop = threading.Event()
        self._pull_thread: threading.Thread | None = None
        self._pull_interval_s: int = 0
        self._pull_repo_path: Path | None = None
        self._pause_writes: (
            Callable[[], contextlib.AbstractContextManager[None]] | None
        ) = None
        self._on_pull: Callable[[], None] | None = None
        if repo_path is not None:
            if self._managed:
                self._ensure_managed_repo(repo_path)
            else:
                self.validate_startup(repo_path)

    def _git_env(self) -> dict[str, str] | None:
        """Build environment for git subprocess calls.

        When a token is set, reuse the existing GIT_ASKPASS mechanism to avoid
        prompting interactively. This mirrors the push path and keeps the token
        out of command-line arguments.
        """
        if not self._token:
            return None
        return _build_askpass_env(self._token, self._username)

    def _cleanup_git_env(self, env: dict[str, str] | None) -> None:
        if env is None:
            return
        env.pop("MVMCP_GIT_USERNAME", None)
        env.pop("MVMCP_GIT_TOKEN", None)
        script_path_str = env.get("GIT_ASKPASS")
        if not script_path_str:
            return
        with contextlib.suppress(OSError):
            Path(script_path_str).unlink()

    def _redact(self, text: str) -> str:
        """Replace the configured PAT with ``***`` so it never reaches logs/responses.

        Args:
            text: Raw stderr / message text that may contain ``self._token``.

        Returns:
            The same text with every occurrence of ``self._token`` replaced by
            ``"***"``.  Returns ``text`` unchanged when no token is configured
            or the text doesn't contain it (cheap no-op for the common case).
        """
        if self._token and self._token in text:
            return text.replace(self._token, "***")
        return text

    def _ensure_git_root(self, repo_path: Path) -> Path | None:
        if self._git_root_checked:
            return self._git_root
        with self._lock:
            if not self._git_root_checked:
                self._git_root = _find_git_root(repo_path)
                self._git_root_checked = True
        return self._git_root

    def _get_origin_url(self, git_root: Path) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(git_root), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _ensure_managed_repo(self, repo_path: Path) -> None:
        if self._repo_url is None:
            raise ConfigurationError("Managed git mode requires a repo_url.")

        if self._token and _is_ssh_remote(self._repo_url):
            raise ConfigurationError(
                f"Managed mode repo URL {self._repo_url!r} uses SSH transport, but "
                "GIT_TOKEN auth requires HTTPS."
            )

        path = Path(repo_path)
        if path.exists():
            if not path.is_dir():
                raise ConfigurationError(
                    f"Managed mode requires SOURCE_DIR to be a directory: {path}"
                )
            is_empty = not any(path.iterdir())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            is_empty = True

        if is_empty:
            env = self._git_env()
            try:
                subprocess.run(
                    ["git", "clone", self._repo_url, str(path)],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise ConfigurationError(
                    "git is not installed or not on PATH."
                ) from exc
            except subprocess.CalledProcessError as exc:
                raise ConfigurationError(
                    f"Failed to clone managed git repo {self._repo_url!r} into {path}: "
                    f"{(exc.stderr or '').strip()}"
                ) from exc
            finally:
                self._cleanup_git_env(env)

        git_root = _find_git_root(path)
        if git_root is None:
            raise ConfigurationError(
                f"Managed mode requires SOURCE_DIR to be empty or a git repository: {path}"
            )
        origin_url = self._get_origin_url(git_root)
        if origin_url is None:
            raise ConfigurationError(
                f"Managed mode requires an 'origin' remote in repository {git_root}."
            )
        if _normalize_remote(origin_url) != _normalize_remote(self._repo_url):
            raise ConfigurationError(
                "Managed mode remote mismatch: existing origin is "
                f"{origin_url!r}, expected {self._repo_url!r}."
            )
        self._git_root = git_root
        self._git_root_checked = True
        self._check_remote_protocol(git_root)

    def _check_remote_protocol(self, git_root: Path) -> None:
        """Raise ConfigurationError if origin uses SSH while token auth is enabled."""
        if not self._token:
            return
        try:
            result = subprocess.run(
                ["git", "-C", str(git_root), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return
        if result.returncode != 0:
            # No remote configured; ignore here.
            return

        url = result.stdout.strip()
        if not _is_ssh_remote(url):
            return

        if url.startswith("ssh://git@"):
            https_url = "https://" + url[len("ssh://git@") :]
        elif url.startswith("ssh://"):
            https_url = "https://" + url[len("ssh://") :]
        else:
            without_prefix = url[len("git@") :]
            https_url = "https://" + without_prefix.replace(":", "/", 1)

        raise ConfigurationError(
            f"Remote URL {url!r} uses SSH transport, but GIT_TOKEN requires HTTPS.\n"
            f"Run: git -C {git_root} remote set-url origin {https_url}"
        )

    def validate_startup(self, repo_path: Path) -> None:
        """Validate startup git settings for token-authenticated workflows."""
        git_root = self._ensure_git_root(repo_path)
        if git_root is None:
            return
        self._check_remote_protocol(git_root)

    def _ensure_write_init(self) -> None:
        """One-time initialisation for the write path (identity/push/LFS)."""
        if self._write_init_done or self._git_root is None:
            return
        with self._lock:
            if self._write_init_done or self._git_root is None:
                return
            self._check_remote_protocol(self._git_root)
            self._check_identity()
            if self._enable_push:
                self._push_if_unpushed()
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

    def __call__(
        self,
        path: Path,
        content: str,  # noqa: ARG002
        operation: Literal["write", "edit", "delete", "rename"],
    ) -> None:
        """WriteCallback interface: stage + commit, then schedule push."""
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
            effective_name = (
                _extract_claim(self._commit_name_claim) or self._commit_name
            )
            effective_email = (
                _extract_claim(self._commit_email_claim) or self._commit_email
            )
            logger.debug(
                "git_identity_resolved name=%s email=%s name_from_claim=%s email_from_claim=%s",
                effective_name,
                effective_email,
                self._commit_name_claim is not None
                and effective_name != self._commit_name,
                self._commit_email_claim is not None
                and effective_email != self._commit_email,
            )
            with self._lock:
                _stage_and_commit(
                    self._git_root,
                    path,
                    operation,
                    commit_name=effective_name,
                    commit_email=effective_email,
                )
            if self._enable_push:
                self._schedule_push()
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

    def _schedule_push(self) -> None:
        """Reset the idle push timer."""
        with self._lock:
            self._push_pending = True
            if self._timer is not None:
                self._timer.cancel()
            if self._push_delay_s > 0:
                self._timer = threading.Timer(self._push_delay_s, self._do_push_safe)
                self._timer.daemon = True
                self._timer.start()

    def _do_push_safe(self) -> None:
        """Push wrapper that catches and logs errors."""
        try:
            self._do_push()
        except subprocess.CalledProcessError as exc:
            sanitized_stderr = self._redact(exc.stderr or "")
            logger.error(
                "Git push failed: command %s returned %d\n%s",
                exc.cmd,
                exc.returncode,
                sanitized_stderr,
            )
        except Exception:
            logger.error("Git push failed", exc_info=True)

    def _do_push(self) -> None:
        """Execute git push and clear pending flag.

        Note: ``_push_pending`` is cleared *before* calling ``_push()``.
        If the push fails, commits are not automatically retried — they
        will be pushed on the next write (which resets ``_push_pending``)
        or on the next startup via ``_push_if_unpushed()``.
        """
        with self._lock:
            if (
                not self._enable_push
                or not self._push_pending
                or self._git_root is None
            ):
                return
            self._push_pending = False

        with self._lock:
            _push(self._git_root, self._token, self._username)
            logger.info("Git: pushed to remote")

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
        if not result.stdout.strip():
            logger.warning(
                "Git: no user.email in git config — commits will use "
                "committer identity '%s <%s>'. Set MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME "
                "and MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL to override.",
                self._commit_name,
                self._commit_email,
            )

    def _push_if_unpushed(self) -> None:
        """On startup, push any local commits ahead of the remote."""
        if self._git_root is None or not self._enable_push:
            return

        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self._git_root),
                    "log",
                    "--oneline",
                    "@{upstream}..HEAD",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            logger.debug("Git: git not found, skipping unpushed check")
            return

        if result.returncode != 0:
            # No upstream or no remote — not an error at startup.
            logger.debug("Git: no upstream to check for unpushed commits")
            return

        if result.stdout.strip():
            logger.info("Git: found unpushed commits on startup, pushing now")
            try:
                _push(self._git_root, self._token, self._username)
            except subprocess.CalledProcessError as exc:
                sanitized_stderr = self._redact(exc.stderr or "")
                logger.error(
                    "Git startup push failed: command %s returned %d\n%s",
                    exc.cmd,
                    exc.returncode,
                    sanitized_stderr,
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

    def _resolve_rebase_conflicts(
        self,
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

    def _resolve_conflicts_safely(
        self,
        git_root: Path,
        env: dict[str, str] | None,
        from_sha: str,
    ) -> tuple[list[tuple[str, str]] | None, PullResult | None]:
        """Defensive wrapper around :meth:`_resolve_rebase_conflicts`.

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

        Returns:
            ``(saved, None)`` on success — ``saved`` is the list of
            ``(rel_path, mcp_content)`` tuples from
            :meth:`_resolve_rebase_conflicts`.

            ``(None, PullResult)`` on failure — caller should return the
            ``PullResult`` immediately.
        """
        try:
            saved = self._resolve_rebase_conflicts(git_root, env)
        except Exception:
            logger.error(
                "Git force_pull: conflict resolution raised — aborting rebase",
                exc_info=True,
            )
            abort_proc = self._run_git_capturing(git_root, "rebase", "--abort", env=env)
            if abort_proc.returncode != 0:
                logger.warning(
                    "Git force_pull: defensive `git rebase --abort` "
                    "after conflict-resolution failure also failed: %s",
                    self._redact((abort_proc.stderr or "").strip()),
                )
            return None, PullResult.head_unchanged_failure(
                from_sha, PULL_REASON_CONFLICT_RESOLUTION_FAILED
            )
        return saved, None

    def _rebase_in_progress(
        self,
        git_root: Path,
        env: dict[str, str] | None,
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

        Returns:
            ``True`` if a rebase appears to be in progress (or if we cannot
            tell); ``False`` only when ``rev-parse --git-dir`` succeeded
            AND no ``rebase-merge`` / ``rebase-apply`` directory exists.
        """
        git_dir_proc = self._run_git_capturing(
            git_root, "rev-parse", "--git-dir", env=env
        )
        if git_dir_proc.returncode != 0:
            logger.error(
                "Git force_pull: `git rev-parse --git-dir` failed; "
                "conservatively assuming rebase is in progress: %s",
                self._redact((git_dir_proc.stderr or "").strip()),
            )
            return True

        git_dir = Path(git_dir_proc.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = git_root / git_dir
        return (git_dir / "rebase-merge").is_dir() or (
            git_dir / "rebase-apply"
        ).is_dir()

    def _abort_in_progress_rebase(
        self,
        git_root: Path,
        env: dict[str, str] | None,
    ) -> bool:
        """Run ``git rebase --abort`` synchronously.

        Args:
            git_root: Working-tree root.
            env: Optional GIT_ASKPASS environment.

        Returns:
            ``True`` if abort succeeded; ``False`` if abort itself failed
            (in which case the caller should bail out — the working tree
            may be inconsistent).  Failure is logged at ERROR with
            token-redacted stderr.
        """
        abort_proc = self._run_git_capturing(git_root, "rebase", "--abort", env=env)
        if abort_proc.returncode != 0:
            logger.error(
                "Git force_pull: failed to abort rebase: %s",
                self._redact((abort_proc.stderr or "").strip()),
            )
            return False
        return True

    def _restore_upstream_paths(
        self,
        git_root: Path,
        env: dict[str, str] | None,
        saved: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        """Restore upstream content for each conflict path after rebase abort.

        After ``git rebase --abort`` the working tree reverts to the
        pre-rebase MCP state — every file in ``saved`` again contains the
        MCP version, not the upstream version.  For each path we run
        ``git checkout @{upstream} -- <path>`` to bring back the upstream
        bytes so :meth:`_write_conflict_files` reads the right side
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
                :meth:`_resolve_conflicts_safely`.

        Returns:
            Subset of ``saved`` whose upstream restore succeeded.  May be
            empty if every checkout failed.
        """
        restored: list[tuple[str, str]] = []
        for rel_path, mcp_content in saved:
            checkout_proc = self._run_git_capturing(
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
                    self._redact((checkout_proc.stderr or "").strip()),
                )
                continue
            restored.append((rel_path, mcp_content))
        return restored

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
        new_head = self._head_sha(git_root)
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

    def _write_conflict_files(
        self,
        git_root: Path,
        saved: list[tuple[str, str]],
        env: dict[str, str] | None,
    ) -> list[str] | None:
        """Write conflict files and add ``conflict_with`` frontmatter to both sides.

        For each ``(relative_path, content)`` in *saved*:

        1. Write the MCP version as ``<stem>.conflict-mcp-<timestamp><ext>``
           with ``conflict_with`` and ``conflict_date`` frontmatter.
        2. Merge ``conflict_with`` and ``conflict_date`` into the original
           file's existing frontmatter.

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
                    orig_post = frontmatter.loads(
                        original_abs.read_text(encoding="utf-8")
                    )
                except Exception:
                    logger.warning(
                        "Git pull: failed to parse frontmatter for original file %s; treating as plain content",
                        rel_path,
                        exc_info=True,
                    )
                    orig_post = frontmatter.Post(
                        original_abs.read_text(encoding="utf-8")
                    )
                orig_post.metadata["conflict_with"] = conflict_rel
                orig_post.metadata["conflict_date"] = conflict_date
                original_abs.write_text(frontmatter.dumps(orig_post), encoding="utf-8")

            written.append(conflict_rel)

        if not written:
            return written

        # Stage only the files we touched — the original files (updated with
        # conflict_with frontmatter) and the new conflict files.
        paths_to_add = [rel_path for rel_path, _ in saved] + written
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
        commit_result = subprocess.run(
            [
                "git",
                "-C",
                root,
                "-c",
                f"user.name={self._commit_name}",
                "-c",
                f"user.email={self._commit_email}",
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
                self._redact(
                    (commit_result.stderr or commit_result.stdout or "").strip()
                ),
            )
            return None

        return written

    # ------------------------------------------------------------------
    # Synchronous force-trigger helpers (used by the ``git_sync`` MCP tool)
    # ------------------------------------------------------------------

    def _resolve_force_repo(self) -> Path:
        """Return the working tree path used by ``force_*`` methods.

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

    def _head_sha(self, git_root: Path) -> str:
        """Return the current HEAD SHA of *git_root*."""
        return self._git(git_root, "rev-parse", "HEAD").strip()

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
        result = subprocess.run(
            ["git", "-C", str(git_root), *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        return result.stdout

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
        return subprocess.run(
            ["git", "-C", str(git_root), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,  # explicit — caller inspects returncode
        )

    def force_pull(self, *, dry_run: bool = False) -> PullResult:
        """Pull from ``origin`` synchronously and return a structured result.

        The remote-tracking branch is identified by reading the upstream of
        the current branch (``@{upstream}``) so this method works even when
        ``origin/HEAD`` has not been set on the local clone.

        Acquires :attr:`_lock` for the duration so the periodic pull loop
        and the per-write commit path cannot race against the fetch /
        merge / rebase pipeline.  This blocks writes for the network
        round-trip; that is acceptable for the interactive ``git_sync``
        tool and mirrors what :meth:`sync_once` already does.

        On ``ff-only`` failure (divergent history) the implementation
        falls through to the same rebase + Syncthing-style sibling write
        path used by :meth:`sync_once` (see :meth:`_resolve_rebase_conflicts`
        and :meth:`_write_conflict_files`).  When the conflict-resolution
        path produces sibling files HEAD has advanced to the remote and
        :attr:`PullResult.applied` is ``True`` with
        :attr:`PullResult.reason` set to
        ``"conflicts_resolved_with_siblings"``.

        After a successful HEAD advance — fast-forward or sibling
        resolution — :meth:`_lfs_pull` runs so any LFS pointers in the
        new commits are materialised before the caller sees the working
        tree.

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
        git_root = self._resolve_force_repo()
        env = self._git_env()
        try:
            with self._lock:
                from_sha = self._head_sha(git_root)

                # Always fetch first — both dry-run and real-pull need the
                # remote-tracking ref refreshed before comparing SHAs.
                try:
                    self._git(git_root, "fetch", "origin", env=env)
                except subprocess.CalledProcessError as exc:
                    # Sanitise the token before logging — fetch is the
                    # network-touching subprocess in this method, and git
                    # error messages can echo the URL with credentials
                    # back at the user.  Mirrors the redaction pattern
                    # already used in ``_do_push_safe`` and ``force_push``.
                    stderr = self._redact((exc.stderr or "").strip())
                    logger.warning(
                        "Git force_pull: fetch failed: %s",
                        stderr,
                    )
                    return PullResult.head_unchanged_failure(
                        from_sha, PULL_REASON_FETCH_FAILED
                    )

                # Resolve the upstream-tracking branch.  Falls back to
                # ``origin/HEAD`` so callers with a non-tracking checkout
                # still get a reasonable answer; both yield the remote-side
                # SHA.
                try:
                    remote_sha = self._git(
                        git_root, "rev-parse", "@{upstream}", env=env
                    ).strip()
                except subprocess.CalledProcessError:
                    try:
                        remote_sha = self._git(
                            git_root, "rev-parse", "origin/HEAD", env=env
                        ).strip()
                    except subprocess.CalledProcessError:
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
                        "Git force_pull: could not parse commit count %r "
                        "from `git rev-list --count %s..%s`",
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
                        "Git force_pull: ff-only merge failed, attempting rebase: %s",
                        (ff_exc.stderr or "").strip(),
                    )
                    return self._force_pull_rebase_fallback(
                        git_root=git_root,
                        env=env,
                        from_sha=from_sha,
                    )

                # Fast-forward succeeded.  ``remote_sha`` is the new HEAD —
                # no need to re-read it via ``_head_sha``.
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
    ) -> PullResult:
        """Attempt rebase + Syncthing-style sibling resolution.

        Called by :meth:`force_pull` when ``merge --ff-only`` failed
        because local and remote histories diverged.  Mirrors the
        rebase / conflict-resolution branch in :meth:`sync_once` but
        returns a structured :class:`PullResult` rather than a bool.

        Must be called with :attr:`_lock` already held — it issues
        further git commands against the same working tree.

        Args:
            git_root: Working-tree root used for git ``-C``.
            env: Optional GIT_ASKPASS environment for token auth.
            from_sha: HEAD SHA captured before the fetch.

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
            self._git(git_root, "rebase", "@{upstream}", env=env)
        except subprocess.CalledProcessError:
            # Real conflicts during rebase — resolve by accepting upstream
            # and saving the local MCP versions as Syncthing-style siblings.
            #
            # ``_resolve_rebase_conflicts`` runs ``git checkout --ours`` and
            # ``git add`` with ``check=True``; if either raises mid-loop the
            # repository is left in a half-rebased state.
            # ``_resolve_conflicts_safely`` wraps that in a defensive abort
            # so a subsequent ``force_pull`` (or any per-write commit path)
            # does not trip over the leftover ``rebase-merge`` directory.
            saved, early_exit = self._resolve_conflicts_safely(git_root, env, from_sha)
            if early_exit is not None:
                return early_exit
            # ``_resolve_conflicts_safely`` returns ``(saved, None)`` on
            # success and ``(None, PullResult)`` on failure — exactly one
            # is non-None.  After the early-exit guard above, ``saved`` is
            # guaranteed non-None; assert so mypy can narrow.
            assert saved is not None

            # If a rebase is still in progress (loop hit its iteration
            # limit, or exited via ``break`` without completing), abort
            # cleanly so the working tree is consistent before we write
            # conflict files.
            rebase_in_progress = self._rebase_in_progress(git_root, env)

            if rebase_in_progress:
                if not self._abort_in_progress_rebase(git_root, env):
                    return PullResult.head_unchanged_failure(
                        from_sha, PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS
                    )
                saved = self._restore_upstream_paths(git_root, env, saved)

            if not saved:
                logger.warning(
                    "Git force_pull: conflict resolution failed, leaving HEAD unchanged"
                )
                return PullResult.head_unchanged_failure(
                    from_sha, PULL_REASON_CONFLICT_RESOLUTION_FAILED
                )

            # Rebase has already completed via ``git rebase --continue`` — HEAD
            # has advanced even if the sibling-files commit below fails.
            actual_head = self._head_sha(git_root)
            written = self._write_conflict_files(git_root, saved, env)
            if written is None:
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
                    "Git force_pull: conflict resolved, saved MCP version as %s",
                    cf,
                )
            logger.info(
                "Git force_pull: rebase completed with %d conflict file(s)",
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
        git_root = self._resolve_force_repo()

        with self._lock:
            local_head = self._head_sha(git_root)

            # Resolve the remote-tracking SHA before the push.  Mirrors
            # :meth:`force_pull` — prefer ``@{upstream}`` (set when the
            # branch was created via ``git push -u``), fall back to
            # ``origin/HEAD`` for non-tracking checkouts.  ``origin/HEAD``
            # alone is unreliable on freshly-pushed clones, hence the
            # two-step lookup.
            try:
                remote_sha_before = self._git(
                    git_root, "rev-parse", "@{upstream}"
                ).strip()
            except subprocess.CalledProcessError:
                try:
                    remote_sha_before = self._git(
                        git_root, "rev-parse", "origin/HEAD"
                    ).strip()
                except subprocess.CalledProcessError:
                    return PushResult(
                        applied=False,
                        commits_pushed=0,
                        remote_sha_before="",
                        remote_sha_after="",
                        reason=PUSH_REASON_NO_REMOTE,
                        hint=(
                            "No upstream tracking branch is configured for the "
                            "current branch.  Run `git push -u origin <branch>` "
                            "from the working tree once to establish tracking."
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

        Tries fast-forward first; falls back to rebase when the local
        and upstream branches have diverged (e.g. Obsidian and MCP both
        committed on different files).  Aborts on true conflicts.
        """
        if self._closed or not self._enable_pull:
            return False

        git_root = self._ensure_git_root(repo_path)
        if git_root is None:
            return False

        env = None
        try:
            env = self._git_env()
            with self._lock:
                upstream_check = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(git_root),
                        "rev-parse",
                        "--verify",
                        "@{upstream}",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if upstream_check.returncode != 0:
                    logger.info("Git pull: no upstream configured; skipping fetch")
                    return False

                old_head = subprocess.run(
                    ["git", "-C", str(git_root), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                ).stdout.strip()

                subprocess.run(
                    ["git", "-C", str(git_root), "fetch"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                )

                try:
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(git_root),
                            "merge",
                            "--ff-only",
                            "@{upstream}",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                        env=env,
                    )
                except subprocess.CalledProcessError as ff_exc:
                    # ff-only failed — the branches have diverged.  Attempt
                    # rebase to replay local MCP commits on top of upstream.
                    # This handles the common case where Obsidian and the MCP
                    # server both committed independently on different files.
                    logger.debug(
                        "Git pull: ff-only failed, attempting rebase: %s",
                        (ff_exc.stderr or "").strip(),
                    )
                    try:
                        subprocess.run(
                            [
                                "git",
                                "-C",
                                str(git_root),
                                "rebase",
                                "@{upstream}",
                            ],
                            capture_output=True,
                            text=True,
                            check=True,
                            env=env,
                        )
                        logger.info(
                            "Git pull: ff-only not possible, rebased local commits onto upstream"
                        )
                    except subprocess.CalledProcessError:
                        # True conflict — resolve by accepting theirs and
                        # saving the MCP version as a conflict file.
                        saved = self._resolve_rebase_conflicts(git_root, env)

                        # Directory check (REBASE_HEAD lingers post-continue, #466).
                        rebase_in_progress = self._rebase_in_progress(git_root, env)

                        if rebase_in_progress:
                            # Abort the incomplete rebase before committing
                            # conflict files so the working tree is clean.
                            abort_proc = subprocess.run(
                                ["git", "-C", str(git_root), "rebase", "--abort"],
                                capture_output=True,
                                text=True,
                                env=env,
                            )
                            if abort_proc.returncode != 0:
                                logger.error(
                                    "Git pull: failed to abort rebase: %s",
                                    self._redact((abort_proc.stderr or "").strip()),
                                )
                            # After abort, the working tree reverts to the
                            # pre-rebase state (MCP commits), so the original
                            # files contain MCP content, not upstream content.
                            # Restore the upstream version for each conflicting
                            # file so _write_conflict_files reads the right side.
                            for rel_path, _ in saved:
                                subprocess.run(
                                    [
                                        "git",
                                        "-C",
                                        str(git_root),
                                        "checkout",
                                        "@{upstream}",
                                        "--",
                                        rel_path,
                                    ],
                                    capture_output=True,
                                    text=True,
                                    env=env,
                                )

                        if saved:
                            written = self._write_conflict_files(git_root, saved, env)
                            if written is None:
                                logger.warning(
                                    "Git pull: conflict commit failed, skipping"
                                )
                                return False
                            for cf in written:
                                logger.warning(
                                    "Git pull: conflict resolved, saved MCP version as %s",
                                    cf,
                                )
                            logger.info(
                                "Git pull: rebase completed with %d conflict file(s)",
                                len(written),
                            )
                        else:
                            # Resolution failed entirely — stay put.
                            logger.warning(
                                "Git pull: conflict resolution failed, skipping"
                            )
                            return False

                new_head = subprocess.run(
                    ["git", "-C", str(git_root), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                ).stdout.strip()

                # Always attempt LFS pull after a successful fetch+ff-only step.
                self._lfs_pull(env=env)

            return old_head != new_head
        except FileNotFoundError:
            logger.info("Git pull: git not found on PATH; pull loop disabled")
            return False
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Git pull: git command failed, skipping: %s",
                (exc.stderr or "").strip(),
            )
            return False
        finally:
            self._cleanup_git_env(env)

    def start(
        self,
        *,
        repo_path: Path,
        pull_interval_s: int,
        pause_writes: Callable[[], contextlib.AbstractContextManager[None]]
        | None = None,
        on_pull: Callable[[], None] | None = None,
    ) -> None:
        """Start a periodic fetch + ff-only update loop in a daemon thread."""
        if self._closed or not self._enable_pull or pull_interval_s <= 0:
            return

        git_root = self._ensure_git_root(repo_path)
        if git_root is None:
            return

        # Guard: do not start the loop if there is no upstream configured.
        # This check is intentionally independent of the sync_once() call in
        # sync_from_remote_before_index() — start() may be called even when
        # the startup sync was skipped (pull_interval_s changed at runtime,
        # or Collection.start() called directly by library users).  The double
        # upstream check is harmless (costs one git subprocess) and avoids
        # noisy "no upstream" logs on every tick.
        env = None
        try:
            env = self._git_env()
            upstream_check = subprocess.run(
                ["git", "-C", str(git_root), "rev-parse", "--verify", "@{upstream}"],
                capture_output=True,
                text=True,
                env=env,
            )
            if upstream_check.returncode != 0:
                logger.info("Git pull: no upstream configured; pull loop disabled")
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
            self._pause_writes = pause_writes
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
        pending local commits.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending = self._push_pending

        if pending and self._git_root is not None:
            self._do_push_safe()

    def close(self) -> None:
        """Cancel timer, flush pending push, mark strategy as closed."""
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
    ) -> list[HistoryEntry]:
        """Return commits that touched *path* (or the whole vault).

        Args:
            repo_path: Path inside the git repository (used to locate the root).
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

        Returns:
            List of :class:`HistoryEntry` ordered from newest to oldest.

        Raises:
            ValueError: If ``git log`` exits non-zero (e.g. an invalid
                ``since`` / ``until`` expression).
        """
        git_root = self._ensure_git_root(repo_path)
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

        env = self._git_env()
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
            self._cleanup_git_env(env)

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

    @staticmethod
    def _resolve_path_at_ref(
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
        self,
        repo_path: Path,
        path: Path,
        ref: str | None,
        per_commit: bool,
        since_timestamp: str | None = None,
        limit: int | None = None,
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

        Returns:
            A unified diff string when *per_commit* is ``False``, or a list of
            :class:`CommitDiff` when *per_commit* is ``True``.

        Raises:
            ValueError: If *ref* is not found in history, *since_timestamp*
                cannot be resolved, or a git subprocess exits non-zero.
        """
        _DIFF_MAX_BYTES = 50 * 1024  # 50 KB

        git_root = self._ensure_git_root(repo_path)
        if git_root is None:
            if per_commit:
                return []
            return ""

        path_str = str(path)
        env = self._git_env()
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
                # Recover path-at-ref so diffs across renames show real deltas.
                try:
                    cur_rel = path.resolve().relative_to(git_root).as_posix()
                except ValueError:
                    cur_rel = None
                old_path = (
                    self._resolve_path_at_ref(git_root, ref, cur_rel, env)
                    if cur_rel is not None
                    else None
                )
                if old_path is None or cur_rel is None or old_path == cur_rel:
                    diff_cmd = [
                        "git",
                        "-C",
                        str(git_root),
                        "diff",
                        f"{ref}..HEAD",
                        "--",
                        path_str,
                    ]
                else:
                    diff_cmd = [
                        "git",
                        "-C",
                        str(git_root),
                        "diff",
                        f"{ref}:{old_path}",
                        f"HEAD:{cur_rel}",
                    ]
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
                commit_path = next(
                    (ln.strip() for ln in lines[1:] if ln.strip()), path_str
                )
                try:
                    show_result = subprocess.run(
                        [
                            "git",
                            "-C",
                            str(git_root),
                            "show",
                            "--format=",
                            "-p",
                            sha,
                            "--",
                            commit_path,
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                        env=env,
                    )
                except subprocess.CalledProcessError as exc:
                    raise ValueError(
                        f"Could not retrieve diff for commit {sha!r}"
                    ) from exc
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
            self._cleanup_git_env(env)


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

    When used via :class:`~markdown_vault_mcp.collection.Collection`,
    ``Collection.close()`` automatically calls the strategy's
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


def _stage_and_commit(
    git_root: Path,
    path: Path,
    operation: Literal["write", "edit", "delete", "rename"],
    commit_name: str = GitWriteStrategy.DEFAULT_COMMIT_NAME,
    commit_email: str = GitWriteStrategy.DEFAULT_COMMIT_EMAIL,
) -> None:
    """Stage and commit a single file change (no push).

    Args:
        git_root: Git repository root.
        path: Absolute path to the changed file.
        operation: The write operation type.
        commit_name: Git committer name (overrides git config).
        commit_email: Git committer email (overrides git config).
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
        # For rename, the old file has been moved on disk.  Stage tracked
        # deletions (-u) to capture the old path removal, then add the new
        # file explicitly.
        # NOTE: ``git add -u`` without a pathspec stages ALL tracked
        # modifications/deletions repo-wide.  In a vault with other
        # uncommitted edits, this may sweep unrelated changes into the
        # auto-commit.  Additionally, if the old file was never committed
        # to git (e.g. written directly by Obsidian and not via this
        # callback), ``git add -u`` will not record its deletion at all —
        # the commit will only add the new path.
        # A future improvement would extend the callback signature to
        # pass both old and new paths, enabling scoped staging.
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
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    logger.info("Git: committed %s (%s)", rel_path, operation)


def _push(git_root: Path, token: str | None, username: str = "x-access-token") -> None:
    """Push to the default remote, using GIT_ASKPASS for token auth.

    When a token is supplied a temporary helper script is written to a
    private temporary file (mode 0o700).  Git reads credentials from this
    script via ``GIT_ASKPASS`` so the token is never present in any
    process's command-line arguments and is therefore not visible in
    ``/proc/<pid>/cmdline``.  The script is deleted in a ``finally`` block
    regardless of push outcome.

    Args:
        git_root: Git repository root.
        token: Optional PAT for HTTPS push.  If ``None``, relies on SSH
            keys or pre-configured git credentials.
        username: Username used for HTTPS auth prompts when *token* is set.
    """
    root = str(git_root)

    # Always push to "origin".  If the remote is named differently,
    # configure a git remote alias or adjust this constant.
    if not token:
        subprocess.run(
            ["git", "-C", root, "push", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        return

    env = _build_askpass_env(token, username)
    script_path_str = env["GIT_ASKPASS"]
    script_path = Path(script_path_str)
    try:
        subprocess.run(
            ["git", "-C", root, "push", "origin"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    finally:
        with contextlib.suppress(OSError):
            script_path.unlink()
