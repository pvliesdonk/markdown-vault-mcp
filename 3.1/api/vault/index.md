# Vault

The `Vault` class is the primary public API for the library. MCP tools, CLI commands, and direct integrations all go through this class. It is a thin composition root: the read / write / graph / index operations live on the four facets, reached through the `reader` / `writer` / `graph` / `index` accessors (see [Facets](https://pvliesdonk.github.io/markdown-vault-mcp/3.1/api/facets/index.md)).

## Quick Start

```
from pathlib import Path
from markdown_vault_mcp import Vault

# Basic read-only vault
vault = Vault(source_dir=Path("/path/to/vault"))
stats = vault.index.build_index()
print(f"Indexed {stats.documents_indexed} documents")

# Search (reader facet)
results = vault.reader.search("query text", limit=10)
for r in results:
    print(f"{r.path}: {r.title} (score: {r.score:.2f})")

# Read a document (reader facet)
note = vault.reader.read("Journal/note.md")
print(note.content)
```

## API Reference

## `Vault(*, source_dir, index_path=None, embeddings_path=None, embedding_provider=None, read_only=True, state_path=None, indexed_frontmatter_fields=None, required_frontmatter=None, chunk_strategy='heading', on_write=None, git_strategy=None, git_pull_interval_s=0, exclude_patterns=None, attachment_extensions=None, max_attachment_size_mb=1.0, max_note_read_bytes=262144, chunks_per_file=2, snippet_words=200, length_downweight_alpha=0.25, max_chunk_words=400, max_chunk_chars=None, max_chunk_chars_override=None, chunk_overlap_words=0)`

Facade over FTS5 index, vector index, and change tracker.

Instantiate once per vault root. The read / write / graph / index operations live on the four facets, reached through the :attr:`reader` / :attr:`writer` / :attr:`graph` / :attr:`index` accessors (e.g. `vault.reader.search(...)`); this class itself exposes only construction, those accessors, and lifecycle.

Callers must invoke :meth:`IndexFacet.build_index` before bucket-3 relational/FTS-backed queries (:meth:`GraphFacet.get_backlinks`, :meth:`GraphFacet.get_outlinks`, :meth:`ReaderFacet.get_similar`, :meth:`ReaderFacet.get_context`, :meth:`GraphFacet.get_connection_path`, :meth:`ReaderFacet.get_toc`) or the bucket-4 coordinators :meth:`IndexFacet.reindex` and :meth:`IndexFacet.build_embeddings`; otherwise :exc:`~markdown_vault_mcp.exceptions.IndexUnavailableError` is raised. :meth:`IndexFacet.build_index` must also precede :meth:`start` — see :meth:`start` for the rationale. Bucket-1 file operations (:meth:`ReaderFacet.read`, :meth:`WriterFacet.write`, :meth:`WriterFacet.edit`, :meth:`WriterFacet.delete`, :meth:`WriterFacet.rename`, :meth:`WriterFacet.write_attachment`) and bucket-2 aggregate queries (:meth:`ReaderFacet.search`, :meth:`ReaderFacet.list_documents`, :meth:`ReaderFacet.stats`, …) work on an unbuilt index — bucket-1 hits disk directly; bucket-2 returns whatever is currently in the index (empty on cold start). See issue #525.

**Index lifecycle (issues #513, #526, #559).** The MCP server lifespan submits a :class:`~markdown_vault_mcp.indexing.BuildIndex` job to the single-owner :class:`~markdown_vault_mcp.indexing.IndexWriter` via :meth:`IndexFacet.build_index_async` and yields immediately. On a warm restart the persisted FTS completeness sentinel (PR #526) causes :meth:`IndexFacet.build_index_async` to return an already-resolved `Future` in O(1) without touching the writer queue. On a cold restart the writer thread runs the job asynchronously while the lifespan yields; bucket-3/4 MCP tool *clients* block on the :class:`markdown_vault_mcp._server_queryable.needs_queryable` decorator, which calls :meth:`IndexFacet.wait_until_queryable` with a bounded default timeout (`MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S`, default 60s). The library stays honest: bucket-3/4 *methods* keep the PR #525 raise-immediately contract via :meth:`_require_built`. Internal callers (lifespan, git pull loop, CLI, direct library users) get the raise contract and handle "not ready" with caller-appropriate logic — never block.

**Thread safety (issue #519):** every facet operation and lifecycle method is safe to call from any thread, concurrently with other reads and writes from any other thread. Index mutations (FTS + vector index) are serialised by the single-owner :class:`~markdown_vault_mcp.indexing.IndexWriter` thread (#559); file-mutation operations on disk are serialised via `_file_write_lock` (RLock) so two MCP write tools racing on the same path do not tear. `close()` is safe from any thread; after `close()` the vault must not be used. Cross-method atomicity (e.g. read-then-write without intervening concurrent write) is the caller's responsibility — pass `if_match=` to write methods for optimistic concurrency. `fork()` is not supported. See `docs/design/design.md` "Vault thread-safety contract" for the underlying per-thread SQLite-connection model.

Parameters:

| Name                         | Type                | Description                                                                                                                                              | Default                                                                                                                                                  |
| ---------------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_dir`                 | `Path`              | Root directory of the markdown vault.                                                                                                                    | *required*                                                                                                                                               |
| `index_path`                 | \`Path              | None\`                                                                                                                                                   | Path to the SQLite index file. None (default) uses an in-memory database that is discarded when the object is collected.                                 |
| `embeddings_path`            | \`Path              | None\`                                                                                                                                                   | Base path for the {path}.npy and {path}.json sidecar files. None (default) means semantic search is disabled.                                            |
| `embedding_provider`         | \`EmbeddingProvider | None\`                                                                                                                                                   | Provider used to generate embeddings. Required when embeddings_path is set.                                                                              |
| `read_only`                  | `bool`              | When True (default), write operations raise :exc:~markdown_vault_mcp.exceptions.ReadOnlyError.                                                           | `True`                                                                                                                                                   |
| `state_path`                 | \`Path              | None\`                                                                                                                                                   | Path to the hash-state JSON file used by :class:~markdown_vault_mcp.tracker.ChangeTracker. Defaults to {source_dir}/.markdown_vault_mcp/state.json.      |
| `indexed_frontmatter_fields` | \`list[str]         | None\`                                                                                                                                                   | Frontmatter keys whose values are promoted to the document_tags table for structured filtering.                                                          |
| `required_frontmatter`       | \`list[str]         | None\`                                                                                                                                                   | If provided, documents missing any listed field are excluded from the index entirely.                                                                    |
| `chunk_strategy`             | \`str               | ChunkStrategy\`                                                                                                                                          | "heading" (default), "whole", or a custom :class:~markdown_vault_mcp.scanner.ChunkStrategy instance.                                                     |
| `on_write`                   | \`WriteCallback     | None\`                                                                                                                                                   | Optional callback invoked after every successful write operation. Signature: Callable\[\[Path, str, Literal["write","edit","delete","rename"]\], None\]. |
| `git_strategy`               | \`GitWriteStrategy  | None\`                                                                                                                                                   | Optional git strategy used for background git tasks (e.g. periodic fetch + ff-only updates). Started via :meth:start.                                    |
| `git_pull_interval_s`        | `int`               | Interval in seconds for periodic pulls. 0 disables the pull loop.                                                                                        | `0`                                                                                                                                                      |
| `exclude_patterns`           | \`list[str]         | None\`                                                                                                                                                   | Glob patterns (relative to source_dir) for files and directories to exclude from indexing.                                                               |
| `attachment_extensions`      | \`list[str]         | None\`                                                                                                                                                   | Allowlist of extensions (without leading dot) for binary attachments. ["\*"] accepts all extensions.                                                     |
| `max_attachment_size_mb`     | `float`             | Attachment context-size cap in megabytes, enforced by the read / write / fetch MCP tools (not by the vault library). 0 disables the limit (default 1.0). | `1.0`                                                                                                                                                    |
| `max_note_read_bytes`        | `int`               | Maximum bytes returned by full-document reads. 0 disables the limit (default 262144, i.e. 256 KB).                                                       | `262144`                                                                                                                                                 |

### `reader`

Read-only facet: search, read, list, toc, similar, stats, history.

### `writer`

Document-mutation facet: write, edit, delete, rename, attachments.

### `graph`

Link-graph facet: backlinks, outlinks, broken, orphans, paths.

### `index`

Index facet: build/reindex/embeddings, readiness, writer status.

### `source_dir`

The vault's root directory.

### `max_attachment_size_mb`

The attachment context-size cap in MB (`0` = unlimited).

Enforced by the `read` / `write` / `fetch` MCP tools, not by the vault library itself.

### `pause_writes()`

Block file-mutation write operations until the context exits.

Holds the :attr:`_file_write_lock` so concurrent :class:`DocumentManager` write/edit/delete/rename calls block on the lock until the context exits. Index mutations on the :class:`IndexWriter` thread continue unaffected — the writer thread does not contend on this lock. Reads and search remain unblocked at the Python level.

### `sync_from_remote_before_index()`

One-time git fetch + ff-only update before build_index().

Intended to run during server startup before the initial index build. No reindex is triggered here because build_index() will scan the updated working tree.

### `start()`

Start background tasks for this Vault (e.g. git pull loop).

Call :meth:`IndexFacet.build_index` **before** :meth:`start`. The git pull loop wires :meth:`IndexFacet.reindex` (bucket 4) as its `on_pull` callback, and `reindex` raises :exc:`IndexUnavailableError` on an unbuilt index — so a pull event firing before the initial build would crash the loop thread.

### `force_pull()`

Pull from the git remote synchronously.

Thin public facade over :meth:`GitWriteStrategy.force_pull` used by the GitHub webhook handler so the strategy stays an implementation detail.

The strategy self-quiesces around its own merge: it pauses new writes (via the :meth:`pause_writes` callable wired in :meth:`__init__` through `set_write_quiescer`) and drains the deferred-commit queue before the merge, so a write that landed just before the pull is committed first and the merge runs on a clean tree (#571). This facade therefore no longer wraps `pause_writes` itself.

Returns:

| Type         | Description |
| ------------ | ----------- |
| \`PullResult | None\`      |
| \`PullResult | None\`      |

### `stop()`

Stop background tasks (e.g. git pull loop) without closing the vault.

Safe to call multiple times. A no-op if no pull loop was started. The SQLite connection and write callback remain open; only the pull loop thread is signalled to stop.

### `close()`

Release resources held by the vault.

Flushes deferred embeddings and pending write callbacks, then closes the SQLite connection and git strategy.
