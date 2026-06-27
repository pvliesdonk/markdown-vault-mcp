"""Low-level git subprocess + credential plumbing.

Stateless helpers -- no shared mutable state and no locks (they do touch the
filesystem and spawn subprocesses). All subprocess calls are module-qualified
(``subprocess.run``) so the test suite's global ``subprocess.run`` monkeypatch
still intercepts them.
"""

from __future__ import annotations

import contextlib
import logging
import os
import stat
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_ssh_remote(url: str) -> bool:
    return url.startswith("git@") or url.startswith("ssh://")


def _normalize_remote(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[: -len(".git")]
    return normalized


def _build_askpass_env(token: str, username: str) -> dict[str, str]:
    fd, script_path_str = tempfile.mkstemp(suffix=".sh", prefix="git_askpass_")
    # GIT_ASKPASS must be executable; mkstemp creates the file 0600. Prefer
    # fchmod on the still-owned fd (no reopen-by-path) where available; fall back
    # to chmod-by-path on platforms without fchmod (e.g. Windows), where the exec
    # bit is a no-op anyway since the script only runs under a POSIX shell.
    _has_fchmod = hasattr(os, "fchmod")
    try:
        if _has_fchmod:
            os.fchmod(fd, stat.S_IRWXU)
        with os.fdopen(fd, "w") as f:  # fdopen takes ownership of fd
            f.write(
                "#!/bin/sh\n"
                'case "$1" in\n'
                "  *sername*) printf '%s\\n' \"${MVMCP_GIT_USERNAME:-}\" ;;\n"
                "  *) printf '%s\\n' \"${MVMCP_GIT_TOKEN:-}\" ;;\n"
                "esac\n"
            )
        if not _has_fchmod:
            Path(script_path_str).chmod(stat.S_IRWXU)
    except BaseException:
        # fdopen took ownership only if it succeeded; suppress double-close.
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            Path(script_path_str).unlink()
        raise
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


def git_env(token: str | None, username: str) -> dict[str, str] | None:
    """Build environment for git subprocess calls.

    When a token is set, reuse the existing GIT_ASKPASS mechanism to avoid
    prompting interactively. This mirrors the push path and keeps the token
    out of command-line arguments.
    """
    if not token:
        return None
    return _build_askpass_env(token, username)


def cleanup_git_env(env: dict[str, str] | None) -> None:
    """Tear down an env built by :func:`git_env`.

    Pops the ``MVMCP_GIT_USERNAME`` / ``MVMCP_GIT_TOKEN`` credential vars and
    unlinks the temporary ``GIT_ASKPASS`` script (suppressing ``OSError`` if it
    is already gone). A ``None`` env (no token was set) is a no-op.

    Args:
        env: The environment dict returned by :func:`git_env`, or ``None``.
    """
    if env is None:
        return
    env.pop("MVMCP_GIT_USERNAME", None)
    env.pop("MVMCP_GIT_TOKEN", None)
    script_path_str = env.pop("GIT_ASKPASS", None)
    if not script_path_str:
        return
    with contextlib.suppress(OSError):
        Path(script_path_str).unlink()


def redact(text: str, token: str | None) -> str:
    """Replace the configured PAT with ``***`` so it never reaches logs/responses.

    Args:
        text: Raw stderr / message text that may contain *token*.
        token: The PAT to redact, or ``None`` when no token is configured.

    Returns:
        The same text with every occurrence of *token* replaced by ``"***"``.
        Returns ``text`` unchanged when *token* is ``None`` or absent from the
        text (cheap no-op for the common case).
    """
    if token and token in text:
        return text.replace(token, "***")
    return text


def run_git(git_root: Path, *args: str, env: dict[str, str] | None = None) -> str:
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


def resolve_tracking_ref(
    git_root: Path, env: dict[str, str] | None = None
) -> str | None:
    """Resolve the remote-tracking ref to sync against, tracking-independent.

    Managed-git sync targets ``origin/<current-branch>`` rather than the
    branch's configured upstream (``@{upstream}``).  A managed clone is created
    and maintained by the server, so it may be a detached or
    ``git clone --branch`` checkout that never had upstream tracking set; in
    that case ``@{upstream}`` does not resolve and fetch/merge/rebase silently
    skip.  Deriving the ref from the current branch name keeps sync working
    regardless of whether tracking was configured.

    Resolution order:

    1. ``origin/<branch>`` where *branch* is ``git symbolic-ref --short HEAD``
       — the normal case.
    2. ``origin/HEAD`` (the remote's default branch) when HEAD is detached or
       the current branch has no counterpart on ``origin``.

    Args:
        git_root: Working-tree root used for ``git -C``.
        env: Optional environment (e.g. from :func:`git_env`).  Pure-local ref
            lookups do not touch the network, but the env is threaded through
            for consistency with the rest of the call sites.

    Returns:
        The verified remote-tracking ref name (e.g. ``"origin/main"`` or
        ``"origin/HEAD"``), or ``None`` when neither could be resolved.
    """
    branch_proc = run_git_capturing(
        git_root, "symbolic-ref", "--quiet", "--short", "HEAD", env=env
    )
    if branch_proc.returncode == 0:
        branch = branch_proc.stdout.strip()
        if branch:
            candidate = f"origin/{branch}"
            verify = run_git_capturing(
                git_root, "rev-parse", "--verify", "--quiet", candidate, env=env
            )
            if verify.returncode == 0:
                return candidate

    # Detached HEAD, or branch has no origin counterpart — fall back to the
    # remote's default branch.
    fallback = run_git_capturing(
        git_root, "rev-parse", "--verify", "--quiet", "origin/HEAD", env=env
    )
    if fallback.returncode == 0:
        return "origin/HEAD"
    return None


def run_git_capturing(
    git_root: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the completed process without raising.

    Sister of :func:`run_git` for paths that need to inspect ``returncode``
    and ``stderr`` instead of letting :class:`subprocess.CalledProcessError`
    propagate.  Used in force-pull rebase fallback where we need to make
    recovery decisions based on whether ``git rebase --abort`` succeeded,
    whether the working tree has an in-progress rebase, etc.

    Args:
        git_root: Working-tree root used for ``git -C``.
        *args: Git subcommand and arguments (without the leading ``git``).
        env: Optional environment, typically from :func:`git_env` for
            token-bearing operations.  ``None`` inherits the parent process
            environment.

    Returns:
        :class:`subprocess.CompletedProcess` with ``returncode``, ``stdout``,
        and ``stderr`` populated.  Stderr will need to be passed through
        :func:`redact` before logging or surfacing to callers.
    """
    return subprocess.run(
        ["git", "-C", str(git_root), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,  # explicit — caller inspects returncode
    )
