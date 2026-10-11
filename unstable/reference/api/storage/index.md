# Storage

Every store the vault uses is opened by a named backend and closed by whoever opened it. The protocols are importable from `markdown_vault_mcp.interfaces`, the value types from `markdown_vault_mcp.types`, the backends from their modules under `markdown_vault_mcp.stores`.

```
from markdown_vault_mcp.interfaces import StoreBackend, StoreLifecycle, SourceProbe
from markdown_vault_mcp.stores.registry import get_backend
from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
```

## Opening a store

A backend carries its own configuration; `open()` takes only the location and whether this process owns the files. An open that fails raises one of five `StoreOpenError` subclasses (see [Exceptions](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/api/exceptions/#store-open-outcomes)); none of them means the store is empty.

## `StoreBackend`

Bases: `Protocol[StoreT_co]`

A named opener for one kind of store (#1766).

A backend carries its own configuration; :meth:`open` takes only what varies per vault: where the store is and whether this process owns its files. The registry in :mod:`markdown_vault_mcp.stores.registry` maps `(family, name)` to a backend factory.

### `family`

Which kind of store this backend opens.

### `name`

The registry name a configuration value selects.

### `open(location, identity=None, *, owns_files=True)`

Open the store at *location*.

Parameters:

| Name         | Type     | Description                                                                                   | Default                                                                                           |
| ------------ | -------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `location`   | \`Path   | str                                                                                           | None\`                                                                                            |
| `identity`   | \`object | None\`                                                                                        | The family's identity value, checked against what the store holds; None for a family without one. |
| `owns_files` | `bool`   | Whether this process owns the store's files. False opens read-only and forbids any self-heal. | `True`                                                                                            |

Returns:

| Type        | Description     |
| ----------- | --------------- |
| `StoreT_co` | The open store. |

Raises:

| Type             | Description                                       |
| ---------------- | ------------------------------------------------- |
| `StoreOpenError` | One of its five subclasses, never an empty store. |

## `get_backend(family, name)`

Return the factory registered under `(family, name)`.

Raises:

| Type          | Description                                                                               |
| ------------- | ----------------------------------------------------------------------------------------- |
| `LookupError` | If nothing is registered there; the message names the registered backends of that family. |

## `register_backend(family, name, factory)`

Register *factory* under `(family, name)`.

Registering the same factory twice is fine (a module imported twice); a different one under a taken key is a programming error.

Parameters:

| Name      | Type                               | Description                                       | Default    |
| --------- | ---------------------------------- | ------------------------------------------------- | ---------- |
| `family`  | `StoreFamily`                      | The store family.                                 | *required* |
| `name`    | `str`                              | The configuration value that selects the backend. | *required* |
| `factory` | `Callable[..., StoreBackend[Any]]` | What the caller constructs the backend with.      | *required* |

Raises:

| Type         | Description                                   |
| ------------ | --------------------------------------------- |
| `ValueError` | If the key holds a different factory already. |

## `backend_names(family)`

Return the registered names of *family*, sorted.

## `SqliteKeywordBackend(*, indexed_frontmatter_fields=None, searchable_frontmatter_fields=None, fts_weights=None)`

Open the keyword index SQLite serves, classifying what goes wrong.

The backend carries the index's configuration; :meth:`open` takes the location and whether this process owns the file. `None` or `":memory:"` opens an in-memory index, which is always this process's own and therefore writable whatever *owns_files* says (#1758).

Parameters:

| Name                            | Type               | Description | Default                                 |
| ------------------------------- | ------------------ | ----------- | --------------------------------------- |
| `indexed_frontmatter_fields`    | \`list[str]        | None\`      | Frontmatter keys indexed for filtering. |
| `searchable_frontmatter_fields` | \`list[str]        | None\`      | Frontmatter keys folded into search.    |
| `fts_weights`                   | \`dict[str, float] | None\`      | Per-column BM25 weights.                |

### `open(location, identity=None, *, owns_files=True)`

Open the index at *location*.

Parameters:

| Name         | Type     | Description                                                                 | Default                                        |
| ------------ | -------- | --------------------------------------------------------------------------- | ---------------------------------------------- |
| `location`   | \`Path   | str                                                                         | None\`                                         |
| `identity`   | \`object | None\`                                                                      | Must be None; a keyword store has no identity. |
| `owns_files` | `bool`   | False opens a file read-only (#1758) and is ignored for an in-memory index. | `True`                                         |

Returns:

| Type       | Description                                             |
| ---------- | ------------------------------------------------------- |
| `FTSIndex` | The open :class:~markdown_vault_mcp.fts_index.FTSIndex. |

Raises:

| Type                          | Description                              |
| ----------------------------- | ---------------------------------------- |
| `ValueError`                  | If identity is given.                    |
| `StoreUnavailableError`       | If the file cannot be opened or written. |
| `StoreCorruptError`           | If the file is not a SQLite database.    |
| `StoreUnsupportedSchemaError` | If its schema_version is newer.          |

## Lifecycle

## `StoreLifecycle`

Bases: `Protocol`

What every open store owes its owner (#1766).

Whoever opened a store closes it: the vault closes the stores its backends opened, a caller closes the store it passed in.

### `close()`

Release the store's resources. Idempotent; use after close raises.

### `checkpoint()`

Make committed state durable where that is a separate step.

A no-op for a store with nothing to flush: in memory, or opened read-only.

## Sources and revisions

## `SourceProbe`

Bases: `Protocol`

The one question the indexing side asks a source about an identity.

### `probe(path)`

Return whether *path* is present (with its token), absent or unavailable.

## `SourceState(status, token=None)`

The answer a :class:`~markdown_vault_mcp.interfaces.SourceProbe` gives.

Attributes:

| Name     | Type            | Description                                                       |
| -------- | --------------- | ----------------------------------------------------------------- |
| `status` | `SourceStatus`  | Whether the identity is present, absent, or could not be checked. |
| `token`  | \`RevisionToken | None\`                                                            |

Raises:

| Type         | Description                                                         |
| ------------ | ------------------------------------------------------------------- |
| `ValueError` | If present comes without a token, or another status comes with one. |
