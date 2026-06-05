# Collection

The `Collection` class is the primary public API for the library. MCP tools, CLI commands, and direct integrations all go through this class. It is a thin composition root: the read / write / graph / index operations live on the four facets, reached through the `reader` / `writer` / `graph` / `index` accessors (see [Facets](facets.md)).

## Quick Start

```python
from pathlib import Path
from markdown_vault_mcp import Collection

# Basic read-only collection
collection = Collection(source_dir=Path("/path/to/vault"))
stats = collection.index.build_index()
print(f"Indexed {stats.documents_indexed} documents")

# Search (reader facet)
results = collection.reader.search("query text", limit=10)
for r in results:
    print(f"{r.path}: {r.title} (score: {r.score:.2f})")

# Read a document (reader facet)
note = collection.reader.read("Journal/note.md")
print(note.content)
```

## API Reference

::: markdown_vault_mcp.collection.Collection
