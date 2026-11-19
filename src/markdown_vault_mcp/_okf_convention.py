"""OKF enforced-write convention maintenance (#964, design §6 phase 5b).

The ``index.md`` / ``log.md`` shapes maintained here are the spec's §8 and §9,
recorded in ``docs/design/reference/okf-v0.2.md``.

After a successful enforced content write (``write`` / ``edit``), the server
keeps the affected folder's reserved files current: it appends a dated bullet
to that folder's ``log.md`` and refreshes its ``index.md`` listing. These are
*secondary* writes riding the primary write — they flow through the same
single-writer index path and git-commit callback as any other write (they are
issued through :class:`DocumentManager`), and any failure degrades to a logged
``WARNING`` without disturbing the already-committed primary write.

The maintainer is built when index or log maintenance is enabled, and it
re-checks ``detector.state().active`` on every call
because an OKF declaration can flip mid-session. The secondary writes are done
under :func:`okf_write_suppressed` so the reserved files are not themselves
provenance-stamped or verification-cleared.
"""

from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING
from unicodedata import normalize

from markdown_vault_mcp._commit_scope import auxiliary_commit_writes
from markdown_vault_mcp._okf_write import current_okf_intent, okf_write_suppressed
from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import (
    append_okf_log_entry,
    is_okf_reserved_file,
)
from markdown_vault_mcp.scanner import strip_frontmatter_block
from markdown_vault_mcp.utils import folder_of, normalize_folder, resolve_inside
from markdown_vault_mcp.utils.fs import is_regular_file

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractContextManager

    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.okf_migrate import OkfMigrationManager
    from markdown_vault_mcp.okf import OkfDetector
    from markdown_vault_mcp.okf_capabilities import OkfCapabilities
    from markdown_vault_mcp.types import WriteOperation

logger = logging.getLogger(__name__)

#: Human-readable verb per maintained operation, for the ``log.md`` bullet.
_OPERATION_VERB: dict[str, str] = {"write": "wrote", "edit": "edited"}


def folder_is_designated(
    folder: str,
    patterns: tuple[str, ...],
    *,
    segment_match: Callable[[str, str], bool] = fnmatchcase,
) -> bool:
    """Return whether a folder matches the configured designation globs.

    ``a/**`` does not designate ``a`` itself. The empty folder is root.
    Leading ``./`` or ``/`` and trailing ``/`` in patterns are ignored.
    Folders and patterns are normalized to NFC before matching.

    Args:
        folder: Vault-relative folder, or an empty string for the root.
        patterns: Configured folder globs.
        segment_match: Matcher for a single folder segment.

    Returns:
        Whether any pattern matches the folder.
    """

    def matches(parts: tuple[str, ...], glob: tuple[str, ...]) -> bool:
        if not glob:
            return not parts
        if glob[0] == "**":
            if not glob[1:]:
                return bool(parts)
            return matches(parts, glob[1:]) or (
                bool(parts) and matches(parts[1:], glob)
            )
        return (
            bool(parts)
            and segment_match(parts[0], glob[0])
            and matches(parts[1:], glob[1:])
        )

    folder = normalize("NFC", folder)
    for pattern in patterns:
        glob = tuple(
            part
            for part in (normalize_folder(normalize("NFC", pattern)) or "").split("/")
            if part and part != "."
        )
        if not folder:
            if not glob or glob == ("**",):
                return True
        elif matches(tuple(folder.split("/")), glob):
            return True
    return False


