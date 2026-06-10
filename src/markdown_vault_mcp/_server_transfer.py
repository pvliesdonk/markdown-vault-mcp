"""HTTP transfer-link tools + route registration (#622).

Defines the module-level link-building logic (unit-testable) and a
``register_transfer`` that wires the two tools and the ``/transfer/{token}``
route so all three close over one shared :class:`TransferStore`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastmcp.dependencies import Depends

from markdown_vault_mcp._icons import _TOOL_ICONS
from markdown_vault_mcp._server_deps import get_vault
from markdown_vault_mcp.config import _ENV_PREFIX
from markdown_vault_mcp.transfer.routes import make_transfer_handler
from markdown_vault_mcp.transfer.store import TransferStore
from markdown_vault_mcp.utils import (
    effective_attachment_extensions,
    validate_path,
)
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from fastmcp import FastMCP

    from markdown_vault_mcp.config import VaultConfig

logger = logging.getLogger(__name__)


def _base_url(config: VaultConfig) -> str:
    """Return the configured public base URL (no trailing slash; empty if unset)."""
    return (config.server.base_url or "").rstrip("/")


def _clamp_ttl(ttl_seconds: int | None, default_s: int, max_s: int) -> int:
    """Return the effective TTL: the default when unset, else clamped to [1, max].

    Args:
        ttl_seconds: Caller-requested lifetime, or ``None`` for the default.
        default_s: Server default lifetime in seconds.
        max_s: Ceiling; any value above this is clamped to *max_s*.

    Returns:
        Effective TTL in seconds.
    """
    if ttl_seconds is None:
        return default_s
    return max(1, min(ttl_seconds, max_s))


def _iso(epoch: float) -> str:
    """Format an epoch time as a UTC ISO-8601 string.

    Args:
        epoch: Seconds since the UNIX epoch.

    Returns:
        UTC ISO-8601 formatted timestamp string.
    """
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _validate_destination(
    path: str,
    source_dir: Path,
    attachment_extensions: Sequence[str] | None,
) -> bool:
    """Validate an upload destination and return whether it is an attachment.

    Args:
        path: Vault-relative destination path.
        source_dir: Vault root.
        attachment_extensions: Configured allowlist (``None`` = defaults).

    Returns:
        ``True`` if *path* is an attachment, ``False`` for a note.

    Raises:
        ValueError: On path traversal or a disallowed attachment extension.
    """
    if path.endswith(".md"):
        validate_path(path, source_dir)
        return False
    resolved = (source_dir / path).resolve()
    if not resolved.is_relative_to(source_dir.resolve()):
        raise ValueError(f"Path traversal detected: {path}")
    exts = effective_attachment_extensions(attachment_extensions)
    ext = resolved.suffix.lstrip(".").lower()
    if "*" not in exts and ext not in exts:
        raise ValueError(f"Attachment extension not allowed: .{ext}")
    return True


def _validate_source(
    path: str,
    source_dir: Path,
    attachment_extensions: Sequence[str] | None,
) -> bool:
    """Validate a download source (stat-only) and return whether it is an attachment.

    Verifies existence without reading the file, so minting a download link for
    a large attachment never loads it into memory.

    Args:
        path: Vault-relative source path.
        source_dir: Vault root.
        attachment_extensions: Configured allowlist (``None`` = defaults).

    Returns:
        ``True`` if *path* is an attachment, ``False`` for a note.

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
    except OSError as exc:
        raise ValueError(f"File not accessible: {path}") from exc
    if not exists:
        kind = "Attachment" if is_attachment else "Note"
        raise ValueError(f"{kind} not found: {path}")
    if is_attachment:
        exts = effective_attachment_extensions(attachment_extensions)
        ext = resolved.suffix.lstrip(".").lower()
        if "*" not in exts and ext not in exts:
            raise ValueError(f"Attachment extension not allowed: .{ext}")
    return is_attachment


def _link_response(base: str, record: Any, ttl: int) -> dict[str, Any]:
    """Build the common tool return payload for a minted token.

    Args:
        base: Public base URL (no trailing slash).
        record: A :class:`~markdown_vault_mcp.transfer.store.TransferToken`.
        ttl: The effective (clamped) TTL in seconds.

    Returns:
        Dict with ``url``, ``path``, ``expires_at``, and ``expires_in_seconds``.
    """
    return {
        "url": f"{base}/transfer/{record.token}",
        "path": record.path,
        "expires_at": _iso(record.expires_at),
        "expires_in_seconds": ttl,
    }


