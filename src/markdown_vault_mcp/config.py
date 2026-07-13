"""Configuration for Markdown Vault MCP.

Composes :class:`fastmcp_pvl_core.ServerConfig` via the domain
:class:`ProjectConfig` dataclass — never inherits.

Add domain-specific fields between the CONFIG-FIELDS sentinels; copier
update preserves that block across template updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastmcp_pvl_core import ServerConfig

from markdown_vault_mcp.config_sections import (
    ContentConfig,
    EmbeddingsConfig,
    GitConfig,
    IndexingConfig,
    SearchConfig,
    SummarizeConfig,
    SyncConfig,
    TransferConfig,
)
from markdown_vault_mcp.config_sections._assembly import (
    derive_max_chunk_chars as derive_max_chunk_chars,  # re-export: tests / scanner xref
)
from markdown_vault_mcp.config_sections._assembly import (
    require_source_dir,
    to_bool,
)
from markdown_vault_mcp.config_sections._assembly import (
    to_vault_kwargs as to_vault_kwargs,  # re-export: cli / _server_deps consumers
)
from markdown_vault_mcp.config_sections._helpers import env

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"


@dataclass(frozen=True)
class ProjectConfig:
    """Domain config for Markdown Vault MCP.  Compose — don't inherit."""

    server: ServerConfig = field(default_factory=ServerConfig)

    # CONFIG-FIELDS-START — domain fields; kept across copier update
    source_dir: Path = Path("/data/vault")
    read_only: bool = True
    server_name: str = "markdown-vault-mcp"
    instructions: str | None = None
    git: GitConfig = field(default_factory=GitConfig)
    indexing: IndexingConfig = field(default_factory=IndexingConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    summarize: SummarizeConfig = field(default_factory=SummarizeConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    content: ContentConfig = field(default_factory=ContentConfig)
    transfer: TransferConfig = field(default_factory=TransferConfig)
    disable_apps_ui: bool = False
    # CONFIG-FIELDS-END

    @classmethod
    def from_env(cls) -> ProjectConfig:
        """Load :class:`ProjectConfig` from ``MARKDOWN_VAULT_MCP_*`` env vars."""
        return cls(
            server=ServerConfig.from_env(_ENV_PREFIX),
            # CONFIG-FROM-ENV-START — domain fields from env; kept across copier update
            source_dir=require_source_dir(env(_ENV_PREFIX, "SOURCE_DIR")),
            read_only=to_bool(env(_ENV_PREFIX, "READ_ONLY"), default=True),
            server_name=(env(_ENV_PREFIX, "SERVER_NAME") or "").strip()
            or "markdown-vault-mcp",
            instructions=(env(_ENV_PREFIX, "INSTRUCTIONS") or "").strip() or None,
            git=GitConfig.from_env(_ENV_PREFIX),
            indexing=IndexingConfig.from_env(_ENV_PREFIX),
            embeddings=EmbeddingsConfig.from_env(_ENV_PREFIX),
            search=SearchConfig.from_env(_ENV_PREFIX),
            summarize=SummarizeConfig.from_env(_ENV_PREFIX),
            sync=SyncConfig.from_env(_ENV_PREFIX),
            content=ContentConfig.from_env(
                _ENV_PREFIX, require_source_dir(env(_ENV_PREFIX, "SOURCE_DIR"))
            ),
            transfer=TransferConfig.from_env(_ENV_PREFIX),
            disable_apps_ui=to_bool(env(_ENV_PREFIX, "DISABLE_APPS_UI"), default=False),
            # CONFIG-FROM-ENV-END
        )
