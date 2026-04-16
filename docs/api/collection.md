# Collection

The `Collection` class is the primary public API for the library. MCP tools, CLI commands, and direct integrations all go through this class.

## Quick Start

```python
from pathlib import Path
from markdown_vault_mcp import Collection

# Basic read-only collection
collection = Collection(source_dir=Path("/path/to/vault"))
stats = collection.build_index()
print(f"Indexed {stats.documents_indexed} documents")

# Search
results = collection.search("query text", limit=10)
for r in results:
    print(f"{r.path}: {r.title} (score: {r.score:.2f})")

# Read a document
note = collection.read("Journal/note.md")
print(note.content)
```

## API Reference

::: markdown_vault_mcp.collection.Collection
