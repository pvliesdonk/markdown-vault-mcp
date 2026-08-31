"""Shared pure-function utilities for markdown-vault-mcp."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

from markdown_vault_mcp.utils.content_kind import (
    artifact_suffix,
    effective_attachment_extensions,
    has_md_suffix,
    is_allowed_artifact,
    is_allowed_artifact_suffix,
    is_note,
)

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


def normalize_folder(folder: str | None) -> str | None:
    """Fold a caller-supplied folder value to its canonical vault spelling.

    Folder values arrive from tool callers in whatever shape a human types:
    ``"X/"``, ``"/X"``, ``"X\\Y"``.  The stored ``folder`` column holds a
    slash-separated path with no surrounding slashes (``""`` for the vault
    root), so every folder-scoped surface folds its input through here
    before comparing.

    Three states stay distinct, and only the third is folded:

    - ``None`` -- no folder restriction at all.
    - ``""`` (and ``"/"``) -- root-level documents only.  Never collapsed to
      ``None``: an explicit empty folder is a restriction, not its absence.
    - anything else -- backslashes folded to slashes, surrounding slashes
      stripped, so ``"X/"`` and ``"/X"`` both select the same notes as
      ``"X"``.

    Args:
        folder: Folder value as received from the caller.

    Returns:
        ``None`` when *folder* is ``None``, else the canonical folder
        string.
    """
    if folder is None:
        return None
    return folder.replace("\\", "/").strip("/")


def folder_matches(row_folder: str, folder: str) -> bool:
    """Whether a stored folder value falls inside a normalized folder scope.

    Args:
        row_folder: The ``folder`` value stored for a row (``""`` for the
            vault root).
        folder: A scope already folded through :func:`normalize_folder`.

    Returns:
        ``True`` when the row is the scope itself or one of its
        sub-folders.  With *folder* ``""`` only root-level rows match,
        since no stored folder starts with ``"/"``.
    """
    return row_folder == folder or row_folder.startswith(folder + "/")


def resolve_inside(path: str, base: Path, *, original: str | None = None) -> Path:
    """Resolve *path* against *base* and verify the result stays inside it.

    The single path-traversal guard shared by every validator: resolve the
    joined path, then reject it unless it is *base* itself or one of its
    descendants. Callers that must also reject *base* itself (e.g. a folder
    scope that may not be the vault root) add that check locally.

    Args:
        path: Relative path to resolve (may contain ``..`` segments —
            that is exactly what the guard catches).
        base: Absolute base directory the result must stay inside.
        original: Spelling of the path to name in the error message, for
            callers that normalized *path* before resolving. Defaults to
            *path* itself.

    Returns:
        The resolved absolute path.

    Raises:
        ValueError: If the resolved path escapes *base*.
    """
    abs_path = (base / path).resolve()
    if not abs_path.is_relative_to(base.resolve()):
        raise ValueError(
            f"Path traversal detected: {path if original is None else original}"
        )
    return abs_path


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
    if not is_note(path):
        raise ValueError(f"Path must end with '.md': {path}")
    return resolve_inside(path, source_dir)


def validate_history_path(
    path: str, source_dir: Path, attachment_extensions: frozenset[str]
) -> Path:
    """Resolve a vault-relative path for read-only git history/diff queries.

    Unlike :func:`validate_path` (which is strictly ``.md`` and is used by the
    write/edit/read paths), this accepts a ``.md`` note OR a path whose suffix
    (lowercased, without the dot) is in *attachment_extensions* (or
    *attachment_extensions* contains ``"*"``, meaning all non-``.md`` files).
    Applies the same traversal guard. Does not require the path to exist —
    history of a since-deleted file is still queryable.

    Args:
        path: Vault-relative path (note or attachment).
        source_dir: Absolute vault root.
        attachment_extensions: Allowed attachment extensions (lowercase, no dot).
            The special value ``frozenset({"*"})`` accepts every non-``.md``
            extension.

    Returns:
        The resolved absolute path.

    Raises:
        ValueError: *path* is neither ``.md`` nor an allowed attachment
            extension, or it escapes *source_dir*.
    """
    if not (
        is_note(path)
        or is_allowed_artifact_suffix(artifact_suffix(path), attachment_extensions)
    ):
        raise ValueError(
            f"Path must be a .md note or a configured attachment type: {path}"
        )
    return resolve_inside(path, source_dir)


def validate_history_dir(path: str, source_dir: Path) -> Path:
    """Resolve a vault-relative *directory* path for read-only git-history queries.

    The directory analogue of :func:`validate_history_path`: applies the same
    traversal guard but imposes no extension requirement, since a folder scope
    (e.g. ``"guides"``) is not a file. Like the file variant it does not require
    the path to exist, so history of a since-removed subtree stays queryable.
    The empty string (the bundle root) is rejected — callers pass ``None`` for
    whole-vault history rather than routing the root through here.

    Args:
        path: Vault-relative directory path (e.g. ``"guides"`` or
            ``"guides/sub"``). Must be non-empty.
        source_dir: Absolute vault root.

    Returns:
        The resolved absolute directory path.

    Raises:
        ValueError: *path* is empty or escapes *source_dir*.
    """
    if not path.strip("/"):
        raise ValueError(
            "A directory history scope must name a folder; pass None for "
            "whole-vault history."
        )
    return resolve_inside(path, source_dir)


__all__ = [
    "CHAR_SUBS",
    "apply_link_replacement",
    "artifact_suffix",
    "build_position_map",
    "compute_new_raw_target",
    "effective_attachment_extensions",
    "find_closest_match",
    "fts_row_to_note_info",
    "has_md_suffix",
    "is_allowed_artifact",
    "is_allowed_artifact_suffix",
    "is_note",
    "is_path_excluded",
    "normalize_text",
    "resolve_inside",
    "validate_history_dir",
    "validate_history_path",
    "validate_path",
]
