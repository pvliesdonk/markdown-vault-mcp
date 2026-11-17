"""Portable raw-file references, independent of indexing and Git retention."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, BinaryIO, Literal

from markdown_vault_mcp.exceptions import DocumentUnreadableError, InvalidRequestError
from markdown_vault_mcp.utils import (
    effective_attachment_extensions,
    validate_history_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def _namespace(value: str | None) -> str:
    if value is None or not value.strip() or len(value) > 256:
        raise InvalidRequestError(
            "File references need a nonempty vault namespace of at most 256 characters. "
            "Ask the operator to configure one that uniquely names this vault."
        )
    return value


def _relative_path(path: str) -> None:
    if not path or any(p in {"", ".", ".."} for p in path.split("/")):
        raise InvalidRequestError(
            "path must be a vault-relative file path with no empty, '.' or '..' "
            "components; use a path from list_documents."
        )


def _open_nonblocking(path: str, flags: int) -> int:
    return os.open(path, flags | getattr(os, "O_NONBLOCK", 0))


@dataclass(frozen=True)
class FileReference:
    """Version-one portable locator with optional raw-byte content identity.

    Attributes:
        namespace: Caller-configured vault identity, stable across mounts.
        path: Vault-relative file path at creation; renames do not follow.
        size_bytes: Complete raw-file byte count from the same read as sha256.
        sha256: Lowercase raw-file SHA-256, or None for a path-only reference.
        provenance: Optional caller label, at most 1024 characters.
        version: Required contract version; currently 1.
    """

    namespace: str
    path: str
    size_bytes: int
    sha256: str | None = None
    provenance: str | None = None
    version: int = field(kw_only=True, metadata={"strict": True})

    def __post_init__(self) -> None:
        """Reject malformed or unsupported saved references."""
        _namespace(self.namespace)
        _relative_path(self.path)
        if type(self.version) is not int or self.version != 1 or self.size_bytes < 0:
            raise InvalidRequestError(
                "Use a version 1 reference with a nonnegative byte count."
            )
        if (
            self.sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
        ):
            raise InvalidRequestError(
                "sha256 must be 64 lowercase hexadecimal characters."
            )
        if self.provenance is not None and len(self.provenance) > 1024:
            raise InvalidRequestError(
                "provenance must contain at most 1024 characters."
            )


@dataclass(frozen=True)
class ReferenceResolution:
    """Result of checking a saved reference, without substituting changed bytes.

    Attributes:
        status: verified, unverified (no digest), changed, missing or wrong_vault.
        reference: The original saved reference, never silently refreshed.
        detail: Outcome and recovery guidance, including the retention limit.
        content_base64: Exact verified bytes on an explicitly requested read only.
    """

    status: Literal["verified", "unverified", "changed", "missing", "wrong_vault"]
    reference: FileReference
    detail: str
    content_base64: str | None = None


_RETENTION = (
    "No historical copy is retained. If this vault has Git history, get_history "
    "and read(revision=...) may help locate old bytes; otherwise they are unavailable."
)


class ReferenceReader:
    """Create and resolve references over notes and allowed attachments.

    Hash-only calls stream files without the body-read size cap. The caller
    owns the namespace and must use the same attachment policy as its vault.
    No Git commit, timestamp or archive is inferred from a current-file read.
    """

    def __init__(
        self,
        source_dir: Path,
        namespace: str | None,
        *,
        attachment_extensions: Sequence[str] | None = None,
    ) -> None:
        """Configure the vault root, durable namespace and attachment allowlist.

        Args:
            source_dir: Root containing files, as for Vault.source_dir.
            namespace: Stable unique name chosen by the caller or operator.
            attachment_extensions: Same allowlist used for vault reads.
        """
        self._root = source_dir.resolve()
        self._namespace = _namespace(namespace)
        self._extensions = effective_attachment_extensions(attachment_extensions)

    def get_file_reference(
        self, path: str, *, include_sha256: bool = False, provenance: str | None = None
    ) -> FileReference:
        """Create a portable reference without returning the file body.

        Args:
            path: Relative note or allowed attachment path.
            include_sha256: Hash the complete raw file, including frontmatter.
            provenance: Optional caller label; no provenance is inferred.

        Returns:
            Versioned reference. No historical copy is retained.

        Raises:
            InvalidRequestError: Invalid path, missing file or concurrent change.
            DocumentUnreadableError: File system refuses the read.
        """
        # Validate before I/O, including opt-in caller metadata.
        FileReference(self._namespace, path, 0, provenance=provenance, version=1)
        try:
            size, digest, _ = self._snapshot(path, include_sha256, None)
        except FileNotFoundError as exc:
            raise InvalidRequestError(
                "File is missing; use list_documents to find a current path."
            ) from exc
        return FileReference(self._namespace, path, size, digest, provenance, version=1)

    def resolve_file_reference(
        self,
        reference: FileReference,
        *,
        include_content: bool = False,
        max_content_bytes: float = 262144,
    ) -> ReferenceResolution:
        """Verify current bytes, optionally returning the exact verified bytes.

        Args:
            reference: Saved version-one reference.
            include_content: Return base64 bytes only after digest verification.
            max_content_bytes: Body buffering cap; 0 means unlimited.

        Returns:
            Explicit outcome; changed, missing and wrong-vault results have no body.

        Raises:
            InvalidRequestError: Invalid path, read cap or concurrent change.
            DocumentUnreadableError: File system refuses the read.
        """
        if reference.namespace != self._namespace:
            return ReferenceResolution(
                "wrong_vault", reference, "Use the connection for the saved namespace."
            )
        if max_content_bytes < 0:
            raise InvalidRequestError("max_content_bytes must be nonnegative.")
        try:
            size, digest, raw = self._snapshot(
                reference.path,
                reference.sha256 is not None,
                max_content_bytes if include_content else None,
            )
        except FileNotFoundError:
            return ReferenceResolution(
                "missing",
                reference,
                "Saved path is missing or renamed. Locate it explicitly; " + _RETENTION,
            )
        if size != reference.size_bytes or (
            reference.sha256 is not None and digest != reference.sha256
        ):
            return ReferenceResolution(
                "changed",
                reference,
                "Current bytes differ from the saved reference. " + _RETENTION,
            )
        if reference.sha256 is None:
            if include_content:
                raise InvalidRequestError(
                    "Saved reference has no SHA-256; its original bytes cannot be verified. "
                    "Resolve with include_content=false to check only the path."
                )
            return ReferenceResolution(
                "unverified",
                reference,
                "Path exists, but no SHA-256 was saved to verify content identity. "
                + _RETENTION,
            )
        if include_content and raw is None:
            raise InvalidRequestError(
                "File exceeds the content read limit; resolve with include_content=false to verify without its body."
            )
        return ReferenceResolution(
            "verified",
            reference,
            _RETENTION,
            base64.b64encode(raw).decode("ascii") if raw is not None else None,
        )

    def _snapshot(
        self, path: str, hash_bytes: bool, body_cap: float | None
    ) -> tuple[int, str | None, bytes | None]:
        absolute = validate_history_path(path, self._root, self._extensions)
        try:
            # O_NONBLOCK prevents a replaced FIFO from hanging before fstat.
            with open(absolute, "rb", opener=_open_nonblocking) as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise InvalidRequestError(
                        "path must name a regular file; use list_documents to locate one."
                    )
                size, digest, raw = self._consume(stream, hash_bytes, body_cap)
                self._check_stable(path, absolute, before, os.fstat(stream.fileno()))
                if hash_bytes and size != before.st_size:
                    raise InvalidRequestError(
                        "File changed during reference read; retry the call."
                    )
                return (size if hash_bytes else before.st_size), digest, raw
        except IsADirectoryError as exc:
            raise InvalidRequestError(
                "path must name a regular file; use list_documents to locate one."
            ) from exc
        except (FileNotFoundError, NotADirectoryError):
            raise FileNotFoundError(path) from None
        except OSError as exc:
            raise DocumentUnreadableError(
                path, "File system refused the reference read."
            ) from exc

    @staticmethod
    def _consume(
        stream: BinaryIO, hash_bytes: bool, body_cap: float | None
    ) -> tuple[int, str | None, bytes | None]:
        if not hash_bytes:
            return 0, None, None
        digest = hashlib.sha256()
        body = bytearray() if body_cap is not None else None
        size = 0
        for block in iter(lambda: stream.read(65536), b""):
            size += len(block)
            digest.update(block)
            if body is not None:
                # Keep hashing after the cap; identity decides the outcome
                # before an unchanged file's body-read limit is reported.
                if body_cap and size > body_cap:
                    body = None
                else:
                    body.extend(block)
        return size, digest.hexdigest(), bytes(body) if body is not None else None

    def _check_stable(
        self, path: str, absolute: Path, before: os.stat_result, after: os.stat_result
    ) -> None:
        def identity(st: os.stat_result) -> tuple[int, ...]:
            return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

        try:
            current = validate_history_path(path, self._root, self._extensions)
            current_stat = absolute.stat()
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise InvalidRequestError(
                "File changed during reference read; retry the call."
            ) from exc
        if (
            current != absolute
            or identity(before) != identity(after)
            or identity(after) != identity(current_stat)
        ):
            raise InvalidRequestError(
                "File changed during reference read; retry the call."
            )
