"""Which vault paths lie in a git submodule, so a write there is refused (#1564).

Every git command runs in the superproject, which refuses to stage a path
inside a submodule (``fatal: Pathspec ... is in submodule``). A write there
would land on disk and never reach git, so the write managers ask
:class:`SubmoduleGuard` first and refuse. What git does is recorded in
``docs/design/reference/git-submodules.md``.

The index decides: a gitlink (mode ``160000`` in ``git ls-files --stage``) is
what makes git refuse, whether the submodule is initialised or not. The list
is read again only when the index file changes, so an unchanged index costs
one ``stat``. When git cannot list the index the guard logs it, keeps the
submodules it last listed, and asks again on the next write; only a guard that
never listed any refuses nothing then.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from markdown_vault_mcp.exceptions import SubmodulePathError
from markdown_vault_mcp.git._run import _find_git_root

__all__ = ["SubmoduleGuard"]

logger = logging.getLogger(__name__)

_GITLINK_MODE = "160000"

# (st_mtime_ns, st_ctime_ns, st_size, st_ino) of the index file, or None when
# it is absent.
_IndexKey = tuple[int, int, int, int] | None


class SubmoduleGuard:
    """Answer whether a vault path lies in, or holds, a git submodule.

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
        """Return the submodule *abs_path* lies in or holds, or ``None``.

        Args:
            abs_path: An absolute path inside the vault.

        Returns:
            ``(submodule, inside)``: the submodule's vault-relative POSIX path,
            and ``True`` when *abs_path* is the submodule or lies inside it,
            ``False`` when it is a folder holding the submodule. ``None`` when
            neither holds, or the vault is not in a git repository.
        """
        target = abs_path.resolve()
        for gitlink in self._current_gitlinks():
            if target == gitlink or target.is_relative_to(gitlink):
                return self._name(gitlink), True
            if gitlink.is_relative_to(target):
                return self._name(gitlink), False
        return None

    def refuse(
        self, abs_path: Path, argument: str, path: str, *, holding: bool = True
    ) -> None:
        """Raise when *abs_path* lies in a submodule, or holds one.

        Args:
            abs_path: The validated absolute path the write would touch.
            argument: The tool parameter that carried *path*.
            path: The path as the caller sent it.
            holding: Also refuse a folder that holds a submodule. ``False``
                for a destination folder, whose submodules a move leaves
                alone.

        Raises:
            SubmodulePathError: When :meth:`submodule_of` finds a submodule
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
