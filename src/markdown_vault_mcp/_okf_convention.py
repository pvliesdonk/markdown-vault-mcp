"""OKF enforced-write convention maintenance (#964, design §6 phase 5b).

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
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp._okf_write import current_okf_intent, okf_write_suppressed
from markdown_vault_mcp.okf import OKF_RESERVED_FILENAMES, append_okf_log_entry

if TYPE_CHECKING:
    from collections.abc import Callable
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
        sync_index: Callable[[], object],
        write_lock: AbstractContextManager[object] | None = None,
        today: Callable[[], date] = date.today,
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
        """
        self._doc_mgr = doc_mgr
        self._okf_migrate = okf_migrate
        self._detector = detector
        self._sync_index = sync_index
        self._write_lock: AbstractContextManager[object] = (
            write_lock if write_lock is not None else nullcontext()
        )
        self._today = today

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

    @staticmethod
    def _folder_of(path: str) -> str:
        """Return the vault-relative folder of *path* (``""`` for the root)."""
        parent = str(Path(path).parent)
        return "" if parent == "." else parent

    def _append_log(self, folder: str, path: str, operation: WriteOperation) -> None:
        """Append a dated ``**Update**`` bullet to the folder's ``log.md``."""
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
                self._doc_mgr.write(log_path, new_text)
        except Exception:
            logger.warning("okf_convention_log_failed path=%s", log_path, exc_info=True)

    def _refresh_index(self, folder: str) -> None:
        """Regenerate the folder's ``index.md`` from the (drained) listing."""
        index_path = f"{folder}/index.md" if folder else "index.md"
        try:
            self._sync_index()
            self._okf_migrate.generate_index(folder=folder)
        except Exception:
            logger.warning(
                "okf_convention_index_failed path=%s", index_path, exc_info=True
            )
