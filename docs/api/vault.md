# Vault

The `Vault` class is the primary public API for the library. MCP tools, CLI commands, and direct integrations all go through this class. It is a thin composition root: the read / write / graph / index operations live on the four facets, reached through the `reader` / `writer` / `graph` / `index` accessors (see [Facets](facets.md)).

## Quick Start

Construction uses `VaultSettings`: pass `source_dir` plus a `VaultSettings` carrying the configuration knobs. Collaborator objects (`embedding_provider`, `summarizer`, `git_strategy`, `on_write`, and `chunk_strategy`) stay explicit keywords.

```python
from pathlib import Path
from markdown_vault_mcp.vault import Vault, VaultSettings

# Basic read-only vault (all settings at their defaults)
vault = Vault(source_dir=Path("/path/to/vault"))
stats = vault.index.build_index()
print(f"Indexed {stats.documents_indexed} documents")
vault.close()

# Configured vault: knobs travel on VaultSettings
vault = Vault(
    source_dir=Path("/path/to/vault"),
    settings=VaultSettings(
        read_only=False,
        index_path=Path("/path/to/index.db"),
        exclude_patterns=[".obsidian/**"],
    ),
)

# Search (reader facet)
results = vault.reader.search("query text", limit=10)
for r in results:
    print(f"{r.path}: {r.title} (score: {r.score:.2f})")

# Read a document (reader facet)
note = vault.reader.read("Journal/note.md")
print(note.content)
vault.close()
```

## Migrating from 4.x

The 31 configuration keywords on `Vault` have been removed. Move each value to the same-named field on `VaultSettings`. Replace `Vault(source_dir=root, read_only=False, index_path=index)` with:

```python
vault = Vault(
    source_dir=root,
    settings=VaultSettings(read_only=False, index_path=index),
)
```

Old keywords now raise `TypeError`, including when their values match the defaults or when `settings` is also supplied. `source_dir` and the five collaborator keywords remain on `Vault`.

Omitting `settings` (or passing `None`) uses `VaultSettings()`. Library defaults are unchanged: read-only, no chunk overlap, and no overwrite protection once writes are enabled. Server configuration continues to use its own defaults through [configuration assembly](config.md).

## Index freshness after writes

File writes complete before returning; index updates run on the background writer. For a following library search or graph read, use `vault.index.wait_for_drain(timeout=60)` and check whether it returns `True`. A disk `read` does not need this wait.

Link conversion, index generation, rename with `update_links=True`, and folder move perform their own queued refresh before reading index data. They fail before mutation if that refresh fails or exceeds 60 seconds. The wait covers prior writes; it does not isolate concurrent edits. A queued initial build completes before this refresh; OKF generators still reject an index that was never built. Healthy notes continue to receive vector updates when another note in their refresh batch fails. Retrying that batch reuses matching stored vectors, avoiding repeated provider requests for unchanged notes. Direct `DocumentManager` integrations can supply the `sync_index` callback to provide the same boundary.

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.config_sections.vault_settings.VaultSettings

::: markdown_vault_mcp.vault.Vault
<!-- vale on -->
