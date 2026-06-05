# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

```python
import os
from markdown_vault_mcp import Vault, load_config

os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/path/to/vault"
config = load_config()
vault = Vault(**config.to_vault_kwargs())
```

## API Reference

::: markdown_vault_mcp.config.VaultConfig

::: markdown_vault_mcp.config.load_config
