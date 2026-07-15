# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

```
import os
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.vault import Vault
from markdown_vault_mcp.config import to_vault_kwargs

os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/path/to/vault"
config = ProjectConfig.from_env()
vault = Vault(**to_vault_kwargs(config))
```

## API Reference

## `ProjectConfig(server=ServerConfig(), source_dir=Path('/data/vault'), read_only=True, server_name='markdown-vault-mcp', instructions=None, git=GitConfig(), indexing=IndexingConfig(), embeddings=EmbeddingsConfig(), search=SearchConfig(), summarize=SummarizeConfig(), sync=SyncConfig(), content=ContentConfig(), transfer=TransferConfig(), disable_apps_ui=False)`

Domain config for Markdown Vault MCP. Compose — don't inherit.

### `from_env()`

Load :class:`ProjectConfig` from `MARKDOWN_VAULT_MCP_*` env vars.
