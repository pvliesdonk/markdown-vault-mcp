from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_queryable import needs_queryable
from ..domain import get_vault


def register(mcp: FastMCP) -> None:
    """Register index-management tools on *mcp*."""

    @mcp.tool(
        icons=_TOOL_ICONS["embeddings_status"],
        annotations={
            "title": "Embeddings Status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def embeddings_status(
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Check the embedding provider configuration and vector index status.

        Use this to diagnose why semantic search is unavailable. Embeddings
        are built automatically on startup when configured, so chunk_count
        should normally match the FTS chunk count from 'stats'. If it is
        lower, call 'build_embeddings' (without force) to embed the missing
        chunks. Use 'build_embeddings' with force=True only to rebuild from
        scratch after changing the embedding model.

        Returns:
            Dict with the following fields:

            - available (bool): True if semantic search can be used in 'search'.
            - provider (str | None): Provider class name when configured
              (e.g. "OllamaProvider"), or null if not configured.
            - chunk_count (int): Number of chunks currently in the vector index.
            - path (str | None): Vector index file path when persisted, or null.
        """
        return await asyncio.to_thread(vault.index.embeddings_status)

    @mcp.tool(
        annotations={
            "title": "Index Status",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
        icons=_TOOL_ICONS["get_index_status"],
    )
    async def get_index_status(
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return background-build state of the FTS index.

        Use this when ``initialize`` returned but bucket-3/4 calls
        block longer than expected or surface
        ``IndexUnavailableError`` — the ``status`` field
        distinguishes "still building" from "build failed," and the
        ``error`` field carries the exception message from the last
        background-build attempt that captured one. ``error`` may be
        populated when ``status`` is ``"queryable"`` (a successful
        build followed by a later failed rebuild leaves the captured
        diagnostic in place until the next successful build clears
        it) and is always ``None`` when ``status`` is ``"building"``.

        Returns:
            Dict with the following fields:

            - status (str): ``"queryable"``, ``"building"``, or
              ``"failed"``.
            - documents_indexed (int): Count of documents committed to
              the FTS index right now (rises during ``"building"``).
              ``0`` both for an empty index and when the count could not
              be read — see ``documents_indexed_error`` to tell them apart.
            - documents_indexed_error (str | None): ``None`` on a normal
              read; the SQLite error message when the document count
              could not be read (e.g. a locked or closed database), in
              which case ``documents_indexed`` is ``0``.
            - error (str | None): ``None`` unless the background build
              raised.
            - skipped_files (list[dict]): Files dropped from the index for a
              surfaced deterministic reason. Each entry is
              ``{"path", "category", "detail"}`` where ``category`` is one of
              ``"parse_error"``, ``"encoding_error"``,
              ``"missing_frontmatter"``, or ``"internal_error"`` (an
              unexpected indexer error, vs a content problem). Empty when
              nothing was skipped.
              Distinguishes a parse-dropped note from an unsynced one without
              reading container logs. Exclude-pattern and transient-I/O skips
              are intentionally not listed.
        """
        return await asyncio.to_thread(vault.index.get_index_status)

    @mcp.tool(
        icons=_TOOL_ICONS["reindex"],
        annotations={
            "title": "Reindex Vault",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def reindex(
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Submit an incremental reindex job to the writer.

        Only needed when files are modified outside this server — for example,
        by a text editor, a sync tool, or another process writing directly to
        the vault directory. Do NOT call this after using 'write', 'edit',
        'delete', or 'rename' — those tools update the index immediately as
        part of the operation.

        To rebuild all embeddings from scratch (e.g. after changing the
        embedding model), use 'build_embeddings' with force=True.

        Returns:
            Dict with ``status: "queued"``. The reindex runs asynchronously on
            the writer thread. Poll ``get_index_status`` for completion:

            - ``status == "queryable"`` AND ``queue_depth == 0`` AND
              ``in_flight is None`` → reindex completed.
            - ``last_reindex_error`` not ``None`` → the most recent async
              reindex failed on the writer thread; the value is the
              stringified exception.  Operators can re-run ``reindex`` to
              retry; a subsequent successful run clears the field.
        """
        vault.index.reindex_async()
        return {"status": "queued"}

    @mcp.tool(
        icons=_TOOL_ICONS["build_embeddings"],
        annotations={
            "title": "Build Embeddings",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    @needs_queryable()
    async def build_embeddings(
        force: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Rebuild vector embeddings for semantic and hybrid search.

        Embeddings are built automatically on startup, so this is normally
        not needed. Use force=True to rebuild from scratch after changing
        the embedding model. Without force, the vector index converges to
        the FTS chunk set: missing or changed documents are embedded,
        orphaned vectors are removed, unchanged chunks are untouched.

        Args:
            force: When True, discards existing embeddings and rebuilds from
                scratch. Use only if the embedding model has changed.
                When False (default), converges the vector index to the
                FTS chunk set — work scales with the size of the drift,
                not the size of the vault (#665).

        Returns:
            Dict with ``status: "queued"``. The build runs asynchronously on
            the writer thread. Poll ``get_index_status`` for completion:

            - ``status == "queryable"`` AND ``queue_depth == 0`` AND
              ``in_flight is None`` → build completed.
            - ``last_build_embeddings_error`` not ``None`` → the most
              recent async build failed on the writer thread; the value is
              the stringified exception.  Operators can re-run
              ``build_embeddings`` to retry; a subsequent successful run
              clears the field.
        """
        vault.index.build_embeddings_async(force=force)
        return {"status": "queued"}
