# Vault

The `Vault` class is the primary public API for the library. MCP tools, CLI commands, and direct integrations all go through this class. It is a thin composition root: the read / write / graph / index operations live on the four facets, reached through the `reader` / `writer` / `graph` / `index` accessors (see [Facets](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/api/facets/index.md)).

## Quick Start

Construction uses `VaultSettings`: pass `source_dir` plus a `VaultSettings` carrying the configuration knobs. Collaborator objects (`embedding_provider`, `summarizer`, `git_strategy`, `on_write`, and `chunk_strategy`) stay explicit keywords.

```
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

```
vault = Vault(
    source_dir=root,
    settings=VaultSettings(read_only=False, index_path=index),
)
```

Old keywords now raise `TypeError`, including when their values match the defaults or when `settings` is also supplied. `source_dir` and the five collaborator keywords remain on `Vault`.

Omitting `settings` (or passing `None`) uses `VaultSettings()`. Library defaults are unchanged: read-only, no chunk overlap, and no overwrite protection once writes are enabled. Server configuration continues to use its own defaults through [configuration assembly](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/api/config/index.md).

## Build completion

Synchronous, asynchronous, and legacy background builds use one build lifecycle. The Future from `vault.index.build_index_async()` completes after the scan, completion-marker write, and readiness update. Marker-write failures raise through this Future. Pending builds can be cancelled; running builds finish normally. The legacy `start_background_build_index()` method schedules work once on the same writer.

## Index freshness after writes

File writes complete before returning; index updates run on the background writer. For a following library search or graph read, use `vault.index.wait_for_drain(timeout=60)` and check whether it returns `True`. A disk `read` does not need this wait.

Link conversion, index generation, rename with `update_links=True`, and folder move perform their own queued refresh before reading index data. They fail before mutation if that refresh fails or exceeds 60 seconds. A timeout raised by the refresh job propagates as that job error; the generic refresh-timeout message is reserved for expiration of the wait budget. The wait covers prior writes; it does not isolate concurrent edits. The same 60-second budget covers the preceding build and its readiness update, including synchronous and background builds. A failed or cancelled build blocks these mutations; an index with no scheduled build still supports rename and folder move. OKF generators still reject an index that was never built. Healthy notes continue to receive vector updates when another note in their refresh batch fails. Retrying that batch reuses matching stored vectors, avoiding repeated provider requests for unchanged notes. Direct `DocumentManager` integrations can supply the `sync_index` callback to provide the same boundary.

## API Reference

## `VaultSettings(index_path=None, embeddings_path=None, read_only=True, write_protect_existing=False, state_path=None, indexed_frontmatter_fields=None, required_frontmatter=None, git_pull_interval_s=0, exclude_patterns=None, attachment_extensions=None, max_attachment_size_mb=1.0, max_note_read_bytes=262144, chunks_per_file=2, snippet_words=200, length_downweight_alpha=0.25, default_search_mode='auto', max_chunk_words=400, max_chunk_chars=None, max_chunk_chars_override=None, chunk_overlap_words=0, summarize_max_notes=50, summarize_max_input_chars=200000, title_field='title', searchable_frontmatter_fields=None, embed_context=False, embedding_batch_size=4, folder_weights=None, fts_weights=None, conventions_file='_conventions.md', okf_mode='auto', okf_write=False)`

Config-derived construction settings for a :class:`~markdown_vault_mcp.vault.Vault`.

One field per configuration knob, preserving the names, types, and defaults of the constructor keywords removed in #1225. Construct directly for library use, or from a served config via :meth:`from_project_config` / :func:`~markdown_vault_mcp.config_sections._assembly.to_vault_settings`.

Attributes:

| Name                            | Type               | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `index_path`                    | \`Path             | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `embeddings_path`               | \`Path             | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `read_only`                     | `bool`             | When True (default), write operations raise :exc:~markdown_vault_mcp.exceptions.ReadOnlyError. This library default deliberately stays True even though the server's MARKDOWN_VAULT_MCP_READ_ONLY now defaults to False (#1113). They are separate tiers: the operator default is a product decision about what an installed server should do, while this one is a fail-safe for a downstream Python consumer who constructs a Vault without naming the argument. Keeping it costs nothing — the server path always passes the value explicitly through to_vault_settings — and moving it would be an independent breaking change to the public library interface. |
| `write_protect_existing`        | `bool`             | When True, a write that would overwrite an existing file without an if_match etag raises :exc:~markdown_vault_mcp.exceptions.DocumentExistsError (default False, i.e. writes overwrite unconditionally).                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `state_path`                    | \`Path             | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `indexed_frontmatter_fields`    | \`Sequence[str]    | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `required_frontmatter`          | \`Sequence[str]    | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `git_pull_interval_s`           | `int`              | Interval in seconds for periodic pulls. 0 disables the pull loop.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `exclude_patterns`              | \`Sequence[str]    | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `attachment_extensions`         | \`Sequence[str]    | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `max_attachment_size_mb`        | `float`            | Attachment context-size cap in megabytes, enforced by the read / write / fetch MCP tools (not by the vault library). 0 disables the limit (default 1.0).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `max_note_read_bytes`           | `int`              | Maximum bytes returned by full-document reads. 0 disables the limit (default 262144, i.e. 256 KB).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `chunks_per_file`               | `int`              | Search results kept per file before grouping.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `snippet_words`                 | `int`              | Snippet truncation length in words.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `length_downweight_alpha`       | `float`            | Length-downweight exponent for ranking.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `default_search_mode`           | `str`              | Mode used when search gets no mode.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `max_chunk_words`               | `int`              | Word cap for the default heading chunker.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `max_chunk_chars`               | \`int              | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `max_chunk_chars_override`      | \`int              | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `chunk_overlap_words`           | `int`              | Overlap carried between split chunks.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `summarize_max_notes`           | `int`              | Cap on notes summarised per summarize call (subtree expansion is truncated to this many notes; default 50).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `summarize_max_input_chars`     | `int`              | Aggregate cap on note characters sent to the summarization backend per call (default 200000).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `title_field`                   | `str`              | Frontmatter key consulted first when resolving document titles (default "title"; falls back to title → first H1 → filename stem).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `searchable_frontmatter_fields` | \`Sequence[str]    | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `embed_context`                 | `bool`             | When True, forces format v2 (document title + chunk heading enrichment) even with no searchable_frontmatter_fields; any searchable field also activates v2 (default False — raw chunk content).                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `embedding_batch_size`          | `int`              | Maximum number of chunk texts sent to the embedding provider per call in the cold-build, convergence, and inline-reindex paths (default 4).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `folder_weights`                | \`dict[str, float] | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `fts_weights`                   | \`dict[str, float] | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `conventions_file`              | \`str              | None\`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `okf_mode`                      | `str`              | OKF (Open Knowledge Format) read-semantics mode — "auto" (default; follow the vault's okf_version declaration in the root index.md), "off", or "on". When read semantics are active at construction time, the OKF scalar keys (type / status / stale_after) extend the effective indexed_frontmatter_fields set. See :attr:~markdown_vault_mcp.vault.Vault.okf.                                                                                                                                                                                                                                                                                                    |
| `okf_write`                     | `bool`             | Enable the OKF enforced-write layer.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

### `from_project_config(config, *, embedding_context_length=None)`

Map a served :class:`ProjectConfig` onto vault settings.

Maps server configuration names onto library setting names (`searchable_frontmatter` → `searchable_frontmatter_fields`, `default_mode` → `default_search_mode`) and the weight-map tuple → dict conversions (#639). The git pull interval resolves the same way the git-strategy assembly does: `config.git.pull_interval_s` only when a remote is configured (repo URL or token), else `0`.

Parameters:

| Name                       | Type            | Description                       | Default                                                                                                                                                                                                                                                                                      |
| -------------------------- | --------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `config`                   | `ProjectConfig` | The served project configuration. | *required*                                                                                                                                                                                                                                                                                   |
| `embedding_context_length` | \`int           | None\`                            | Token context length of the resolved embedding provider, used to derive max_chunk_chars (None — no provider — falls back to the bounded ceiling). Prefer :func:~markdown_vault_mcp.config_sections.\_assembly.to_vault_settings, which resolves the provider and threads this automatically. |

Returns:

| Type            | Description                      |
| --------------- | -------------------------------- |
| `VaultSettings` | The mapped :class:VaultSettings. |

### `effective_indexed_fields(*, okf_active)`

Return the indexed-frontmatter set, OKF-extended when active.

When OKF read semantics are active at construction time, the OKF scalar keys (`type` / `status` / `stale_after`) join the configured set so `document_tags` carries them (design okf.md §3).

Parameters:

| Name         | Type   | Description                                                            | Default    |
| ------------ | ------ | ---------------------------------------------------------------------- | ---------- |
| `okf_active` | `bool` | Whether the vault's OKF detection probe reports active read semantics. | *required* |

Returns:

| Type        | Description                                            |
| ----------- | ------------------------------------------------------ |
| `list[str]` | The effective field list (possibly empty, never None). |

### `effective_exclude_patterns()`

Return the exclusion patterns with the conventions-file derivation.

When a conventions file is configured, both fnmatch forms are appended (`name` and `**/name` — the `**/` form alone does not match a root-level file) so convention files stay out of the index while remaining disk-readable. Without one, the configured patterns pass through unchanged (possibly `None`).

Returns:

| Type            | Description |
| --------------- | ----------- |
| \`Sequence[str] | None\`      |
| \`Sequence[str] | None\`      |

Raises:

| Type         | Description                                                                             |
| ------------ | --------------------------------------------------------------------------------------- |
| `ValueError` | If conventions_file contains fnmatch metacharacters (which would invert the exclusion). |

### `effective_state_path(source_dir)`

Return the hash-state path, defaulting under the vault root.

Parameters:

| Name         | Type   | Description                                                     | Default    |
| ------------ | ------ | --------------------------------------------------------------- | ---------- |
| `source_dir` | `Path` | The vault root, used when no explicit state_path is configured. | *required* |

Returns:

| Type   | Description                                  |
| ------ | -------------------------------------------- |
| `Path` | The explicit state_path, or                  |
| `Path` | {source_dir}/.markdown_vault_mcp/state.json. |

## `Vault(*, source_dir, settings=None, embedding_provider=None, summarizer=None, git_strategy=None, on_write=None, chunk_strategy='heading')`

Facade over FTS5 index, vector index, and change tracker.

Instantiate once per vault root. The read / write / graph / index operations live on the four facets, reached through the :attr:`reader` / :attr:`writer` / :attr:`graph` / :attr:`index` accessors (e.g. `vault.reader.search(...)`); this class itself exposes only construction, those accessors, and lifecycle.

Callers must invoke :meth:`IndexFacet.build_index` before bucket-3 relational/FTS-backed queries (:meth:`GraphFacet.get_backlinks`, :meth:`GraphFacet.get_outlinks`, :meth:`ReaderFacet.get_similar`, :meth:`ReaderFacet.get_context`, :meth:`GraphFacet.get_connection_path`, :meth:`ReaderFacet.get_toc`) or the bucket-4 coordinators :meth:`IndexFacet.reindex` and :meth:`IndexFacet.build_embeddings`; otherwise :exc:`~markdown_vault_mcp.exceptions.IndexUnavailableError` is raised. :meth:`IndexFacet.build_index` must also precede :meth:`start` — see :meth:`start` for the rationale. Bucket-1 file operations (:meth:`ReaderFacet.read`, :meth:`WriterFacet.write`, :meth:`WriterFacet.edit`, :meth:`WriterFacet.delete`, :meth:`WriterFacet.rename`, :meth:`WriterFacet.write_attachment`) and bucket-2 aggregate queries (:meth:`ReaderFacet.search`, :meth:`ReaderFacet.list_documents`, :meth:`ReaderFacet.stats`, …) work on an unbuilt index — bucket-1 hits disk directly; bucket-2 returns whatever is currently in the index (empty on cold start). See issue #525.

**Index lifecycle (issues #513, #526, #559).** The MCP server lifespan submits a :class:`~markdown_vault_mcp.indexing.BuildIndex` job to the single-owner :class:`~markdown_vault_mcp.indexing.IndexWriter` via :meth:`IndexFacet.build_index_async` and yields immediately. On a warm restart the persisted FTS completeness sentinel (PR #526) causes :meth:`IndexFacet.build_index_async` to return an already-resolved `Future` in O(1) without touching the writer queue. On a cold restart the writer thread runs the job asynchronously while the lifespan yields; bucket-3/4 MCP tool *clients* block on the :class:`markdown_vault_mcp._server_queryable.needs_queryable` decorator, which calls :meth:`IndexFacet.wait_until_queryable` with a bounded default timeout (`MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S`, default 60s). The library stays honest: bucket-3/4 *methods* keep the PR #525 raise-immediately contract via :meth:`_require_built`. Internal callers (lifespan, git pull loop, CLI, direct library users) get the raise contract and handle "not ready" with caller-appropriate logic — never block.

**Thread safety (issue #519):** every facet operation and lifecycle method is safe to call from any thread, concurrently with other reads and writes from any other thread. Index mutations (FTS + vector index) are serialised by the single-owner :class:`~markdown_vault_mcp.indexing.IndexWriter` thread (#559); file-mutation operations on disk are serialised via `_file_write_lock` (RLock) so two MCP write tools racing on the same path do not tear. `close()` is safe from any thread; after `close()` the vault must not be used. Cross-method atomicity (e.g. read-then-write without intervening concurrent write) is the caller's responsibility — pass `if_match=` to write methods for optimistic concurrency. `fork()` is not supported. See `docs/design/design.md` "Vault thread-safety contract" for the underlying per-thread SQLite-connection model.

**Construction (#1225).** Pass `source_dir` and optional :class:`~markdown_vault_mcp.config_sections.vault_settings.VaultSettings`. All configuration knobs belong on `settings`; the five collaborators remain explicit keyword arguments. Omitting settings uses `VaultSettings()`: read-only, with no chunk overlap. These library defaults remain independent of the server's operator defaults.

Parameters:

| Name                 | Type                | Description                           | Default                                                                                              |
| -------------------- | ------------------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `source_dir`         | `Path`              | Root directory of the markdown vault. | *required*                                                                                           |
| `settings`           | \`VaultSettings     | None\`                                | Configuration settings. None uses VaultSettings().                                                   |
| `embedding_provider` | \`EmbeddingProvider | None\`                                | Provider used to generate embeddings; required when settings.embeddings_path is set.                 |
| `summarizer`         | \`Summarizer        | None\`                                | Optional summarization backend. Without one the :attr:summarizer accessor raises.                    |
| `git_strategy`       | \`VersionedStore    | None\`                                | Optional strategy for background Git tasks, started via :meth:start.                                 |
| `on_write`           | \`WriteCallback     | None\`                                | Callback invoked after successful writes; see :obj:~markdown_vault_mcp.types.WriteCallback.          |
| `chunk_strategy`     | \`str               | ChunkStrategy\`                       | "heading" (default), "whole", or a custom :class:~markdown_vault_mcp.scanner.ChunkStrategy instance. |

### `reader`

Read-only facet: search, read, list, toc, similar, stats, history.

### `writer`

Document-mutation facet: write/edit/append/delete/rename/attachments.

### `graph`

Link-graph facet: backlinks, outlinks, broken, orphans, paths.

### `index`

Index facet: build/reindex/embeddings, readiness, writer status.

### `summarizer`

Summarize facet: LLM-backed note/subtree summarization.

Raises:

| Type           | Description                                                                                                                         |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `RuntimeError` | If no summarization backend was configured (set OPENAI_API_KEY or an OpenAI-compatible base URL and install the [summarize] extra). |

### `conventions`

Folder-conventions resolver (disk-read, index-independent).

### `okf`

OKF detection probe (disk-read, index-independent).

### `exclude_patterns`

The effective exclusion patterns, including derived ones.

Contains the configured patterns plus the conventions-file patterns derived in `__init__` — the list the scanner, reconcile, and incremental index paths actually enforce.

### `source_dir`

The vault's root directory.

### `max_attachment_size_mb`

The attachment context-size cap in MB (`0` = unlimited).

Enforced by the `read` / `write` / `fetch` MCP tools, not by the vault library itself.

### `pause_writes()`

Block file-mutation write operations until the context exits.

Holds the :attr:`_file_write_lock` so concurrent :class:`DocumentManager` document-mutation calls block on the lock until the context exits. Index mutations on the :class:`IndexWriter` thread continue unaffected — the writer thread does not contend on this lock. Reads and search remain unblocked at the Python level.

### `sync_from_remote_before_index()`

One-time git fetch + ff-only update before build_index().

Intended to run during server startup before the initial index build. No reindex is triggered here: on a cold start build_index() will scan the updated working tree, but on a warm restart build_index_async() short-circuits in O(1) on the existing FTS sentinel and scans nothing. In that case the boot reindex (gated by `config.boot_reindex`, see #1535) is what actually indexes the tree this pull just updated — with it disabled, the pulled content stays unindexed until a later pull moves HEAD.

### `start()`

Start background tasks for this Vault (e.g. git pull loop).

Call :meth:`IndexFacet.build_index` **before** :meth:`start`. The git pull loop wires :meth:`IndexFacet.reindex` (bucket 4) as its `on_pull` callback, and `reindex` raises :exc:`IndexUnavailableError` on an unbuilt index — so a pull event firing before the initial build would crash the loop thread.

### `force_pull()`

Pull from the git remote synchronously.

Thin public facade over :meth:`~markdown_vault_mcp.git.Syncer.force_pull` used by the GitHub webhook handler so the store stays an implementation detail.

The strategy self-quiesces around its own merge: it pauses new writes (via the :meth:`pause_writes` callable wired in :meth:`__init__` through `set_write_quiescer`) and drains the deferred-commit queue before the merge, so a write that landed just before the pull is committed first and the merge runs on a clean tree (#571). This facade therefore no longer wraps `pause_writes` itself.

Returns:

| Type         | Description |
| ------------ | ----------- |
| \`PullResult | None\`      |
| \`PullResult | None\`      |

### `stop()`

Stop background tasks (e.g. git pull loop) without closing the vault.

Safe to call multiple times. A no-op if no pull loop was started. The SQLite connection and write callback remain open; only the pull loop thread is signalled to stop.

### `end_commit_scope(scope)`

Close a tool call's commit scope, grouping its writes into one commit.

Called by :class:`~markdown_vault_mcp._commit_scope.CommitScopeMiddleware` when a tool call returns. Never blocks: the marker is queued behind the writes that call fired, and the dispatcher flushes it in order.

Parameters:

| Name    | Type          | Description         | Default    |
| ------- | ------------- | ------------------- | ---------- |
| `scope` | `CommitScope` | The scope to close. | *required* |

### `close()`

Release resources held by the vault.

Flushes deferred embeddings and pending write callbacks, then closes the SQLite connection and git strategy.
