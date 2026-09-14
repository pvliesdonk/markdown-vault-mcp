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

## Migrating from 4.x

`GitWriteStrategy` no longer accepts `commit_name_claim` or `commit_email_claim`. Remove these constructor arguments. The remaining `git_lfs` and `repo_path` options must now be passed by keyword, so old positional claim arguments cannot silently become LFS or repository settings:

```python
strategy = GitWriteStrategy(
    commit_name="vault-service",
    commit_email="vault-service@example.com",
    git_lfs=False,
    repo_path=Path("/path/to/vault"),
)
```

Author identity is supplied per write as a resolved `Principal`, through the callback's `principal=` argument. Integrations writing through `Vault` can use `markdown_vault_mcp._identity.bound_principal` around the write. Claim extraction belongs at the request boundary; the strategy does not read tokens.

Server operators keep using `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` and `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM`. Configuration assembly registers those keys with the identity layer, which resolves them when the tool call arrives. A configured claim that an authenticated token cannot supply still produces its warning and falls back to the static identity.

The older startup warning about missing `git config user.email` is removed. The strategy supplies its configured committer name and email, falling back to `markdown-vault-mcp <noreply@markdown-vault-mcp>`, so an empty checkout identity is supported.

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.git.GitWriteStrategy

::: markdown_vault_mcp.git.git_write_strategy
<!-- vale on -->
