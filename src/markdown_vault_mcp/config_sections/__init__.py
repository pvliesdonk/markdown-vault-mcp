"""Domain-grouped sub-configs composed by :class:`CollectionConfig`."""

from __future__ import annotations

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.config_sections.embeddings import EmbeddingsConfig
from markdown_vault_mcp.config_sections.git import GitConfig
from markdown_vault_mcp.config_sections.indexing import IndexingConfig
from markdown_vault_mcp.config_sections.search import SearchConfig
from markdown_vault_mcp.config_sections.sync import SyncConfig

__all__ = [
    "ContentConfig",
    "EmbeddingsConfig",
    "GitConfig",
    "IndexingConfig",
    "SearchConfig",
    "SyncConfig",
]
