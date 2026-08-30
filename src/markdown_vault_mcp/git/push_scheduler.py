"""Deferred-push scheduling for the git write strategy.

Extracted verbatim from :mod:`markdown_vault_mcp.git.strategy` (#893):
:class:`PushScheduler` owns the idle push timer and the pending-push flag,
plus the push execution paths (:meth:`PushScheduler.do_push_safe`,
:meth:`PushScheduler.do_push`, :meth:`PushScheduler.push_if_unpushed`,
:meth:`PushScheduler.flush`).  It shares the strategy-wide lock with
:class:`~markdown_vault_mcp.git.strategy.GitWriteStrategy` — the same object,
so the documented lock-ordering contracts of the pull/write paths still hold.

The module-level :func:`_push` helper (GIT_ASKPASS-authenticated
``git push origin``) also lives here; tests patch it as
``markdown_vault_mcp.git.push_scheduler._push``.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp.git._run import (
    _build_askpass_env,
    redact,
    resolve_tracking_ref,
)

if TYPE_CHECKING:
    from markdown_vault_mcp.git.bootstrap import RepoBootstrap

logger = logging.getLogger(__name__)


class PushScheduler:
    """Idle-timer push deferral and push execution for GitWriteStrategy.

    A collaborator composed into
    :class:`~markdown_vault_mcp.git.strategy.GitWriteStrategy` (#893).  It
    owns the shared push state — the idle :class:`threading.Timer` and the
    ``push_pending`` flag — and executes pushes under the strategy-wide
    lock.  The strategy's pull loop retries a failed deferred push through
    :meth:`do_push_safe` (#957), and the strategy's public ``flush`` /
    ``close`` delegate their timer/pending mechanics to :meth:`flush`.

    Args:
        lock: The strategy-wide lock, shared with the strategy's pull/write
            paths and :class:`~markdown_vault_mcp.git.bootstrap.RepoBootstrap`.
            The SAME object must be passed to every collaborator; this class
            introduces no lock of its own.
        bootstrap: The repo-bootstrap collaborator; supplies the memoised
            git root and the token/username credentials for push auth.
        enable_push: Whether deferred push behaviour is enabled at all.
        push_delay_s: Seconds of idle before the timer pushes.  ``0``
            disables the timer (push only on ``flush``/``close``).
    """

    def __init__(
        self,
        *,
        lock: threading.Lock,
        bootstrap: RepoBootstrap,
        enable_push: bool,
        push_delay_s: float,
    ) -> None:
        self._lock = lock
        self._bootstrap = bootstrap
        self._enable_push = enable_push
        self._push_delay_s = push_delay_s
        self._push_pending = False
        self._timer: threading.Timer | None = None

    @property
    def _git_root(self) -> Path | None:
        """The memoised working-tree root, read from the bootstrap."""
        return self._bootstrap.git_root

    @property
    def _token(self) -> str | None:
        """PAT for HTTPS push auth, read from the bootstrap."""
        return self._bootstrap.token

    @property
    def _username(self) -> str:
        """Username used with token auth, read from the bootstrap."""
        return self._bootstrap.username

    def _redact(self, text: str) -> str:
        """Replace the configured PAT with ``***`` so it never reaches logs."""
        return redact(text, self._token)

    def schedule_push(self) -> None:
        """Reset the idle push timer."""
        with self._lock:
            self._push_pending = True
            if self._timer is not None:
                self._timer.cancel()
            if self._push_delay_s > 0:
                self._timer = threading.Timer(self._push_delay_s, self.do_push_safe)
                self._timer.daemon = True
                self._timer.start()

    def do_push_safe(self) -> None:
        """Push wrapper that catches and logs errors."""
        try:
            self.do_push()
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

    def do_push(self) -> None:
        """Execute git push and clear the pending flag on success.

        ``_push_pending`` is cleared *after* ``_push()`` returns without
        raising. On failure the flag stays set so the periodic pull loop
        retries the push after its rebase step resolves any non-fast-forward
        divergence (#957); previously the flag was cleared before the push,
        so a failed deferred push left commits local with no retry until the
        next write or startup.
        """
        with self._lock:
            git_root = self._git_root
            if not self._enable_push or not self._push_pending or git_root is None:
                return
            _push(git_root, self._token, self._username)
            self._push_pending = False
            logger.info("Git: pushed to remote")

    def push_if_unpushed(self) -> None:
        """On startup, push any local commits ahead of the remote.

        Runs WITHOUT taking the lock — the strategy's one-time write init
        calls it while already holding the shared lock.
        """
        git_root = self._git_root
        if git_root is None or not self._enable_push:
            return

        try:
            ref = resolve_tracking_ref(git_root)
            if ref is None:
                # No remote-tracking ref resolvable — not an error at startup.
                logger.debug("Git: no remote ref to check for unpushed commits")
                return
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(git_root),
                    "log",
                    "--oneline",
                    f"{ref}..HEAD",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            logger.debug("Git: git not found, skipping unpushed check")
            return

        if result.returncode != 0:
            # No remote-tracking ref or no remote — not an error at startup.
            logger.debug("Git: no remote ref to check for unpushed commits")
            return

        if result.stdout.strip():
            logger.info("Git: found unpushed commits on startup, pushing now")
            try:
                _push(git_root, self._token, self._username)
            except subprocess.CalledProcessError as exc:
                sanitized_stderr = self._redact(exc.stderr or "")
                logger.error(
                    "Git startup push failed: command %s returned %d\n%s",
                    exc.cmd,
                    exc.returncode,
                    sanitized_stderr,
                )

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
            self.do_push_safe()


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
