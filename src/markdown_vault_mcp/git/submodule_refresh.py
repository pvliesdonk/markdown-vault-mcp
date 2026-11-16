"""Opt-in: check each submodule out at the commit the superproject records (#1565).

A plain clone leaves a submodule's folder empty and a pull that moves the
gitlink leaves the old checkout (``docs/design/reference/git-submodules.md``).
:class:`SubmoduleRefresher` runs ``git submodule update`` per submodule, only
when the URL git would fetch from is on the operator's list. Safety rules:

- no ``GIT_ASKPASS`` (the token is ``GIT_REPO_URL``'s), no terminal prompt,
  and SSH in batch mode unless the operator set ``GIT_SSH_COMMAND``;
- the URL compared is the one git contacts, after ``url.*.insteadOf``;
- a submodule with uncommitted changes is never touched;
- ``:(literal)`` pathspecs, so a gitlink named ``s*`` selects no sibling;
- no nested submodule is checked out or fetched;
- every git call is time-bounded, and one pass stops starting updates after
  a budget, since the pull's lock is held.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from typing import TYPE_CHECKING

from markdown_vault_mcp.git._run import _normalize_remote, literal_pathspec
from markdown_vault_mcp.git.health import one_line
from markdown_vault_mcp.utils.fs import path_exists

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["SubmoduleRefresher"]

logger = logging.getLogger(__name__)

SUBMODULE_REASON_DIRTY = "submodule_dirty"
SUBMODULE_REASON_REMOTE_NOT_ALLOWED = "submodule_remote_not_allowed"
SUBMODULE_REASON_NOT_REGISTERED = "submodule_not_registered"
SUBMODULE_REASON_UPDATE_FAILED = "submodule_update_failed"

_QUERY_TIMEOUT_S = 30
_UPDATE_TIMEOUT_S = 300
_PASS_BUDGET_S = 900
# Greedy to the last ``@`` before the path, so a password holding ``@`` goes.
_USERINFO = re.compile(r"(?<=://)[^/\s]*@")
_NO_NESTING = ("-c", "fetch.recurseSubmodules=false", "-c", "submodule.recurse=false")


def _redact_url(url: str) -> str:
    return _USERINFO.sub("", url)


class SubmoduleRefresher:
    """Check out each submodule at the commit the superproject's HEAD records.

    Args:
        allowed_remotes: Remotes a submodule may be fetched from, compared
            after ``GIT_REPO_URL``'s normalisation (trailing ``/`` and
            ``.git`` ignored). Empty means not opted in.

    Raises:
        ValueError: If *allowed_remotes* is empty.
    """

    def __init__(self, allowed_remotes: tuple[str, ...]) -> None:
        if not allowed_remotes:
            raise ValueError("SubmoduleRefresher needs at least one allowed remote")
        self._allowed = frozenset(_normalize_remote(url) for url in allowed_remotes)

    def refresh(self, git_root: Path) -> tuple[list[str], list[str]]:
        """Bring every out-of-date submodule current, or say why not.

        A current submodule costs one ``rev-parse``. One submodule's failure
        never raises; a missing ``git`` binary propagates as ``OSError``.

        Args:
            git_root: The superproject's working-tree root.

        Returns:
            The paths updated, and one ``path:reason detail`` line per
            submodule still out of date.
        """
        names = _registered_names(git_root)
        updated: list[str] = []
        failures: list[str] = []
        deadline = time.monotonic() + _PASS_BUDGET_S
        for path, sha in _recorded_gitlinks(git_root).items():
            failure: str | None
            if time.monotonic() > deadline:
                failure = f"{SUBMODULE_REASON_UPDATE_FAILED} pass_budget_spent"
            else:
                failure = self._refresh_one(git_root, path, sha, names.get(path))
            if failure == "":
                updated.append(path)
                logger.info("git_submodule_updated path=%s", one_line(path))
            elif failure is not None:
                failures.append(f"{one_line(path)}:{failure}".strip())
        return updated, failures

    def _refresh_one(
        self, git_root: Path, path: str, sha: str, name: str | None
    ) -> str | None:
        """Return ``None`` if current, ``""`` once updated, else ``reason detail``."""
        sub = git_root / path
        populated = path_exists(sub / ".git")
        if populated and _out(_git(sub, "rev-parse", "--verify", "-q", "HEAD")) == sha:
            return None
        if name is None:
            return SUBMODULE_REASON_NOT_REGISTERED
        if populated and _is_dirty(sub):
            return SUBMODULE_REASON_DIRTY
        url = _effective_url(git_root, sub, path, name, populated=populated)
        if url is None or _normalize_remote(url) not in self._allowed:
            shown = _redact_url(url) if url is not None else "unknown"
            return f"{SUBMODULE_REASON_REMOTE_NOT_ALLOWED} remote={shown}"
        error = _update(git_root, path)
        if error is None and _out(_git(sub, "rev-parse", "HEAD")) != sha:
            error = "checkout did not reach the recorded commit"
        return "" if error is None else f"{SUBMODULE_REASON_UPDATE_FAILED} {error}"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run a local git query; a timeout reads as a failed command."""
    cmd = ["git", "-C", str(cwd), *args]
    try:
        return subprocess.run(
            cmd, capture_output=True, check=False, timeout=_QUERY_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, b"", b"timed out")


