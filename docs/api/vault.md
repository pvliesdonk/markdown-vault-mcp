# Vault

The `Vault` class is the primary public API for the library. MCP tools, CLI commands, and direct integrations all go through this class. It is a thin composition root: the read / write / graph / index operations live on the four facets, reached through the `reader` / `writer` / `graph` / `index` accessors (see [Facets](facets.md)).

## Quick Start

Construction is *settings-first*: pass `source_dir` plus a `VaultSettings` carrying the configuration knobs. Collaborator objects that are never derived from configuration (`embedding_provider`, `summarizer`, `git_strategy`, `on_write`, and `chunk_strategy`) stay explicit keywords.

```python
from pathlib import Path
from markdown_vault_mcp.vault import Vault, VaultSettings

# Basic read-only vault (all settings at their defaults)
vault = Vault(source_dir=Path("/path/to/vault"))
stats = vault.index.build_index()
print(f"Indexed {stats.documents_indexed} documents")

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
```

## Deprecated: per-knob keyword arguments

Before `VaultSettings`, every configuration knob was its own `Vault(...)` keyword (`Vault(source_dir=..., read_only=False, index_path=...)`). Those keywords still work unchanged, but they are deprecated in favor of the same-named `VaultSettings` fields and scheduled for removal in the next major release. Passing `settings=` together with a non-default legacy keyword raises `ValueError`: pick one mode per construction. `source_dir` and the five collaborator keywords are not deprecated.

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.config_sections.vault_settings.VaultSettings

::: markdown_vault_mcp.vault.Vault
<!-- vale on -->
