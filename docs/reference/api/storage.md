---
description: "How stores are opened, owned and closed: the backend registry, the open outcomes and the lifecycle every store shares."
kind: reference
---

# Storage

Every store the vault uses is opened by a named backend and closed by whoever opened it. The protocols are importable from `markdown_vault_mcp.interfaces`, the value types from `markdown_vault_mcp.types`, the backends from their modules under `markdown_vault_mcp.stores`.

```python { .fragment }
from markdown_vault_mcp.interfaces import StoreBackend, StoreLifecycle, SourceProbe
from markdown_vault_mcp.stores.registry import get_backend
from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
```

## Opening a store

A backend carries its own configuration; `open()` takes only the location and whether this process owns the files. An open that fails raises one of five `StoreOpenError` subclasses (see [Exceptions](exceptions.md#store-open-outcomes)); none of them means the store is empty.

<!-- vale off -->
::: markdown_vault_mcp.interfaces.StoreBackend

::: markdown_vault_mcp.stores.registry.get_backend

::: markdown_vault_mcp.stores.registry.register_backend

::: markdown_vault_mcp.stores.registry.backend_names

::: markdown_vault_mcp.stores.sqlite_keyword.SqliteKeywordBackend
<!-- vale on -->

## Vector stores

A vector store takes query vectors, not text: the caller embeds. Publication is conditional on the token the caller saw; `ANY_TOKEN` skips the comparison.

<!-- vale off -->
::: markdown_vault_mcp.interfaces.VectorReader

::: markdown_vault_mcp.interfaces.VectorPublisher

::: markdown_vault_mcp.interfaces.EmbeddingStore

::: markdown_vault_mcp.interfaces.VectorBackend

::: markdown_vault_mcp.types.EmbeddingIdentity

::: markdown_vault_mcp.types.VectorRow

::: markdown_vault_mcp.types.DocumentRows

::: markdown_vault_mcp.stores.numpy_sidecar.NumpySidecarBackend

::: markdown_vault_mcp.stores.numpy_sidecar.NumpySidecarStore
<!-- vale on -->

## Lifecycle

<!-- vale off -->
::: markdown_vault_mcp.interfaces.StoreLifecycle
<!-- vale on -->

## Sources and revisions

<!-- vale off -->
::: markdown_vault_mcp.interfaces.SourceProbe

::: markdown_vault_mcp.types.SourceState
<!-- vale on -->

## Exact keyword queries

An open `FTSIndex` can query the entire matching note population without
semantic retrieval or a top-k approximation. Its page size bounds output;
counts and aggregates always describe the full filtered population.

```python { .fragment }
from markdown_vault_mcp.query_types import NoteGrouping, NoteQuery

request = NoteQuery(query="release", scope="body", folder="projects", limit=20)
page = index.query_documents(request)
count = index.count_documents_matching(request)
by_tag = index.aggregate_documents(request, NoteGrouping(by="tag", field="tags"))
```

Only configured indexed frontmatter fields support exact filters and tag
grouping; invalid fields raise `InvalidRequestError`, also a `ValueError`. Computed OKF filters require existing ranked search. Modification
bounds are RFC3339 instants with a timezone, inclusive `modified_after` and
exclusive `modified_before`. Metadata scope omits body sections. A lexical
query containing NUL is invalid; other malformed FTS expressions fall back to
literal terms within the same scope. See [query types](types.md#exact-query-types)
for defaults and bounds. This additive library surface reads indexed data only.

<!-- vale off -->
::: markdown_vault_mcp.fts_index.FTSIndex.query_documents

::: markdown_vault_mcp.fts_index.FTSIndex.count_documents_matching

::: markdown_vault_mcp.fts_index.FTSIndex.aggregate_documents
<!-- vale on -->