async def _create_download_link(
    store: TransferStore,
    config: VaultConfig,
    _vault: Vault,
    path: str,
    ttl_seconds: int | None,
) -> dict[str, Any]:
    """Mint a one-time download link for an existing vault file.

    Args:
        store: The shared token store.
        config: Loaded vault configuration.
        _vault: The live vault instance (unused at mint time; kept for symmetry).
        path: Vault-relative path of an existing note or attachment.
        ttl_seconds: Requested lifetime; clamped to the server ceiling.

    Returns:
        Dict with ``url``, ``path``, ``expires_at``, and ``expires_in_seconds``.

    Raises:
        ValueError: If ``BASE_URL`` is unset or the file does not exist.
    """
    base = _base_url(config)
    if not base:
        raise ValueError(f"{_ENV_PREFIX}_BASE_URL must be set to create transfer links")
    is_attachment = _validate_source(
        path, config.source_dir, config.content.attachment_extensions
    )
    ttl = _clamp_ttl(
        ttl_seconds, config.transfer.ttl_default_s, config.transfer.ttl_max_s
    )
    record = store.create("download", path, is_attachment, ttl)
    return _link_response(base, record, ttl)


async def _create_upload_link(
    store: TransferStore,
    config: VaultConfig,
    _vault: Vault,
    path: str,
    ttl_seconds: int | None,
) -> dict[str, Any]:
    """Mint a one-time upload link bound to a validated destination path.

    Args:
        store: The shared token store.
        config: Loaded vault configuration.
        _vault: The live vault instance (unused at mint time; kept for symmetry).
        path: Vault-relative destination (a note or an allowed attachment).
        ttl_seconds: Requested lifetime; clamped to the server ceiling.

    Returns:
        Dict with ``url``, ``path``, ``expires_at``, and ``expires_in_seconds``.

    Raises:
        ValueError: If ``BASE_URL`` is unset, the destination escapes the vault,
            or its attachment extension is not allowed.
    """
    base = _base_url(config)
    if not base:
        raise ValueError(f"{_ENV_PREFIX}_BASE_URL must be set to create transfer links")
    is_attachment = _validate_destination(
        path, config.source_dir, config.content.attachment_extensions
    )
    ttl = _clamp_ttl(
        ttl_seconds, config.transfer.ttl_default_s, config.transfer.ttl_max_s
    )
    record = store.create(
        "upload",
        path,
        is_attachment,
        ttl,
        max_upload_bytes=config.transfer.max_upload_bytes,
    )
    return _link_response(base, record, ttl)


def register_transfer(mcp: FastMCP, config: VaultConfig) -> None:
    """Register the transfer tools and the /transfer/{token} route on *mcp*.

    Builds one shared :class:`TransferStore` captured by both the route
    handler and the two tools. Call only on HTTP/SSE transport.

    Args:
        mcp: The FastMCP server.
        config: The loaded vault configuration.
    """
    store = TransferStore()

    mcp.custom_route("/transfer/{token}", methods=["GET", "POST", "PUT"])(
        make_transfer_handler(store)
    )

    @mcp.tool(
        icons=_TOOL_ICONS["create_download_link"],
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def create_download_link(
        path: str,
        ttl_seconds: int | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Create a one-time HTTP link to download a vault note or attachment.

        The link serves the file's current content once, then expires.
        Requires ``MARKDOWN_VAULT_MCP_BASE_URL``.

        Args:
            path: Vault-relative path of an existing note or attachment.
            ttl_seconds: Requested lifetime; clamped to the server ceiling.

        Returns:
            A dict with:
            - ``url``: the one-time download URL.
            - ``path``: the vault path.
            - ``expires_at``: ISO-8601 UTC expiry.
            - ``expires_in_seconds``: the effective (clamped) TTL.

        Raises:
            ValueError: If ``BASE_URL`` is unset or the file does not exist.
        """
        return await _create_download_link(store, config, vault, path, ttl_seconds)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["create_upload_link"],
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def create_upload_link(
        path: str,
        ttl_seconds: int | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Create a one-time HTTP link to upload bytes into the vault.

        The destination is fixed and validated now; the link accepts one
        POST/PUT of raw bytes, commits them via the normal write path, then
        expires. Requires ``MARKDOWN_VAULT_MCP_BASE_URL``.

        Args:
            path: Vault-relative destination (a note or an allowed attachment).
            ttl_seconds: Requested lifetime; clamped to the server ceiling.

        Returns:
            A dict with:
            - ``url``: the one-time upload URL.
            - ``path``: the destination vault path.
            - ``expires_at``: ISO-8601 UTC expiry.
            - ``expires_in_seconds``: the effective (clamped) TTL.

        Raises:
            ValueError: If ``BASE_URL`` is unset, the destination escapes the
                vault, or its attachment extension is not allowed.
        """
        return await _create_upload_link(store, config, vault, path, ttl_seconds)
