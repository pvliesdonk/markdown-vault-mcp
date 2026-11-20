"""MCP adapter for the same bounded metadata inventory as changed-files."""

from __future__ import annotations

import asyncio
from threading import Event
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from pydantic import Field

from markdown_vault_mcp._file_inventory import ScanLimits, Window, scan_current_files
from markdown_vault_mcp._icons import _TOOL_ICONS
from markdown_vault_mcp._server_deps import get_service
from markdown_vault_mcp._tool_boundary import tool_boundary
from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
from markdown_vault_mcp.domain import Service
from markdown_vault_mcp.exceptions import (
    ConfigurationError,
    InvalidRequestError,
    VaultUnavailableError,
)

_Instant = Annotated[str, Field(strict=True, max_length=128)]
_Entries = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
_Seconds = Annotated[float, Field(strict=True, ge=0.001, le=300, allow_inf_nan=False)]


def _observe(service: Service, window: Window, limits: ScanLimits) -> dict[str, Any]:
    try:
        vault = service.vault
        root = vault.source_dir.resolve(strict=True)
    except (ConfigurationError, OSError, RuntimeError):
        raise VaultUnavailableError(
            "The configured vault is unavailable. Retry later; tell the user if it persists."
        ) from None
    settings = VaultSettings(
        exclude_patterns=vault.exclude_patterns,
        conventions_file=None,  # Already included in the effective exclusions.
        attachment_extensions=service.config.content.attachment_extensions,
    )
    data = scan_current_files(root, window, settings, limits)
    # Explicit wire projection: the local CLI's source_dir is operator data,
    # and must not cross the MCP boundary, including on partial scans.
    return {
        key: data[key]
        for key in (
            "start",
            "end",
            "interval",
            "basis",
            "complete",
            "incomplete_reasons",
            "items",
        )
    }


def register(mcp: FastMCP) -> None:
    """Register the read-only current-file inventory tool.

    Args:
        mcp: Server receiving the tool under its existing access policy.
    """

    @mcp.tool(
        icons=_TOOL_ICONS["list_documents"],
        tags={"group:reader"},
        annotations={
            "title": "List Changed Files",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def list_changed_files(
        start: _Instant,
        end: _Instant,
        max_entries: _Entries = 100_000,
        max_seconds: _Seconds = 10.0,
        service: Service = Depends(get_service),
    ) -> dict[str, Any]:
        """List current Markdown and allowed attachments by filesystem modification time.

        Use this instead of indexed listings to include untyped or malformed notes.
        Covers the served vault only, omitting dot paths, exclusions and symlinks;
        returns relative paths and completeness, not deleted files or earlier changes.

        Args:
            start: Inclusive RFC3339 timestamp with Z or an explicit UTC offset.
            end: Exclusive RFC3339 timestamp with Z or an explicit UTC offset, after start.
            max_entries: Maximum directory entries to visit, 1 to 1000000; omit for 100000.
            max_seconds: Cooperative traversal time budget, 0.001 to 300 seconds; omit for 10.

        Returns:
            UTC bounds, interval, current-file basis, complete, incomplete_reasons,
            and path-sorted items with path, kind and modified_at. At most 1000
            items and 1000000 bytes of escaped item JSON; result_limit marks
            partial output. An empty partial result does not establish absence.
            No host paths or file contents. A live scan is not an atomic snapshot.
            Cancellation stops at the next scan checkpoint; blocked filesystem
            calls cannot be interrupted.
        """
        try:
            window = Window(start, end)
        except ValueError:
            raise InvalidRequestError(
                "list_changed_files requires valid RFC3339 start and end with UTC offsets, "
                "and start before end. Call again with explicit timezone-aware bounds."
            ) from None
        cancel = Event()
        limits = ScanLimits(max_entries, max_seconds, 1000, 1_000_000, cancel)
        try:
            return await asyncio.to_thread(_observe, service, window, limits)
        finally:
            cancel.set()
