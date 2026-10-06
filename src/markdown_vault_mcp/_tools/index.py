from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp_pvl_core import register_long_running_tool, tool_boundary

from markdown_vault_mcp._tools._outcomes import library_outcomes
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
    ``_server_wiring`` (called from ``make_server``'s DOMAIN-WIRING): they are dual-mode long-running
    tools (#1033) and need the config-built ``Jobs`` mechanics. The status
    tools below stay here on purpose — they also report work no client call
    initiated (boot-time builds, file-watcher reindexes), so they remain
    independent observability surfaces rather than job pollers.
    """

    @mcp.tool(
        description="Check embedding provider and vector-index status.",
        tags={"group:indexing"},
        icons=_TOOL_ICONS["embeddings_status"],
        annotations={
            "title": "Embeddings Status",
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def embeddings_status(
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Report the embedding provider and the vector index behind semantic search.

        Returns:
            Dict with the following fields:

            - available (bool): True when an embedding provider is
              configured, even before the first build finishes. It does not
              say the embeddings are complete; get_index_status does.
            - provider (str | None): Provider class name when configured
              (such as `"OllamaProvider"`), or null if not configured.
            - chunk_count (int): Number of chunks currently in the vector index.
            - path (str | None): Vector index file path when persisted, or
              null when the vectors are held in memory or not configured.
        """
        return await asyncio.to_thread(vault.index.embeddings_status)

    @mcp.tool(
        description="Report FTS index readiness, progress, and last build error.",
        tags={"group:indexing"},
        annotations={
            "title": "Index Status",
            "read_only_hint": True,
            "open_world_hint": False,
        },
        icons=_TOOL_ICONS["get_index_status"],
    )
    @tool_boundary
    @library_outcomes
    async def get_index_status(
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Return the background-build state of the search index.

        Returns:
            Dict with the following fields:

            - status (str): `"queryable"`, `"building"`, or
              `"failed"`.
            - documents_indexed (int): Count of documents committed to
              the FTS index right now (rises during `"building"`).
              `0` both for an empty index and when the count could not
              be read; see `documents_indexed_error` to tell them apart.
            - documents_indexed_error (str | None): `None` on a normal
              read; the SQLite error message when the document count
              could not be read (such as a locked or closed database), in
              which case `documents_indexed` is `0`.
            - error (str | None): The message of the last background build
              that failed, or `None`. It can stay set while status is
              `"queryable"`, until the next successful build clears it, and is
              always `None` while status is `"building"`.
            - last_reindex_error (str | None): The message of the last
              reindex that failed, or `None` once a reindex succeeds.
            - last_build_embeddings_error (str | None): The message of the last
              embeddings build that failed, or `None` once one succeeds.
            - queue_depth (int): Jobs waiting for the index writer.
            - in_flight (str | None): The kind of job the index writer is
              running, such as `"process_dirty_paths"`, or `None` when idle.
            - dirty_paths (int): Changed notes not yet refreshed in the index.
            - dirty_embeddings (int): Changed notes not yet re-embedded.
            - write_generation (int): A counter that rises each time the index
              writer finishes a job.
            - skipped_files (list[dict]): Files dropped from the index for a
              surfaced deterministic reason. Each entry is
              `{"path", "category", "detail"}` where `category` is one of
              `"parse_error"`, `"encoding_error"`,
              `"missing_frontmatter"`, or `"internal_error"` (an
              unexpected indexer error, vs a content problem). Empty when
              nothing was skipped.
              Distinguishes a parse-dropped note from an unsynced one without
              reading container logs. Exclude-pattern and transient-I/O skips
              are intentionally not listed.

            The index, and the embeddings when semantic search is configured,
            have caught up once status is `"queryable"`, queue_depth is 0,
            in_flight is `None`, and dirty_paths and dirty_embeddings are both 0.
        """
        return await asyncio.to_thread(vault.index.get_index_status)


def register_index_jobs(mcp: FastMCP, jobs: Jobs) -> None:
    """Register the dual-mode index-maintenance tools on *mcp* (#1033).

    ``reindex`` and ``build_embeddings`` submit work to the single-owner
    writer thread and await the submission's own ``Future``, so a fast run
    returns its real result inline; a run still going at the jobs soft
    deadline is promoted to a background job polled via ``get_job_result``.
    Registered from ``_server_wiring`` (``make_server``'s DOMAIN-WIRING; the
    same pattern as the summarize group) because the ``Jobs`` mechanics are built from
    the loaded config.

    Args:
        mcp: The server to register on.
        jobs: The server's shared jobs mechanics (``build_jobs`` result).
    """

    @register_long_running_tool(
        mcp,
        jobs,
        icons=_TOOL_ICONS["reindex"],
        tags={"group:indexing"},
        annotations={
            "title": "Reindex Vault",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @library_outcomes
    @needs_queryable()
    async def reindex(
        force: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Bring the search index up to date with files changed outside this server,
        such as by an editor or a sync tool; returns counts of added, modified, deleted
        and unchanged notes.

        Writes through this server's tools update the index themselves, so do not call
        it after them.

        Args:
            force: Drop the index and re-parse every note instead of only changed ones:
                slower, and search waits until it finishes. Use it when results no
                longer match the notes, then run build_embeddings if the vault has
                semantic search. Default false.

        Returns:
            On inline completion, a dict with `"status": "completed"` plus
            the reindex counts:

            - added (int): Documents added since the last index. On a
              force=True rebuild every indexed document is counted here,
              because the rebuild dropped and re-added them all.
            - modified (int): Documents that changed since the last index
              (always 0 on a force=True rebuild).
            - deleted (int): Documents removed since the last index (always
              0 on a force=True rebuild; the drop is not a vault change).
            - unchanged (int): Documents with no changes (always 0 on a
              force=True rebuild).
            - skipped (int): Files deliberately not indexed (missing required
              frontmatter, exclude patterns, unparseable).
            - full_rebuild (bool): True when force=True re-parsed everything.

            When promoted, a dict with `"status": "working"`, a `job_id`,
            and a `poll_with` field naming `get_job_result`. A failure after
            promotion is reported through `get_job_result` and mirrored in
            `get_index_status`'s `last_reindex_error`.

        Raises:
            ToolError: If the index is busy or still building; retry shortly.
            IndexUnavailableError: If the index build failed or the index is broken;
                get_index_status reports the error.
            ToolError: If the caller's job limit is reached when the call is
                promoted to a background job; the queued reindex is cancelled, so
                fetch pending job results and retry.
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
        tags={"group:indexing"},
        annotations={
            "title": "Build Embeddings",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @library_outcomes
    @needs_queryable()
    async def build_embeddings(
        force: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Bring the vector index behind semantic and hybrid search up to date with the
        notes; returns the number of chunks embedded.

        Embeddings are built automatically, so it is needed only after reindex with
        force, after a failed build (last_build_embeddings_error in
        get_index_status), or with force to rebuild them all.

        Args:
            force: Discard every embedding and rebuild from scratch, as after the
                embedding model changed; default false embeds only what changed.

        Returns:
            On inline completion, a dict with `"status": "completed"` and
            `chunks_embedded` (int): the total number of chunks embedded.
            When promoted, a dict with `"status": "working"`, a `job_id`,
            and a `poll_with` field naming `get_job_result`. A failure after
            promotion is reported through `get_job_result` and mirrored in
            `get_index_status`'s `last_build_embeddings_error`.

        Raises:
            ToolError: If the index is busy or still building; retry shortly.
            IndexUnavailableError: If the index build failed or the index is broken;
                get_index_status reports the error.
            ToolError: If the caller's job limit is reached when the call is
                promoted to a background job; the queued build is cancelled, so
                fetch pending job results and retry.
            EmbeddingsNotConfiguredError: If no embedding provider is
                configured.
        """
        embedded = await asyncio.wrap_future(
            vault.index.build_embeddings_async(force=force)
        )
        return {"status": "completed", "chunks_embedded": embedded}
