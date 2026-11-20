"""Private operational lifecycle for one already-assembled vault.

Configuration/identity assembly and MCP publication belong to ``domain.Service``.
This owner only starts and closes the resources supplied for its vault.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from markdown_vault_mcp._file_watcher import VaultFileWatcher
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.config_sections._assembly import VaultInstances
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
    from markdown_vault_mcp.vault import Vault

logger = logging.getLogger(__name__)


class _VaultRuntime:
    """Own boot jobs, the watcher and ordered cleanup for one live vault."""

    def __init__(
        self,
        vault: Vault,
        settings: VaultSettings,
        instances: VaultInstances,
        config: ProjectConfig,
        transport: str,
    ) -> None:
        self.vault = vault
        self._settings = settings
        self._instances = instances
        self._config = config
        self._transport = transport
        self._file_watcher: VaultFileWatcher | None = None

    async def start(self) -> None:
        """Submit boot jobs in order, then start the vault and its watcher."""
        vault, config, instances = self.vault, self._config, self._instances
        # If periodic git pull is enabled, sync before submitting the
        # initial index build so the scan sees the latest working tree.
        await asyncio.to_thread(vault.sync_from_remote_before_index)

        # Submit the initial build jobs to the IndexWriter and yield
        # immediately (#559). build_index_async() short-circuits in
        # O(1) on warm restarts (existing FTS sentinel from PR #526);
        # cold restarts submit a BuildIndex job that the writer
        # processes asynchronously while the lifespan yields.
        # Bucket-3 tools block on @needs_queryable until the build
        # completes; bucket-2 tools return whatever is currently in
        # the index per #526.
        vault.index.build_index_async()
        logger.info("build_index_job_submitted")

        # Reconcile offline changes (#665): files added, modified, or
        # deleted while no server was running are invisible to both the
        # warm-restart short-circuit above (O(1), no filesystem scan) and
        # the file watcher below (future events only).  Enqueue an
        # incremental reindex behind the build: the writer's FIFO ordering
        # guarantees build-before-reindex, and on a cold boot the full
        # build has just recorded tracker state (including skipped files),
        # so the reindex degenerates to a cheap hash scan instead of a
        # second full parse.  While this job is pending, the writer is
        # non-drained, so #646's out-of-band `index_stale` meta signal
        # honestly reports True until the boot reconciliation completes.
        # This job is now conditional on config.boot_reindex (#1535);
        # index_stale consequently reports drained sooner without implying
        # the index agrees with disk.
        # The HEAD the startup sync left behind is what the boot reindex
        # covers; recording it lets later reconciles (#1532) skip a redundant
        # full scan and reindex only once HEAD moves past it.
        head_at_boot = await asyncio.to_thread(vault.git_head)
        if config.boot_reindex:
            vault.adopt_boot_reindex(vault.index.reindex_async(), head_at_boot)
            logger.info("boot_reindex_job_submitted")
        else:
            # Offline changes (files added/modified/deleted while no server
            # was running) stay invisible to the index until an explicit
            # reindex runs — either the `reindex` tool or the
            # `markdown-vault-mcp reindex` CLI command.  Accepting the index
            # at the boot HEAD keeps that trade: later pulls that move HEAD
            # still reindex.  This records the git HEAD the index is taken
            # to reflect; get_index_status still reports offline_changes as
            # not reconciled, because nothing compared the index with disk.
            vault.mark_index_reconciled(head_at_boot)
            logger.info("boot_reindex_disabled reason=config")

        if instances.embedding_provider is not None:
            vault.index.build_embeddings_async()
            logger.info("build_embeddings_job_submitted")

        # Start any other background tasks (e.g. git pull loop).
        vault.start()

        self._start_file_watcher()

    def _start_file_watcher(self) -> None:
        """Start the file watcher when no other source reports vault changes."""
        config = self._config
        vault, settings, instances = self.vault, self._settings, self._instances
        # File watcher — only when git pull and webhook are both inactive so the
        # watcher and git checkout don't race to trigger reindex (#558).
        from markdown_vault_mcp._file_watcher import (
            VaultFileWatcher,
            should_start_file_watcher,
        )
        from markdown_vault_mcp._webhooks import webhook_routes_active
        from markdown_vault_mcp.exceptions import IndexUnavailableError

        # Use the *resolved* pull interval from the git assembly, not
        # config.git.pull_interval_s: the config default is 600 even on
        # non-git vaults, but to_vault_instances() only resolves a non-zero
        # interval when a sync-enabled git strategy is configured.
        git_pull_active = instances.git_pull_interval_s > 0

        # Ask whether a webhook can actually deliver, not merely whether its
        # credentials are set: under stdio no route is mounted, so standing the
        # watcher down for it would leave no external-change detection at all
        # (#1263). Same predicate register_webhook_routes mounts on, fed the
        # same transport make_server was called with.
        webhook_active = webhook_routes_active(config, self._transport)

        if should_start_file_watcher(
            config.sync.file_watcher_enabled,
            git_pull_active,
            webhook_active,
        ):

            def _on_file_change() -> None:
                try:
                    with vault.pause_writes():
                        vault.index.reindex()
                except IndexUnavailableError:
                    logger.info(
                        "file_watcher_reindex_skipped reason=index_not_queryable"
                    )
                except Exception:
                    logger.error("file_watcher_reindex_failed", exc_info=True)

            # Never watch the vault's own write targets: reindex() rewrites the
            # state file on every run, and a watch on its dir would re-trigger
            # the watcher into a self-feedback loop (#830). .git is protected
            # too so local git-write commits don't storm reindexes.
            internal_dirs = [config.source_dir / ".git"]
            for extra in (
                settings.effective_state_path(config.source_dir),
                config.indexing.index_path,
                settings.effective_embeddings_path(),
            ):
                if extra is not None:
                    internal_dirs.append(extra.parent)

            self._file_watcher = VaultFileWatcher(
                config.source_dir,
                _on_file_change,
                debounce_s=config.sync.file_watcher_debounce_s,
                exclude_patterns=config.indexing.exclude_patterns,
                root_floor=config.sync.file_watcher_root_floor,
                internal_dirs=internal_dirs,
            )
            self._file_watcher.start()
        elif not config.sync.file_watcher_enabled:
            logger.debug("file_watcher_disabled reason=file_watcher_false")
        else:
            logger.info("file_watcher_disabled reason=other_cadence_source_active")

    def close(self) -> None:
        """Stop the watcher, drain the vault, then close its collaborators."""
        if self._file_watcher is not None:
            self._file_watcher.stop()
        self.vault.close()
        # Flush Git pushes only after the vault has drained its write callbacks.
        self._instances.close()
