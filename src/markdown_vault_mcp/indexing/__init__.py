"""Index-write subsystem: the single-owner writer thread and its coordinator."""

from markdown_vault_mcp.indexing.coordinator import IndexWriteCoordinator
from markdown_vault_mcp.indexing.index_writer import (
    BuildEmbeddings,
    BuildIndex,
    FlushDirtyEmbeddings,
    IndexWriter,
    JobRunner,
    ProcessDirtyPaths,
    ReindexAll,
    WriterContext,
    run_build_embeddings,
    run_build_index,
    run_flush_dirty_embeddings,
    run_process_dirty_paths,
    run_reindex_all,
)
from markdown_vault_mcp.indexing.readiness import ReadinessState

__all__ = [
    "BuildEmbeddings",
    "BuildIndex",
    "FlushDirtyEmbeddings",
    "IndexWriteCoordinator",
    "IndexWriter",
    "JobRunner",
    "ProcessDirtyPaths",
    "ReadinessState",
    "ReindexAll",
    "WriterContext",
    "run_build_embeddings",
    "run_build_index",
    "run_flush_dirty_embeddings",
    "run_process_dirty_paths",
    "run_reindex_all",
]
