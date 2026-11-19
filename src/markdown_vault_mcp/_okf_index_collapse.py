"""Plan and apply the removal of obsolete generated index boundaries."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING

from markdown_vault_mcp._okf_write import okf_write_suppressed
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentUnreadableError,
    InvalidRequestError,
    MarkdownMCPError,
    NoteTooLargeError,
)
from markdown_vault_mcp.scanner import strip_frontmatter_block

if TYPE_CHECKING:
    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.okf_migrate import OkfMigrationManager


@dataclass(frozen=True)
class CollapsePlan:
    """Index paths to remove, paths to keep with reasons, and parents to refresh."""

    removable: tuple[tuple[str, str], ...]
    kept: tuple[tuple[str, str], ...]
    parents: tuple[str, ...]


def _owner(
    folder: str, boundaries: set[str], removed: set[str], doc: DocumentManager
) -> tuple[str | None, str]:
    """Find the nearest surviving boundary or designated ancestor."""
    current = posixpath.dirname(folder)
    while True:
        if doc.index_designation(current) is True:
            return current, ""
        if current in boundaries and current not in removed:
            return None, "below a kept index" if current else "no designated parent"
        if not current:
            return None, "no designated parent"
        current = posixpath.dirname(current)


def plan_collapse(
    doc: DocumentManager, migrate: OkfMigrationManager, *, allow_protected: bool = False
) -> CollapsePlan:
    """Preview generated indexes that can be folded into designated parents.

    Args:
        doc: Document manager for the vault.
        migrate: Generator used to compare current bodies.
        allow_protected: Permit removal of operator-protected paths.

    Returns:
        A plan containing etag-guarded removals and preserved boundaries.
    """
    doc.ensure_index_current()
    queue = [""]
    boundaries = {""}
    removed: set[str] = set()
    removable: list[tuple[str, str]] = []
    kept: list[tuple[str, str]] = []
    parents: set[str] = set()
    while queue:
        top = queue.pop(0)
        paths = doc.index_note_paths(top, top)
        children = sorted(
            {
                posixpath.dirname(p)
                for p in paths
                if p.endswith("/index.md") and posixpath.dirname(p) != top
            }
        )
        boundaries.update(children)
        queue.extend(children)
        if not top:
            continue
        path = f"{top}/index.md"
        if doc.index_designation(top) is True:
            continue
        reason = doc.protection_reason(path, allow_protected=allow_protected)
        try:
            note = doc.read(path)
            if reason is None and note is not None:
                if strip_frontmatter_block(
                    note.content, preserve_body=True
                ) != migrate.index_body(top):
                    reason = "authored"
                else:
                    parent, reason = _owner(top, boundaries, removed, doc)
                    if parent is not None and note.etag is not None:
                        removable.append((path, note.etag))
                        removed.add(top)
                        parents.add(parent)
                        continue
        except (DocumentUnreadableError, NoteTooLargeError):
            reason = "unreadable"
        kept.append((path, reason or "unreadable"))
    return CollapsePlan(tuple(removable), tuple(kept), tuple(sorted(parents)))


def apply_collapse(
    doc: DocumentManager,
    migrate: OkfMigrationManager,
    plan: CollapsePlan,
    *,
    allow_protected: bool = False,
) -> CollapsePlan:
    """Apply a plan, preserving files that changed after the preview.

    Args:
        doc: Document manager for the vault.
        migrate: Generator used to refresh designated parents.
        plan: Preview to apply.
        allow_protected: Permit changes to operator-protected paths.

    Returns:
        The paths removed, paths kept, and refreshed parent folders.
    """
    doc.ensure_writable()
    for parent in plan.parents:
        path = f"{parent}/index.md" if parent else "index.md"
        reason = doc.protection_reason(
            path, allow_protected=allow_protected, allow_designated=True
        )
        if reason:
            raise InvalidRequestError(f"Cannot refresh {path}: {reason}")
        doc.read(path)
    for folder in {
        *(posixpath.dirname(path) for path, _ in plan.removable),
        *plan.parents,
    }:
        migrate.preflight_index_descendants(folder, allow_protected=allow_protected)
    removed: list[tuple[str, str]] = []
    kept = list(plan.kept)
    blocked: list[str] = []
    with okf_write_suppressed(), doc.write_scope():
        for path, etag in plan.removable:
            if any(path.startswith(f"{folder}/") for folder in blocked):
                kept.append((path, "below a kept index"))
                continue
            try:
                doc.delete(path, if_match=etag, allow_protected=allow_protected)
                removed.append((path, etag))
            except (MarkdownMCPError, OSError) as exc:
                reason = (
                    "changed since preview"
                    if isinstance(exc, ConcurrentModificationError)
                    else f"could not remove: {exc}"
                )
                kept.append((path, reason))
                blocked.append(posixpath.dirname(path))
        for parent in plan.parents:

            def designated(folder: str, anchor: str = parent) -> bool:
                return (
                    bool(folder)
                    and not anchor.startswith(f"{folder}/")
                    and doc.index_designation(folder) is True
                )

            migrate.generate_index(
                folder=parent, allow_protected=allow_protected, designated=designated
            )
    return CollapsePlan(tuple(removed), tuple(kept), plan.parents)
