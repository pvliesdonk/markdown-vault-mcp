"""Artifact (non-``.md`` attachment) storage (#1235).

A collaborator composed into
:class:`~markdown_vault_mcp.managers.document.DocumentManager`, following the
#893 precedent set by :class:`~markdown_vault_mcp.git.bootstrap.RepoBootstrap`
and :class:`~markdown_vault_mcp.git.push_scheduler.PushScheduler`: it shares
the manager's write lock and write notifier — **the same objects**, never
copies — and introduces no lock of its own. A private lock here would stop
artifact writes serialising against note writes, and would leave
:meth:`Vault.pause_writes` unable to hold artifacts still during a git rebase.

Deliberately a plain class, not a Protocol. The seams in
:mod:`markdown_vault_mcp.interfaces` earned theirs because several consumers
wanted different subsets and a backend swap was a documented need; this has one
implementation and one consumer. The one caller that might motivate an
interface — ``VaultTransferSink`` — cannot hold a vault-owned object at all,
since it validates at link-mint time from a ``ProjectConfig`` before any vault
is resolved; it is served by the pure predicates in
:mod:`markdown_vault_mcp.utils.content_kind` instead.

:meth:`unlink` and :meth:`move` return paths and fire nothing. In ``delete``
and ``rename`` the manager fires one callback after the branch closes, for
both notes and artifacts alike; owning that here would either double-fire or
move it outside the lock.
"""

from __future__ import annotations

import base64
import dataclasses
import mimetypes
import shutil
from typing import TYPE_CHECKING

from markdown_vault_mcp.exceptions import (
    DocumentExistsError,
    DocumentNotFoundError,
    ReadOnlyError,
)
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.managers._write_kernel import atomic_write, check_if_match
from markdown_vault_mcp.types import AttachmentContent, WriteResult
from markdown_vault_mcp.utils import (
    artifact_suffix,
    effective_attachment_extensions,
    is_allowed_artifact_suffix,
    is_note,
    resolve_inside,
)

if TYPE_CHECKING:
    import threading
    from collections.abc import Sequence
    from pathlib import Path

    from markdown_vault_mcp.managers._write_notifier import WriteNotifier

__all__ = ["ArtifactPolicy", "ArtifactStore"]


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactPolicy:
    """Construction-time artifact policy, mirroring the manager's own.

    All three are immutable for the manager's lifetime — ``pause_writes``
    suspends writing through the lock, not by flipping ``read_only`` — so the
    store may safely hold its own copies rather than reading back through the
    manager.

    Attributes:
        attachment_extensions: Configured allowlist, or ``None`` for the
            default set.
        read_only: When ``True`` every write is refused.
        write_protect_existing: When ``True`` a blind overwrite of an existing
            file is refused unless the caller proves a prior read.
    """

    attachment_extensions: Sequence[str] | None = None
    read_only: bool = True
    write_protect_existing: bool = False


