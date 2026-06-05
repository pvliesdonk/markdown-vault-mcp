"""Writer facet: the document-mutation surface (#604).

A thin view over :class:`~markdown_vault_mcp.managers.document.DocumentManager`
exposing the vault's write / edit / delete / rename / attachment operations.
Part of the ``vault.py`` facade decomposition (#576); reached via the
``Vault.writer`` accessor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    from markdown_vault_mcp.managers.document import DocumentManager
    from markdown_vault_mcp.types import (
        DeleteResult,
        EditResult,
        RenameResult,
        WriteResult,
    )


class WriterFacet:
    """Document-mutation operations, backed by :class:`DocumentManager`."""

    def __init__(self, doc_mgr: DocumentManager) -> None:
        """Hold the document manager the write operations delegate to.

        Args:
            doc_mgr: The shared :class:`DocumentManager` owned by the root.
        """
        self._doc_mgr = doc_mgr

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
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

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash, or *if_match* is supplied
                for a file that does not yet exist.
            ValueError: If *path* escapes the source directory.
        """
        return self._doc_mgr.write(
            path, content, frontmatter=frontmatter, if_match=if_match
        )

    def edit(
        self,
        path: str,
        old_text: str | None = None,
        new_text: str = "",
        if_match: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
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

        Returns:
            :class:`~markdown_vault_mcp.types.EditResult`.

        Raises:
            EditConflictError: If *old_text* is not found or appears more than
                once.
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match.
            DocumentNotFoundError: If the file does not exist.
            ValueError: If *path* escapes the source directory.
        """
        return self._doc_mgr.edit(
            path,
            old_text=old_text,
            new_text=new_text,
            if_match=if_match,
            line_start=line_start,
            line_end=line_end,
        )

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
        return self._doc_mgr.delete(path, if_match=if_match)

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
            ValueError: If *old_path* or *new_path* escapes the source
                directory.
        """
        return self._doc_mgr.rename(
            old_path,
            new_path,
            if_match=if_match,
            update_links=update_links,
        )

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
            ValueError: If the path escapes the source directory or has an
                extension not in the allowlist.
        """
        return self._doc_mgr.write_attachment(path, content, if_match=if_match)
