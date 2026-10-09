---
description: "How stores are opened, owned and closed: the backend registry, the open outcomes and the lifecycle every store shares."
kind: reference
---

# Storage

Every store the vault uses, keyword today and vector from 6.0, is opened by a named backend and closed by whoever opened it. The protocols are importable from `markdown_vault_mcp.interfaces`, the value types from `markdown_vault_mcp.types`, the backends from their modules under `markdown_vault_mcp.stores`.

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

## Lifecycle

<!-- vale off -->
::: markdown_vault_mcp.interfaces.StoreLifecycle
<!-- vale on -->

## Sources and revisions

<!-- vale off -->
::: markdown_vault_mcp.interfaces.SourceProbe

::: markdown_vault_mcp.types.SourceState
<!-- vale on -->
