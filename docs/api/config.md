# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

```python
import os
from markdown_vault_mcp import Vault, ProjectConfig
from markdown_vault_mcp.config import to_vault_kwargs

os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/path/to/vault"
config = ProjectConfig.from_env()
vault = Vault(**to_vault_kwargs(config))
```

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.config.ProjectConfig
<!-- vale on -->
