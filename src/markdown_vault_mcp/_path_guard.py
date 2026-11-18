"""Operator policy for ordinary mutations of vault paths."""

from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from unicodedata import normalize

from markdown_vault_mcp.utils import resolve_inside


class PathGuard:
    """Match configured protected globs against paths and their ancestors."""

    def __init__(self, source_dir: Path, patterns: Sequence[str] | None) -> None:
        self._source_dir = source_dir
        self._patterns = tuple(normalize("NFC", p).casefold() for p in patterns or ())

    def reason(self, path: str) -> str | None:
        """Return the protection reason for a path, or ``None`` if unprotected.

        Args:
            path: Vault-relative path to check.

        Returns:
            The reason a mutation is refused, if one applies.
        """
        if not self._patterns:
            return None
        resolved = resolve_inside(path, self._source_dir)
        relative = resolved.relative_to(self._source_dir.resolve()).as_posix()
        for candidate in (path, relative):
            parts = PurePosixPath(normalize("NFC", candidate).casefold()).parts
            for end in range(1, len(parts) + 1):
                prefix = "/".join(parts[:end])
                if any(fnmatchcase(prefix, pattern) for pattern in self._patterns):
                    return "protected by MARKDOWN_VAULT_MCP_PROTECTED_PATHS"
        return None
