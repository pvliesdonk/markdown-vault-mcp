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

The ``index.md`` listing is a projection of the index, so it is also
regenerated after everything else that can change what a folder holds
(#1392): a rename, delete or folder move through the server
(:meth:`ConventionMaintainer.after_rename` and siblings, each a no-op for a
path that is not a note, since a listing names notes and refreshing for an
attachment would invent a listing in a folder that never had one — a folder
move of attachments alone is a no-op for the same reason), and any change
that
arrived from outside — a git pull, the file watcher, an explicit ``reindex``
— through :meth:`ConventionMaintainer.refresh_folders` on the folders the
reindex reports. ``log.md`` is history rather than a projection and is not
touched by those paths.

The maintainer is built only when ``OKF_WRITE`` is enabled (mirroring the
enricher's gating), and it re-checks ``detector.state().active`` on every call
because an OKF declaration can flip mid-session. The secondary writes are done
under :func:`okf_write_suppressed` so the reserved files are not themselves
provenance-stamped or verification-cleared.
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp._okf_write import current_okf_intent, okf_write_suppressed
from markdown_vault_mcp.okf import (
    OKF_INDEX_FILENAME,
    OKF_LOG_TITLE,
    OKF_RESERVED_FILENAMES,
    ReservedFrontmatterPolicy,
    append_okf_log_entry,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractContextManager

    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.okf_migrate import OkfMigrationManager
    from markdown_vault_mcp.okf import OkfDetector
    from markdown_vault_mcp.types import WriteOperation

logger = logging.getLogger(__name__)


def _is_note(path: str) -> bool:
    """Whether *path* is a markdown note, the only thing a listing names."""
    return path.lower().endswith(".md")


#: Human-readable verb per maintained operation, for the ``log.md`` bullet.
_OPERATION_VERB: dict[str, str] = {"write": "wrote", "edit": "edited"}


class ConventionMaintainer:
    """Keep a written note's folder ``log.md`` / ``index.md`` current (#964)."""

    def __init__(
        self,
        *,
        doc_mgr: DocumentManager,
        okf_migrate: OkfMigrationManager,
        detector: OkfDetector,
        sync_index: Callable[[], object],
        write_lock: AbstractContextManager[object] | None = None,
        today: Callable[[], date] = date.today,
        reserved_frontmatter: ReservedFrontmatterPolicy | None = None,
        folder_exists: Callable[[str], bool] = lambda _folder: True,
        list_listing_folders: Callable[[str], Iterable[str]] = lambda _folder: (),
        subtree_has_notes: Callable[[str], bool] = lambda _folder: True,
    ) -> None:
        """Hold the collaborators the secondary writes delegate to.

        Args:
            doc_mgr: Reads the current ``log.md`` and issues the secondary
                writes (the shared write path → single-writer index +
                git-commit callback).
            okf_migrate: Supplies :meth:`OkfMigrationManager.generate_index`
                for the ``index.md`` refresh.
            detector: OKF detector; ``active`` is re-probed per write.
            sync_index: Called before the index refresh to drain the
                single-writer so the just-written note is reflected in the
                FTS-backed listing. Its return value is ignored (best effort).
            write_lock: The vault's shared (re-entrant) write lock. The
                ``log.md`` read-modify-write is held under it so concurrent
                writes into the same folder cannot lost-update the log;
                :class:`DocumentManager.write` re-enters the same lock safely.
                ``None`` (tests) serialises nothing.
            today: Date provider (injectable for tests); defaults to
                :meth:`datetime.date.today`.
            reserved_frontmatter: Frontmatter policy for the maintained
                ``log.md``, so the log this maintainer rewrites on every
                enforced write satisfies the vault's own index gate (#1174).
                Defaults to the no-required-fields policy.
            folder_exists: Whether a vault-relative folder is still on disk;
                a listing is never regenerated into a folder that is gone
                (a rename or move can empty one). ``True`` for everything by
                default (tests).
            list_listing_folders: Vault-relative folders under a given
                subtree that hold an ``index.md``, read from disk — what
                :meth:`after_move` walks to find every listing the move
                carried along. From disk rather than the index because the
                move has already landed there, while the index catches up
                behind a drain that is allowed to time out. Empty by
                default (tests).
        """
        self._doc_mgr = doc_mgr
        self._okf_migrate = okf_migrate
        self._detector = detector
        self._sync_index = sync_index
        self._write_lock: AbstractContextManager[object] = (
            write_lock if write_lock is not None else nullcontext()
        )
        self._today = today
        self._reserved_frontmatter = reserved_frontmatter or ReservedFrontmatterPolicy()
        self._folder_exists = folder_exists
        self._list_listing_folders = list_listing_folders
        self._subtree_has_notes = subtree_has_notes

    def maintain(self, path: str, operation: WriteOperation) -> None:
        """Refresh the written note's folder ``log.md`` and ``index.md``.

        A no-op unless *operation* is a content write (``write`` / ``edit``)
        on an OKF-active vault, and never for a write whose target is itself a
        reserved file (that would recurse and is not a maintenance trigger).
        The listing refresh covers the folder and its ancestors, so a note
        written into a brand-new subfolder shows up in the parent's pointers
        at once (#1392). Never raises — each secondary write is isolated so
        one failure neither blocks the other nor rolls back the primary write.

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
        if Path(path).name in OKF_RESERVED_FILENAMES:
            return
        if not self._detector.state().active:
            return
        folder = self._folder_of(path)
        with okf_write_suppressed():
            self._append_log(folder, path, operation)
        self.refresh_folders([folder])

    def after_delete(self, path: str) -> None:
        """Refresh the listing a deleted note was in (#1392).

        A listing names notes, so an attachment leaving a folder changes
        nothing in it — and refreshing anyway would *create* a listing in a
        folder of attachments that never had one and would never have got
        one.
        """
        if not _is_note(path):
            return
        self.refresh_folders([self._folder_of(path)])

    def after_rename(self, old_path: str, new_path: str) -> None:
        """Refresh the listings a rename left and entered (#1392).

        The destination folder is skipped when the rename *made* the folder's
        listing: regenerating it would overwrite the note just moved there. A
        write whose target is a reserved file is skipped for the same reason
        (:meth:`maintain`), and a rename can create one just as directly. A
        rename to ``log.md`` is not spared — the listing must still lose the
        note's old entry, and generating it cannot touch the log.
        """
        if not _is_note(old_path) and not _is_note(new_path):
            # Attachments only: no listing anywhere names them (see
            # :meth:`after_delete`).
            return
        folders = [self._folder_of(old_path), self._folder_of(new_path)]
        # Only the listing itself: a note renamed to ``log.md`` leaves the
        # folder's ``index.md`` naming a path that no longer exists, and
        # regenerating it cannot touch the log.
        if Path(new_path).name == OKF_INDEX_FILENAME:
            # Excluded, not merely left unadded: a same-folder rename reaches
            # the destination through the *source* too, and refreshing it
            # would overwrite the note the rename just made.
            destination = self._folder_of(new_path)
            folders = [f for f in folders if f != destination]
            logger.info(
                "okf_convention_rename_to_reserved path=%s: leaving it as written",
                new_path,
            )
        self.refresh_folders(folders)

    def after_move(self, old_dir: str, new_dir: str) -> None:
        """Refresh every listing a folder move touched (#1392).

        The old and new parents lose and gain a subfolder pointer, and every
        listing carried along under *new_dir* links its notes by
        root-absolute path, so each of those is regenerated too.

        A move of attachments alone is a no-op: nothing there warrants a
        listing, and refreshing would invent one for the destination and
        every folder above it.
        """
        if not self._active():
            return
        carried = list(self._list_listing_folders(new_dir))
        if not carried and not self._subtree_has_notes(new_dir):
            # Attachments only: no listing here or above names them, and
            # refreshing would invent listings for the whole ancestor chain
            # (:meth:`after_delete` gives the rule).
            logger.debug("okf_convention_move_no_notes dir=%s", new_dir)
            return
        # No drain here: the enumeration below reads disk, and
        # ``refresh_folders`` drains before it generates. Draining twice cost
        # up to two timeouts for one move.
        folders = {self._folder_of(old_dir), self._folder_of(new_dir), new_dir}
        folders.update(carried)
        self.refresh_folders(folders)

    def refresh_folders(self, folders: Iterable[str]) -> None:
        """Regenerate the ``index.md`` of each folder and of its ancestors (#1392).

        Ancestors are included because a folder appearing or emptying changes
        its parent's subfolder pointers. Each folder is generated once, root
        first; one that is no longer on disk is skipped rather than recreated.
        The single-writer index is drained first so the listings reflect the
        change that prompted the refresh. A no-op on an inactive vault; never
        raises — a failed folder is logged and the rest still refresh.

        Args:
            folders: Vault-relative folders (``""`` for the root).
        """
        if not self._active():
            return
        targets: set[str] = set()
        for folder in folders:
            current = folder
            while True:
                targets.add(current)
                if not current:
                    break
                current = self._folder_of(current)
        self._drain()
        # The existence check and the generation are one decision: a
        # concurrent move between them would have the write recreate the
        # folder it just emptied, leaving a listing nothing maintains
        # (``DocumentManager.write`` creates missing parents). The lock is
        # the vault's shared re-entrant one, which the writes below re-enter,
        # and it is taken after the drain, never across it.
        with okf_write_suppressed(), self._write_lock:
            for folder in sorted(targets):
                if folder and not self._folder_exists(folder):
                    continue
                self._refresh_index(folder)

    def _active(self) -> bool:
        """Whether the vault is OKF-active right now (re-probed per call)."""
        return bool(self._detector.state().active)

    def _drain(self) -> None:
        """Drain the single-writer index, best effort."""
        try:
            self._sync_index()
        except Exception:
            logger.debug("okf_convention_drain_failed", exc_info=True)

    @staticmethod
    def _folder_of(path: str) -> str:
        """Return the vault-relative folder of *path* (``""`` for the root)."""
        parent = str(Path(path).parent)
        return "" if parent == "." else parent

    def _append_log(self, folder: str, path: str, operation: WriteOperation) -> None:
        """Append a dated ``**Update**`` bullet to the folder's ``log.md``.

        The read-modify-write splits frontmatter from body: ``read()`` hands
        back the body alone, so the log's frontmatter has to be carried over
        explicitly or the rewrite would strip it — including frontmatter an
        operator seeded by hand to satisfy ``required_frontmatter`` (#1174).
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
                text = existing.content if existing is not None else None
                new_text = append_okf_log_entry(
                    text, date=self._today().isoformat(), summary=summary
                )
                self._doc_mgr.write(
                    log_path,
                    new_text,
                    frontmatter=self._reserved_frontmatter.build(
                        existing.frontmatter if existing is not None else None,
                        title=OKF_LOG_TITLE,
                    ),
                    allow_overwrite=True,
                )
        except Exception:
            logger.warning("okf_convention_log_failed path=%s", log_path, exc_info=True)

    def _refresh_index(self, folder: str) -> None:
        """Regenerate one folder's ``index.md`` from the (drained) listing."""
        index_path = f"{folder}/index.md" if folder else "index.md"
        try:
            self._okf_migrate.generate_index(folder=folder)
        except Exception:
            logger.warning(
                "okf_convention_index_failed path=%s", index_path, exc_info=True
            )
