"""Refuse writes hidden inside submodules or nested repositories (#1564, #1949).

Every git command runs in the superproject, which refuses to stage a path
inside a submodule (``fatal: Pathspec ... is in submodule``). A write there
would land on disk and never reach git, so the write managers ask
:class:`SubmoduleGuard` first and refuse. What git does is recorded in
``docs/design/reference/git-submodules.md``.

For registered submodules, a gitlink (mode ``160000`` in
``git ls-files --stage``) makes git refuse, whether initialised or not. The list
is read again only when the index file changes, so an unchanged index costs
one ``stat``. When git cannot list the index the guard logs it, keeps the
gitlinks it last listed, and asks again on the next write. With no successful
listing it has no gitlink boundaries; live marker discovery still applies.

Plain nested repositories have no gitlink: check their .git directory or
gitfile live, confirming each candidate through git root discovery. File
queries inspect ancestors only; holding queries walk only the requested
directory, pruning metadata and stopping at the first repository. Filesystem
inspection faults propagate rather than treating an unreadable path as safe.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

from markdown_vault_mcp.exceptions import SubmodulePathError
from markdown_vault_mcp.git._run import _find_git_root
from markdown_vault_mcp.utils.fs import is_directory, is_regular_file

__all__ = ["SubmoduleGuard"]

logger = logging.getLogger(__name__)

_GITLINK_MODE = "160000"

# (st_mtime_ns, st_ctime_ns, st_size, st_ino) of the index file, or None when
# it is absent.
_IndexKey = tuple[int, int, int, int] | None


class SubmoduleGuard:
    """Answer whether a path lies in, or holds, a submodule or nested repository.

    The git root is found from the vault directory on the first call and kept,
    found or not, as the git strategy keeps its own; never from the path
    asked about, which may itself be inside a submodule. With no repository
    nothing is refused. Thread-safe.

    Args:
        source_dir: The vault root.
    """

    def __init__(self, source_dir: Path) -> None:
        self._source_dir = source_dir.resolve()
        self._lock = threading.Lock()
        self._root_checked = False
        self._root: Path | None = None
        self._index: Path | None = None
        self._index_key: _IndexKey = None
        self._listed = False
        self._gitlinks: tuple[Path, ...] = ()

    def submodule_of(self, abs_path: Path) -> tuple[str, bool] | None:
        """Return the submodule or nested repository *abs_path* lies in or holds.

        Args:
            abs_path: An absolute path inside the vault.

        Returns:
            ``(repository, inside)``: its vault-relative POSIX path, and
            ``True`` when *abs_path* is the repository or lies inside it,
            ``False`` when it is a folder holding the repository. ``None`` when
            neither holds, or the vault is not in a git repository.

        Raises:
            OSError: If filesystem inspection is refused.
        """
        target = abs_path.resolve()
        gitlinks = self._current_gitlinks()
        if self._root is None:
            return None
        for gitlink in gitlinks:
            if target == gitlink or target.is_relative_to(gitlink):
                return self._name(gitlink), True
        # Every containing boundary precedes holding-only matches: an outer
        # plain repository can itself hold an indexed gitlink (#1949).
        nested = self._containing_repository(target)
        if nested is not None:
            return self._name(nested), True
        for gitlink in gitlinks:
            if gitlink.is_relative_to(target):
                return self._name(gitlink), False
        nested = self._held_repository(target)
        return (self._name(nested), False) if nested is not None else None

    def _containing_repository(self, target: Path) -> Path | None:
        """Find a real repository on the target's ancestry below the vault."""
        candidate = target
        while candidate != self._source_dir and candidate.is_relative_to(
            self._source_dir
        ):
            if _is_repository(candidate):
                return candidate
            candidate = candidate.parent
        return None

    def _held_repository(self, target: Path) -> Path | None:
        """Find the first repository below this directory, without following links."""
        if not is_directory(target):
            return None
        for dirpath, dirnames, _files in os.walk(
            target, onerror=_raise_walk_error, followlinks=False
        ):
            dirnames[:] = [name for name in dirnames if name != ".git"]
            candidate = Path(dirpath)
            if candidate != target and _is_repository(candidate):
                return candidate
        return None

    def refuse(
        self, abs_path: Path, argument: str, path: str, *, holding: bool = True
    ) -> None:
        """Raise when *abs_path* lies in or holds a submodule or nested repository.

        Args:
            abs_path: The validated absolute path the write would touch.
            argument: The tool parameter that carried *path*.
            path: The path as the caller sent it.
            holding: Also refuse a folder holding a protected repository. ``False``
                for a destination folder, whose repositories a move leaves
                alone.

        Raises:
            SubmodulePathError: When :meth:`submodule_of` finds a repository
                that the arguments refuse.
        """
        found = self.submodule_of(abs_path)
        if found is None:
            return
        submodule, inside = found
        if not (inside or holding):
            return
        build = SubmodulePathError.inside if inside else SubmodulePathError.holds
        raise build(argument, path, submodule)

    def _name(self, gitlink: Path) -> str:
        return gitlink.relative_to(self._source_dir).as_posix()

    def _current_gitlinks(self) -> tuple[Path, ...]:
        with self._lock:
            root = self._git_root()
            if root is None:
                return ()
            # An index file git would not name cannot be watched: list it
            # on every call instead.
            key = _index_key(self._index) if self._index is not None else None
            if self._listed and self._index is not None and key == self._index_key:
                return self._gitlinks
            gitlinks = self._read_gitlinks(root)
            if gitlinks is None:
                # Key left as it was, so the next call asks git again.
                return self._gitlinks
            self._gitlinks, self._index_key, self._listed = gitlinks, key, True
            return gitlinks

    def _git_root(self) -> Path | None:
        if not self._root_checked:
            found = _find_git_root(self._source_dir)
            if found is None:
                logger.info(
                    "submodule_guard_inactive source_dir=%s reason=no_repository",
                    self._source_dir,
                )
            else:
                self._root = found.resolve()
                self._index = _index_file(self._root)
            self._root_checked = True
        return self._root

    def _read_gitlinks(self, root: Path) -> tuple[Path, ...] | None:
        """List the gitlinks under the vault, or ``None`` when git fails."""
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--stage", "-z"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("submodule_list_failed root=%s err=%s", root, exc)
            return None
        gitlinks = []
        for entry in result.stdout.split(b"\0"):
            meta, _, name = entry.partition(b"\t")
            if not meta.startswith(_GITLINK_MODE.encode()):
                continue
            gitlink = root / name.decode("utf-8", "surrogateescape")
            # A gitlink outside the vault (a vault that is a repository's
            # subfolder) is never a write target.
            if gitlink.is_relative_to(self._source_dir):
                gitlinks.append(gitlink)
        return tuple(gitlinks)


def _is_repository(candidate: Path) -> bool:
    """Confirm only directories with a .git directory or gitfile as candidates."""
    if not is_directory(candidate):
        return False
    marker = candidate / ".git"
    if not (is_directory(marker) or is_regular_file(marker)):
        return False
    root = _find_git_root(candidate)
    return root is not None and root.resolve() == candidate


def _raise_walk_error(exc: OSError) -> None:
    """Keep an unreadable subtree from looking like a safe empty directory."""
    raise exc


def _index_file(root: Path) -> Path | None:
    """Return the index file of the repository at *root*.

    Asked of git, because ``.git`` is a file pointing elsewhere in a linked
    worktree and in a vault that is itself a submodule.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("submodule_index_unknown root=%s err=%s", root, exc)
        return None
    return Path(result.stdout.strip()) / "index"


def _index_key(index: Path) -> _IndexKey:
    """Identify the index file's current version, or ``None`` when absent."""
    try:
        st = index.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino
