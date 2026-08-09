"""Vault-backed transfer hooks for pvl-core's capability-link routes (#979).

``fastmcp_pvl_core.register_transfer_routes`` owns the token store, the
``/transfer/{token}`` route, the generic ``create_download_link`` /
``create_upload_link`` tools (with their titles, hints, and icons), and all
size-cap / TTL mechanics. This module supplies the one domain hook it consumes:
a :class:`~fastmcp_pvl_core.TransferSink` (where bytes come from / go to) plus a
``TransferValidator`` (which refs are acceptable):

- **download** — ``read`` serves an existing vault note or attachment's bytes.
- **upload** — ``write`` commits uploaded bytes to a validated destination path
  through the normal write path.

The sink is byte-oriented (pvl-core materialises the whole body, bounded by the
per-upload cap); it never interprets the ``/transfer`` route or the token store.
Path validation runs at link-creation time in :meth:`VaultTransferSink.validate`
(the ``TransferValidator``), so a bad ref is rejected before a token is minted.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp_pvl_core import (
    TransferReadResult,
    TransferResourceGoneError,
    TransferUnavailableError,
)

from markdown_vault_mcp.domain import get_vault_singleton
from markdown_vault_mcp.utils import effective_attachment_extensions, validate_path
from markdown_vault_mcp.utils.text import decode_utf8

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from fastmcp_pvl_core import TransferKind

    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.vault import Vault

_MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_OCTET_STREAM = "application/octet-stream"


def _validate_destination(
    path: str,
    source_dir: Path,
    attachment_extensions: Sequence[str] | None,
) -> None:
    """Validate an upload destination path (a note or an allowed attachment).

    Args:
        path: Vault-relative destination path.
        source_dir: Vault root.
        attachment_extensions: Configured allowlist (``None`` = defaults).

    Raises:
        ValueError: On path traversal or a disallowed attachment extension.
    """
    if path.endswith(".md"):
        validate_path(path, source_dir)
        return
    resolved = (source_dir / path).resolve()
    if not resolved.is_relative_to(source_dir.resolve()):
        raise ValueError(f"Path traversal detected: {path}")
    exts = effective_attachment_extensions(attachment_extensions)
    ext = resolved.suffix.lstrip(".").lower()
    if "*" not in exts and ext not in exts:
        raise ValueError(f"Attachment extension not allowed: .{ext}")


def _validate_source(
    path: str,
    source_dir: Path,
    attachment_extensions: Sequence[str] | None,
) -> None:
    """Validate a download source path (stat-only — never reads the file).

    Verifies existence without reading, so minting a download link for a large
    attachment never loads it into memory.

    Args:
        path: Vault-relative source path.
        source_dir: Vault root.
        attachment_extensions: Configured allowlist (``None`` = defaults).

    Raises:
        ValueError: On path traversal, a missing file, or a disallowed
            attachment extension.
    """
    is_attachment = not path.endswith(".md")
    if not is_attachment:
        resolved = validate_path(path, source_dir)
    else:
        resolved = (source_dir / path).resolve()
        if not resolved.is_relative_to(source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")
    try:
        exists = resolved.is_file()
    except OSError as exc:  # pragma: no cover - defensive: stat fault on is_file()
        raise ValueError(f"File not accessible: {path}") from exc
    if not exists:
        kind = "Attachment" if is_attachment else "Note"
        raise ValueError(f"{kind} not found: {path}")
    if is_attachment:
        exts = effective_attachment_extensions(attachment_extensions)
        ext = resolved.suffix.lstrip(".").lower()
        if "*" not in exts and ext not in exts:
            raise ValueError(f"Attachment extension not allowed: .{ext}")


class VaultTransferSink:
    """:class:`TransferSink` + validator backed by the vault.

    The live :class:`~markdown_vault_mcp.vault.Vault` is resolved per request
    (it is created by the server lifespan), so a single sink instance can be
    wired at server-build time. The opaque ``sink_handle`` is simply the
    vault-relative path; :meth:`read` / :meth:`write` re-derive note-vs-attachment
    from the ``.md`` suffix.
    """

    def __init__(
        self,
        config: ProjectConfig,
        vault_provider: Callable[[], Vault] = get_vault_singleton,
    ) -> None:
        """Hold the config (for path validation) and a live-vault resolver.

        Args:
            config: The loaded project config — supplies the vault root and the
                attachment-extension allowlist for validation.
            vault_provider: Resolves the live vault at request time (defaults to
                the module singleton; injectable for tests).
        """
        self._config = config
        self._vault = vault_provider

    async def validate(self, ref: str, kind: TransferKind) -> str:
        """Validate a link ref; return the opaque sink handle or raise to reject.

        The handle is the vault-relative path itself. Download validation is
        stat-only (existence without a read); upload validation checks the
        destination is a note or an allowed attachment. ``kind`` selects which.

        Args:
            ref: Vault-relative path of a note or attachment.
            kind: ``"download"`` or ``"upload"``.

        Returns:
            The vault-relative path, stored verbatim as the sink handle.

        Raises:
            ValueError: On path traversal, a missing download source, or a
                disallowed attachment extension.
        """
        source_dir = self._config.source_dir
        exts = self._config.content.attachment_extensions
        if kind == "download":
            _validate_source(ref, source_dir, exts)
        else:
            _validate_destination(ref, source_dir, exts)
        return ref

    def _resolve_vault(self) -> Vault:
        """Resolve the live vault, or signal a retryable 503 if it is torn down.

        The vault singleton is owned by a ref-counted session lifespan and is
        cleared when all MCP sessions close, while the ``/transfer/{token}``
        route stays mounted for the server's lifetime. A link followed in that
        window would otherwise raise a bare ``RuntimeError`` and surface as a
        generic 500; mapping it to :class:`~fastmcp_pvl_core.TransferUnavailableError`
        gives the caller a clean, retryable **503** instead (the route releases
        the token, so the link survives the retry). See fastmcp-pvl-core#233.

        Raises:
            TransferUnavailableError: If the vault is not currently available.
        """
        try:
            return self._vault()
        except RuntimeError as exc:
            logger.warning("transfer_vault_unavailable: %s", type(exc).__name__)
            raise TransferUnavailableError(
                "vault is not currently available; retry shortly"
            ) from exc

    async def read(self, handle: str) -> TransferReadResult:
        """Serve a vault note or attachment's bytes for a download handle.

        Args:
            handle: The vault-relative path the link was minted with.

        Returns:
            A :class:`~fastmcp_pvl_core.TransferReadResult` — bytes, media type,
            and download filename.

        Raises:
            TransferUnavailableError: The vault is being torn down (retryable 503).
            TransferResourceGoneError: The note/attachment existed at mint time
                but has since been removed (410 Gone).
        """
        vault = self._resolve_vault()
        filename = Path(handle).name
        if handle.endswith(".md"):
            note = await asyncio.to_thread(vault.reader.read, handle)
            if note is None:
                logger.warning("transfer_download_note_gone path=%s", handle)
                raise TransferResourceGoneError(f"note no longer available: {handle}")
            body = note.content.encode("utf-8")
            logger.info("transfer_download_served path=%s bytes=%d", handle, len(body))
            return TransferReadResult(body, _MARKDOWN_MEDIA_TYPE, filename)
        try:
            att = await asyncio.to_thread(vault.reader.read_attachment, handle)
        except ValueError as exc:
            # read_attachment raises ValueError("Attachment not found: ...") once
            # the file is gone; validate confirmed it at mint time, so this is a
            # 410 Gone, not a 500.
            logger.warning("transfer_download_attachment_gone path=%s", handle)
            raise TransferResourceGoneError(
                f"attachment no longer available: {handle}"
            ) from exc
        body = base64.b64decode(att.content_base64)
        media_type = att.mime_type or _OCTET_STREAM
        logger.info("transfer_download_served path=%s bytes=%d", handle, len(body))
        return TransferReadResult(body, media_type, filename)

    async def write(self, handle: str, body: bytes) -> Mapping[str, Any]:
        """Commit an uploaded body to a vault note or attachment path.

        Args:
            handle: The vault-relative destination path.
            body: The uploaded bytes (already size-capped by the route).

        Returns:
            A payload dict with the written ``path`` and ``bytes``.

        Raises:
            TransferUnavailableError: The vault is being torn down (retryable 503).
            UnicodeDecodeError: A note upload whose body is not valid UTF-8.
        """
        vault = self._resolve_vault()
        if handle.endswith(".md"):
            text = decode_utf8(body)  # strips a leading BOM (#681); raises on bad UTF-8
            await asyncio.to_thread(vault.writer.write, handle, text)
        else:
            await asyncio.to_thread(vault.writer.write_attachment, handle, body)
        logger.info("transfer_upload_committed path=%s bytes=%d", handle, len(body))
        return {"path": handle, "bytes": len(body)}
