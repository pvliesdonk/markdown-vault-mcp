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
    """Removal candidates, preserved boundaries and index write destinations."""

    removable: tuple[tuple[str, str, str], ...]
    kept: tuple[tuple[str, str], ...]
    refresh: tuple[str, ...]


class CollapseInterrupted(MarkdownMCPError):
    """A collapse stopped after some writes or removals may have completed."""

    def __init__(
        self,
        written: list[str],
        removed: list[str],
        pending: list[str],
        cause: Exception,
    ) -> None:
        """Record completed work and the uncertain remainder.

        Args:
            written: Owners whose generation completed.
            removed: Index paths successfully removed.
            pending: Paths still pending or possibly written.
            cause: Error that interrupted the operation.
        """
        self.written = tuple(written)
        self.removed = tuple(removed)
        self.pending = tuple(pending)
        super().__init__(
            f"Collapse interrupted: {cause}; written owners {written!r}; removed {removed!r}; pending or possibly written {pending!r}"
        )


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
        current = posixpath.dirname(current)


def _preview(
    migrate: OkfMigrationManager,
    removable: list[tuple[str, str, str]],
    kept: list[tuple[str, str]],
    allow_protected: bool,
) -> tuple[str, ...]:
    """Discard refused owners and preview again against the reduced tree."""
    while removable:
        dissolve = {posixpath.dirname(path) for path, _, _ in removable}
        destinations: list[str] = []
        refused: dict[str, str] = {}
        for owner in sorted({owner for _, _, owner in removable}):
            try:
                migrate.generate_index(
                    folder=owner,
                    allow_protected=allow_protected,
                    dissolve=dissolve,
                    preview=destinations,
                )
            except (
                InvalidRequestError,
                DocumentUnreadableError,
                NoteTooLargeError,
            ) as exc:
                refused[owner] = str(exc)
        if not refused:
            return tuple(dict.fromkeys(destinations))
        survivors = []
        for path, etag, owner in removable:
            if owner in refused:
                kept.append((path, refused[owner]))
            else:
                survivors.append((path, etag, owner))
        removable[:] = survivors
    return ()


def plan_collapse(
    doc: DocumentManager, migrate: OkfMigrationManager, *, allow_protected: bool = False
) -> CollapsePlan:
    """Preview generated indexes that can be folded into designated parents.

    Args:
        doc: Document manager for the vault.
        migrate: Generator used to compare current bodies and preview writes.
        allow_protected: Permit changes to operator-protected paths.

    Returns:
        A plan containing etag-guarded removals and write destinations.
    """
    if doc.index_designation("") is None:
        raise InvalidRequestError(
            "Index designation is inactive; enable OKF index maintenance and configure MARKDOWN_VAULT_MCP_OKF_FOLDERS."
        )
    doc.ensure_index_current()
    queue = [""]
    boundaries = {""}
    removed: set[str] = set()
    removable: list[tuple[str, str, str]] = []
    kept: list[tuple[str, str]] = []
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
        if not top or doc.index_designation(top) is True:
            continue
        path = f"{top}/index.md"
        reason = doc.protection_reason(path, allow_protected=allow_protected)
        try:
            note = doc.read(path)
            if reason is None and note is not None:
                if note.path != path:
                    kept.append((path, "alias"))
                    continue
                if strip_frontmatter_block(
                    note.content, preserve_body=True
                ) != migrate.index_body(top):
                    reason = "authored"
                else:
                    parent, reason = _owner(top, boundaries, removed, doc)
                    if parent is not None and note.etag is not None:
                        removable.append((path, note.etag, parent))
                        removed.add(top)
                        continue
        except (DocumentUnreadableError, NoteTooLargeError):
            reason = "unreadable"
        kept.append((path, reason or "unreadable"))
    refresh = _preview(migrate, removable, kept, allow_protected)
    return CollapsePlan(tuple(removable), tuple(kept), refresh)


def _reconcile(
    migrate: OkfMigrationManager,
    owners: list[str],
    removed: list[tuple[str, str, str]],
    allow_protected: bool,
) -> None:
    """Refresh owners against the remaining visible boundaries."""
    actual = {posixpath.dirname(path) for path, _, _ in removed}
    for owner in owners:
        migrate.generate_index(
            folder=owner, allow_protected=allow_protected, dissolve=actual
        )


def apply_collapse(
    doc: DocumentManager,
    migrate: OkfMigrationManager,
    plan: CollapsePlan,
    *,
    allow_protected: bool = False,
) -> CollapsePlan:
    """Write planned listings, delete candidates, and reconcile kept boundaries.

    Args:
        doc: Document manager for the vault.
        migrate: Generator used to refresh designated parents.
        plan: Preview to apply.
        allow_protected: Permit changes to operator-protected paths.

    Returns:
        The paths removed, paths kept, and refreshed index paths.
    """
    doc.ensure_writable()
    owners = sorted({owner for _, _, owner in plan.removable})
    dissolve = {posixpath.dirname(path) for path, _, _ in plan.removable}
    written: list[str] = []
    removed: list[tuple[str, str, str]] = []
    kept = list(plan.kept)
    blocked: list[str] = []
    try:
        with okf_write_suppressed():
            for owner in owners:
                migrate.generate_index(
                    folder=owner, allow_protected=allow_protected, dissolve=dissolve
                )
                written.append(owner)
            for path, etag, owner in plan.removable:
                if any(path.startswith(f"{folder}/") for folder in blocked):
                    kept.append((path, "below a kept index"))
                    continue
                try:
                    with doc.write_scope():
                        note = doc.read(path)
                        if note is not None and note.path != path:
                            kept.append((path, "changed since preview"))
                            blocked.append(posixpath.dirname(path))
                            continue
                        doc.delete(path, if_match=etag, allow_protected=allow_protected)
                    removed.append((path, etag, owner))
                except ConcurrentModificationError:
                    kept.append((path, "changed since preview"))
                    blocked.append(posixpath.dirname(path))
                except (MarkdownMCPError, OSError) as exc:
                    try:
                        _reconcile(migrate, owners, removed, allow_protected)
                    except (MarkdownMCPError, OSError) as refresh_error:
                        raise exc from refresh_error
                    raise
            if len(removed) != len(plan.removable):
                _reconcile(migrate, owners, removed, allow_protected)
    except (MarkdownMCPError, OSError) as exc:
        completed = [path for path, _, _ in removed]
        pending = [path for path, _, _ in plan.removable if path not in completed]
        pending.extend(path for path in plan.refresh if path not in pending)
        raise CollapseInterrupted(written, completed, pending, exc) from exc
    return CollapsePlan(tuple(removed), tuple(kept), plan.refresh)
