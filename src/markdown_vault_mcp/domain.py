"""Domain service that owns the Vault lifecycle for the MCP server.

The template-owned ``_server_deps`` scaffold constructs a domain :class:`Service`
(``start``/``stop``) and yields it to the request context. MVM's ``Service`` builds
the :class:`~markdown_vault_mcp.vault.Vault`, submits the boot index/reindex/embeddings
jobs, and runs the file watcher — the lifecycle that used to live inline in
``_server_deps.make_vault_lifespan`` (#902, epic #898). ``get_vault`` / ``get_config``
resolve the running service's vault/config for ``Depends``-injected handlers, and the
module-level singleton reaches the live Vault from HTTP routes outside DI.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._assembly import (
    to_vault_instances,
    to_vault_settings,
)
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from markdown_vault_mcp._file_watcher import VaultFileWatcher

logger = logging.getLogger(__name__)


_vault_singleton: Vault | None = None

# Config handoff: make_server sets the config it already loaded so the no-arg
# ``Service()`` the template's server_lifespan constructs builds the vault from
# that same config — the caller-supplied config stays authoritative and the
# environment is not parsed a second time (#609). NOT cleared on read: FastMCP's
# lifespan is ref-counted and re-enters (constructs a fresh ``Service()``) each
# time a new session opens after all sessions closed, so the value must survive
# to rebuild the vault from the same config on every re-entry. Single-server-
# per-process (last make_server wins), like the vault singleton above.
_pending_config: ProjectConfig | None = None


def set_pending_config(config: ProjectConfig | None) -> None:
    """Stage the config for the no-arg :class:`Service` construction (#609)."""
    global _pending_config
    _pending_config = config


def set_vault_singleton(vault: Vault | None) -> None:
    """Set the module-level :class:`Vault` singleton.

    Set by :meth:`Service.start` with the live Vault, and cleared to ``None`` by
    :meth:`Service.stop` so a subsequent server in the same process starts clean.

    Args:
        vault: The live :class:`Vault`, or ``None`` to clear.
    """
    global _vault_singleton
    _vault_singleton = vault


def get_vault_singleton() -> Vault:
    """Return the module-level :class:`Vault` singleton.

    Used by HTTP route handlers (e.g. the GitHub webhook route) that run outside
    FastMCP's ``Depends(get_vault)`` injection and cannot resolve the Vault from
    the lifespan context.

    Returns:
        The live :class:`Vault` set by :meth:`Service.start`.

    Raises:
        RuntimeError: If the singleton has not been set yet.
    """
    if _vault_singleton is None:
        msg = (
            "Vault not initialised — Service.start was never called.  In normal "
            "operation the server lifespan starts it; in tests, set explicitly "
            "via set_vault_singleton(col)."
        )
        raise RuntimeError(msg)
    return _vault_singleton


class Service:
    """Owns the Vault lifecycle: build, boot index/embeddings jobs, file watcher.

    Constructed no-args by the template's ``server_lifespan``; resolves config
    from the config make_server staged (#609), falling back to an env read only
    if none was staged. Tests may pass a pre-built ``config``.
    """

    def __init__(self, config: ProjectConfig | None = None) -> None:
        # Prefer an explicit config, then the one make_server staged (#609), then
        # a fresh env read. The staged config is NOT cleared: a ref-counted
        # lifespan re-entry constructs a new Service() and must rebuild from the
        # same config, not re-read env.
        self._config = config or _pending_config or ProjectConfig.from_env()
        self._vault: Vault | None = None
        self._file_watcher: VaultFileWatcher | None = None

    @property
    def vault(self) -> Vault:
        """The live :class:`Vault` (raises if :meth:`start` has not run)."""
        if self._vault is None:
            msg = "Service not started — call start() first"
            raise RuntimeError(msg)
        return self._vault

    @property
    def config(self) -> ProjectConfig:
        """The :class:`ProjectConfig` this service was built from."""
        return self._config

    async def start(self) -> None:
        """Build the Vault and submit the boot jobs; start background tasks."""
        config = self._config
        logger.info("Initialising vault from %s", config.source_dir)

        # Settings-first construction (#1158): the config-derived knobs
        # travel as one VaultSettings; the constructed collaborators stay
        # explicit keywords, resolved once into VaultInstances.
        instances = to_vault_instances(config)
        settings = to_vault_settings(config, instances=instances)
        if instances.embedding_provider is not None:
            logger.info(
                "Embedding provider: %s",
                type(instances.embedding_provider).__name__,
            )
        vault = Vault(
            source_dir=config.source_dir,
            settings=settings,
            embedding_provider=instances.embedding_provider,
            summarizer=instances.summarizer,
            git_strategy=instances.git_strategy,
            on_write=instances.on_write,
        )
        self._vault = vault
        set_vault_singleton(vault)

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
        logger.info("Submitted BuildIndex job to writer")

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
        vault.index.reindex_async()
        logger.info("Submitted boot Reindex job to writer")

        if instances.embedding_provider is not None:
            vault.index.build_embeddings_async()
            logger.info("Submitted BuildEmbeddings job to writer")

        # Start any other background tasks (e.g. git pull loop).
        vault.start()

        # File watcher — only when git pull and webhook are both inactive so the
        # watcher and git checkout don't race to trigger reindex (#558).
        from markdown_vault_mcp._file_watcher import (
            VaultFileWatcher,
            should_start_file_watcher,
        )
        from markdown_vault_mcp.exceptions import IndexUnavailableError

        # Use the *resolved* pull interval from the git assembly, not
        # config.git.pull_interval_s: the config default is 600 even on
        # non-git vaults, but to_vault_instances() only resolves a non-zero
        # interval when a sync-enabled git strategy is configured.
        git_pull_active = instances.git_pull_interval_s > 0

        if should_start_file_watcher(
            config.sync.file_watcher_enabled,
            git_pull_active,
            config.sync.github_webhook_secret,
        ):

            def _on_file_change() -> None:
                try:
                    with vault.pause_writes():
                        vault.index.reindex()
                except IndexUnavailableError:
                    logger.info(
                        "file_watcher: index not yet queryable, skipping reindex"
                    )
                except Exception:
                    logger.error("file_watcher: reindex failed", exc_info=True)

            # Never watch the vault's own write targets: reindex() rewrites the
            # state file on every run, and a watch on its dir would re-trigger
            # the watcher into a self-feedback loop (#830). .git is protected
            # too so local git-write commits don't storm reindexes.
            from markdown_vault_mcp.vault import _DEFAULT_STATE_SUBDIR

            state_dir = (
                config.indexing.state_path.parent
                if config.indexing.state_path is not None
                else config.source_dir / _DEFAULT_STATE_SUBDIR
            )
            internal_dirs = [config.source_dir / ".git", state_dir]
            for extra in (
                config.indexing.index_path,
                config.indexing.embeddings_path,
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
            logger.debug("file_watcher: disabled via FILE_WATCHER=false")
        else:
            logger.info(
                "file_watcher: disabled — git pull loop / webhook handles reindex cadence"
            )

    async def stop(self) -> None:
        """Stop the file watcher and close the Vault."""
        if self._file_watcher is not None:
            self._file_watcher.stop()
        # Clear the singleton before closing so any in-flight HTTP handler gets a
        # clean RuntimeError instead of touching a Vault mid-close().
        set_vault_singleton(None)
        if self._vault is not None:
            self._vault.close()
        logger.info("Vault shut down")


def get_vault(ctx: Context = CurrentContext()) -> Vault:
    """Resolve the live Vault from the request context.

    Used as a ``Depends()`` default in tool/resource/prompt signatures.

    Raises:
        RuntimeError: If the server lifespan has not run.
    """
    service: Service | None = ctx.lifespan_context.get("service")
    if service is None:
        msg = "Vault not initialised — server lifespan has not run"
        raise RuntimeError(msg)
    return service.vault


def get_config(ctx: Context = CurrentContext()) -> ProjectConfig:
    """Resolve the :class:`ProjectConfig` from the request context.

    Raises:
        RuntimeError: If the server lifespan has not run.
    """
    service: Service | None = ctx.lifespan_context.get("service")
    if service is None:
        msg = "Config not initialised — server lifespan has not run"
        raise RuntimeError(msg)
    return service.config
