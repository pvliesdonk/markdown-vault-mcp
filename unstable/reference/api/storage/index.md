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

## Vector stores

A vector store takes query vectors, not text: the caller embeds. Publication is conditional on the token the caller saw; `ANY_TOKEN` skips the comparison.

## `VectorReader`

Bases: `Protocol`

Read a vector store: nearest rows, per-document rows, tokens (#1767).

One call observes one committed state; a document is never half visible. The store holds no embedding provider: callers embed queries.

### `identity`

The embedding space of the rows the store holds.

### `generation`

Bumped each time :meth:`VectorPublisher.publish_generation` lands.

### `count`

The number of stored rows.

### `search(vector, *, limit=10, predicate=None, folder=None)`

Return the rows nearest *vector*, most similar first.

Parameters:

| Name        | Type                                   | Description                                      | Default                                                                                       |
| ----------- | -------------------------------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `vector`    | `Sequence[float]`                      | The query embedding, of the store's dimension.   | *required*                                                                                    |
| `limit`     | `int`                                  | Maximum number of rows; zero or less returns []. | `10`                                                                                          |
| `predicate` | \`Callable\[\[dict[str, Any]\], bool\] | None\`                                           | Keeps only the rows it accepts, applied before limit.                                         |
| `folder`    | \`str                                  | None\`                                           | Keeps only rows in this folder or below it, applied before limit; a backend may push it down. |

Returns:

| Type                   | Description                                          |
| ---------------------- | ---------------------------------------------------- |
| `list[dict[str, Any]]` | Copies of the rows' metadata, each with a score: the |
| `list[dict[str, Any]]` | normalised dot product, 0.0 for a zero vector.       |

### `similar_to_document(path, *, limit=10, predicate=None, folder=None)`

Return the rows nearest the mean of *path*'s rows, excluding *path*.

Parameters:

| Name        | Type                                   | Description                                      | Default                                                                                       |
| ----------- | -------------------------------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `path`      | `str`                                  | The document whose rows are averaged.            | *required*                                                                                    |
| `limit`     | `int`                                  | Maximum number of rows; zero or less returns []. | `10`                                                                                          |
| `predicate` | \`Callable\[\[dict[str, Any]\], bool\] | None\`                                           | Keeps only the rows it accepts, applied before limit.                                         |
| `folder`    | \`str                                  | None\`                                           | Keeps only rows in this folder or below it, applied before limit; a backend may push it down. |

Returns:

| Name | Type                   | Description                            |
| ---- | ---------------------- | -------------------------------------- |
| `As` | `list[dict[str, Any]]` | meth:search; [] when path has no rows. |

### `rows_for(path)`

Return *path*'s stored rows, vectors normalised, in storage order.

### `tokens()`

Return each stored document's token; `None` when unknown.

## `VectorPublisher`

Bases: `Protocol`

Publish to a vector store, conditionally on the stored token (#1767).

### `replace_document(document, *, expected_token)`

Replace every row of `document.path` with `document.rows`.

Parameters:

| Name             | Type            | Description                                                                               | Default    |
| ---------------- | --------------- | ----------------------------------------------------------------------------------------- | ---------- |
| `document`       | `DocumentRows`  | The document's new rows and the token they came from.                                     | *required* |
| `expected_token` | `ExpectedToken` | The token the caller saw when it read the source; ANY_TOKEN publishes whatever is stored. | *required* |

Returns:

| Type                 | Description                                              |
| -------------------- | -------------------------------------------------------- |
| `PublicationOutcome` | "published", or "conflict" when the stored token differs |
| `PublicationOutcome` | from expected_token; nothing changes on a conflict.      |

### `remove_document(path, *, expected_token)`

Remove every row and the token of *path*, with the same condition.

### `publish_generation(identity, documents)`

Replace the whole store with *documents* under *identity*, in one step.

A reader sees the previous generation until this returns; an interrupted publication leaves the previous generation in place.

## `EmbeddingStore`

Bases: `VectorReader`, `VectorPublisher`, `StoreLifecycle`, `Protocol`

What a vector backend opens: read, publish and close (#1767).

## `VectorBackend`

Bases: `StoreBackend[EmbeddingStore]`, `Protocol`

A backend that opens vector stores, including a fresh one (#1777).

### `open_fresh(location, identity)`

Open *location* as an empty store at *identity*, reading nothing there.

What the location holds stays in place until the fresh store's first persist replaces it, so a rebuild that never completes leaves the previous data for the next open. Used to rebuild a store that would not open (corrupt) or holds another identity.

## `EmbeddingIdentity(provider, model, variant, text_format, dimension)`

The embedding space a vector store's rows belong to.

Two stores hold comparable vectors only when every field matches: the same provider, model and variant embed the same text format into the same number of dimensions.

Attributes:

| Name          | Type  | Description                                                                                                                                                           |
| ------------- | ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `provider`    | `str` | The provider's name (EmbeddingProvider.provider_name).                                                                                                                |
| `model`       | `str` | The model's name.                                                                                                                                                     |
| `variant`     | `str` | How the provider embeds (provider_variant), "" for most providers.                                                                                                    |
| `text_format` | `str` | The embedding-text format token (EmbedTextBuilder.format_token()).                                                                                                    |
| `dimension`   | `int` | Floats per vector; 0 when not known yet, in which case a store opened against the identity takes the dimension it has stored (and refuses writes until it knows one). |

### `of(provider, text_format)`

Return the identity of *provider* embedding *text_format* text.

Parameters:

| Name          | Type                | Description                      | Default    |
| ------------- | ------------------- | -------------------------------- | ---------- |
| `provider`    | `EmbeddingProvider` | The embedding provider.          | *required* |
| `text_format` | `str`               | The embedding-text format token. | *required* |

Returns:

| Type                | Description                                                   |
| ------------------- | ------------------------------------------------------------- |
| `EmbeddingIdentity` | The identity a store holding this provider's vectors records. |

## `VectorRow(vector, metadata)`

One stored chunk: its vector and the metadata returned with it.

Attributes:

| Name       | Type              | Description                                                          |
| ---------- | ----------------- | -------------------------------------------------------------------- |
| `vector`   | `Sequence[float]` | The embedding, as the provider returned it; the store normalises it. |
| `metadata` | `dict[str, Any]`  | Every key the store hands back with a search result.                 |

## `DocumentRows(path, token, rows)`

Every row of one document, with the source revision they came from.

Attributes:

| Name    | Type                  | Description                         |
| ------- | --------------------- | ----------------------------------- |
| `path`  | `str`                 | The document's vault-relative path. |
| `token` | \`RevisionToken       | None\`                              |
| `rows`  | `Sequence[VectorRow]` | The document's rows, possibly none. |

Raises:

| Type         | Description                                         |
| ------------ | --------------------------------------------------- |
| `ValueError` | If a row's metadata["path"] names another document. |

## `NumpySidecarBackend`

Open a :class:`NumpySidecarStore` at a base path, or in memory.

### `open(location, identity=None, *, owns_files=True)`

Open the store.

Parameters:

| Name         | Type     | Description                                     | Default                                                                         |
| ------------ | -------- | ----------------------------------------------- | ------------------------------------------------------------------------------- |
| `location`   | \`Path   | str                                             | None\`                                                                          |
| `identity`   | \`object | None\`                                          | The :class:~markdown_vault_mcp.types.EmbeddingIdentity the rows must belong to. |
| `owns_files` | `bool`   | False opens files read-only; ignored in memory. | `True`                                                                          |

Returns:

| Type                | Description     |
| ------------------- | --------------- |
| `NumpySidecarStore` | The open store. |

Raises:

| Type                          | Description                              |
| ----------------------------- | ---------------------------------------- |
| `ImportError`                 | If numpy is not installed.               |
| `ValueError`                  | If identity is not an EmbeddingIdentity. |
| `StoreUnavailableError`       | If the files cannot be reached.          |
| `StoreCorruptError`           | If they do not form a store.             |
| `StoreUnsupportedSchemaError` | If their format is newer.                |
| `StoreIncompatibleError`      | If they hold another identity.           |

### `open_fresh(location, identity)`

Open *location* as an empty store at *identity*, reading nothing there.

The next persist commits over whatever the location held, under a matrix name above every one already there.

Raises:

| Type          | Description                |
| ------------- | -------------------------- |
| `ImportError` | If numpy is not installed. |

## `NumpySidecarStore(snapshot, *, base, read_only, seq, matrix_path)`

Bases: `ResidentStore`

A vector store held in memory, optionally persisted beside a base path.

Construct through :class:`NumpySidecarBackend`.

### `replace_document(document, *, expected_token)`

Replace `document.path`'s rows; see :class:`~markdown_vault_mcp.interfaces.VectorPublisher`.

Raises:

| Type            | Description                                     |
| --------------- | ----------------------------------------------- |
| `ReadOnlyError` | If the store was opened read-only.              |
| `ValueError`    | If a row is of another dimension or not finite. |

### `remove_document(path, *, expected_token)`

Remove *path*'s rows and token, with the same condition.

Raises:

| Type            | Description                        |
| --------------- | ---------------------------------- |
| `ReadOnlyError` | If the store was opened read-only. |

### `publish_generation(identity, documents)`

Replace the store with *documents* under *identity*, durably.

Raises:

| Type            | Description                                                              |
| --------------- | ------------------------------------------------------------------------ |
| `ReadOnlyError` | If the store was opened read-only.                                       |
| `ValueError`    | If a path appears twice, or a row is of another dimension or not finite. |

### `checkpoint()`

Persist pending changes; a no-op in memory, read-only or clean.

Raises:

| Type           | Description             |
| -------------- | ----------------------- |
| `RuntimeError` | If the store is closed. |

### `close()`

Persist pending changes and release the store. Idempotent.

The store is closed afterwards even if the final persist raises.

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
