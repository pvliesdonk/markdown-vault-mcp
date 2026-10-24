"""The file-backed answer to "is this note still there?" (#1785).

A ``stat`` classified by errno, as the spec's ``SourceProbe`` says: a path
that does not exist, or names something that is not a regular file, is
*absent*; a stat the file system refuses is *unavailable*, as is any path
under a source root that is not a statable, listable directory holding at
least one entry: an unmounted volume, or the empty mount point it leaves,
must remove nothing (#1791). *Present* carries the file's content hash, the
keyword index's token, so it reads the file; the deletion rule only asks
about paths the keyword index holds no rows for.
"""

from __future__ import annotations

import logging
import os
import stat as stat_module
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.types import SourceState

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class FileSourceProbe:
    """Answers :class:`~markdown_vault_mcp.interfaces.SourceProbe` from a directory.

    Args:
        source_dir: The vault's root directory.
    """

    def __init__(self, source_dir: Path) -> None:
        self._source_dir = source_dir

    def probe(self, path: str) -> SourceState:
        """Return whether *path* is present (with its content hash), absent or unavailable.

        Args:
            path: Vault-relative path.

        Returns:
            The source's answer.
        """
        if not self._root_available():
            return SourceState("unavailable")
        target = self._source_dir / path
        try:
            st = target.stat()
        except (FileNotFoundError, NotADirectoryError):
            return SourceState("absent")
        except OSError as exc:
            logger.debug("source_probe_stat_refused path=%s err=%s", path, exc)
            return SourceState("unavailable")
        if not stat_module.S_ISREG(st.st_mode) or not self._named_exactly(path):
            return SourceState("absent")
        try:
            return SourceState("present", compute_file_hash(target))
        except OSError as exc:
            logger.debug("source_probe_read_refused path=%s err=%s", path, exc)
            return SourceState("unavailable")

    def _named_exactly(self, path: str) -> bool:
        """Whether every component of *path* is listed under its exact name.

        On a case-insensitive file system a stat of ``Notes/Foo.md`` succeeds
        after ``Notes/`` became ``notes/`` or ``Foo.md`` became ``foo.md``;
        only the listings tell the old name is gone, so its vectors are not
        kept as a keyword gap (#1785). One listing per component, and the
        probe only sees paths the keyword index holds no rows for.
        """
        current = self._source_dir
        for part in PurePosixPath(path).parts:
            try:
                if not any(entry.name == part for entry in current.iterdir()):
                    return False
            except OSError:
                return True  # the stat succeeded: a listing error is not absence
            current = current / part
        return True

    def _root_available(self) -> bool:
        """Whether the root is a directory that holds anything at all (see :func:`holds_entries`)."""
        return holds_entries(self._source_dir)


def holds_entries(folder: Path) -> bool:
    """Whether *folder* is a listable directory holding at least one entry.

    An empty one is what an unmounted volume's mount point, or a bind mount
    Docker created, looks like, as much as a vault whose notes were all
    deleted, and only the first is common: the keyword index and the vector
    store remove nothing under it, and an explicit forced rebuild empties a
    vault deliberately (#1791). A folder holding only non-note files
    (``.obsidian/``) is a vault whose notes are gone.

    Args:
        folder: The vault's root directory.

    Returns:
        ``False`` for a missing, unlistable, non-directory or empty folder.
    """
    try:
        if not stat_module.S_ISDIR(folder.stat().st_mode):
            return False
        with os.scandir(folder) as entries:
            return next(entries, None) is not None
    except OSError:
        return False
