# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

`to_vault_settings` maps a loaded configuration onto a `VaultSettings`, and `to_vault_instances` resolves the constructed collaborators (embedding provider, summarizer, git strategy). Together they feed `Vault` construction:

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

## Migrating from 4.x

`to_vault_kwargs` has been removed from both `markdown_vault_mcp.config` and `markdown_vault_mcp.config_sections._assembly`. Replace `Vault(**to_vault_kwargs(config))` with the construction shown above. Resolve `instances` once and pass it to `to_vault_settings` so the provider is loaded once and its context limit determines the chunk size.

For overrides formerly applied to the keyword dictionary, use `dataclasses.replace` on the settings before constructing the vault:

```python
from dataclasses import replace
from pathlib import Path

settings = replace(settings, index_path=Path("/path/to/other-index.db"))
```

Keep collaborator overrides on the corresponding `Vault` keyword arguments.

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.config.ProjectConfig
<!-- vale on -->
