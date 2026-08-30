"""Repository bootstrap and startup validation for the git write strategy.

Extracted verbatim from :mod:`markdown_vault_mcp.git.strategy` (#893):
:class:`RepoBootstrap` owns managed-repo cloning/validation, the
remote-protocol (SSH vs HTTPS) check for token auth, and the memoised
git-root discovery that :class:`~markdown_vault_mcp.git.strategy.GitWriteStrategy`
and :class:`~markdown_vault_mcp.git.push_scheduler.PushScheduler` share.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.git._run import (
    _find_git_root,
    _is_ssh_remote,
    _normalize_remote,
    cleanup_git_env,
    git_env,
)


class RepoBootstrap:
    """Repository discovery, managed-clone bootstrap, and startup validation.

    A collaborator composed into
    :class:`~markdown_vault_mcp.git.strategy.GitWriteStrategy` (#893).  It
    owns the shared repository identity: the memoised working-tree root
    (:attr:`git_root`) plus the credentials used for clone-time auth.  The
    strategy and :class:`~markdown_vault_mcp.git.push_scheduler.PushScheduler`
    read :attr:`git_root` through this object, so there is exactly one copy
    of that state.

    Args:
        lock: The strategy-wide lock, shared with the strategy's pull/write
            paths and the push scheduler.  The SAME object must be passed to
            every collaborator; this class introduces no lock of its own.
        token: PAT for HTTPS auth via ``GIT_ASKPASS``; also gates the
            SSH-remote rejection in :meth:`check_remote_protocol`.
        username: Username used with token auth.
        repo_url: Remote URL expected in managed mode (``None`` outside
            managed mode).
    """

    def __init__(
        self,
        *,
        lock: threading.Lock,
        token: str | None,
        username: str,
        repo_url: str | None,
    ) -> None:
        self._lock = lock
        self.token = token
        self.username = username
        self._repo_url = repo_url
        #: Memoised working-tree root; ``None`` until discovered (or when
        #: the source dir is not a git repository).
        self.git_root: Path | None = None
        #: Whether root discovery has run (so a ``None`` root is not retried).
        self.git_root_checked = False

    def _git_env(self) -> dict[str, str] | None:
        """Build the GIT_ASKPASS environment for clone-time subprocess calls."""
        return git_env(self.token, self.username)

    def ensure_git_root(self, repo_path: Path) -> Path | None:
        """Discover and memoise the git root containing *repo_path*.

        Thread-safe double-checked discovery under the shared strategy lock;
        after the first call the cached result is returned without locking.

        Args:
            repo_path: Any path inside the candidate working tree.

        Returns:
            The working-tree root, or ``None`` when *repo_path* is not
            inside a git repository.
        """
        if self.git_root_checked:
            return self.git_root
        with self._lock:
            if not self.git_root_checked:
                self.git_root = _find_git_root(repo_path)
                self.git_root_checked = True
        return self.git_root

    def get_origin_url(self, git_root: Path) -> str | None:
        """Return the ``origin`` remote URL of *git_root*, or ``None``.

        ``None`` covers every failure shape: git missing from PATH, no
        ``origin`` remote configured, or an empty URL.
        """
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

    def ensure_managed_repo(self, repo_path: Path) -> None:
        """Ensure a managed repository exists at *repo_path* (clone or validate).

        Clones ``repo_url`` into an empty (or absent) *repo_path*; for an
        existing checkout, verifies the ``origin`` remote matches the
        configured URL.  On success, memoises the discovered git root.

        Args:
            repo_path: The configured ``SOURCE_DIR``.

        Raises:
            ConfigurationError: When no ``repo_url`` is configured, the URL
                uses SSH transport while token auth is enabled, *repo_path*
                is not a directory, the clone fails, the result is not a git
                repository, ``origin`` is missing, or the remote URL does
                not match ``repo_url``.
        """
        if self._repo_url is None:
            raise ConfigurationError("Managed git mode requires a repo_url.")

        if self.token and _is_ssh_remote(self._repo_url):
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
            self._clone_into(path)

        git_root = _find_git_root(path)
        if git_root is None:
            raise ConfigurationError(
                f"Managed mode requires SOURCE_DIR to be empty or a git repository: {path}"
            )
        origin_url = self.get_origin_url(git_root)
        if origin_url is None:
            raise ConfigurationError(
                f"Managed mode requires an 'origin' remote in repository {git_root}."
            )
        if _normalize_remote(origin_url) != _normalize_remote(self._repo_url):
            raise ConfigurationError(
                "Managed mode remote mismatch: existing origin is "
                f"{origin_url!r}, expected {self._repo_url!r}."
            )
        self.git_root = git_root
        self.git_root_checked = True
        self.check_remote_protocol(git_root)

    def _clone_into(self, path: Path) -> None:
        """Clone ``repo_url`` into *path* with GIT_ASKPASS credentials.

        Raises:
            ConfigurationError: When git is missing from PATH or the clone
                itself fails (stderr is included in the message).
        """
        # Only reachable from ensure_managed_repo, after its repo_url guard;
        # re-checked here (not asserted) so mypy narrows without S101.
        if self._repo_url is None:
            raise ConfigurationError("Managed git mode requires a repo_url.")
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
            raise ConfigurationError("git is not installed or not on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            raise ConfigurationError(
                f"Failed to clone managed git repo {self._repo_url!r} into {path}: "
                f"{(exc.stderr or '').strip()}"
            ) from exc
        finally:
            cleanup_git_env(env)

    def check_remote_protocol(self, git_root: Path) -> None:
        """Raise ConfigurationError if origin uses SSH while token auth is enabled."""
        if not self.token:
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
        git_root = self.ensure_git_root(repo_path)
        if git_root is None:
            return
        self.check_remote_protocol(git_root)