def _out(result: subprocess.CompletedProcess[bytes]) -> str | None:
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "surrogateescape").strip() or None


def _recorded_gitlinks(git_root: Path) -> dict[str, str]:
    """Return ``{path: commit}`` for every gitlink (mode 160000) in HEAD's tree."""
    result = _git(git_root, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    links: dict[str, str] = {}
    for entry in result.stdout.split(b"\0") if result.returncode == 0 else ():
        meta, _, name = entry.partition(b"\t")
        fields = meta.split(b" ")
        if len(fields) == 3 and fields[0] == b"160000":
            links[name.decode("utf-8", "surrogateescape")] = fields[2].decode()
    return links


def _registered_names(git_root: Path) -> dict[str, str]:
    """Map each submodule path declared in ``.gitmodules`` to its name."""
    result = _git(
        git_root, "config", "-f", ".gitmodules", "-z", "--get-regexp", r"\.path$"
    )
    names: dict[str, str] = {}
    for entry in result.stdout.split(b"\0"):
        key, _, value = entry.decode("utf-8", "surrogateescape").partition("\n")
        if key.startswith("submodule."):
            names[value] = key[len("submodule.") : -len(".path")]
    return names


def _is_dirty(sub: Path) -> bool:
    """A status git cannot produce counts as dirty: never update blind."""
    result = _git(sub, "status", "--porcelain=v1", "--untracked-files=no")
    return result.returncode != 0 or bool(result.stdout.strip())


def _effective_url(
    git_root: Path, sub: Path, path: str, name: str, *, populated: bool
) -> str | None:
    """Return the URL ``git submodule update`` would contact.

    A populated submodule fetches from its own ``origin``; an uninitialised
    one from ``submodule.<name>.url``, which a network-free
    ``git submodule init`` resolves from ``.gitmodules``, relative URLs too.
    Both are expanded through ``url.*.insteadOf`` by ``ls-remote --get-url``,
    which reads config only, so the two cases compare the same form.
    """
    if populated:
        raw = _out(_git(sub, "config", "--get", "remote.origin.url"))
        where = sub
    else:
        key = f"submodule.{name}.url"
        if _out(_git(git_root, "config", "--get", key)) is None:
            _git(git_root, "submodule", "init", "--", literal_pathspec(path))
        raw = _out(_git(git_root, "config", "--get", key))
        where = git_root
    if raw is None:
        return None
    return _out(_git(where, "ls-remote", "--get-url", "--", raw))


def _update(git_root: Path, path: str) -> str | None:
    """Run ``submodule update --init --checkout`` for one path.

    ``--checkout`` overrides a configured ``submodule.<name>.update``.

    Returns:
        ``None`` on success, else git's redacted stderr on one line.
    """
    env = {k: v for k, v in os.environ.items() if k != "GIT_ASKPASS"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    cmd = ["git", *_NO_NESTING, "-C", str(git_root), "submodule", "update"]
    cmd += ["--init", "--checkout", "--", literal_pathspec(path)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, check=False, env=env, timeout=_UPDATE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {_UPDATE_TIMEOUT_S}s"
    if result.returncode == 0:
        return None
    return _redact_url(one_line(result.stderr.decode("utf-8", "replace")))