class ArtifactStore:
    """Validate, read, write, unlink and move non-``.md`` artifacts.

    Args:
        source_dir: Absolute path to the vault root directory.
        write_lock: The manager's shared re-entrant write lock — the same
            object, so artifact and note writes serialise against each other.
        notifier: The manager's shared write notifier.
        policy: Construction-time artifact policy.
    """

    def __init__(
        self,
        source_dir: Path,
        *,
        write_lock: threading.RLock,
        notifier: WriteNotifier,
        policy: ArtifactPolicy,
    ) -> None:
        self._source_dir = source_dir
        self._file_write_lock = write_lock
        self._notifier = notifier
        self._policy = policy

    def extensions(self) -> frozenset[str]:
        """Return the effective allowlist of artifact extensions."""
        return effective_attachment_extensions(self._policy.attachment_extensions)

    def _check_writable(self) -> None:
        """Raise when the vault is read-only.

        Raises:
            ReadOnlyError: If the vault was constructed read-only.
        """
        if self._policy.read_only:
            raise ReadOnlyError(
                "Vault is read-only; write operations are not permitted."
            )

    def _check_no_clobber(
        self, abs_path: Path, path: str, if_match: str | None
    ) -> None:
        """Reject a blind overwrite when write protection is enabled.

        Args:
            abs_path: Absolute path of the target file.
            path: Vault-relative path, used in the error.
            if_match: Etag supplied by the caller, or ``None``.

        Raises:
            DocumentExistsError: If write protection is enabled, *if_match* is
                ``None``, and *abs_path* already exists.
        """
        if not self._policy.write_protect_existing or if_match is not None:
            return
        if abs_path.is_file():
            raise DocumentExistsError(
                f"{path} exists; overwriting requires proof of read: call "
                "'read', then retry with if_match=<etag> — or use 'edit' for "
                "targeted changes, or 'append' to add to the end. Do not "
                "delete and recreate: that destroys the note first and "
                "proves nothing. (Write protection is enabled by the "
                "operator: MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING.)"
            )

    def validate_path(self, path: str) -> Path:
        """Resolve and validate a non-``.md`` artifact path.

        The extension is taken from the caller's spelling, before the
        traversal guard resolves it — a symlink is judged by its name here.

        Args:
            path: Relative artifact path.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path escapes the source directory, ends with
                ``.md``, or has an extension not in the allowlist.
        """
        if is_note(path):
            raise ValueError(
                f"Path ends with '.md' — use the note read/write methods "
                f"instead: {path}"
            )
        exts = self.extensions()
        suffix = artifact_suffix(path)
        if not is_allowed_artifact_suffix(suffix, exts):
            allowed_str = ", ".join(f".{e}" for e in sorted(exts))
            raise ValueError(
                f"Extension '.{suffix}' is not in the attachment allowlist. "
                f"Allowed: {allowed_str}. "
                "Set MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS=* to allow "
                "all non-.md files."
            )
        return resolve_inside(path, self._source_dir)

    def size(self, path: str) -> int:
        """Return the on-disk byte size of an artifact without reading it.

        Kept separate from :meth:`read` so the MCP layer can enforce its size
        cap by ``stat`` before any bytes are loaded into memory.

        Args:
            path: Relative artifact path.

        Returns:
            The file size in bytes.

        Raises:
            ValueError: If the path is invalid or the file does not exist.
        """
        abs_path = self.validate_path(path)
        try:
            if not abs_path.is_file():
                raise ValueError(f"Attachment not found: {path}")
            return abs_path.stat().st_size
        except OSError as exc:
            raise ValueError(f"Attachment not found: {path}") from exc

    def read(self, path: str) -> AttachmentContent:
        """Read an artifact's bytes, base64-encoded, with its metadata.

        Args:
            path: Relative artifact path.

        Returns:
            The artifact content and metadata.

        Raises:
            ValueError: If the path is invalid or the file does not exist.
        """
        abs_path = self.validate_path(path)
        if not abs_path.is_file():
            raise ValueError(f"Attachment not found: {path}")
        stat = abs_path.stat()
        mime_type, _ = mimetypes.guess_type(path)
        raw = abs_path.read_bytes()
        return AttachmentContent(
            path=path,
            mime_type=mime_type,
            size_bytes=stat.st_size,
            content_base64=base64.b64encode(raw).decode("ascii"),
            modified_at=stat.st_mtime,
            etag=compute_etag(raw),
        )

    def write(
        self, path: str, content: bytes, if_match: str | None = None
    ) -> WriteResult:
        """Write raw bytes to an artifact path, creating parents as needed.

        Args:
            path: Relative artifact path.
            content: Raw bytes to write.
            if_match: Etag from a previous read, enforced as a precondition.

        Returns:
            The write result, recording whether the file was created.

        Raises:
            ReadOnlyError: If the vault is read-only.
            ConcurrentModificationError: If *if_match* does not match.
            DocumentExistsError: If write protection refuses a blind overwrite.
            ValueError: If the path is invalid.
        """
        self._check_writable()
        with self._file_write_lock:
            abs_path = self.validate_path(path)
            self._check_no_clobber(abs_path, path, if_match)
            check_if_match(abs_path, path, if_match)
            created = not abs_path.is_file()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(abs_path, content)
            result = WriteResult(path=path, created=created)
            self._notifier.fire(abs_path, "", "write")
        return result

    def unlink(self, path: str, if_match: str | None) -> Path:
        """Delete an artifact, returning the path it occupied.

        Fires no callback: the caller reports the delete once, after its own
        note/artifact branch closes.

        Guards read-only and takes the shared lock itself rather than relying
        on the caller to have done so, so the store's policy holds however it
        is reached. The lock is re-entrant, so the nested acquisition when
        ``DocumentManager.delete`` already holds it is free.

        Args:
            path: Relative artifact path.
            if_match: Etag from a previous read, enforced as a precondition.

        Returns:
            The absolute path that was removed.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If the artifact does not exist.
            ConcurrentModificationError: If *if_match* does not match.
            ValueError: If the path is invalid.
        """
        self._check_writable()
        with self._file_write_lock:
            abs_path = self.validate_path(path)
            if not abs_path.is_file():
                raise DocumentNotFoundError(f"Attachment not found: {path}")
            check_if_match(abs_path, path, if_match)
            abs_path.unlink()
        return abs_path

    def move(
        self, old_path: str, new_path: str, if_match: str | None
    ) -> tuple[Path, Path]:
        """Move an artifact, returning the old and new absolute paths.

        Fires no callback, and guards read-only and the shared lock, for the
        same reasons as :meth:`unlink`.

        Args:
            old_path: Current relative artifact path.
            new_path: Destination relative artifact path.
            if_match: Etag from a previous read of *old_path*.

        Returns:
            ``(old_abs, new_abs)``.

        Raises:
            ReadOnlyError: If the vault is read-only.
            DocumentNotFoundError: If *old_path* does not exist.
            DocumentExistsError: If *new_path* already exists.
            ConcurrentModificationError: If *if_match* does not match.
            ValueError: If either path is invalid.
        """
        self._check_writable()
        with self._file_write_lock:
            old_abs = self.validate_path(old_path)
            new_abs = self.validate_path(new_path)
            if not old_abs.is_file():
                raise DocumentNotFoundError(f"Attachment not found: {old_path}")
            if new_abs.is_file():
                raise DocumentExistsError(f"Target already exists: {new_path}")
            check_if_match(old_abs, old_path, if_match)
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_abs), str(new_abs))
        return old_abs, new_abs
