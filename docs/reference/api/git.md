---
description: "The git module: commit strategies, history and diffs for a vault under git."
kind: reference
---

# Git Integration

The `git` module provides:

- Auto-commit + deferred push for write operations (via `on_write`)
- Periodic pull (ff-only) primitives used by the server to keep the working tree up to date

## Quick Start

```python { .fragment }
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

# Clean up on shutdown: the vault first, then the strategy you built
vault.close()
strategy.close()
```

## Local libgit2 store

Library callers can install `"markdown-vault-mcp[libgit2]"` and pass one
`Libgit2Store` as both `git_strategy` and `on_write`. This local store commits
only paths touched by each write and provides history, diffs and revision
reads; it does not pull or push. The server continues to use the CLI strategy.

```python { .fragment }
from pathlib import Path
from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.vault import Vault, VaultSettings

store = Libgit2Store(repo_path=Path("/path/to/vault"))
vault = Vault(source_dir=Path("/path/to/vault"), settings=VaultSettings(read_only=False),
              git_strategy=store, on_write=store)
```

The constructor rejects unsupported repository formats and partial clones.
It also refuses non-default `core.sharedRepository` permissions; unset,
`umask`, and false settings remain supported.
It also refuses commit signing and executable commit or reference hooks, such
as `reference-transaction` and `post-index-change`. For tracked paths, a
configured external clean or process filter, including Git LFS, or a
working-tree encoding is refused. Writes check these policies again and refuse in-progress Git
operations, such as a merge. Use `GitWriteStrategy` for these repositories.
A refused write updates the store's uncommitted-work health when inspection
succeeds. A write matching HEAD reconciles its index entries without creating a
commit. History and per-commit diff limits are clamped between 1 and 100; an omitted
diff limit is unbounded. Dates support ISO 8601, `N units ago` (including compound and dotted forms),
`yesterday`, `last <weekday>`, `today`, `midnight`, and `noon`. For other expressions, use
`GitWriteStrategy`. History and per-commit diff queries require a numeric
`core.abbrev` setting; configure one or use `GitWriteStrategy` for Git’s
adaptive default. Writes, revision reads and aggregate diffs remain available
without a numeric setting. Full commit IDs are unchanged.

Git and libgit2 may score edited renames differently. The native store refuses
queries that require such scoring. An unambiguous exact-content rename remains
supported, including alongside an unrelated deletion. A newly added note in a
commit that deletes another file may also require uncertain matching and be
refused.

Binary diff summaries and rename diffs with
mode changes are also refused. Use `GitWriteStrategy` for those queries.
Committed-revision lookup examines the current path without following renames;
an unprovable result returns no breadcrumb and an overwrite can proceed.
Vault and directory history refuse when a merge falls within the requested
window; a limit reached before the merge can still return results. Single-file
history and per-commit diffs refuse a merge that differs from every parent.
Revision reads that need to traverse a merge also refuse.
Repositories with `core.symlinks=false` are refused. With `core.filemode=false`,
a regular replacement for a symlink and a new regular file use mode 100644;
an existing regular file retains its mode. A local `force_push` reports success when
the tracking ref already equals HEAD; a dry run remains unsupported.

## Migrating from 4.x

`GitWriteStrategy` no longer accepts `commit_name_claim` or `commit_email_claim`. Remove these constructor arguments. The remaining `git_lfs` and `repo_path` options must now be passed by keyword, so old positional claim arguments cannot silently become LFS or repository settings:

```python { .fragment }
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

The optional `BatchAwareWriteCallback` protocol in `markdown_vault_mcp.types`
describes the batch opt-in. `Versioner` remains the per-write contract.
