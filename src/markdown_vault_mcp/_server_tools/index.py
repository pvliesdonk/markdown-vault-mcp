from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp_pvl_core import register_long_running_tool

from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_queryable import needs_queryable
from ..domain import get_vault

if TYPE_CHECKING:
    from fastmcp_pvl_core import Jobs


def register(mcp: FastMCP) -> None:
    """Register the config-free index-observability tools on *mcp*.

    The call-initiated maintenance tools (``reindex``, ``build_embeddings``)
    are registered separately by :func:`register_index_jobs` from
    ``make_server``'s DOMAIN-WIRING block: they are dual-mode long-running
    tools (#1033) and need the config-built ``Jobs`` mechanics. The status
    tools below stay here on purpose — they also report work no client call
    initiated (boot-time builds, file-watcher reindexes), so they remain
    independent observability surfaces rather than job pollers.
    """

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


def register_index_jobs(mcp: FastMCP, jobs: Jobs) -> None:
    """Register the dual-mode index-maintenance tools on *mcp* (#1033).

    ``reindex`` and ``build_embeddings`` submit work to the single-owner
    writer thread and await the submission's own ``Future``, so a fast run
    returns its real result inline; a run still going at the jobs soft
    deadline is promoted to a background job polled via ``get_job_result``.
    Registered from ``make_server``'s DOMAIN-WIRING block (the same pattern
    as the summarize group) because the ``Jobs`` mechanics are built from
    the loaded config.

    Args:
        mcp: The server to register on.
        jobs: The server's shared jobs mechanics (``build_jobs`` result).
    """

    @register_long_running_tool(
        mcp,
        jobs,
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
        force: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Run an incremental reindex on the writer thread.

        Only needed when files are modified outside this server — for example,
        by a text editor, a sync tool, or another process writing directly to
        the vault directory. Do NOT call this after using 'write', 'edit',
        'delete', or 'rename' — those tools update the index immediately as
        part of the operation.

        Change detection is hash-based, so an unchanged file is never
        re-parsed. Use force=True to drop the index and re-parse every file
        regardless of hashes — the repair for index content that no longer
        matches what the current server would extract. A version upgrade that
        changes extraction does this by itself on the next start (#1124), so
        force=True is a manual escape hatch, not routine maintenance. When
        semantic search is configured, follow a force=True run with
        'build_embeddings' (without force) so the vector index converges to
        the rebuilt chunk set; an ordinary reindex re-embeds as it goes.

        To rebuild all embeddings from scratch (e.g. after changing the
        embedding model), use 'build_embeddings' with force=True.

        A fast reindex (the common case — work scales with the drift, not
        the vault) returns its result inline. A reindex still running at the
        server's soft deadline continues in the background and returns
        ``{"status": "working", "job_id": ...}`` immediately — fetch the
        outcome with ``get_job_result``. ``get_index_status`` remains the
        observability view of the index (it also covers boot-time builds and
        file-watcher reindexes no client call initiated).

        Args:
            force: When True, drop every indexed document and re-parse the
                whole vault instead of applying the hash-detected delta.
                The index is not queryable while the rebuild runs, and the
                cost scales with the vault rather than the drift, so prefer
                the default.

        Returns:
            On inline completion, a dict with ``"status": "completed"`` plus
            the reindex counts:

            - added (int): Documents added since the last index. On a
              force=True rebuild every indexed document is counted here,
              because the rebuild dropped and re-added them all.
            - modified (int): Documents that changed since the last index
              (always 0 on a force=True rebuild).
            - deleted (int): Documents removed since the last index (always
              0 on a force=True rebuild — the drop is not a vault change).
            - unchanged (int): Documents with no changes (always 0 on a
              force=True rebuild).
            - skipped (int): Files deliberately not indexed (missing required
              frontmatter, exclude patterns, unparseable).
            - full_rebuild (bool): True when force=True re-parsed everything.

            When promoted, a dict with ``"status": "working"``, a ``job_id``,
            and a ``poll_with`` field naming ``get_job_result``.

        Raises:
            IndexUnavailableError: If the index is not queryable (cold-start
                build pending/failed, or a SQLite failure remapped by the
                ``needs_queryable`` layer). Any other failure within the
                soft deadline re-raises the writer job's own exception; a
                failure after promotion is reported through
                ``get_job_result`` instead (and mirrored in
                ``get_index_status``'s ``last_reindex_error``). If the
                per-subject job cap is hit at promotion time, the call
                fails with a job-limit error and the queued reindex is
                cancelled — retry after fetching pending job results.
        """
        if force:
            stats = await asyncio.wrap_future(vault.index.build_index_async(force=True))
            return {
                "status": "completed",
                "added": stats.documents_indexed,
                "modified": 0,
                "deleted": 0,
                "unchanged": 0,
                "skipped": stats.skipped,
                "full_rebuild": True,
            }
        result = await asyncio.wrap_future(vault.index.reindex_async())
        return {**asdict(result), "status": "completed", "full_rebuild": False}

    @register_long_running_tool(
        mcp,
        jobs,
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

        A fast convergence (small drift) returns its result inline. A build
        still running at the server's soft deadline — typical for a
        force=True rebuild of a large vault — continues in the background
        and returns ``{"status": "working", "job_id": ...}`` immediately;
        fetch the outcome with ``get_job_result``. ``embeddings_status``
        remains the observability view of the vector index.

        Args:
            force: When True, discards existing embeddings and rebuilds from
                scratch. Use only if the embedding model has changed.
                When False (default), converges the vector index to the
                FTS chunk set — work scales with the size of the drift,
                not the size of the vault (#665).

        Returns:
            On inline completion, a dict with ``"status": "completed"`` and
            ``chunks_embedded`` (int): the total number of chunks embedded.
            When promoted, a dict with ``"status": "working"``, a ``job_id``,
            and a ``poll_with`` field naming ``get_job_result``.

        Raises:
            IndexUnavailableError: If the index is not queryable (cold-start
                build pending/failed, or a SQLite failure remapped by the
                ``needs_queryable`` layer).
            EmbeddingsNotConfiguredError: If no embedding provider is
                configured — this now surfaces immediately instead of
                landing only in ``get_index_status``. Any other failure
                within the soft deadline re-raises the writer job's own
                exception; a failure after promotion is reported through
                ``get_job_result`` instead (and mirrored in
                ``last_build_embeddings_error``). If the per-subject job
                cap is hit at promotion time, the call fails with a
                job-limit error and the queued build is cancelled — retry
                after fetching pending job results.
        """
        embedded = await asyncio.wrap_future(
            vault.index.build_embeddings_async(force=force)
        )
        return {"status": "completed", "chunks_embedded": embedded}
