"""Shared pure-function utilities for markdown-vault-mcp."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

from markdown_vault_mcp.types import DEFAULT_ATTACHMENT_EXTENSIONS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
from markdown_vault_mcp.utils.fts import fts_row_to_note_info
from markdown_vault_mcp.utils.links import (
    apply_link_replacement,
    compute_new_raw_target,
)
from markdown_vault_mcp.utils.text import (
    CHAR_SUBS,
    build_position_map,
    find_closest_match,
    normalize_text,
)


def is_path_excluded(path: str, exclude_patterns: Sequence[str] | None) -> bool:
    """Check whether *path* matches any configured exclude pattern.

    Args:
        path: Relative POSIX path string.
        exclude_patterns: Glob patterns to check against.  ``None`` or
            empty means nothing is excluded.

    Returns:
        ``True`` if the path matches any pattern in *exclude_patterns*.
    """
    if not exclude_patterns:
        return False
    return any(fnmatch.fnmatch(path, pat) for pat in exclude_patterns)


def effective_attachment_extensions(
    attachment_extensions: Sequence[str] | None,
) -> frozenset[str]:
    """Return the effective set of allowed attachment extensions.

    Args:
        attachment_extensions: User-configured extension list, or ``None``
            to use the default set.

    Returns:
        Frozenset of lower-case extension strings (without leading dot).
        The special value ``frozenset(["*"])`` means all non-.md files.
    """
    if attachment_extensions is None:
        return DEFAULT_ATTACHMENT_EXTENSIONS
    return frozenset(attachment_extensions)


def validate_path(path: str, source_dir: Path) -> Path:
    """Resolve a relative path and validate it is inside *source_dir*.

    Args:
        path: Relative document path (must end with ``.md``).
        source_dir: Absolute path to the vault root directory.

    Returns:
        The resolved absolute path.

    Raises:
        ValueError: If the path escapes the source directory or does
            not end with ``.md``.
    """
    if not path.endswith(".md"):
        raise ValueError(f"Path must end with '.md': {path}")
    abs_path = (source_dir / path).resolve()
    if not abs_path.is_relative_to(source_dir.resolve()):
        raise ValueError(f"Path traversal detected: {path}")
    return abs_path


__all__ = [
    "CHAR_SUBS",
    "apply_link_replacement",
    "build_position_map",
    "compute_new_raw_target",
    "effective_attachment_extensions",
    "find_closest_match",
    "fts_row_to_note_info",
    "is_path_excluded",
    "normalize_text",
    "validate_path",
]
