# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

```python
import os
from markdown_vault_mcp import Vault, VaultConfig

os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/path/to/vault"
config = VaultConfig.from_env()
vault = Vault(**config.to_vault_kwargs())
```

## API Reference

::: markdown_vault_mcp.config.VaultConfig
