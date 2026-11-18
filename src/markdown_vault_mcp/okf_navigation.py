"""Read authored folder navigation or render a bounded live listing."""

from __future__ import annotations

import errno
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from markdown_vault_mcp.okf import OKF_ROOT_INDEX_TITLE, build_index_markdown
from markdown_vault_mcp.utils.fs import open_directory_nofollow, read_regular_nofollow
from markdown_vault_mcp.utils.text import decode_utf8

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.bundle_inventory import (
        BundleEntry,
        BundleInspection,
        Enumeration,
        ScopeState,
    )


@dataclass(frozen=True)
class FolderNavigation:
    """A folder's navigation body and the observations supporting it."""

    folder: str
    state: ScopeState
    source: Literal["authored", "synthesized"] | None
    body: str | None
    index_path: str
    reason: str | None
    entries: tuple[BundleEntry, ...]
    enumeration: Enumeration


def authored_body(text: str) -> str:
    """Remove a leading frontmatter block while preserving the body verbatim.

    Args:
        text: Complete decoded index text.

    Returns:
        The body, or the original text when no complete block leads it.
    """
    lines = text.splitlines(keepends=True)
    if lines and lines[0].rstrip("\r\n") == "---":
        for index, line in enumerate(lines[1:], 1):
            if line.rstrip("\r\n") == "---":
                return "".join(lines[index + 1 :])
    return text


def read_authored(
    source: Path, folder: str, note_limit: int
) -> tuple[str | None, str | None]:
    """Read a no-follow index, returning text or a stable refusal reason.

    Args:
        source: Vault root.
        folder: Validated vault-relative folder.
        note_limit: Configured byte limit, or zero for the default ceiling.

    Returns:
        Text and reason; both are None when the index is absent.
    """
    limit = min(65536, note_limit) if note_limit > 0 else 65536
    try:
        with open_directory_nofollow(
            source, folder.split("/") if folder else ()
        ) as parent:
            raw = read_regular_nofollow(parent, "index.md", limit + 1)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None, None
        return None, {
            errno.ELOOP: "symlink",
            errno.EINVAL: "nonregular",
            errno.EISDIR: "nonregular",
        }.get(exc.errno or 0, "io_error")
    if len(raw) > limit:
        return None, "note_limit"
    try:
        return decode_utf8(raw), None
    except UnicodeDecodeError:
        return None, "invalid_encoding"


def synthesize(folder: str, inspection: BundleInspection) -> str:
    """Render visible immediate concepts and directories from live observations.

    Args:
        folder: Validated vault-relative folder.
        inspection: Bounded live observations.

    Returns:
        Markdown listing of immediate concepts and directories.
    """
    entries: list[tuple[str, str, str | None]] = [
        (
            entry.path.rsplit("/", 1)[-1],
            "/" + entry.path + ("/" if entry.role == "directory" else ""),
            None,
        )
        for entry in inspection.entries
        if entry.role in {"concept_candidate", "directory"}
    ]
    return build_index_markdown(
        folder.rsplit("/", 1)[-1] or OKF_ROOT_INDEX_TITLE, entries
    )
