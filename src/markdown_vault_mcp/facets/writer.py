"""Writer facet: the document-mutation surface (#604).

A thin view over :class:`~markdown_vault_mcp.managers.document.DocumentManager`
exposing the vault's write / edit / append / delete / rename / attachment
operations.
Part of the ``vault.py`` facade decomposition (#576); reached via the
``Vault.writer`` accessor.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, TypeVar

from markdown_vault_mcp.okf import OKF_RESERVED_FILENAMES
from markdown_vault_mcp.utils import folder_of, is_note

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from markdown_vault_mcp._okf_convention import ConventionMaintainer
    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.managers.link_report import UnresolvedLinkReporter
    from markdown_vault_mcp.managers.okf_migrate import OkfMigrationManager
    from markdown_vault_mcp.okf import (
        OkfConvertResult,
        OkfIndexResult,
        OkfLogResult,
    )
    from markdown_vault_mcp.types import (
        DeleteResult,
        EditResult,
        MoveFolderResult,
        RenameResult,
        WriteResult,
    )

_R = TypeVar("_R", "WriteResult", "EditResult")


def _is_reserved(path: str) -> bool:
    """Whether *path* names a reserved OKF file (``index.md`` / ``log.md``)."""
    return PurePosixPath(path).name in OKF_RESERVED_FILENAMES


class WriterFacet:
    """Document-mutation operations, backed by :class:`DocumentManager`."""

    def __init__(
        self,
        doc_mgr: DocumentManager,
        *,
        okf_migrate: OkfMigrationManager | None = None,
        convention_maintainer: ConventionMaintainer | None = None,
        previous_revision: Callable[[str], str | None] | None = None,
        link_reporter: UnresolvedLinkReporter | None = None,
    ) -> None:
        """Hold the managers the write operations delegate to.

        Args:
            doc_mgr: The shared :class:`DocumentManager` owned by the root.
            okf_migrate: The OKF migration manager (#963); ``None`` leaves
                the ``okf_*`` migration methods unavailable.
            convention_maintainer: The OKF enforced-write convention
                maintainer (#964); ``None`` (the default, and whenever
                ``OKF_WRITE`` is off) disables ``log.md`` / ``index.md``
                upkeep on ``write`` / ``edit``.
            previous_revision: Resolves the revision holding a note's current
                content, for the overwrite breadcrumb (#1137).  ``None`` (the
                default, and on a vault without git) omits the breadcrumb.
                Wired here rather than inside
                :class:`~markdown_vault_mcp.managers.document.DocumentManager`
                deliberately: that class is also the server's own maintenance
                write path (OKF link conversion rewrites thousands of notes in
                one call), and a git probe per file there would spend several
                subprocesses each on a result nobody reads.
            link_reporter: Answers ``report_unresolved_links=True`` on
                :meth:`write`, :meth:`edit` and :meth:`append` (#1725).
                ``None`` (the default) makes such a call raise
                :exc:`RuntimeError`.
        """
        self._doc_mgr = doc_mgr
        self._okf_migrate = okf_migrate
        self._convention_maintainer = convention_maintainer
        self._previous_revision = previous_revision
        self._link_reporter = link_reporter

    def _with_link_report(
        self, path: str, report: bool, mutate: Callable[[], _R]
    ) -> _R:
        """Run *mutate*, then set the links it added that do not resolve.

        The note's links are read before and after *mutate* under one hold of
        the write lock, so neither snapshot can see another writer's change.
        The index refresh the report needs runs after the lock is released.
        """
        if not report:
            return mutate()
        if self._link_reporter is None:
            raise RuntimeError("Unresolved-link reporter is not configured")
        reporter = self._link_reporter
        # Refuse a read-only vault before reading the note, as the write would.
        self._doc_mgr.ensure_writable()
        with self._doc_mgr.write_scope():
            before = reporter.note_links(path)
            result = mutate()
            after = reporter.note_links(path)
        result.unresolved_links = reporter.introduced(path, before, after)
        return result

    def _migrate(self) -> OkfMigrationManager:
        """Return the migration manager or raise if it was not wired."""
        if self._okf_migrate is None:
            raise RuntimeError("OKF migration manager is not configured")
        return self._okf_migrate

    def okf_convert_links(self, *, folder: str | None = None) -> OkfConvertResult:
        """Rewrite resolvable wikilinks as root-absolute markdown links (#963).

        Args:
            folder: Restrict to this folder subtree; ``None`` covers the
                whole vault.

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfConvertResult`.
        """
        return self._migrate().convert_links(folder=folder)

    def okf_generate_index(self, *, folder: str = "") -> OkfIndexResult:
        """Generate a reserved ``index.md`` listing from the TOC (#963).

        Args:
            folder: Vault-relative folder (``""`` for the bundle root).

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfIndexResult`.
        """
        return self._migrate().generate_index(folder=folder)

    def okf_seed_log(self, *, folder: str = "") -> OkfLogResult:
        """Seed a reserved ``log.md`` from git history (#963).

        Args:
            folder: Vault-relative folder (``""`` for the bundle root).

        Returns:
            An :class:`~markdown_vault_mcp.okf.OkfLogResult`.
        """
        return self._migrate().seed_log(folder=folder)

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
        *,
        report_unresolved_links: bool = False,
    ) -> WriteResult:
        """Create or overwrite a document.

        Creates intermediate directories as needed.  If *frontmatter* is
        provided, it is serialised as a YAML header at the top of the file.

        Args:
            path: Relative document path (e.g. ``"notes/topic.md"``).
            content: Markdown body (excluding frontmatter).
            frontmatter: Optional frontmatter dict serialised as a YAML header.
            if_match: Optional etag from a previous :meth:`ReaderFacet.read` call.  When
                provided, the write is only performed if the current file hash
                matches this value, preventing overwrites of concurrent
                modifications.  Pass ``None`` (default) to skip the check.
            report_unresolved_links: When ``True``, wait for the index to take
                in the write and set the result's ``unresolved_links`` (#1725).

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.  On an overwrite of
            a committed note in a git-backed vault, its ``previous_revision``
            names the commit holding the replaced content, readable back with
            :meth:`~markdown_vault_mcp.facets.reader.ReaderFacet.read_revision`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash, or *if_match* is supplied
                for a file that does not yet exist.
            DocumentExistsError: If write protection is enabled and *path*
                already exists while no *if_match* is supplied.
            InvalidRequestError: If *path* escapes the source directory.
        """

        # Read before writing: the auto-commit is asynchronous, so afterwards
        # the note's newest commit may already be this write's own.  Both steps
        # hold the vault's (re-entrant) write lock, so no other writer can
        # change which note occupies the path in between — otherwise the
        # breadcrumb could name a commit belonging to a note this write did
        # not replace.
        def mutate() -> WriteResult:
            with self._doc_mgr.write_scope():
                breadcrumb = self._breadcrumb_for(path)
                result = self._doc_mgr.write(
                    path, content, frontmatter=frontmatter, if_match=if_match
                )
                if not result.created:
                    result.previous_revision = breadcrumb
            return result

        result = self._with_link_report(path, report_unresolved_links, mutate)
        if self._convention_maintainer is not None:
            self._convention_maintainer.maintain(path, "write")
        return result

    def _breadcrumb_for(self, path: str) -> str | None:
        """Return the revision holding *path*'s current content, if one does.

        Probed before the overwrite; the resolver skips a path that is not on
        disk, so a create costs no git calls and the result is discarded
        anyway when the write turns out to have created the note.

        The read-only check comes first so a vault that is going to refuse the
        write refuses it without spending git subprocesses on a breadcrumb for
        a write that will not happen.  It raises the same
        :exc:`~markdown_vault_mcp.exceptions.ReadOnlyError` the write itself
        would, just sooner.
        """
        if self._previous_revision is None:
            return None
        self._doc_mgr.ensure_writable()
        return self._previous_revision(path)

    def edit(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str = "",
        if_match: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        *,
        report_unresolved_links: bool = False,
    ) -> EditResult:
        """Patch a section of a document.

        Replaces the first occurrence of *old_text* with *new_text*, or
        replaces the line range [*line_start*, *line_end*] when line numbers
        are given instead.

        Args:
            path: Relative document path.
            old_text: Exact text to replace (must occur exactly once).
                Mutually exclusive with *line_start* / *line_end*.
            new_text: Replacement text (may be empty to delete *old_text*).
            if_match: Optional etag for optimistic concurrency; see
                :meth:`WriterFacet.write`.
            line_start: 1-based start line for line-range mode.
            line_end: 1-based end line (inclusive) for line-range mode.
            report_unresolved_links: When ``True``, wait for the index to take
                in the edit and set the result's ``unresolved_links`` (#1725).

        Returns:
            :class:`~markdown_vault_mcp.types.EditResult`.

        Raises:
            EditConflictError: If *old_text* is not found or appears more than
                once.
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            DocumentNotFoundError: If the file does not exist.
            InvalidRequestError: If *path* escapes the source directory.
        """
        result = self._with_link_report(
            path,
            report_unresolved_links,
            lambda: self._doc_mgr.edit(
                path,
                old_text=old_text,
                new_text=new_text,
                if_match=if_match,
                line_start=line_start,
                line_end=line_end,
            ),
        )
        if self._convention_maintainer is not None:
            self._convention_maintainer.maintain(path, "edit")
        return result

    def append(
        self,
        path: str,
        content: str,
        if_match: str | None = None,
        *,
        create_if_missing: bool = False,
        report_unresolved_links: bool = False,
    ) -> WriteResult:
        """Append text to the end of a document without reading it first (#980).

        A newline is inserted between the existing content and *content*
        when the file does not already end with one.

        Args:
            path: Relative document path.
            content: Text to append (must be non-empty).
            if_match: Optional etag for optimistic concurrency; see
                :meth:`WriterFacet.write`.
            create_if_missing: When ``True``, create a missing document with
                *content* as its body instead of raising.
            report_unresolved_links: When ``True``, wait for the index to take
                in the append and set the result's ``unresolved_links``
                (#1725).

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If the file does not exist and
                *create_if_missing* is ``False``.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            InvalidRequestError: If *content* is empty or *path* escapes the
                source directory.
        """
        result = self._with_link_report(
            path,
            report_unresolved_links,
            lambda: self._doc_mgr.append(
                path,
                content,
                if_match=if_match,
                create_if_missing=create_if_missing,
            ),
        )
        if self._convention_maintainer is not None:
            self._convention_maintainer.maintain(path, "edit")
        return result

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment.

        Removes the file from disk and purges its entries from the FTS and
        vector indices.

        Args:
            path: Relative path of the document or attachment to remove.
            if_match: Optional etag for optimistic concurrency; see
                :meth:`WriterFacet.write`.

        Returns:
            :class:`~markdown_vault_mcp.types.DeleteResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            DocumentNotFoundError: If *path* does not exist.
        """
        result = self._doc_mgr.delete(path, if_match=if_match)
        if self._convention_maintainer is not None and is_note(path):
            # The folder's listing still names the deleted note (#1609).
            self._convention_maintainer.refresh_indexes(
                [folder_of(path)], trigger_paths=[path]
            )
        return result

    def rename(
        self,
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        *,
        update_links: bool = False,
    ) -> RenameResult:
        """Rename or move a document or attachment.

        Moves the file on disk and updates the FTS / vector indices.  When
        *update_links* is ``True``, all wikilinks and markdown links in other
        documents that pointed to *old_path* are rewritten to *new_path*.
        For an attachment the flag does not apply (its references are not
        tracked as links) and the result's ``hint`` says so.

        Args:
            old_path: Current relative path of the document or attachment.
            new_path: Desired relative path after the move.
            if_match: Optional etag for optimistic concurrency; see
                :meth:`WriterFacet.write`.
            update_links: When ``True``, rewrite internal links across the
                vault to reflect the new path.  Defaults to ``False``.

        Returns:
            :class:`~markdown_vault_mcp.types.RenameResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            DocumentNotFoundError: If *old_path* does not exist.
            DocumentExistsError: If *new_path* already exists.
            InvalidRequestError: If *old_path* or *new_path* escapes the source
                directory.
        """
        result = self._doc_mgr.rename(
            old_path,
            new_path,
            if_match=if_match,
            update_links=update_links,
        )
        if (
            self._convention_maintainer is not None
            and is_note(new_path)
            and not _is_reserved(old_path)
            and not _is_reserved(new_path)
        ):
            # The old folder still lists the note; the new one does not yet
            # (#1609). Regenerating the nearest indexed level at or above the
            # new folder lists it there and indexes every missing level below
            # (#1647). A rename to or from a reserved name triggers nothing:
            # regenerating would overwrite the file just placed, and a
            # reserved-file change never triggers itself (#1414).
            maintainer = self._convention_maintainer
            maintainer.refresh_indexes(
                [folder_of(old_path)],
                trigger_paths=[old_path, new_path],
                create=[maintainer.indexed_anchor(folder_of(new_path))],
            )
        return result

    def move_folder(self, old_dir: str, new_dir: str) -> MoveFolderResult:
        """Move a folder subtree to a new prefix, rewriting links vault-wide.

        Moves every file under *old_dir* (notes, attachments, and other
        files) to the matching path under *new_dir* and rewrites all links
        across the vault that point into the moved subtree, including links
        between documents inside it.

        The move is atomic at the gate (a destination-file collision aborts
        before anything moves); link rewrites are best-effort.

        Args:
            old_dir: Relative source folder prefix (e.g. ``"drafts"``).
            new_dir: Relative target folder prefix (e.g. ``"archive/2026"``).

        Returns:
            :class:`~markdown_vault_mcp.types.MoveFolderResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If *old_dir* is missing, not a directory,
                or empty.
            DocumentExistsError: If any destination file already exists.
            InvalidRequestError: If either path escapes the vault or the two
                paths are nested.
            OSError: If the OS raises during the move phase (e.g. a permission
                error or full disk). The collision gate prevents pre-existing
                destination clashes, but a mid-move OS error leaves the subtree
                partially moved with the index unchanged; reindex recovers.
        """
        result = self._doc_mgr.move_folder(old_dir, new_dir)
        if self._convention_maintainer is not None:
            # The moved index.md files still list the old paths, and the old
            # parent still points at a subfolder that is gone (#1609). The
            # nearest indexed level above the destination gets it listed, with
            # every missing level between indexed (#1647).
            maintainer = self._convention_maintainer
            new_root = new_dir.strip("/")
            maintainer.refresh_indexes(
                [
                    *maintainer.subtree_folders(new_root),
                    folder_of(old_dir.strip("/")),
                ],
                trigger_paths=[old_dir, new_dir],
                create=[maintainer.indexed_anchor(folder_of(new_root))],
            )
        return result

    def write_attachment(
        self,
        path: str,
        content: bytes,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment.

        Delegates to :meth:`DocumentManager.write_attachment`.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash, or *if_match* is supplied
                for a file that does not yet exist.
            DocumentExistsError: If write protection is enabled and *path*
                already exists while no *if_match* is supplied.
            InvalidRequestError: If the path escapes the source directory or
                has an extension not in the allowlist.
        """
        return self._doc_mgr.write_attachment(path, content, if_match=if_match)
