"""Live note membership for generated indexes.

A generated ``index.md`` lists what the folder holds on disk, not what search
admitted, so this walks names and opens no note. It leaves out dot paths,
excluded paths, links that resolve outside the vault, and whatever the
inspection policy owns (the index, change tracker, vector store and state
files, with their aliases and hardlinks), so a listing never links a note the
vault cannot read or a file that is the server's own.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp.bundle_inventory import BundleChildrenRequest
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.utils import is_path_excluded, resolve_inside
from markdown_vault_mcp.utils.content_kind import is_note
from markdown_vault_mcp.utils.fs import (
    could_be_regular_file,
    is_directory,
    is_subtree_excluded,
    walk_files_strict,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from markdown_vault_mcp.bundle_inventory import BundleInspector


class IndexListing:
    """Find the notes a generated index may show, by a name-only walk.

    Args:
        source_dir: Vault root, as the document manager holds it.
        exclude_patterns: Effective vault-relative exclude patterns.
        inspector: The inspection policy owner; it alone decides which paths
            belong to the server.
    """

    def __init__(
        self,
        source_dir: Path,
        exclude_patterns: Sequence[str],
        inspector: BundleInspector,
    ) -> None:
        self._source = source_dir
        self._excludes = exclude_patterns
        self._inspector = inspector

    def visible(self, path: str) -> bool:
        """Whether a generated index may list or link *path*.

        Args:
            path: Vault-relative path of a note or index file.

        Returns:
            ``False`` for a path with a dot component or matching an exclude
            pattern.
        """
        hidden = any(part.startswith(".") for part in path.split("/"))
        return not hidden and not is_path_excluded(path, self._excludes)

    def note_paths(self, folder: str) -> list[str]:
        """List every note under *folder* an index may show.

        Args:
            folder: Normalized vault-relative folder (``""`` for the root).

        Returns:
            Sorted vault-relative note paths; empty for a missing folder.

        Raises:
            InvalidRequestError: If *folder* or a directory the listing would
                enter cannot be listed; the message names that directory.
        """
        top = self._source / folder
        try:
            if not is_directory(top):
                return []
            self._load_policy()
            return [
                self._rel(path)
                for path in walk_files_strict(
                    top, prune=self._skips_directory, follow_links=True
                )
                if is_note(path.name)
                and could_be_regular_file(path)
                and self.visible(self._rel(path))
                and self._admits(path)
            ]
        except OSError as exc:
            where = Path(exc.filename or top).relative_to(self._source).as_posix()
            raise InvalidRequestError(
                f"Cannot list {where!r}; no index was written. Ask the user to "
                "restore access to it, then call okf_generate_index again."
            ) from exc

    def _rel(self, path: Path) -> str:
        return path.relative_to(self._source).as_posix()

    def _skips_directory(self, path: Path) -> bool:
        """Whether the walk leaves *path* alone: hidden, excluded or not ours."""
        return (
            path.name.startswith(".")
            or is_subtree_excluded(self._rel(path), self._excludes)
            or not self._admits(path)
        )

    def _admits(self, path: Path) -> bool:
        """Whether *path* is the vault's own and not the server's.

        A link is the vault's own when it resolves inside it, as the write
        path requires; only a link needs resolving, for a plain path below an
        admitted directory is inside already.
        """
        rel = self._rel(path)
        if self._inspector._internal(rel):
            return False
        if not path.is_symlink():
            return True
        try:
            resolve_inside(rel, self._source)
        except InvalidRequestError:
            return False
        return True

    def _load_policy(self) -> None:
        """Have the policy owner read the configured state paths.

        Its ownership test works only after an inspection has prepared it, as
        ``okf_navigation`` relies on; the vault root is the cheapest scope.
        """
        inspection = self._inspector.inspect(
            BundleChildrenRequest(root="", entry_limit=1)
        )
        if inspection.state != "inspected":
            raise OSError(
                errno.EACCES, "Vault root cannot be inspected", os.fspath(self._source)
            )
