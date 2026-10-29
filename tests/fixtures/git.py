"""Pytest fixtures for tests that need a real git repository.

Provides:
- :class:`GitRepoPair`: a small named tuple bundling the bare-remote and
  local-clone paths produced by :func:`git_repo_pair`.
- :func:`git_repo_pair`: a function-scoped fixture creating a fresh bare
  "remote" + local clone with one initial commit on ``main``.

Both pieces are intentionally minimal so they can be reused for any future
git-tool tests (force-pull, force-push, conflict resolution, etc.).  Every
invocation gets its own ``tmp_path`` so tests do not pollute each other.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, NamedTuple

import pytest

if TYPE_CHECKING:
    from pathlib import Path


class GitRepoPair(NamedTuple):
    """A bare 'remote' repo plus a local clone of it.

    Attributes:
        remote_path: Filesystem path to the bare repository acting as the
            upstream.  Pass this as ``origin`` when constructing additional
            clones in tests.
        local_path: Filesystem path to the local working clone.  Has one
            initial commit on ``main`` already pushed to ``origin``.
    """

    remote_path: Path
    local_path: Path


def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command synchronously and return stdout.

    Args:
        cwd: Working directory for the git command.
        *args: Arguments passed to ``git`` (do not include the ``git``
            executable itself).

    Returns:
        Captured stdout from the git command.

    Raises:
        subprocess.CalledProcessError: If the command exits non-zero.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture
def git_repo_pair(tmp_path: Path) -> GitRepoPair:
    """Set up a bare 'remote' + local clone with one initial commit on main.

    The local clone has ``origin`` pointing at the bare remote and an
    upstream tracking branch (``main`` → ``origin/main``) established via
    ``git push -u``.

    Returns:
        :class:`GitRepoPair` with both paths populated.
    """
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    remote.mkdir()
    _run_git(remote, "init", "--bare", "--initial-branch=main")

    local.mkdir()
    _run_git(local, "init", "--initial-branch=main")
    _run_git(local, "config", "user.email", "test@example.com")
    _run_git(local, "config", "user.name", "Test User")
    _run_git(local, "remote", "add", "origin", str(remote))

    # Initial commit so HEAD exists on both sides.
    (local / "README.md").write_text("# Test\n")
    _run_git(local, "add", "README.md")
    _run_git(local, "commit", "-m", "initial")
    _run_git(local, "push", "-u", "origin", "main")

    return GitRepoPair(remote_path=remote, local_path=local)


def make_superproject(
    tmp_path: Path, *, vault: str = "", sub: str = "sub", note: str = "# Upstream\n"
) -> Path:
    """Build a superproject holding a submodule at ``<vault>/<sub>`` (#1564).

    The layout ``docs/design/reference/git-submodules.md`` was observed on:
    the submodule holds ``note.md``, added with ``git submodule add`` from a
    local repository and committed. With *vault* set, the vault is that
    subfolder of the repository and a second submodule sits at ``other``,
    outside it.

    Args:
        tmp_path: A fresh directory to build under.
        vault: The vault's folder inside the repository, ``""`` for its root.
        sub: The submodule's path inside the vault.
        note: The text of the submodule's ``note.md``.

    Returns:
        The vault directory: the repository root, or its *vault* subfolder.
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run_git(upstream, "init", "-q", "--initial-branch=main")
    _run_git(upstream, "config", "user.email", "test@example.com")
    _run_git(upstream, "config", "user.name", "Test User")
    (upstream / "note.md").write_text(note)
    _run_git(upstream, "add", "note.md")
    _run_git(upstream, "commit", "-qm", "initial")

    repo = tmp_path / "repo"
    source = repo / vault if vault else repo
    source.mkdir(parents=True)
    _run_git(repo, "init", "-q", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")
    (source / "top.md").write_text("# Top\n")
    _run_git(repo, "add", ".")
    add = ["-c", "protocol.file.allow=always", "submodule", "add", "-q"]
    _run_git(repo, *add, str(upstream), f"{vault}/{sub}" if vault else sub)
    if vault:
        _run_git(repo, *add, str(upstream), "other")
    _run_git(repo, "commit", "-qm", "add submodule")
    return source
