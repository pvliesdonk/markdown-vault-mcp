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
import os
from dataclasses import dataclass
from pathlib import Path

import frontmatter as fm
import yaml

logger = logging.getLogger(__name__)

# Cap on transported convention text per file so a pathological conventions
# file cannot flood tool results.
_MAX_ENTRY_CHARS = 4000
_TRUNCATION_MARKER = "\n… [truncated]"


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
    files are excluded from the search index (via ``exclude_patterns``)
    but read directly from the vault directory each call. Files are small
    and lookups touch at most one file per ancestor folder, so no caching
    is applied.

    Args:
        source_dir: Root directory of the markdown vault.
        filename: Well-known convention filename (e.g. ``"_conventions.md"``),
            or ``None`` to disable the feature entirely.
    """

    def __init__(self, source_dir: Path, filename: str | None) -> None:
        self._source_dir = source_dir
        self._filename = filename

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

        Returns:
            Sorted vault-relative folder paths (``""`` for the vault root).
            Empty when disabled or the vault directory does not exist yet
            (e.g. before a managed-git clone completes).
        """
        if self._filename is None or not self._source_dir.is_dir():
            return []
        root = self._source_dir.resolve()
        folders: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            if self._filename in filenames:
                rel = Path(dirpath).resolve().relative_to(root).as_posix()
                folders.append("" if rel == "." else rel)
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
            raw = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
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
