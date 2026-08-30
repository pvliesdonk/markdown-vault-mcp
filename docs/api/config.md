# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

`to_vault_settings` maps a loaded configuration onto a `VaultSettings`, and `to_vault_instances` resolves the constructed collaborators (embedding provider, summarizer, git strategy). Together they feed settings-first `Vault` construction:

```python
import os
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._assembly import (
    to_vault_instances,
    to_vault_settings,
)
from markdown_vault_mcp.vault import Vault

os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/path/to/vault"
config = ProjectConfig.from_env()
instances = to_vault_instances(config)
settings = to_vault_settings(config, instances=instances)
vault = Vault(
    source_dir=config.source_dir,
    settings=settings,
    embedding_provider=instances.embedding_provider,
    summarizer=instances.summarizer,
    git_strategy=instances.git_strategy,
    on_write=instances.on_write,
)
```

## Deprecated: `to_vault_kwargs`

The historical bridge `to_vault_kwargs(config)` returns a flat keyword dict for `Vault(**kwargs)`. It now delegates to `to_vault_settings` and `to_vault_instances` while keeping its historical dict shape for existing callers. It is scheduled for removal in the next major release, so prefer the settings-first construction above.

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.config.ProjectConfig
<!-- vale on -->
