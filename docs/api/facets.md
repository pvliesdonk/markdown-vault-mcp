# Facets

The read / write / graph / index operations live on four cohesive facets,
reached through the `reader` / `writer` / `graph` / `index` accessors on the
[`Collection`](collection.md) composition root.

```python
from pathlib import Path
from markdown_vault_mcp import Collection

collection = Collection(source_dir=Path("/path/to/vault"))
collection.index.build_index()

# Reader facet — search / read / list / metadata
results = collection.reader.search("query text", limit=10)
note = collection.reader.read("Journal/note.md")

# Writer facet — write / edit / delete / rename / attachments
collection.writer.write("Journal/new.md", "# New note")

# Graph facet — backlinks / outlinks / orphans / paths
backlinks = collection.graph.get_backlinks("Journal/note.md")

# Index facet — build / reindex / embeddings / readiness
collection.index.reindex()
```

## ReaderFacet

Search, read, listing, table-of-contents, similarity, context, history/diff,
and attachment reads.

::: markdown_vault_mcp.facets.reader.ReaderFacet

## WriterFacet

Create, edit, delete, rename, and attachment writes.

::: markdown_vault_mcp.facets.writer.WriterFacet

## GraphFacet

Backlinks, outlinks, broken links, orphans, most-linked notes, and connection
paths.

::: markdown_vault_mcp.facets.graph.GraphFacet

## IndexFacet

Index build / reindex / embeddings (sync + async), readiness, and writer
status.

::: markdown_vault_mcp.facets.index.IndexFacet
