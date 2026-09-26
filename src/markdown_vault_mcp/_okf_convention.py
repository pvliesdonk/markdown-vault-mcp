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

The maintainer is built only when ``OKF_WRITE`` is enabled (mirroring the
enricher's gating), and it re-checks ``detector.state().active`` on every call
because an OKF declaration can flip mid-session. The secondary writes are done
under :func:`okf_write_suppressed` so the reserved files are not themselves
provenance-stamped or verification-cleared.
"""

from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp._okf_write import current_okf_intent, okf_write_suppressed
from markdown_vault_mcp.okf import (
    OKF_LOG_TITLE,
    OKF_RESERVED_FILENAMES,
    ReservedFrontmatterPolicy,
    append_okf_log_entry,
)
from markdown_vault_mcp.scanner import strip_frontmatter_block
from markdown_vault_mcp.utils.fs import is_regular_file

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractContextManager

    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.okf_migrate import OkfMigrationManager
    from markdown_vault_mcp.okf import OkfDetector
    from markdown_vault_mcp.types import WriteOperation

logger = logging.getLogger(__name__)

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
        sync_index: Callable[[], object] | None = None,
        write_lock: AbstractContextManager[object] | None = None,
        today: Callable[[], date] = date.today,
        reserved_frontmatter: ReservedFrontmatterPolicy | None = None,
        source_dir: Path | None = None,
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
            reserved_frontmatter: Frontmatter policy for the maintained
                ``log.md``, so the log this maintainer rewrites on every
                enforced write satisfies the vault's own index gate (#1174).
                Defaults to the no-required-fields policy.
            source_dir: The vault root, used after a delete, rename or folder
                move to find which folders already carry an ``index.md``
                (#1609). ``None`` (tests) treats every folder as having one.
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
        self._source_dir = source_dir

    def maintain(self, path: str, operation: WriteOperation) -> None:
        """Refresh the written note's folder ``log.md`` and ``index.md``.

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
        if Path(path).name in OKF_RESERVED_FILENAMES:
            return
        if not self._detector.state().active:
            return
        folder = self._folder_of(path)
        with okf_write_suppressed():
            self._append_log(folder, path, operation)
            self._refresh_index(folder)

    def refresh_indexes(
        self,
        folders: Iterable[str],
        *,
        trigger_paths: Iterable[str],
        create: Iterable[str] = (),
    ) -> None:
        """Refresh folder ``index.md`` files after a delete, rename or move.

        A folder in *folders* is refreshed only if it already has an
        ``index.md``: the change can only have made that listing stale. A
        folder in *create* is refreshed regardless, as a folder a note was
        written into is (#1609). The same guards as :meth:`maintain` apply:
        nothing on an inactive vault or under a suppressed intent, and nothing
        when every *trigger_paths* entry is a reserved file, whose change never
        triggers its own regeneration (#1414). ``log.md`` is left to the log
        design; only the listings are kept current here. Never raises.

        Args:
            folders: Folders whose existing listing may be stale.
            trigger_paths: The vault-relative paths the change touched.
            create: Folders a note arrived in, refreshed even without an
                ``index.md`` yet.
        """
        if not self._maintains_after(trigger_paths):
            return
        wanted = [f for f in folders if self._has_index(f)]
        with okf_write_suppressed():
            for folder in dict.fromkeys([*wanted, *create]):
                self._refresh_index(folder)

    def subtree_folders(self, root: str) -> list[str]:
        """Return *root* and every non-hidden folder below it, vault-relative.

        After a folder move these are the folders whose ``index.md`` moved
        along, still listing the old paths (#1609).
        """
        if self._source_dir is None:
            return [root]
        base = self._source_dir / root if root else self._source_dir
        found = [root]
        for dirpath, dirnames, _files in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in dirnames:
                found.append(
                    Path(dirpath, name).relative_to(self._source_dir).as_posix()
                )
        return found

    def _maintains_after(self, trigger_paths: Iterable[str]) -> bool:
        """Whether a structural change should refresh listings at all."""
        intent = current_okf_intent()
        if intent is not None and intent.suppress:
            return False
        if all(Path(p).name in OKF_RESERVED_FILENAMES for p in trigger_paths):
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

    @staticmethod
    def _folder_of(path: str) -> str:
        """Return the vault-relative folder of *path* (``""`` for the root)."""
        parent = str(Path(path).parent)
        return "" if parent == "." else parent

    def _append_log(self, folder: str, path: str, operation: WriteOperation) -> None:
        """Append a dated ``**Update**`` bullet to the folder's ``log.md``.

        The read-modify-write splits frontmatter from body itself. ``read()``
        hands back the *whole file*, block included, while ``write()`` puts a
        ``frontmatter=`` mapping above the body it is given — so the block has
        to come off the text before the append and be carried across as the
        mapping, or the log grows one more identical block per write (#1391).
        Carrying it across at all is #1174: frontmatter an operator seeded by
        hand to satisfy ``required_frontmatter`` must survive the rewrite, or
        the vault's own change history stops being indexed.
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
        """Regenerate the folder's ``index.md`` from the (drained) listing."""
        index_path = f"{folder}/index.md" if folder else "index.md"
        try:
            if self._sync_index is not None:
                self._sync_index()
            self._okf_migrate.generate_index(folder=folder)
        except Exception:
            logger.warning(
                "okf_convention_index_failed path=%s", index_path, exc_info=True
            )
