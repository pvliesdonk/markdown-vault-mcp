# Git Integration

The `git` module provides:

- Auto-commit + deferred push for write operations (via `on_write`)
- Periodic pull (ff-only) primitives used by the server to keep the working tree up to date

## Quick Start

```python
from pathlib import Path
from markdown_vault_mcp.git import GitWriteStrategy
from markdown_vault_mcp.vault import Vault, VaultSettings

strategy = GitWriteStrategy(
    token="ghp_your_token",
    push_delay_s=30,
)

vault = Vault(
    source_dir=Path("/path/to/vault"),
    settings=VaultSettings(read_only=False),
    on_write=strategy,
)

# Writes are now auto-committed and pushed
vault.writer.write("notes/new.md", "Hello world")

# Clean up on shutdown
vault.close()
```

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.git.GitWriteStrategy

::: markdown_vault_mcp.git.git_write_strategy
<!-- vale on -->
