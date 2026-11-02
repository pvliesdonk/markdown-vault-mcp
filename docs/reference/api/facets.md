---
description: "The reader, writer, graph and index facets that carry the library's operations."
kind: reference
---

# Facets

The read / write / graph / index operations live on four cohesive facets,
reached through the `reader` / `writer` / `graph` / `index` accessors on the
[`Vault`](vault.md) composition root.

```python { .run data-expect="results" }
from pathlib import Path
from markdown_vault_mcp.vault import Vault, VaultSettings

# The library is read-only by default; writes need read_only=False.
vault = Vault(source_dir=Path("/path/to/vault"), settings=VaultSettings(read_only=False))
vault.index.build_index()

# Reader facet — search / read / list / metadata
results = vault.reader.search("query text", limit=10)
note = vault.reader.read("Journal/note.md")

# Writer facet — write / edit / delete / rename / attachments
vault.writer.write("Journal/new.md", "# New note")

# Graph facet — backlinks / outlinks / orphans / paths
backlinks = vault.graph.get_backlinks("Journal/note.md")

# Index facet — build / reindex / embeddings / readiness
vault.index.reindex()
```

## ReaderFacet

Search, exact queries, read, listing, table-of-contents, similarity, context,
history/diff, revision reads, and attachment reads. Exact query methods check
library index readiness and raise `IndexUnavailableError` if unavailable; an
unavailable index is never an exact zero. They accept the [query types](types.md#exact-query-types)
and return the same projections as the [keyword backend](storage.md#exact-keyword-queries).

```python { .fragment }
from markdown_vault_mcp.query_types import NoteGrouping, NoteQuery

request = NoteQuery(query="release", scope="body", limit=20)
page = vault.reader.query_documents(request)
count = vault.reader.count_documents_matching(request)
grouped = vault.reader.aggregate_documents(request, NoteGrouping(by="folder"))
```

Path discovery uses the same indexed metadata and readiness gate. It reports
approximate coverage separately from output truncation:

```python { .fragment }
from markdown_vault_mcp.query_types import PathQuery

paths = vault.reader.search_paths(PathQuery(query="releaze", fuzzy=True))
# Inspect paths.coverage and paths.reasons before treating an empty list as absence.
```

Exact paths rank first. Literal prefix matches rank ahead of matches within a path;
both tiers use `str.casefold()` before comparing. Typo matches follow those tiers.
Approximation is ASCII-only, bounded to 10,000 candidates and 200 ms; output
defaults to 100 items (maximum 1000). Unicode retains direct matching with `str.casefold()`.

Invalid indexed fields raise `InvalidRequestError`. Existing ranked search
continues to support computed OKF filters.

<!-- vale off -->
::: markdown_vault_mcp.facets.reader.ReaderFacet
<!-- vale on -->

## WriterFacet

Create, edit, append, delete, rename, and attachment writes.

<!-- vale off -->
::: markdown_vault_mcp.facets.writer.WriterFacet
<!-- vale on -->

## GraphFacet

Backlinks, outlinks, broken links, orphans, most-linked notes, and connection
paths.

<!-- vale off -->
::: markdown_vault_mcp.facets.graph.GraphFacet
<!-- vale on -->

## IndexFacet

Index build / reindex / embeddings (sync + async), readiness, and writer
status.

<!-- vale off -->
::: markdown_vault_mcp.facets.index.IndexFacet
<!-- vale on -->