class ConventionMaintainer:
    """Keep a written note's folder ``log.md`` / ``index.md`` current (#964)."""

    def __init__(
        self,
        *,
        doc_mgr: DocumentManager,
        okf_migrate: OkfMigrationManager,
        detector: OkfDetector,
        sync_index: Callable[[], object] | None = None,
        write_lock: AbstractContextManager[object] | None = None,
        today: Callable[[], date] = date.today,
        source_dir: Path | None = None,
        capabilities: OkfCapabilities | None = None,
    ) -> None:
        """Hold the collaborators the secondary writes delegate to.

        Args:
            doc_mgr: Reads the current ``log.md`` and issues the secondary
                writes (the shared write path → single-writer index +
                git-commit callback).
            okf_migrate: Supplies :meth:`OkfMigrationManager.generate_index`
                for the ``index.md`` refresh.
            detector: OKF detector; ``active`` is re-probed per write.
            sync_index: Optional preflight hook for existing integrations.
                Its return value is ignored. Vault leaves it unset because
                ``generate_index`` owns the required index refresh (#1464).
            write_lock: The vault's shared (re-entrant) write lock. The
                ``log.md`` read-modify-write is held under it so concurrent
                writes into the same folder cannot lost-update the log;
                :class:`DocumentManager.write` re-enters the same lock safely.
                ``None`` (tests) serialises nothing.
            today: Date provider (injectable for tests); defaults to
                :meth:`datetime.date.today`.
            source_dir: The vault root, used after a delete, rename or folder
                move to find which folders already carry an ``index.md``
                (#1609). ``None`` (tests) treats every folder as having one.
            capabilities: Resolved maintenance authority and folder scope.
                ``None`` preserves the legacy all-folders behavior.
        """
        self._doc_mgr = doc_mgr
        self._okf_migrate = okf_migrate
        self._detector = detector
        self._sync_index = sync_index
        self._write_lock: AbstractContextManager[object] = (
            write_lock if write_lock is not None else nullcontext()
        )
        self._today = today
        self._source_dir = source_dir
        self._capabilities = (
            capabilities or ContentConfig(okf_write=True).okf_capabilities
        )

    def _canonical(self, path: str) -> str:
        """Return the vault-relative identity of *path* after validation's resolution.

        Reuses the shared traversal guard, so ``.``/``..`` segments and symlink
        aliases fold to the same note or folder every write already targets.
        Designation, anchor choice and generated writes decide on this identity,
        never on the caller's spelling. The vault root is ``""``.
        """
        rel = Path(path)
        if self._source_dir is not None:
            rel = resolve_inside(path, self._source_dir)
            rel = rel.relative_to(self._source_dir.resolve())
        return "" if rel == Path() else rel.as_posix()

    def _designated(self, folder: str) -> bool:
        return folder_is_designated(self._canonical(folder), self._capabilities.folders)

    def _is_reserved(self, path: str) -> bool:
        """Whether *path* is a reserved file by its own spelling or its target's."""
        return is_okf_reserved_file(Path(path).as_posix()) or is_okf_reserved_file(
            self._canonical(path)
        )

    def _destination_ok(self, folder: str, name: str) -> bool:
        """Whether *folder*'s generated reserved file *name* lands where it says.

        The resolved destination must be *name* in the folder's own canonical
        identity, so a leaf alias to a concept, another reserved file or
        another folder's file is never written through, existing or not.
        A folder reached through a directory alias still resolves to itself.
        """
        home = self._canonical(folder)
        try:
            return self._canonical(f"{folder}/{name}" if folder else name) == (
                f"{home}/{name}" if home else name
            )
        except InvalidRequestError:
            return False

    def _may_index(self, folder: str) -> bool:
        """Whether *folder*'s ``index.md`` may be generated automatically."""
        return self._designated(folder) and self._destination_ok(folder, "index.md")

    def _may_log(self, folder: str) -> bool:
        """Whether a write into *folder* may append to its ``log.md``."""
        return self._capabilities.log == "writes" and self._destination_ok(
            folder, "log.md"
        )

    def _maintain_index(self, folder: str) -> None:
        """Refresh the nearest indexed level at or above *folder* if allowed."""
        anchor = self.indexed_anchor(folder)
        if self._may_index(anchor):
            self._refresh_index(anchor)

    def maintain(self, path: str, operation: WriteOperation) -> None:
        """Refresh the written note's folder ``log.md`` and its listing.

        Index maintenance reaches a designated ancestor through folders that
        have no index. An undesignated existing index stops that traversal.
        Log maintenance remains limited to the written folder's designation.

        A no-op unless *operation* is a content write (``write`` / ``edit``)
        on an OKF-active vault, and never for a write whose target is itself a
        reserved file (that would recurse and is not a maintenance trigger).
        Never raises — each secondary write is isolated so one failure neither
        blocks the other nor rolls back the primary write.

        Args:
            path: Vault-relative path of the note the primary write landed on.
            operation: The primary write operation.
        """
        if operation not in ("write", "edit"):
            return
        intent = current_okf_intent()
        if intent is not None and intent.suppress:
            # A suppressed write (okf_verify's attestation, a mechanical
            # transform) gets neither provenance stamping nor maintenance.
            return
        if self._is_reserved(path):
            return
        if not self._detector.state().active:
            return
        folder = folder_of(path := self._canonical(path))
        with okf_write_suppressed(), auxiliary_commit_writes():
            if self._designated(folder) and self._may_log(folder):
                self._append_log(folder, path, operation)
            # A note in a folder without an index is listed from the nearest
            # indexed level, which indexes every missing level below (#1647).
            if self._capabilities.index:
                self._maintain_index(folder)

    def refresh_indexes(
        self,
        folders: Iterable[str],
        *,
        trigger_paths: Iterable[str],
        create: Iterable[str] = (),
    ) -> None:
        """Refresh folder ``index.md`` files after a delete, rename or move.

        The index anchor for a folder in *folders* is refreshed only if it
        already has an ``index.md``. A
        folder in *create* is refreshed regardless, as a folder a note was
        written into is (#1609). The same guards as :meth:`maintain` apply:
        nothing on an inactive vault or under a suppressed intent, and nothing
        when any *trigger_paths* entry is a reserved file, whose change never
        triggers its own regeneration (#1414). ``log.md`` is left to the log
        design; only the listings are kept current here. Never raises.

        Args:
            folders: Folders whose existing listing may be stale.
            trigger_paths: The vault-relative paths the change touched.
            create: Folders a note arrived in, refreshed even without an
                ``index.md`` yet.
        """
        if not self._capabilities.index or not self._maintains_after(trigger_paths):
            return
        anchors = (self.indexed_anchor(f) for f in folders)
        wanted = [f for f in anchors if self._may_index(f) and self._has_index(f)]
        with okf_write_suppressed(), auxiliary_commit_writes():
            for folder in dict.fromkeys(
                [*wanted, *(f for f in create if self._may_index(f))]
            ):
                self._refresh_index(self._canonical(folder))

    def subtree_folders(self, root: str) -> list[str]:
        """Return vault-owned non-hidden folders under *root*, vault-relative.

        After a folder move these are the folders whose ``index.md`` moved
        along, still listing the old paths (#1609).

        Args:
            root: Vault-relative folder (``""`` for the vault root).

        Returns:
            *root* first, then its descendants in walk order, omitting git
            submodules and their descendants. Empty when *root* is inside a
            submodule; only *root* when no vault directory is known.
        """
        if self._doc_mgr.in_submodule(root):
            return []
        if self._source_dir is None:
            return [root]
        base = self._source_dir / root if root else self._source_dir
        found = [root]
        for dirpath, dirnames, _files in os.walk(base):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not d.startswith(".")
                and not self._doc_mgr.in_submodule(
                    Path(dirpath, d).relative_to(self._source_dir).as_posix()
                )
            )
            for name in dirnames:
                found.append(
                    Path(dirpath, name).relative_to(self._source_dir).as_posix()
                )
        return found

    def indexed_anchor(self, folder: str) -> str:
        """Choose the listing that owns a folder's generated navigation.

        An existing undesignated index is a boundary. Otherwise start at the
        nearest designated folder, then walk contiguous designated parents to
        an existing index, preserving recursive generation for that chain.

        Args:
            folder: Vault-relative folder (``""`` for the vault root).

        Returns:
            The anchor folder, or ``""`` when no level has an index.
        """
        current = self._canonical(folder)
        while not self._designated(current) and not self._has_index(current):
            if not current:
                return current
            current = folder_of(current)
        while current and self._designated(current) and not self._has_index(current):
            parent = folder_of(current)
            if not self._designated(parent):
                break
            current = parent
        return current

    def _maintains_after(self, trigger_paths: Iterable[str]) -> bool:
        """Whether a structural change should refresh listings at all."""
        intent = current_okf_intent()
        if intent is not None and intent.suppress:
            return False
        if any(self._is_reserved(p) for p in trigger_paths):
            return False
        return self._detector.state().active

    def _has_index(self, folder: str) -> bool:
        """Whether *folder* already carries an ``index.md`` to keep current.

        A refused stat counts as "has one", so the refresh is attempted and
        its own handling logs any failure (#1625).
        """
        if self._source_dir is None:
            return True
        index = (
            self._source_dir / folder / "index.md"
            if folder
            else (self._source_dir / "index.md")
        )
        try:
            return is_regular_file(index)
        except OSError:
            return True

    def _append_log(self, folder: str, path: str, operation: WriteOperation) -> None:
        """Append a dated ``**Update**`` bullet to the folder's ``log.md``.

        Existing frontmatter is retained byte-for-byte during the body edit.
        """
        log_path = f"{folder}/log.md" if folder else "log.md"
        verb = _OPERATION_VERB.get(operation, operation)
        summary = f"**Update**: {verb} `{path}`"
        try:
            # Hold the shared write lock across the read-modify-write so two
            # concurrent writes into the same folder cannot both read the same
            # log and clobber each other's bullet (lost update). write()
            # re-enters this same re-entrant lock.
            with self._write_lock:
                existing = self._doc_mgr.read(log_path)
                body = (
                    strip_frontmatter_block(existing.content)
                    if existing is not None
                    else None
                )
                new_text = append_okf_log_entry(
                    body, date=self._today().isoformat(), summary=summary
                )
                self._doc_mgr.replace_body(
                    log_path,
                    existing.content if existing is not None else None,
                    new_text,
                )
        except Exception:
            logger.warning("okf_convention_log_failed path=%s", log_path, exc_info=True)

    def _refresh_index(self, folder: str) -> None:
        """Regenerate the folder's ``index.md`` from the (drained) listing."""
        index_path = f"{folder}/index.md" if folder else "index.md"
        try:
            if self._sync_index is not None:
                self._sync_index()
            self._okf_migrate.generate_index(folder=folder, designated=self._may_index)
        except Exception:
            logger.warning(
                "okf_convention_index_failed path=%s", index_path, exc_info=True
            )
