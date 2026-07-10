"""In-vault folder conventions: user-authored per-folder authoring policy.

A vault may carry well-known convention files (default ``_conventions.md``)
whose free-form markdown describes how notes in that folder should be
authored — for example "reference material: keep notes self-contained".
The server *transports* this text to MCP clients verbatim; it never parses,
ranks by, or otherwise interprets the content (see the design doc's
"no frontmatter-based ranking" non-goal and its folder-conventions
carve-out).

Convention files accumulate down the tree, CLAUDE.md-style: a file at the
vault root applies everywhere, and a file in a nested folder *adds to* (never
replaces) its ancestors. Resolution order is root-first so the most specific
guidance appears last.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import frontmatter as fm
import yaml

from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.fs import iter_markdown_files

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)

# Cap on transported convention text per file so a pathological conventions
# file cannot flood tool results.
_MAX_ENTRY_CHARS = 4000
_TRUNCATION_MARKER = "\n… [truncated]"

# Read cap (characters): bounds I/O and frontmatter parsing, not just the
# transported string — a runaway multi-MB file named like a conventions file
# must not be slurped wholesale on every write/context call.
_MAX_READ_CHARS = 65536


@dataclass(frozen=True)
class ConventionEntry:
    """One convention file's contribution to a path's convention chain.

    Attributes:
        folder: Vault-relative folder the file lives in (``""`` for the
            vault root).
        path: Vault-relative path of the convention file itself.
        content: The file's markdown body with frontmatter stripped,
            truncated to a sane transport size.
    """

    folder: str
    path: str
    content: str


class ConventionsResolver:
    """Resolve accumulated folder conventions from disk on demand.

    The resolver is pure disk I/O with zero index coupling: convention
    files are excluded from the search index (the Vault derives the
    exclusion from *filename*) but read directly from the vault directory
    each call. Files are small and lookups touch at most one file per
    ancestor folder, so no caching is applied.

    Args:
        source_dir: Root directory of the markdown vault.
        filename: Well-known convention filename (e.g. ``"_conventions.md"``),
            or ``None`` to disable the feature entirely.
        exclude_patterns: The *configured* vault exclude patterns (without
            the derived convention-file patterns). Folders under these
            patterns are not reported by :meth:`list_folders`.
    """

    def __init__(
        self,
        source_dir: Path,
        filename: str | None,
        *,
        exclude_patterns: Sequence[str] | None = None,
    ) -> None:
        self._source_dir = source_dir
        self._filename = filename
        self._exclude_patterns = list(exclude_patterns or [])

    @property
    def enabled(self) -> bool:
        """Whether a convention filename is configured."""
        return self._filename is not None

    @property
    def filename(self) -> str | None:
        """The configured convention filename (``None`` when disabled)."""
        return self._filename

    def for_path(self, path: str) -> list[ConventionEntry]:
        """Return the accumulated convention chain for a note or folder path.

        Args:
            path: Vault-relative note path (``.md``) or folder path. A note
                path resolves to its parent folder; ``""`` means the vault
                root. Backslashes and surrounding slashes are normalized.

        Returns:
            Convention entries from the vault root down to the path's
            folder, root-first. Empty when disabled or no files exist.

        Raises:
            ValueError: If *path* escapes the vault root.
        """
        if self._filename is None:
            return []
        folder = self._normalize_folder(path)
        entries: list[ConventionEntry] = []
        parts = folder.split("/") if folder else []
        for depth in range(len(parts) + 1):
            candidate = "/".join(parts[:depth])
            entry = self._load(candidate)
            if entry is not None:
                entries.append(entry)
        return entries

    def list_folders(self) -> list[str]:
        """Return all vault folders carrying a convention file.

        Walks via :func:`~markdown_vault_mcp.utils.fs.iter_markdown_files`
        (shared symlink policy, unreadable-directory logging, exclude-based
        directory pruning) and additionally skips dot-directories and
        folders whose convention file matches the configured exclude
        patterns, so folders outside the note space are not reported.

        Returns:
            Sorted vault-relative folder paths (``""`` for the vault root).
            Empty when disabled or the vault directory does not exist yet
            (e.g. before a managed-git clone completes).
        """
        if self._filename is None or not self._source_dir.is_dir():
            return []
        folders: set[str] = set()
        for abs_path in iter_markdown_files(self._source_dir, self._exclude_patterns):
            if abs_path.name != self._filename:
                continue
            rel = abs_path.relative_to(self._source_dir)
            if any(part.startswith(".") for part in rel.parts[:-1]):
                continue
            if is_path_excluded(rel.as_posix(), self._exclude_patterns):
                continue
            folder = rel.parent.as_posix()
            folders.add("" if folder == "." else folder)
        return sorted(folders)

    def _normalize_folder(self, path: str) -> str:
        """Normalize *path* to a vault-relative folder, guarding traversal.

        Args:
            path: Raw note or folder path from a client.

        Returns:
            Normalized vault-relative folder string (``""`` for root).

        Raises:
            ValueError: If the path escapes the vault root.
        """
        cleaned = path.replace("\\", "/").strip("/")
        if cleaned.endswith(".md"):
            cleaned = cleaned.rsplit("/", 1)[0] if "/" in cleaned else ""
        if not cleaned:
            return ""
        abs_path = (self._source_dir / cleaned).resolve()
        if not abs_path.is_relative_to(self._source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")
        return abs_path.relative_to(self._source_dir.resolve()).as_posix()

    def _load(self, folder: str) -> ConventionEntry | None:
        """Load the convention file for *folder*, if present.

        Reads at most :data:`_MAX_READ_CHARS` characters so a runaway file
        cannot flood memory or parsing time.

        Args:
            folder: Normalized vault-relative folder (``""`` for root).

        Returns:
            The entry with frontmatter stripped and content truncated, or
            ``None`` when the file does not exist or cannot be read.
        """
        if self._filename is None:
            return None
        rel = f"{folder}/{self._filename}" if folder else self._filename
        file_path = self._source_dir / rel
        try:
            with file_path.open(encoding="utf-8") as fh:
                raw = fh.read(_MAX_READ_CHARS)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError):
            # Unreadable or non-UTF-8 file: skip this entry rather than
            # breaking the whole chain (valid ancestor files still apply).
            logger.debug("conventions_read_failed path=%s", rel, exc_info=True)
            return None
        content = self._strip_frontmatter(raw, rel)
        if len(content) > _MAX_ENTRY_CHARS:
            content = content[:_MAX_ENTRY_CHARS] + _TRUNCATION_MARKER
        return ConventionEntry(folder=folder, path=rel, content=content)

    @staticmethod
    def _strip_frontmatter(raw: str, rel: str) -> str:
        """Return the markdown body of *raw* with YAML frontmatter removed.

        Args:
            raw: Full file text.
            rel: Vault-relative path, for logging only.

        Returns:
            The body text; falls back to the raw text when the frontmatter
            block fails to parse.
        """
        try:
            return fm.loads(raw).content.strip()
        except yaml.YAMLError:
            logger.debug("conventions_frontmatter_invalid path=%s", rel, exc_info=True)
            return raw.strip()
