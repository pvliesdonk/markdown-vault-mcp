# markdown-vault-mcp: Design Specification v2

> Generic markdown vault MCP server with FTS5 + semantic search,
> frontmatter-aware indexing, and incremental reindexing. Extracted from
> and replacing the search layer in `pvliesdonk/if-craft-corpus`.

## Terminology

This spec uses the following terms consistently:

- **Document**: a single `.md` file in the vault. The primary term used
  throughout this spec.
- **Folder**: a subdirectory within `source_dir`, represented as a
  `/`-separated relative path (e.g., `Journal/2024`). The root of `source_dir`
  is represented as an empty string `""`.
- **Chunk**: a portion of a document, typically a section under a heading.
  Stored in the `sections` database table.
- **Tag**: a key-value pair from document frontmatter, stored in the
  `document_tags` table for indexed filtering.

In code: `ParsedNote` (scanner output), `Chunk` (section of a document),
`sections` (database table name).

## Problem

`pvliesdonk/if-craft-corpus` has a well-tested search stack (FTS5 + vector
embeddings + FastMCP server) that is coupled to the IF corpus domain. The same
infrastructure is needed for serving an Obsidian vault (or any directory of
markdown files) over MCP. Rather than duplicating code, extract the generic
layer into a reusable package. The corpus becomes just one instance; a personal
vault becomes another.

## Use Cases

1. **Obsidian vault** (`pvliesdonk/obsidian.md`, private): personal knowledge
   base served over MCP with read/write support and optional git-backed sync.
2. **IF Craft Corpus** (`pvliesdonk/if-craft-corpus`): read-only curated
   vault with domain-specific tools, strict frontmatter requirements.
3. **Python library**: direct use as a search API (e.g., wrapped as a LangChain
   tool by downstream projects like QuestFoundry). The `Vault` class is
   the primary interface; MCP is one consumer, not the only one. Other
   frameworks (LangChain, LlamaIndex, etc.) may wrap `Vault` directly.

## Shared Infrastructure

Generic FastMCP infrastructure (auth providers, middleware stack, logging
bootstrap, server-factory helpers, artifact store, CLI helpers) lives in the
`fastmcp-pvl-core` PyPI package. markdown-vault-mcp composes this library
via `ServerConfig` (never inheritance) and imports the building blocks
directly — see `make_server()` in `src/markdown_vault_mcp/server.py` for the
assembled call graph.

Design spec: `docs/superpowers/specs/2026-04-20-fastmcp-core-and-copier-template-design.md`.
Adoption plan: `docs/superpowers/plans/2026-04-20-fastmcp-pvl-core-extraction.md`.

## Architecture

Two packages, one dependency edge (eventual):

```
markdown-vault-mcp (new package)
+-- scanner.py        -- file discovery, frontmatter parsing, chunking
+-- fts_index.py      -- SQLite FTS5 schema, BM25 search
+-- vector_index.py   -- numpy embeddings, cosine similarity
+-- providers.py      -- Ollama / OpenAI / SentenceTransformers
+-- tracker.py        -- hash-based change detection
+-- vault.py          -- thin composition root: lifecycle, wiring, facet accessors (index-write → indexing/coordinator.py)
+-- write_callback.py -- WriteCallbackDispatcher: deferred git-commit callback worker (#599)
+-- config.py         -- configuration loading
+-- config_sections/  -- domain-grouped sub-configs (git/indexing/embeddings/search/sync/content)
+-- server.py         -- generic FastMCP server
+-- cli.py            -- CLI entry point
+-- utils/
|   +-- text.py       -- text normalization and fuzzy matching
|   +-- links.py      -- link target computation and replacement
+-- managers/
|   +-- link.py       -- LinkManager: backlinks, outlinks, broken, orphans, hubs, paths
|   +-- search.py     -- SearchManager: keyword/semantic/hybrid search, list, context, stats
|   +-- index.py      -- IndexManager: build_index, reindex, embeddings, flush
|   +-- document.py   -- DocumentManager: CRUD, attachments, path validation
|   +-- git_query.py  -- GitQueryManager: git history/diff reads (#610)
+-- indexing/
|   +-- index_writer.py -- IndexWriter: single-owner FIFO writer thread + jobs/runners
|   +-- readiness.py  -- ReadinessState: build-readiness state machine (#576)
|   +-- coordinator.py -- IndexWriteCoordinator: writer + build/async orchestration (#576)
+-- facets/
|   +-- reader.py     -- ReaderFacet: search/read/list/toc/similar/context/stats/history (#604)
|   +-- writer.py     -- WriterFacet: write/edit/delete/rename/attachments (#604)
|   +-- graph.py      -- GraphFacet: backlinks/outlinks/broken/orphans/most-linked/paths (#604)
|   +-- index.py      -- IndexFacet: thin wrapper over the coordinator (#604)

ifcraftcorpus (existing, refactored later)
+-- depends on markdown-vault-mcp
+-- ships corpus/ content
+-- adds domain-specific tools (search_exemplars, list_exemplar_tags)
+-- adds subagent prompts
+-- thin wrapper: configures Vault with required_frontmatter
```

**ifcraftcorpus stays as-is** during markdown-vault-mcp development. No changes to
the existing package until a complete refactor after markdown-vault-mcp is stable.

## Reference Code

All code below lives in `pvliesdonk/if-craft-corpus`. Read these files for
implementation patterns:

| File | Reuse Strategy | Notes |
|------|----------------|-------|
| `providers.py` | **Copy + adapt** | Rename env var prefix `IFCRAFTCORPUS_` to `MARKDOWN_VAULT_MCP_`. Fix hardcoded imports. |
| `embeddings.py` | **Copy + adapt** | Rename to `vector_index.py`. The `load()` classmethod contains a hardcoded `from ifcraftcorpus.providers import get_embedding_provider` -- this **must** be changed to `from markdown_vault_mcp.providers import get_embedding_provider` or it will raise `ImportError` at runtime. |
| `search.py` | **Adapt** | Pattern for `Vault` facade. Replace domain methods with generic API. |
| `index.py` | **Adapt** | Pattern for `fts_index.py`. Replace corpus-specific schema. Fix hybrid score bug (see RRF section). |
| `parser.py` | **Replace** | Replace with generic frontmatter + heading-based chunking using `python-frontmatter`. |
| `server.py` | **Adapt** | Replace domain tools with generic tools. Use lifespan hooks instead of lazy global singleton. |
| `cli.py` | **Adapt** | Simplify for markdown-vault-mcp. |

**Reuse strategy definitions**:
- **Copy + adapt**: copy the file as a starting point, then modify for the new
  package. Temporary code duplication accepted until ifcraftcorpus refactor.
- **Adapt**: use as a design reference; rewrite for the new package.
- **Replace**: discard and write new implementation.

## Core Design Decisions

### Document Identity

Documents are identified by their **relative path from the vault root**,
including the `.md` extension. Example: `Journal/2024-01-15.md`.

This avoids collisions between files with the same stem in different
directories (e.g., `Journal/2024-01-15.md` vs `Archive/2024-01-15.md`).

### Folder Derivation

The `folder` field is derived as the parent directory of the document's
relative path:

- `README.md` -> folder `""`  (root)
- `Journal/2024-01-15.md` -> folder `"Journal"`
- `Journal/2024/January/note.md` -> folder `"Journal/2024/January"`

`list_folders()` returns all distinct folder values across the vault.

### Frontmatter Handling

Frontmatter is **optional by default**. Documents without frontmatter are
indexed normally with an empty metadata dict. Title defaults to the first H1
heading, then the filename (without extension).

A `required_frontmatter` configuration option enforces specific fields:

```python
Vault(
    source_dir=Path("corpus/"),
    required_frontmatter=["title", "cluster"],
)
```

- `None` (default): all `.md` files are indexed regardless of frontmatter.
- `["title", "cluster"]`: documents missing any listed field are excluded from
  the index entirely and will not be searchable. At scan completion, the
  number of skipped documents is logged at `INFO` level.

### Frontmatter Filtering

Hybrid approach:

1. **`document_tags` table** (indexed) for structured filtering. An
   `indexed_frontmatter_fields` config option specifies which frontmatter keys
   get promoted into `(tag_key, tag_value)` rows. Each unique
   `(document_id, tag_key, tag_value)` tuple produces one row (duplicates in
   source lists are deduplicated). Complex types (nested dicts, objects) in
   frontmatter are stored in the JSON blob but are **not** indexed -- only
   scalar and simple list values are indexed.
2. **Raw frontmatter JSON blob** stored in the `documents` table for display
   and retrieval only -- not queried via index.

The `filters` parameter on `search()` generates
`document_id IN (SELECT ... FROM document_tags WHERE ...)` subqueries. This
gives O(1) indexed lookup without `json_extract()` performance problems.

**Filter semantics**: `filters` is `dict[str, str]`. Each key-value pair is
ANDed. Example: `filters={"cluster": "fiction", "genre": "horror"}` returns
documents tagged with both `cluster=fiction` AND `genre=horror`. Multi-valued
OR queries within a single key are not supported in Phase 1; use multiple
searches and merge client-side.

### FTS5 Schema

See the [Database Schema](#database-schema) section for full DDL.

Generic columns replacing the corpus-specific `cluster`/`topics`:
`path`, `title`, `folder`, `heading`, `content`.

Domain-specific filtering (by cluster, topic, tag) happens via the
`document_tags` table, not FTS5 columns.

### Hybrid Search: Reciprocal Rank Fusion

BM25 scores (0-20+) and cosine similarity (0-1) are on incompatible scales.
Raw comparison is a latent bug carried from ifcraftcorpus.

**Solution**: Reciprocal Rank Fusion (RRF). Each result set is ranked
independently. Merged score: `1 / (k + rank)` where `k` is a constant
(typically 60). Results are sorted by summed RRF score.

This produces sensible merged rankings regardless of the raw score scales.

### Search Ranking and Snippet Truncation

Four complementary mechanisms improve result diversity and bound LLM context cost:

1. **Cap document concentration.** No single document occupies more than `chunks_per_file`
   slots in the result list, regardless of search mode. Default cap: 2.
2. **Length downweight.** Within each search channel (keyword or semantic), each chunk's
   raw score is adjusted by `score / (1 + alpha · log(chunk_count_in_doc))` before ranking
   or fusion. Long documents with many chunks slide down; short focused notes rise.
3. **Snippet truncation.** `SectionHit.content` (per section of a `GroupedResult`) is truncated to approximately
   `snippet_words` words (default 200). For keyword and hybrid results, FTS5's built-in
   `snippet()` function selects a tokenizer-aware window centered on query terms. For
   semantic-only results, a Python word-window scan picks the densest-matching window.
   Full chunk recovery is available via `read(path, section=heading)`.
4. **Adaptive heading-level chunking.** The `HeadingChunker` recursively re-splits
   oversize chunks at deeper heading levels (H1 → H6) until each fits `max_chunk_words`
   words. When heading-based refinement cannot make further progress (a leaf section
   with no deeper sub-headings, a preamble before the first heading, or a
   no-headings document), the chunker falls back to a paragraph- and word-boundary
   split so the budget is a **hard** invariant for every emitted chunk. Default
   threshold: 400 words. This matters for embedding providers with context limits
   (e.g. the default FastEmbed model `BAAI/bge-small-en-v1.5` exposes a 512-token
   context; `nomic-embed-text-v1.5` has 8192 tokens natively but Ollama serves it
   with `n_ctx_train=2048` by default — both silently truncate beyond their cap).

**Config knobs:**

| Env var | Default | Description |
|---|---|---|
| `MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE` | `2` | Per-document cap on result slots. |
| `MARKDOWN_VAULT_MCP_SNIPPET_WORDS` | `200` | Approximate word budget for `SectionHit.content`. `0` = no truncation. |
| `MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA` | `0.25` | Strength of length downweight. `0` disables. |
| `MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS` | `400` | Adaptive chunker threshold. Set very high to disable. |

**Pipeline order:** Per-channel length downweight → fuse (RRF for hybrid) → cap per path → snippet projection → return `limit` results. See **Field collapsing** below for the post-433 grouping step.

### Field collapsing

Adaptive chunking can produce multiple high-scoring rows from the same
document, which would otherwise dominate top-K results.  The final
shaping stage of every search mode (keyword, semantic, hybrid) collapses
chunks under their parent document via `_group_by_path`:

1. Rows arrive sorted by descending score (already length-downweighted;
   for hybrid, RRF-fused).
2. The helper walks rows, opening a new group per unseen path until
   `file_limit` groups exist; subsequent rows from a seen path append
   to the existing group up to `chunks_per_file` rows.
3. Sections within a group are sorted `(score DESC, start_line ASC,
   section_id ASC)` so ties surface in document order. The `section_id`
   key (the `sections` rowid) makes the order fully deterministic even
   when chunks share a `start_line` — e.g. word-split fragments of one
   oversize source line, which the adaptive chunker emits with identical
   `start_line` values.

The returned shape is `list[GroupedResult]` where each result wraps one
file with a `sections: list[SectionHit]` sub-list (length 1..N).  File
score = `max(section.score)` — the MaxP aggregation established by
[PARADE](https://ar5iv.labs.arxiv.org/html/2008.09093) and used by
Elasticsearch's `collapse`, Vespa grouping, and Qdrant's
`query_points_groups` primitive.

`get_similar` and `get_context.similar` share the same collapsing core
so dossiers never re-apply the cap on top of the cap.  `get_context`
defaults to `chunks_per_file=1` for compact dossiers; `search` and
`get_similar` default to `chunks_per_file=2`.

**Length-downweight is skipped in `get_similar` / `get_context.similar`** (issue
#472).  Grouping already collapses multi-chunk dominators to one entry per file;
compounding the downweight on top buries legitimately-long authoritative
documents — e.g. a reference book scored highest by raw cosine for a similarity
query whose reference doc is a summary of that book.  The three `search` modes
(keyword/semantic/hybrid) keep the downweight because their use case (query →
focused result) still benefits from biasing toward short focused docs.

Replaces the per-path cap from PR #433 (`_apply_chunks_per_doc_cap`),
which thinned duplicates but did not group them.

**Non-goal:** No frontmatter-based ranking. The MCP must not require, recommend, or
special-case any frontmatter convention on vault content (no `kind`, no `noindex`, no
`boost`). Vault organisation is the user's choice; the server treats all `.md` files
structurally identically.

**Migration note:** A reindex is required for the adaptive chunker change to take effect
(the new chunk boundaries and `documents.chunk_count` column differ from older indexes).
The existing `reindex` MCP tool and startup reindex path handle this automatically.

### Chunking Strategy

A `ChunkStrategy` protocol enables extensible chunking:

```python
@runtime_checkable
class ChunkStrategy(Protocol):
    def chunk(self, content: str, metadata: dict[str, Any]) -> list[Chunk]:
        """Chunk the markdown body into sections.

        Args:
            content: Markdown body after frontmatter has been stripped.
            metadata: Parsed frontmatter dict (for context, not modification).

        Returns:
            List of Chunk objects.
        """
        ...
```

**Phase 1 implementations**:
- `HeadingChunker`: split on H1/H2 boundaries. Short documents stay as single
  chunk. Each chunk inherits the document's frontmatter. Default.
- `WholeDocumentChunker`: one chunk per document. Good for short documents.

**Adaptive heading-level chunking**: When `max_chunk_words` is set, oversize
chunks are recursively re-split at deeper heading levels (H1 → H6). Any chunk
that still exceeds the budget after the H6 pass — or that never had a deeper
heading to split on (e.g. a preamble, a no-headings document, a long flat H6
section) — is then fragmented on paragraph and word boundaries by an internal
`_budget_split` helper. The result is a **hard cap**: every emitted chunk
satisfies `words(chunk) <= max_chunk_words` regardless of source structure,
so embedding providers with context-window limits don't silently truncate.
`max_chunk_words=None` preserves the legacy H1/H2-only behaviour with no
word-budget enforcement.

**Future** (deferred):
- `SlidingWindowChunker`: fixed-size overlapping windows with configurable
  tokenizer.

The `Vault` config accepts `chunk_strategy: str | ChunkStrategy` -- string
for built-in names, or pass a custom instance.

### Change Tracking

**Hash-based**, not git-based. Works with any directory, no git dependency.

- **State file** (the JSON persistence layer for hash-based change detection):
  versioned format `{"version": 2, "indexed": {relative_path: sha256_hash},
  "skipped": {relative_path: sha256_hash}}` as JSON (#665). The legacy flat
  `{relative_path: sha256_hash}` format still loads — every entry is treated
  as indexed — so upgrades need no migration step.
- **Default path**: `{source_dir}/.markdown_vault_mcp/state.json` (when
  `state_path=None`).
- On `reindex()`: scan all files, compare hashes to stored state, re-parse and
  re-embed only changed/added files, remove deleted entries. Files matching
  `exclude_patterns` are skipped during re-parsing (mirroring `scan_directory`
  behaviour). Any previously indexed documents that now match `exclude_patterns`
  are purged from the FTS and vector indexes.
- **Skipped-file memory (#665)**: deterministic skips — missing required
  frontmatter, exclude-pattern matches, decode/parse errors — are recorded in
  the `skipped` map with the file's content hash, during both full builds and
  incremental reindexes. An unchanged skipped file is neither re-parsed nor
  re-logged on later scans and is reported in the `skipped` count of
  `ReindexResult` instead of `added`; it is re-evaluated (and indexed, if it
  gained valid frontmatter) only when its content hash changes. Transient
  `OSError` skips are deliberately *not* recorded, so those files retry on
  every scan. A skipped file deleted from disk is dropped silently — it was
  never indexed, so it is not counted as `deleted`.

**Trigger model**: boot reconciliation reindex (submitted by the server
lifespan behind the initial build job, #665) + explicit `reindex` tool call
+ file watcher / git pull / webhook events. No background polling in Phase 1.
Architecture supports adding `watch_interval` or watchdog integration later
without refactoring.

### Incremental Reindex

Full numpy array rebuild on every reindex (filter unchanged rows + append new).
Only changed files are re-embedded (the expensive API call part). This is
correct and simple at vault scale (even 10k chunks at 768 dimensions is ~60MB).

The `VectorIndex` maintains a sidecar metadata list mapping each row index to
its source document path, enabling bulk deletion when a document is reindexed.

### Index Lifecycle

Two methods manage the index:

- **`build_index(force=False)`**: initial population. Scans `source_dir` and
  builds the FTS index. Short-circuits as a no-op when the persisted FTS
  database contains documents **and** carries the completeness sentinel
  written by a prior clean build (warm restart on the same `index_path`).
  A database with rows but no sentinel — residue of a process that
  crashed mid-build, since `IndexManager.build_index` commits per-document
  in its own transaction — is treated as cold and triggers a full
  rebuild. The sentinel is the `build_completed_at` row in the FTS
  `meta` table, cleared by `IndexFacet.build_index` before any
  destructive rebuild and written only after `_index_mgr.build_index`
  returns cleanly. `force=True` drops and rebuilds from scratch. When a
  persistent `index_path` contains documents that now match
  `exclude_patterns`, they are purged from the FTS and vector indexes
  after the scan — but only when a scan actually runs (i.e. on a cold
  index or with `force=True`); a warm-restart short-circuit does not
  apply config changes.

  **Chunking-provenance invalidation (issue #649)**: the chunker is shared
  by FTS and embeddings, and its per-chunk character cap is derived from the
  embedding model — so a change to the embedding model (or to an explicit
  `max_chunk_chars` override) changes FTS chunk boundaries, not just
  embeddings. Each clean build records the two **stable inputs** to the
  derived cap — the embedding `model_name` and the explicit operator
  `max_chunk_chars` override (`None` when the cap was derived from the model
  context) — in the FTS `meta` table (`embed_model_name` /
  `max_chunk_chars_override` rows). The runtime-derived cap itself is
  deliberately **not** recorded. On restart the warm-restart short-circuit
  additionally requires these stored values to match the current config
  (`IndexWriteCoordinator._chunking_meta_matches`); a model or override
  change rejects the short-circuit, so the existing #513 cold-start path
  runs a full background FTS rebuild followed by embeddings — no manual
  `reindex` is needed. Because only the stable inputs are compared, a
  *transient* model-context read (e.g. the Ollama instance briefly
  unreachable at startup, so its context length reads as `None` and the cap
  falls back to a conservative default) changes neither key and so does
  **not** force a rebuild, avoiding flap.
- **`reindex()`**: incremental update. Uses `ChangeTracker` to detect
  adds/modifies/deletes since the last scan and applies only the delta.
  Applies `exclude_patterns` filtering and purges stale excluded documents.

**FTS5 segment hygiene after bulk purges**: deleting rows from an FTS5 table
only marks their tokens as deleted — the dead entries remain in the on-disk
inverted-index segments until FTS5's lazy merge gets around to them, which it
may never do. A bulk purge (e.g. newly-configured `exclude_patterns` expelling
previously indexed documents, issue #255) can therefore leave large dead
segments that bloat the index file and slow keyword queries. When a single
purge pass removes ≥ 25 documents or ≥ 10% of the pre-purge corpus
(`should_optimize()` in `fts_index.py`), the purge call sites in
`IndexManager.build_index()` and `IndexManager.reindex()` run
`FTSIndex.optimize()` — `INSERT INTO notes_fts(notes_fts)
VALUES('optimize')` — which merges all segments and drops the dead entries.
The merge frees pages inside the file; the file itself only shrinks after a
`VACUUM`, which is never run automatically because it takes an exclusive lock
and multiple server processes may share one index file — `optimize()` logs
the reclaimable size (freelist × page size) at INFO so operators can decide
whether a manual `VACUUM` is worthwhile. Both call sites run on the
single-owner IndexWriter thread, like all other index mutations; lock
contention beyond the retry budget is tolerated (skip with a warning; the
next bulk purge retries).

**Readiness contract (issue #525)**: `Vault.__init__` does not
populate the index. Callers must invoke `build_index()` explicitly
before bucket-3 relational/FTS-backed queries (`get_backlinks`,
`get_outlinks`, `get_similar`, `get_context`, `get_connection_path`,
`get_toc`) or the bucket-4 coordinators (`reindex`,
`build_embeddings`); otherwise `IndexUnavailableError(reason="never_built")`
is raised.
`start()` must also be called after `build_index()` because its git
pull loop wires `reindex` as the `on_pull` callback. Bucket-1 file
operations (`read`, `write`, `edit`, `delete`, `rename`,
`write_attachment`) and bucket-2 aggregate queries (`search`, `list`,
`stats`, `list_folders`, `list_tags`, `get_recent`,
`get_orphan_notes`, `get_most_linked`, `get_broken_links`) work on an
unbuilt index — bucket-1 hits disk directly; bucket-2 queries
whatever is currently in the index (empty on cold start).
`wait_until_queryable(timeout=None)` is the readiness primitive: it
blocks on the background-build completion event with a bounded
timeout and raises `IndexUnavailableError(reason="timeout")` on
timeout, `IndexUnavailableError(reason="build_failed")` when a build
ran and failed (the captured error is read via `get_index_status`),
or `IndexUnavailableError(reason="never_built")` when no build was
ever scheduled. The worker resets `_index_built=False` before the
destructive rebuild, so a failed build leaves `_index_built=False`
with a captured error — `wait_until_queryable` reports that as
`build_failed`, distinct from a never-scheduled `never_built` (#586).

**Cold-start background FTS (issue #513 PR1, tool-layer wait
boundary)**: when the persisted FTS DB is cold (sentinel absent),
the MCP server lifespan calls
`IndexFacet.start_background_build_index()` to spawn a daemon
thread that runs `build_index()` to completion. Bucket-3/4 calls
arriving at the MCP layer go through the
`needs_queryable` decorator (in
`src/markdown_vault_mcp/_server_queryable.py`), which blocks via
`IndexFacet.wait_until_queryable(timeout)` with a configurable
default (env `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S`, default 60s).
A failed background build surfaces to operators as
`get_index_status` reporting
`{"status": "failed", "error": "..."}`. MCP clients see
`IndexUnavailableError` with `reason="build_failed"` (or
`reason="timeout"` if the decorator's bounded wait elapsed first)
from the decorator's `wait_until_queryable` call. The `build_failed`
branch fires because the worker resets `_index_built=False` and
records the captured error; the error message itself is read via
`get_index_status` (#586).

**Runtime corruption check (issue #541, MCP-layer):** the
`needs_queryable` decorator also wraps the handler call in a narrow
`try/except sqlite3.OperationalError`. When a bucket-3/4 handler's
SQLite operation raises, the decorator classifies by errorname:
`SQLITE_BUSY` and `SQLITE_LOCKED` (lock contention) remap to
`IndexUnavailableError(reason="busy")`; anything else (corruption,
malformed schema, I/O failure, disk full, unknown codes) remaps to
`reason="broken"`. The original exception is preserved as
`__cause__`. Library callers (direct Vault method use) see
the raw `sqlite3.OperationalError` — the catch is MCP-layer only,
to keep the library boundary thin and let internal callers
classify on their own. Sibling SQLite exceptions
(`ProgrammingError`, `IntegrityError`, etc.) bubble unwrapped from
the decorator — they signify caller bugs or constraint violations,
not index unavailability. Embeddings stay on the
synchronous lifespan path in PR1 — on cold start
`build_embeddings()` is skipped with a log entry and semantic
search returns empty until PR2 backgrounds embeddings or the
operator runs CLI `index`. Warm starts continue to use PR #526's
O(1) sentinel short-circuit and never spawn the background thread.
The library's `_require_built()` is unchanged from PR #525
— it raises immediately on not-ready, which is what lets the git
pull loop and lifespan's embeddings path handle "not ready"
without deadlocking on internal blocking.

The `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S` env var bounds the
`@needs_queryable` decorator's wait, which calls
`IndexFacet.wait_until_queryable`. The env var and the method name
describe the same wait from different angles — operators tune the
timeout in seconds; the method describes what predicate the wait
resolves to.

To apply a configuration change (e.g. new `exclude_patterns`,
`required_frontmatter`) to a pre-existing index, call
`build_index(force=True)` — the short-circuit is keyed on FTS contents
alone and does not detect config drift. When embeddings are configured, a
follow-up plain `build_embeddings()` converges the vector index to the
rebuilt chunk set (see Embedding Convergence below); `force=True` remains
necessary only when the embedding model itself changed, because identical
chunks embedded by a different model are invisible to the chunk-set diff
(the persisted provider/model fingerprint check normally catches this case
at load time and forces the rebuild automatically).

### Embedding Convergence (#665)

`build_embeddings(force=False)` over a **non-empty** vector index does not
skip and does not rebuild — it diffs the FTS `sections` table (the
canonical chunk set, via `FTSIndex.list_chunks()`) against the stored
vector metadata grouped by path (`VectorIndex.chunks_by_path()`) and
reconciles:

- Documents missing from the vector index are embedded and added.
- Documents whose per-path `(title, heading, content)` chunk multiset
  differs in any way (modified content, changed title, re-chunked
  boundaries) are re-embedded in full. The multiset is the chunk identity
  — `VectorIndex` has no per-chunk keys, only a parallel metadata list
  with path-level deletion.
- Vectors for documents no longer in the FTS index (deleted, or newly
  excluded) are removed.
- Unchanged documents are untouched; the sidecar is saved only when
  something actually changed, and the run is summarised in a single
  `build_embeddings_converged added=N removed=M up_to_date=K` log line.

This closes the FTS-vs-vector gap that the boot reconciliation reindex
would otherwise widen: the boot `ReindexAll` job runs before the vector
index is loaded, so offline document changes reach the FTS index but not
the vectors — the boot `BuildEmbeddings` job that follows it (writer FIFO
order) now converges the difference instead of skipping because vectors
exist. Embedding work scales with the size of the drift, not the size of
the vault, so a steady-state boot does zero embedding work.

Convergence embeds per document, in the same bounded batches as the cold
build (#159): a provider failure on one document's chunks (token-context
rejection, transient outage) skips exactly that document — its existing
vectors stay intact — and the rest still converge. This is also the
self-healing property: a boot `BuildEmbeddings` job that failed outright
(recorded in `last_build_embeddings_error`, never retried in-process)
merely leaves a larger diff for the next successful run to converge. A
cold build (empty vector index) and `force=True` behave exactly as
before.

### Error Handling

Two-layer model:

- **Library layer** (`Vault`, `FTSIndex`, `VectorIndex`, etc.): raises
  specific exceptions. Callers catch and handle.
- **MCP tool layer**: catches exceptions, returns structured error responses
  per FastMCP conventions.

**Exception types**:

| Exception | Raised by | When |
|-----------|-----------|------|
| `DocumentNotFoundError` | `edit()`, `delete()`, `rename()` | Document path does not exist on disk |
| `ReadOnlyError` | `write()`, `edit()`, `delete()`, `rename()` | `read_only=True` |
| `EditConflictError` | `edit()` | `old_text` not found or appears more than once. Includes optional diagnostic fields: `closest_match_line`, `first_diff_char`, `expected_snippet`, `found_snippet` |
| `DocumentExistsError` | `rename()` | `new_path` already exists |
| `ConcurrentModificationError` | `write()`, `edit()`, `delete()`, `rename()`, `write_attachment()` | `if_match` provided and current file hash does not match |
| `ValueError` | `build_embeddings()` | No `embedding_provider` or `embeddings_path` configured |
| `None` return | `read()` | Path escapes `source_dir` (traversal attempt) or file does not exist on disk |
| `ValueError` | `edit()` | `old_text` is empty string |

`build_embeddings()` processes chunks in bounded batches (default 64) to avoid
pathological memory allocation from embedding providers (see issue #159).
FastEmbed's ONNX inference uses a further inner batch size of 4 to keep
per-call memory bounded — without this, the ONNX attention matrix for 64 long
chunks can require >192 GB of allocation. The save happens once at the end so a
mid-run crash does not leave a partial index on disk; if one exists anyway,
the next startup's convergence pass (see Embedding Convergence, #665) embeds
exactly the missing chunks rather than treating the index as complete.

### Thread Safety

**Single-writer architecture (issue #559).** `Vault` owns exactly one
worker thread — an :class:`~markdown_vault_mcp.indexing.IndexWriter` — that
serves every FTS and vector-index mutation through a FIFO job queue. The
writer is constructed and `start()`ed in `Vault.__init__` and closed
first inside `Vault.close()`, before any downstream resource teardown.
No other thread mutates the FTS or vector index directly; submission is the
only entry point. This replaces the legacy `_write_lock` + `threading.Timer`
embedding flush from issue #175, which interleaved fine-grained locking and
periodic timer callbacks in a way that proved hard to reason about under
the #513 background-build and #519 per-thread-SQLite work.

The writer accepts five job kinds, each a frozen dataclass:

| Job | Purpose |
|-----|---------|
| `BuildIndex(force)` | Full FTS index build (sync or background-spawned). |
| `ReindexAll` | Incremental FTS reindex via the change tracker. |
| `BuildEmbeddings(force)` | Full vector index build. |
| `ProcessDirtyPaths` | Drain the FTS-dirty set, upsert FTS rows, then queue a `FlushDirtyEmbeddings` follow-up for the same paths. |
| `FlushDirtyEmbeddings` | Drain the vector-dirty set, re-embed, and save. |

Submission returns a `concurrent.futures.Future`; callers wait via
`.result()` for synchronous semantics (e.g. library-level `build_index()`,
`reindex()`, `build_embeddings()`) or fire-and-forget via the `*_async`
counterparts (e.g. MCP-tool-level `reindex` and `build_embeddings`, both of
which return `{"status": "queued"}` immediately and let the writer thread
do the work).

**Boot reconciliation (#665).** The server lifespan submits
`reindex_async()` immediately after `build_index_async()`. On a warm
restart the build short-circuits in O(1) via the FTS sentinel and scans
nothing, so the queued `ReindexAll` job is what picks up files added,
modified, or deleted while no server was running (the file watcher only
sees future events). FIFO ordering guarantees build-before-reindex; on a
cold boot the full build has just recorded tracker state — including
skipped files — so the reindex degenerates to a hash scan with zero
re-parses and zero re-upserts. While the boot reindex is pending or in
flight the writer is non-drained, so the `_meta.index_stale` signal
(#646) honestly reports `true` to early readers until offline changes are
reconciled — no extra staleness bookkeeping is needed. Follow-up submissions issued from inside the writer thread
itself succeed even during shutdown drain, so `ProcessDirtyPaths` can
chain into `FlushDirtyEmbeddings` and both flush before the sentinel
terminates the worker loop.

**Per-document write semantics.** `write()`, `edit()`, `delete()`,
`rename()`, and `write_attachment()` perform the file mutation under a
narrow `_file_write_lock` (a `threading.RLock` that serialises only the
read-modify-write of disk content), then call
`writer.mark_dirty([path])` and submit a `ProcessDirtyPaths` job before
returning. The user-visible call returns as soon as the file is on disk;
the FTS upsert and any vector re-embedding run asynchronously on the
writer. The `on_write` callback (git commit) is submitted to a separate
background worker queue as before — that queue is unrelated to the
IndexWriter and is drained in `close()` step 2.

**Embedding flush.** The legacy 30-second `threading.Timer` is gone.
The writer's `FlushDirtyEmbeddings` job is the sole flush mechanism; it
fires either as a `ProcessDirtyPaths` follow-up (covering all path-level
write tools) or on direct submission. `IndexManager.flush_dirty_embeddings`
takes a snapshot set as an argument (no lock contention with the writer's
dirty-set ownership) and performs the slow embed step outside any
file-mutation lock.

**Status surface.** `IndexFacet.get_index_status()` merges the writer's
non-blocking snapshot (`queue_depth`, `in_flight`, `dirty_paths`,
`dirty_embeddings`) into its response shape, so operators can observe
exactly what the writer is doing without taking any lock. The document
count is read from `FTSIndex.list_notes()`; a `sqlite3.Error` there
(locked / corrupt / closed DB) is not an empty index, so `get_index_status`
keeps `documents_indexed` at `0` and surfaces the reason in a separate
`documents_indexed_error` field (logged at `WARNING`) rather than masking
the failure as a clean zero (#583).

The contract is:

- Concurrent reads are safe without locking.
- Concurrent file-write tools on the same path are serialised by
  `_file_write_lock`; the IndexWriter's FIFO queue serialises everything
  else.
- Before a git pull (periodic `sync_once` or interactive `force_pull`), the
  strategy **pauses new writes and drains the deferred-commit queue** so the
  merge runs on a clean working tree (#571). A write that landed on disk just
  before the pull is committed first, rather than aborting the merge on a dirty
  tree (which previously caused a non-fast-forward push rejection and a spurious
  `.conflict-mcp-*` sibling on the eventual reconcile). The write callback now
  fires *inside* `_file_write_lock`, so once the puller holds that lock no
  landed write can still be unqueued; `WriteCallbackDispatcher.drain()` then
  blocks until the queue is empty before the merge takes the git lock. The
  drain is **best-effort and bounded**: if it does not finish within the
  timeout (or the dispatcher worker has died), the pull logs a WARNING and
  proceeds anyway, accepting the pre-fix dirty-tree behavior for the
  still-pending commit rather than blocking the pull indefinitely.
- The `on_write` callback fires in a **background thread** — it must not
  itself call write methods on the same Vault instance (deadlock).
- Callbacks must not raise; exceptions are logged and swallowed.
- `close()` shuts the writer down first (with a 30 s drain timeout), then
  joins the background-build thread, drains the write-callback queue,
  closes the git strategy, and closes SQLite.

See `docs/superpowers/specs/2026-05-31-issue-559-single-writer-for-indexes-design.md`
for the full design rationale, including the cascade of #513 / #519
prerequisites that motivated centralising mutations on a single writer.

#### Drift signals on index-querying read tools (#534, #641, #645)

Every MCP read tool that queries the index — `search`, the B2 listing /
aggregate tools (`list_documents`, `list_folders`, `list_tags`, `stats`,
`get_recent`, `get_broken_links`, `get_orphan_notes`, `get_most_linked`),
and the B3 graph tools (`get_backlinks`, `get_outlinks`, `get_similar`,
`get_context`, `get_connection_path`) — returns its **bare** payload (a
list or dict) and reports index freshness **out-of-band in the MCP
response's `_meta.index_stale` field** via FastMCP `ToolResult(meta=...)`.
The data payload is identical whether the index is fresh or stale, so
clients that do not care about drift read it exactly as before; the ~1%
that need a fresh-read guarantee inspect `result._meta.index_stale`. This
replaces the earlier `{"stale": bool, "data": ...}` envelope (#534): the
envelope conflated response metadata with domain data, could not extend to
resources without breaking their bare-JSON contract, and added wrapper
noise on every fresh read. `_meta` is MCP's dedicated out-of-band channel
for exactly this kind of response metadata.

`index_stale` is the OR of three signals: the optional `wait_for_pending_writes`
timed out (writer never went idle within the budget), the writer's
monotonic `write_generation` counter advanced during the read (a write
cycle completed inside the read window), or `is_drained()` reports a
non-idle writer at response-construction time (a write is in flight). The
`write_generation` counter — incremented under `_in_flight_lock` once per
completed job — closes the case the pre/post `is_drained()` pair could not
detect: a write that started and finished entirely between two snapshots.

Each such tool accepts an optional `wait_for_pending_writes: bool = false`
parameter (client-facing name; the internal primitive is still "drain").
When `true`, the tool layer polls `IndexFacet.is_drained()`
with `asyncio.sleep` until the writer drains or
`MARKDOWN_VAULT_MCP_DRAIN_TIMEOUT_S` (default 60s) elapses, then
runs the query. On timeout the tool answers from the current index
rather than raising — best-effort fresh-read semantics, with
`index_stale=true` in `_meta`. (`IndexFacet.wait_for_drain()` is the
synchronous counterpart for in-process callers.)

The index-querying **resources** (`config://vault`, `stats://vault`,
`tags://vault`, `tags://vault/{field}`, `folders://vault`,
`toc://vault/{path}`, `similar://vault/{path}`, `recent://vault`) carry
the same `_meta.index_stale` signal via FastMCP `ResourceResult(meta=...)`,
readable through the resource read's `_meta`. Resources take no
`wait_for_pending_writes` parameter (a resource URI template binds only address
path segments, not ad-hoc control parameters), so they signal staleness
only. Each body is wrapped in an explicit `application/json`
`ResourceContent` so the declared MIME type survives — a bare `str` in a
`ResourceResult` would default to `text/plain`.

Implementation: the shared `_staleness_result()` helper (in
`_server_tools.py`) wraps a tool's data in a `ToolResult` whose
`structured_content` mirrors FastMCP's wrap-result convention (a
list/primitive payload is nested under `{"result": ...}`) so the client
still deserializes `result.data` to the bare shape advertised by the
tool's data-typed return annotation. The annotation drives the output
schema; the `ToolResult` is the runtime payload. The resource counterpart
is `_stale_resource()` in `_server_resources.py`.

The drift signal reflects writer-internal state only: paths in
`dirty_paths`, paths in `dirty_embeddings`, the in-flight job
kind, and the queue depth. **External file changes on disk** —
files modified outside the MCP server with no `write` tool call
and no git pull — are not covered by this signal; that drift mode
is tracked separately in #558.

#### Vault thread-safety contract (issue #519)

Every public method on `Vault`, `FTSIndex`, and the managers is safe
to call from any thread, concurrently with calls on any other thread.
This is the contract that issue #513 (non-blocking MCP initialize via
background indexing) depends on; PRs #510, #515, #516, #518 all failed
because the single-connection model violated it on Python 3.12+.

The mechanism is **per-thread `sqlite3.Connection` instances** managed by
`FTSIndex`:

- Each thread that touches the index opens its own connection on first
  call, via `_conn()` (which routes through `threading.local`).
- A side registry (`_all_conns: list[sqlite3.Connection]`, guarded by
  `_reg_lock`) holds strong references to every opened connection so
  `close()` can close all of them — including those opened by threads
  that have since exited. `check_same_thread=False` is set on every
  connection so `close()` can iterate cross-thread.
- The constructing thread is special only in that it runs schema/migrations
  exactly once and applies WAL (a DB-header pragma) for file-backed DBs.
  Per-thread opens apply only per-connection pragmas (`foreign_keys=ON`,
  `busy_timeout=5000`, `synchronous=NORMAL`) — **pragmas apply BEFORE
  schema/migrations** so `busy_timeout` is active during `ALTER TABLE`.
- `_closed: bool` uses double-checked locking: the fast path is lock-free;
  the slow path re-checks under `_reg_lock` so a concurrent `close()`
  cannot race with a new-thread connection open.
- Slow-path open + pragma + register is wrapped in `except BaseException`
  so `KeyboardInterrupt` / `SystemExit` / `asyncio.CancelledError` during
  interpreter teardown cannot leak the half-initialized connection.
- `_primary_conn` is a strong instance attribute (alongside
  `self._local.conn`) so the primary connection survives the constructing
  thread's exit; the registry-based `close()` then still closes it.
- `:memory:` databases are translated to a unique-per-instance shared-cache
  URI (`file:fts_<uuid4hex>?mode=memory&cache=shared`) so every per-thread
  open joins the same in-memory DB. A startup probe opens a second
  connection to the URI and raises `RuntimeError` with an operator-actionable
  message if `SQLITE_ENABLE_SHARED_CACHE` is unavailable.

Dead-thread connections accumulate in `_all_conns` until `close()`. This
is bounded for the MCP server's threading model (long-lived lifespan
thread + bounded `asyncio.to_thread` pool of ~30 recycled workers) and
is preferred over the `weakref` approach that introduced a 3-finding
cascade in PR #520 (see project-memory file
`feedback_519_weakref_whackamole.md`).

Cursors never escape the method that created them — verified by audit
during the #519 design. Each `with self._conn():` block emits one
BEGIN/COMMIT pair; no nested `with conn:` exists in the current code, so
Python 3.12's implicit-transaction wrapper is never re-entered.

Acceptance evidence: `tests/test_thread_safety.py` exercises per-thread
identity, pragma application, pragmas-before-schema ordering, single
schema run, close-closes-all, idempotent close, post-close rejection,
close/open race safety, strong-ref retention across thread+gc, shared-cache
`:memory:`, concurrent file writers via `_file_write_lock` (#559) routed
through the single-owner IndexWriter, and the PR #518 failure pattern
(background `build_index(force=True)` interleaved with main-thread
`read/search/write/edit`).

#### Optimistic Concurrency (`if_match`)

All five write methods (`write()`, `edit()`, `delete()`, `rename()`,
`write_attachment()`) accept an optional `if_match: str | None = None`
parameter.  When provided, the method computes the SHA-256 hex digest of the
current file **inside `_file_write_lock`** and compares it to `if_match`.  If
the digests differ, `ConcurrentModificationError` is raised and no mutation
occurs.  Passing `if_match=None` (the default) skips the check and preserves
pre-existing unconditional-write behavior.

The etag used for comparison is the same value returned in the `etag` field of
`read()` and `read_attachment()` responses, so the round-trip pattern is:

```python
note = vault.reader.read("doc.md")
vault.writer.write("doc.md", new_content, if_match=note.etag)
```

### Security: Path Traversal Protection

All public **write** methods accepting a `path` parameter call
`Vault._validate_path()` before any disk I/O. This method:

1. Resolves the path to an absolute path via `Path.resolve()`.
2. Checks the resolved path is within `source_dir` via `is_relative_to()`.
3. Raises `ValueError("Path traversal detected: ...")` if it escapes.

This applies to `write()`, `edit()`, `delete()`, `rename()`, and all
attachment write operations.

`read()` validates the path inline rather than via `_validate_path()`: if the
resolved path escapes `source_dir`, it returns `None` instead of raising.

### Lifecycle: Vault.close()

`Vault.close()` must be called on shutdown to release resources:

1. Closes the :class:`~markdown_vault_mcp.indexing.IndexWriter` first (30 s
   drain timeout). The writer drains any pending jobs — including the
   final `ProcessDirtyPaths`/`FlushDirtyEmbeddings` chain — so deferred FTS
   upserts and embedding flushes complete before downstream resources tear
   down (#559).
2. Joins the background-build thread (if `start_background_build_index()`
   spawned one and it has not yet returned).
3. Drains the background write-callback queue (waits for pending git commits).
4. Closes the `GitWriteStrategy` (flushes and pushes pending commits).
5. Closes the SQLite database connection.

This ensures no work is lost on shutdown. The full lifecycle contract is:

```
Vault(...)
  → sync_from_remote_before_index()   # git fetch + ff-only before first index
  → build_index()                     # build FTS index
  → build_embeddings()                # build vector index (when configured)
  → start()                           # launch background pull loop
  → zero or more read/write operations
  → close()                           # stop pull loop, flush git, release SQLite
```

`stop()` may also be called independently to pause the pull loop without closing
the vault (e.g. during maintenance or test teardown). It is a no-op if the
loop was never started.

In the MCP server, `close()` is called in the FastMCP lifespan `finally` block.
Callers using `Vault` as a Python library must call `close()` explicitly
(or use it as a context manager if one is added in future).

### One-Time Transfer Links (`transfer/` subsystem)

The transfer subsystem lets vault files move out-of-band — to a browser or
another service — without passing bytes through the LLM context. It is an
HTTP-layer feature: the route is registered only on HTTP/SSE transports and
requires `MARKDOWN_VAULT_MCP_BASE_URL` to construct capability URLs.

#### Trust model

The `/transfer/{token}` route is mounted **outside** the auth middleware.
The unguessable token (`secrets.token_urlsafe(32)`, 43 URL-safe characters,
256 bits of entropy) is the authorization. No `Authorization` header is
required or checked on the route. The security properties that follow from
this design:

- A valid token grants exactly one operation (download or upload) on one
  fixed path.
- Tokens expire after a configurable TTL (default 3600 s, ceiling 86400 s).
- Upload size is capped per-upload (`MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES`,
  default 100 MiB).
- A successfully completed transfer burns the token; subsequent requests with
  the same token return HTTP 404.
- A transient failure (network drop, size limit exceeded) does **not** burn
  the token — the transfer can be retried until expiry.

#### `TransferStore` state machine

`TransferStore` is an in-memory registry (a `dict` guarded by `threading.Lock`)
that holds all live tokens. Each token record progresses through three states:

```
available → in-flight → consumed
```

- **`available`**: the token has been minted and has not been claimed by an
  ongoing request. Attempts to use an expired token in this state return 404.
- **`in-flight`**: a request has claimed the token (`claim()`) and is actively
  performing the transfer. Concurrent claims on the same token are rejected
  (idempotency guard). If the transfer fails, `release()` moves the token back
  to `available` so a retry is possible.
- **`consumed`**: `complete()` was called after a successful transfer. The
  token is marked consumed and `claim()` rejects every further request
  (returning 404).

Expired and consumed tokens are always rejected by `claim()`. Stale entries are
purged from memory the next time a token is minted — `create()` sweeps expired
records before inserting the new one — so no background thread is needed.

#### Download path (`GET /transfer/{token}`)

1. `claim(token)` — verifies the token exists, is `available`, and is not
   expired; atomically transitions it to `in-flight`.
2. The path stored on the token is resolved via `vault.reader.read()` or
   `vault.reader.read_attachment()` (lazy read from disk).
3. The file bytes are streamed to the client with an appropriate
   `Content-Type` and `Content-Disposition: attachment` header.
4. On success: `complete(token)` burns the token.
5. On failure: `release(token)` returns the token to `available`.

Single full-fetch only — HTTP range requests (`Range:`) are not supported.
The entire file is read into memory before streaming.

#### Upload path (`POST /transfer/{token}` and `PUT /transfer/{token}`)

`PUT` is accepted as an alias for `POST` to accommodate HTTP clients that
prefer it for byte-range-like semantics, but both behave identically.

1. `claim(token)` — same as download.
2. The raw request body bytes are collected up to `TRANSFER_MAX_UPLOAD_BYTES`;
   a body that exceeds the cap triggers `release(token)` and returns HTTP 413.
3. The bytes are written to the fixed destination path via the normal write
   path (`vault.writer.write()` for `.md`, `vault.writer.write_attachment()`
   for other extensions). Path traversal and extension validation are
   re-applied at write time (defense-in-depth against a bug in the link-creation
   validation). The write updates the FTS index and fires the git-commit callback.
4. On success: `complete(token)` burns the token.
5. On failure: `release(token)` returns the token to `available`.

The upload body is raw bytes — not `multipart/form-data`. The destination
path is decided at link-creation time and cannot be overridden by the uploader.

#### MCP tools

Two MCP tools create tokens and return the capability URL:

- **`create_download_link(path, ttl_seconds=None)`** — read tool (available in
  read-only mode). Validates that `path` exists before minting the token (fail-fast).
  Returns `{url, path, expires_at, expires_in_seconds}`.
- **`create_upload_link(path, ttl_seconds=None)`** — write tool (hidden in
  read-only mode). Validates the destination path (traversal + extension check)
  at link-creation time. Returns the same shape.

Both tools require `MARKDOWN_VAULT_MCP_BASE_URL` and raise `ValueError` when it
is unset. Both tools are hidden when the transport is stdio (no HTTP server to
receive the transfer request).

### HTTP Session Persistence

For HTTP/streamable-HTTP transport, the server uses an `EventStore` so MCP
sessions survive container restarts. The backend is configured via the unified
key-value store URL `MARKDOWN_VAULT_MCP_KV_STORE_URL` (the legacy
`MARKDOWN_VAULT_MCP_EVENT_STORE_URL` is still honoured when `KV_STORE_URL` is
unset, with a one-shot deprecation warning):

- **Default** (unset): a file-backed store at `/data/state` (the `events`
  keyspace is namespaced inside the directory). Sessions persist on disk inside
  the Docker state volume.
- **`memory://`**: In-memory store; sessions are lost on restart. Suitable for
  development or single-shot CI environments.

The event store is only constructed for HTTP transport (`serve --transport http`).
stdio transport does not use it.

### Concurrency

The library is **synchronous** internally. This is appropriate for the
single-user vault use case and for Python library consumers (LangChain tools
are typically sync).

In the MCP server layer, use `asyncio.to_thread(vault.reader.search, ...)`
for tool handlers to avoid blocking the FastMCP event loop.

**Future work**: async embedding provider path for non-blocking batch
operations.

### Logging

Follow FastMCP conventions and standard Python logging:
`logging.getLogger(__name__)` throughout. No `print()` for operational output.

**Log level control:** `FASTMCP_LOG_LEVEL` env var controls FastMCP internals
(`DEBUG`, `INFO`, `WARNING`, `ERROR`). Default `INFO`. App loggers use `INFO`
unless overridden by `-v` (sets both app and FastMCP to `DEBUG`). When
`DEBUG` is active, `httpx` and
`httpcore` loggers are pinned to `WARNING` to reduce noise.

**Request logging:** `make_server()` wires pvl-core's single
`RequestLoggingMiddleware` (via `wire_middleware_stack`), which emits
family-conforming, tool-aware lines — a bare event name first, then
`key=value` pairs, with request timing carried inline on the terminal line.
Tool calls (`tools/call`) use the `tool_call_started` / `tool_call_completed`
/ `tool_call_failed` vocabulary and carry `tool=<name>`; other messages use
`<type>_started` / `<type>_completed` / `<type>_failed`. Output is `key=value`
text by default, or one JSON object per record when
`FASTMCP_ENABLE_RICH_LOGGING=false`. Tracebacks attach to `*_failed` records
when the root logger is at `DEBUG`.

**Auth logging:** At `DEBUG`, the OIDC and bearer auth builders log full
configuration details (secrets redacted). At `INFO`, only the auth mode
decision and a startup summary line are emitted.

## Data Types

All public return types and major internal structures:

```python
from dataclasses import dataclass, field
from typing import Any, Literal

# --- Scanner types ---

@dataclass
class ParsedNote:
    """A parsed markdown document."""
    path: str                         # relative to source_dir, includes .md
    frontmatter: dict[str, Any]       # parsed YAML frontmatter (empty dict if none)
    title: str                        # from frontmatter, first H1, or filename
    chunks: list[Chunk]               # content chunks
    content_hash: str                 # SHA256 of raw file content
    modified_at: float                # file mtime

@dataclass
class Chunk:
    """A chunk of a document, typically a section under a heading."""
    heading: str | None               # heading text, None for preamble
    heading_level: int                # 0 for preamble, 1-6 for headings
    content: str                      # markdown body (frontmatter stripped)
    start_line: int                   # line number in source file

# --- Search types ---

@dataclass
class SectionHit:
    """One section's contribution to a GroupedResult."""
    heading: str | None               # section heading (None for the intro)
    content: str                      # matched snippet (query-relevant window
                                      # by default, full chunk if snippet_words=0)
    score: float                      # chunk-level score after length-downweight

@dataclass
class GroupedResult:
    """A file-grouped search result returned by search(), get_similar(),
    and get_context.similar (since v2.0.0 / issue #469).

    One entry per file with up to chunks_per_file best-matching sections."""
    path: str                         # document relative path
    title: str                        # document title
    folder: str                       # derived folder
    score: float                      # file-level score = max(section.score)
    search_type: Literal["keyword", "semantic", "hybrid"]
    frontmatter: dict[str, Any]       # document frontmatter
    sections: list[SectionHit]        # up to chunks_per_file sections, sorted
                                      # by (score DESC, start_line ASC,
                                      # section_id ASC)

@dataclass
class SearchResult:
    """Legacy single-chunk shape.  Retained for backward API compatibility
    (exported via ``__all__``); new code returns GroupedResult.  Not
    directly returned by search()/get_similar()/get_context after v2.0.0
    — see GroupedResult."""
    path: str                         # document relative path
    title: str                        # document title
    folder: str                       # derived folder
    heading: str | None               # matched section heading (None for summary)
    content: str                      # matched text content
    score: float                      # relevance score (RRF in hybrid mode)
    search_type: Literal["keyword", "semantic"]
    frontmatter: dict[str, Any]       # document frontmatter

@dataclass
class FTSResult:
    """A raw search result from the FTS5 index layer."""
    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float                      # BM25 score (abs value)

# --- CRUD types ---

@dataclass
class NoteContent:
    """Full content of a document, returned by read()."""
    path: str
    title: str
    folder: str
    content: str                      # raw markdown (including frontmatter)
    frontmatter: dict[str, Any]
    modified_at: float
    etag: str                         # SHA256 hex of raw file bytes; use as if_match

@dataclass
class NoteInfo:
    """Summary info for a document, returned by list_documents()."""
    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    kind: str = "note"                # always "note" for markdown documents

@dataclass
class WriteResult:
    """Result of a write operation."""
    path: str
    created: bool                     # True if new file, False if overwrite

@dataclass
class EditResult:
    """Result of an edit operation."""
    path: str
    replacements: int                 # always 1 (enforced by edit semantics)
    match_type: str = "exact"         # "exact" or "normalized"

@dataclass
class DeleteResult:
    """Result of a delete operation."""
    path: str

@dataclass
class RenameResult:
    """Result of a rename operation."""
    old_path: str
    new_path: str
    updated_links: int = 0  # number of source docs updated (update_links=True)

# --- Index types ---

@dataclass
class IndexStats:
    """Statistics from build_index()."""
    documents_indexed: int
    chunks_indexed: int
    skipped: int                      # documents skipped (required_frontmatter)

@dataclass
class ReindexResult:
    """Result of an incremental reindex."""
    added: int
    modified: int
    deleted: int
    unchanged: int
    skipped: int = 0                  # deliberately not indexed (#665)

@dataclass
class VaultStats:
    """Vault-wide statistics."""
    document_count: int
    chunk_count: int
    folder_count: int
    semantic_search_available: bool
    indexed_frontmatter_fields: list[str]
    attachment_extensions: list[str]
    link_count: int = 0               # total rows in the links table
    broken_link_count: int = 0        # links where target_path not in documents
    orphan_count: int = 0             # documents with no inbound or outbound links

# --- Change tracking ---

@dataclass
class ChangeSet:
    """Documents that changed since last index."""
    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: int
    skipped_unchanged: int = 0        # recorded skips, content unchanged (#665)

# --- Graph types ---

@dataclass
class BacklinkInfo:
    """A document that links to a given path."""
    source_path: str
    source_title: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""              # literal link string as written in the source file

@dataclass
class OutlinkInfo:
    """A link from a document to another path."""
    target_path: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""              # literal link string as written in the source file
    exists: bool = False              # True if target_path is indexed in the vault

@dataclass
class BrokenLinkInfo:
    """A link whose target does not exist in the vault."""
    source_path: str
    source_title: str
    target_path: str
    link_text: str
    link_type: Literal["markdown", "wikilink", "reference"]
    fragment: str | None = None
    raw_target: str = ""              # literal link string as written in the source file

@dataclass
class MostLinkedNote:
    """A document with its inbound backlink count, returned by get_most_linked()."""
    path: str
    title: str
    backlink_count: int               # number of distinct source documents linking here
```

## Database Schema

Full DDL for the SQLite database used by `FTSIndex`:

```sql
-- Documents table: one row per .md file
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,        -- relative path (document identity)
    title TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',  -- derived from path parent
    frontmatter_json TEXT,            -- raw YAML frontmatter as JSON (for display)
    content_hash TEXT NOT NULL,       -- SHA256 of raw file content
    modified_at REAL NOT NULL         -- file mtime
);

-- Sections table: one row per chunk within a document
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    heading TEXT,                     -- heading text, NULL for preamble
    heading_level INTEGER NOT NULL,   -- 0 for preamble, 1-6 for headings
    content TEXT NOT NULL,            -- chunk content (frontmatter stripped)
    start_line INTEGER NOT NULL,      -- line number in source file
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- Document tags: indexed frontmatter key-value pairs
CREATE TABLE IF NOT EXISTS document_tags (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    tag_key TEXT NOT NULL,
    tag_value TEXT NOT NULL,
    UNIQUE(document_id, tag_key, tag_value),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tags_kv
    ON document_tags(tag_key, tag_value);

CREATE INDEX IF NOT EXISTS idx_tags_docid
    ON document_tags(document_id);

-- Links: inter-document references extracted from markdown content
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    target_path TEXT NOT NULL,        -- resolved relative path (may not exist)
    link_text TEXT NOT NULL DEFAULT '',
    link_type TEXT NOT NULL,          -- 'markdown', 'wikilink', 'reference'
    fragment TEXT,                    -- heading anchor, NULL if none
    raw_target TEXT NOT NULL DEFAULT '',  -- literal link string as written in file
    FOREIGN KEY (source_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_path);
CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id);

-- FTS5 virtual table for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path,
    title,
    folder,
    heading,
    content,
    tokenize='porter unicode61'
);
```

### Link Extraction

Links are extracted from markdown content during `parse_note()` and stored in the
`links` table. Three formats are supported:

- **Inline markdown**: `[text](path.md)`, `[text](path.md#heading)`
- **Reference-style**: `[text][ref]` with `[ref]: path.md` definitions
- **Wikilinks**: `[[path]]`, `[[path|alias]]`, `[[path#heading]]`

**Exclusions**: links inside fenced code blocks (`` ``` ``) and inline code (`` ` ``)
are not extracted. External URLs (`http://`, `https://`, `mailto:`) and pure anchors
(`#heading`) are skipped.

**Path resolution for markdown links**: relative paths are resolved against the
source document's directory. `../sibling.md` from `Journal/2024/today.md` resolves
to `Journal/sibling.md`. Traversal above the vault root clamps to root.

**Fragment handling**: `path.md#heading` splits into `target_path=path.md` and
`fragment=heading`. The fragment is preserved on `LinkInfo` but the target path
is stored without it.

**Wikilink resolution (Obsidian semantics)**: Wikilinks follow Obsidian's
vault-wide resolution rules rather than relative path resolution:

- **Bare wikilinks** (`[[Note]]`, `[[folder/Note]]`): the scanner stores the
  path as-is after appending `.md` (e.g. `Note.md`, `folder/Note.md`). After
  all documents are indexed, `FTSIndex.resolve_vault_wikilinks()` performs a
  bulk SQL UPDATE that resolves each unmatched wikilink target vault-wide:
  it searches for any document whose path equals the target or ends with
  `/target`. When multiple candidates match, the shortest path (fewest path
  components) wins. This mirrors Obsidian's tie-breaking rule.

- **Explicit relative wikilinks** (`[[./note]]`, `[[../note]]`): the `./` or
  `../` prefix opts out of vault-wide resolution. These are resolved against
  the source document's directory at scan time, identical to markdown links.

- `[[Note Title]]` appends `.md` → `Note Title.md` before resolution.

- **Alias resolution**: When no path match is found,
  `resolve_vault_wikilinks()` also checks the `document_aliases` table.
  Documents can declare alternative names via a YAML `aliases` (list) or
  `alias` (string) frontmatter field. For example, `[[AI]]` resolves to a
  document with `aliases: [AI, A.I.]` in its frontmatter. Alias matching
  is case-insensitive. When multiple documents share the same alias, the
  shortest path wins. Path matches always take priority over alias matches.

`resolve_vault_wikilinks()` is called automatically at the end of
`IndexFacet.build_index()`, `IndexFacet.reindex()`, and every
`DocumentManager` write that mutates the `links` table — `write()` and
`edit()` of a `.md` document, `delete()` of a `.md` document, and
`rename()` of a `.md` document.  Attachment writes do not invoke the
resolver because attachments do not produce `links` rows.  Without the
per-write call, wikilinks introduced or invalidated by a tool-driven edit
would persist as bare basenames (e.g. `Target.md`) — leaving them as
false-positive broken outlinks and invisible to backlink queries against
the full document path (e.g. `notes/Target.md`).

### Graph Traversal

The `links` table is a directed graph where notes are nodes and links are edges.
`FTSIndex` provides a BFS-based traversal treating the graph as **undirected** —
a link from A→B or B→A both count as a connection.

**`get_connection_path(source_path, target_path, max_depth=10)`**

Returns the shortest path between two notes as an ordered `list[str]`, or
`None` if unreachable within `max_depth` hops. `max_depth` is clamped to
`[1, 10]`.

Algorithm:
1. Validate both endpoints exist in the `documents` table (raises `ValueError`
   if missing).
2. Trivial case: `source == target` → returns `[source]`.
3. Load all edges into an undirected adjacency dict:
   `{path: set(neighbours)}` — both forward and reverse directions.
4. BFS from `source`, tracking the full path at each node. Early exit when
   `target` is found. Nodes beyond `max_depth` edges are not expanded.

The adjacency dict is built per-query from the `links` table; it is not cached
between calls. For typical vault sizes (hundreds to low thousands of notes),
this is fast enough that caching adds complexity without measurable benefit.

`GraphFacet.get_connection_path()` wraps the FTS call and applies path
traversal protection via `_validate_path()` before delegating.

The MCP tool `get_connection_path` returns
`{"found": bool, "path": list[str], "hops": int}`.

## Module Design

### `__init__.py` -- Lazy Package Root (PEP 562, #665)

The package root resolves its public attributes lazily via module-level
``__getattr__``/``__dir__`` (PEP 562) from an explicit name -> submodule map
(`_EXPORTS`), instead of eagerly importing every exporting submodule. The
public API is unchanged: ``from markdown_vault_mcp import Vault`` still works,
``__all__`` lists the same names, and a test pins ``_EXPORTS`` == ``__all__``.

Rationale: an eager root pulled the full dependency tree (``config`` ->
``fastmcp_pvl_core`` -> ``beartype``; ``frontmatter`` -> PyYAML) into *any*
import of the package. coverage.py resolves dotted ``--cov=`` source packages
with ``importlib.util.find_spec`` inside a sys.modules-restoring context, so
those dependencies were imported and then purged while their process-global
side effects (beartype's claw entry in ``sys.path_hooks``, PyYAML's cached
single-phase-init C extension) survived, breaking every subsequent import in
the process. The package root must therefore stay import-light: it may not
import (directly or transitively) ``fastmcp_pvl_core``, ``beartype``,
``frontmatter``, or ``yaml``. Regression tests live in
``tests/test_package_imports.py``.

### `vault.py` -- Thin Facade

The main interface. Orchestrates specialized manager modules via dependency
injection. Vault creates managers in ``__init__`` and delegates all
operations to them. No manager holds a back-reference to Vault.

#### Internal Manager Architecture

| Manager | Responsibility | Dependencies |
|---------|---------------|-------------|
| ``LinkManager`` | Backlinks, outlinks, broken links, orphans, hubs, connection paths | ``FTSIndex``, ``source_dir`` |
| ``SearchManager`` | Keyword/semantic/hybrid search, list, folders, tags, recent, similar, context, stats | ``FTSIndex``, ``source_dir``, embedding config, ``LinkManager`` |
| ``IndexManager`` | build_index, reindex, build_embeddings, process_dirty_paths, flush_dirty_embeddings | ``FTSIndex``, ``ChangeTracker``, ``source_dir``, chunk strategy (no lock — driven by the single-owner :class:`~markdown_vault_mcp.indexing.IndexWriter`, #559) |
| ``DocumentManager`` | read, write, edit, delete, rename, attachments, TOC | ``FTSIndex``, ``source_dir``, ``_file_write_lock`` (file-mutation atomicity only — see #559), ``mark_paths_dirty`` hook, callbacks |
| ``GitQueryManager`` | Git history / diff reads (read-only, #610) | ``GitWriteStrategy`` (or ``None`` when not a git repo), ``source_dir`` |

Each manager receives its dependencies as constructor arguments. This enables
isolated unit testing and clear dependency boundaries. Pure utility functions
live in ``utils/text.py`` (normalization, position mapping, fuzzy matching) and
``utils/links.py`` (link target computation and replacement).

#### Facets (`facets/`, #604)

The ``facets/`` package groups the formerly-flat ``Vault`` surface into
four cohesive views, each a thin delegator over the managers/coordinator the
root already owns:

| Facet | Surface | Collaborators |
|-------|---------|--------------|
| ``ReaderFacet`` | search, read, list, folders, tags, toc, recent, similar, context, stats, history, diff, read_attachment | ``SearchManager``, ``DocumentManager``, ``GitQueryManager``, ``require_built`` |
| ``WriterFacet`` | write, edit, delete, rename, write_attachment | ``DocumentManager`` |
| ``GraphFacet`` | backlinks, outlinks, broken_links, orphans, most_linked, connection_path | ``LinkManager``, ``require_built`` |
| ``IndexFacet`` | build/reindex/embeddings (sync + async), readiness, writer status, embeddings_status | ``IndexWriteCoordinator`` (public subset only), ``IndexManager`` (embeddings_status) |

``Vault`` constructs the facets once in ``__init__`` and exposes them via
the ``reader`` / ``writer`` / ``graph`` / ``index`` properties. The bucket-3
readiness gate (``require_built``) lives inside the facets — ``ReaderFacet``
(``get_toc`` / ``get_similar`` / ``get_context``) and ``GraphFacet``
(``get_backlinks`` / ``get_outlinks`` / ``get_connection_path``) call it before
delegating, so the gate is expressed in exactly one place. ``IndexFacet`` is a
deliberate wrapper: it surfaces the coordinator's public operations (plus
``IndexManager.embeddings_status``) and hides the root-owned coordinator
internals (``close``, ``writer``, ``require_built``, ``mark_paths_dirty``,
``rebuild_embeddings``).

The migration followed **addition before removal**: the flat ``Vault``
methods first delegated to the facets (PR3a, #604); production callers (PR3b,
#605) and the test suite (PR3c, #606) then migrated to the facet accessors; the
flat delegators were removed (PR4a, #627), leaving the composition root thin;
it was then renamed ``Collection`` → ``Vault`` (PR4b, #629), completing the epic.

Clients reach the read / write / graph / index operations through the facet
accessors (e.g. ``vault.reader.search(...)``), never through managers
directly. ``Vault`` itself now exposes only construction, the four facet
accessors, and lifecycle — the per-facet method surface is the Facets table
above.

```python
class Vault:
    def __init__(
        self,
        *,
        source_dir: Path,
        index_path: Path | None = None,       # None = in-memory SQLite
        embeddings_path: Path | None = None,  # None = semantic search disabled
        embedding_provider: EmbeddingProvider | None = None,
        read_only: bool = True,
        state_path: Path | None = None,       # None = {source_dir}/.markdown_vault_mcp/state.json
        indexed_frontmatter_fields: list[str] | None = None,
        required_frontmatter: list[str] | None = None,
        chunk_strategy: str | ChunkStrategy = "heading",
        on_write: WriteCallback | None = None,
        exclude_patterns: list[str] | None = None,
    ): ...

    # --- Facet accessors (the public operation surface) ---
    # search / read / write / edit / delete / rename / list / graph / index /
    # stats operations live on these facets; see the Facets table above for the
    # per-facet method surface (e.g. vault.reader.search(...),
    # vault.writer.write(...), vault.index.build_index(...)).
    @property
    def reader(self) -> ReaderFacet: ...
    @property
    def writer(self) -> WriterFacet: ...
    @property
    def graph(self) -> GraphFacet: ...
    @property
    def index(self) -> IndexFacet: ...

    # --- Lifecycle ---
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
    def pause_writes(self) -> Iterator[None]: ...        # context manager
    def force_pull(self) -> PullResult | None: ...
    def sync_from_remote_before_index(self) -> None: ...
```

**Constructor defaults**:
- `index_path=None`: index is created in-memory (`:memory:` SQLite). If
  provided, persisted to disk.
- `embeddings_path=None`: semantic search is disabled.
- `state_path=None`: defaults to `{source_dir}/.markdown_vault_mcp/state.json`.

**Index build**: callers build the FTS index explicitly via
`IndexFacet.build_index` — the server builds at startup, and a cold on-disk
start builds in the background (#513). There is no lazy build on first query.

**Write operations** (`write`, `edit`, `delete`, `rename`) raise
`ReadOnlyError` when `read_only=True`.

**`write()` behavior**: creates or overwrites the document at `path`. Creates
intermediate directories as needed (`mkdir -p` semantics). If `frontmatter` is
provided, it is serialized as YAML front matter at the top of the file. Updates
the FTS index and triggers `on_write`.

**`edit()` behavior**: supports three modes: (1) exact match — reads file,
verifies `old_text` exists exactly once, replaces with `new_text`; (2) line-range
— replaces lines `line_start..line_end` (1-based, inclusive) with `new_text`;
(3) scoped match — searches for `old_text` within the specified line range only.

When exact match fails (count == 0), a normalized comparison is attempted:
Unicode NFC, en-dash/em-dash → hyphen, smart quotes → straight quotes,
whitespace collapse within lines, trailing whitespace stripping. If exactly
one normalized match is found, the original byte range is replaced and
`match_type="normalized"` is returned. Raises `DocumentNotFoundError`
if the file does not exist. Raises `EditConflictError` if `old_text` is
not found (after both exact and normalized matching) or appears more than
once. When both exact and normalized match fail, `EditConflictError`
carries optional diagnostic fields: `closest_match_line`, `first_diff_char`,
`expected_snippet`, `found_snippet`. For a multi-line `old_text` these
locate the *first line that genuinely diverges* from the file — the
diagnostic anchors on the first line, then walks subsequent lines so a
later-line mismatch is reported rather than a perfectly-matching first line.
The fields are omitted (left `None`) when no divergence can be localized:
no file line is similar enough to anchor on, or every line of `old_text`
matches the file region.

**`delete()` behavior**: removes the file from disk, deletes FTS and embedding
entries, triggers `on_write`. Raises `DocumentNotFoundError` if not found.

**`rename()` behavior** (Phase 2-3): renames the file on disk, deletes old
FTS/embedding entries, inserts new entries under the new path, updates
embedding metadata in-place. Triggers `on_write` with the new path. Raises
`DocumentNotFoundError` if `old_path` does not exist. Raises
`DocumentExistsError` if `new_path` already exists.

When `update_links=True` and `old_path` is a `.md` document, every document
that links to `old_path` is also updated so its links point to `new_path`.
The replacement is **best-effort**: per-file failures are logged at `WARNING`
but do not prevent the rename from succeeding. The `RenameResult.updated_links`
count reflects source documents successfully rewritten. Link style is
preserved: vault-root-relative links are rewritten as vault-root-relative;
source-directory-relative links (e.g. `../notes/target.md`) are rewritten
with the correct new relative path from the source file's directory.
`update_links` is silently ignored for attachments (non-`.md` files).

**`list_documents()` pattern parameter**: if provided, `pattern` is a Unix glob
matched against the relative path using `fnmatch.fnmatch()`. Example:
`pattern="Journal/*.md"` returns only documents in the Journal folder.

**`list_tags(field)` behavior**: queries only the `document_tags` table. If
`field` was not in `indexed_frontmatter_fields`, returns `[]`.

**`on_write` callback**:

```python
WriteCallback = Callable[[Path, str, Literal["write", "edit", "delete", "rename"]], None]
```

- `path`: absolute path on disk.
  - For `write`, `edit`: the file's final path after the operation.
  - For `rename`: the **new** path (old path is gone).
  - For `delete`: the path before deletion (file no longer exists on disk).
- `content`: updated file content as a string.
  - For `delete`: empty string `""` (file no longer exists).
  - For `rename` of a note (`.md`): the full file content at the new path.
  - For `rename` of an attachment: empty string `""` (binary content).
  - For `write` and `edit` of a note (`.md`): the new file content.
  - For `write` of an attachment: empty string `""` (binary content cannot be passed as a string).
- `operation`: the operation that triggered the callback.

**Contract**: the callback fires **outside** the write lock. It must not raise;
unhandled exceptions are logged at `ERROR` but not propagated to the caller.

Default: `None` (no callback). Built-in option: `GitWriteStrategy` (or the
legacy factory `git_write_strategy(token=...)`) that auto-commits and pushes.

**Deprecation note**: `git_write_strategy()` factory function is preserved for
backward compatibility. Prefer constructing `GitWriteStrategy` directly for
access to `flush()` and `close()` methods.

### `scanner.py` -- File Discovery and Parsing

```python
def scan_directory(
    source_dir: Path,
    *,
    glob_pattern: str = "**/*.md",
    exclude_patterns: list[str] | None = None,
    required_frontmatter: list[str] | None = None,
) -> Iterator[ParsedNote]: ...

def parse_note(path: Path, source_dir: Path) -> ParsedNote: ...
```

**Frontmatter parsing**: use `python-frontmatter` library. Schema-agnostic.
Documents without frontmatter get an empty dict and proceed normally.

**Exclude patterns**: glob patterns (e.g., `[".obsidian/**", "_templates/**"]`)
matched against relative paths from `source_dir` using `pathlib.Path.match()`.

**Fault tolerance**: documents that cannot be decoded as UTF-8 are skipped with
a `WARNING` log message. `scan_directory()` is fault-tolerant; a single bad
file does not abort the scan.

**UTF-8 BOM normalization (#673).** All vault-markdown reads strip a leading
UTF-8 BOM via `utils.text.read_text_utf8` (path → str) and `decode_utf8`
(bytes → str), both using the `utf-8-sig` codec. The scanner hashes the raw
on-disk bytes (BOM included) but decodes the text without the BOM, so a
BOM-prefixed file's frontmatter parses and is indexed correctly. Writes are
plain `utf-8` (no BOM), so the vault normalizes to no-BOM: a BOM-prefixed file
loses its BOM the next time it is rewritten. A genuinely non-UTF-8 file still
raises `UnicodeDecodeError`. The same `decode_utf8` is applied on **ingress**
(#681) — the `fetch` tool and the transfer-upload route decode externally
supplied markdown bodies through it, so a fetched/uploaded file is written
BOM-free rather than carrying its BOM until the next rewrite.

### `fts_index.py` -- SQLite FTS5

```python
class FTSIndex:
    def __init__(self, db_path: Path | str = ":memory:"): ...
    def build_from_notes(self, notes: Iterable[ParsedNote]) -> int: ...
    def upsert_note(self, note: ParsedNote) -> int: ...
    def delete_by_path(self, path: str) -> int: ...
    def search(self, query: str, *, limit: int = 10,
               folder: str | None = None,
               filters: dict[str, str] | None = None) -> list[FTSResult]: ...
    def get_note(self, path: str) -> dict | None: ...
    def list_notes(self, *, folder: str | None = None) -> list[dict]: ...
    def list_folders(self) -> list[str]: ...
    def list_field_values(self, field: str) -> list[str]: ...
    def close(self) -> None: ...
```

Uses the schema defined in [Database Schema](#database-schema). Note that
`FTSIndex.search()` returns `list[FTSResult]` (raw BM25 results), while
`ReaderFacet.search()` returns `list[GroupedResult]` (file-grouped results
with RRF scoring in hybrid mode; each file appears once with up to
`chunks_per_file` matching sections).

### `vector_index.py` -- Numpy Embeddings

Adapted from ifcraftcorpus `embeddings.py`. Rename `EmbeddingIndex` to
`VectorIndex`. The `load()` classmethod **must** import from
`markdown_vault_mcp.providers`, not `ifcraftcorpus.providers`.

The `VectorIndex` maintains a sidecar metadata list where each entry maps a
row index to `{path, title, folder, heading, content}`. This enables:
- Bulk deletion by document path (for reindex)
- Returning rich metadata with semantic search results

Key methods:
- `add(texts, metadata)` — embeds texts via the provider then appends rows.
- `add_vectors(raw_vectors, metadata)` — appends pre-computed float vectors
  (L2-normalised internally) **without** calling the provider. Use this when
  embeddings have been computed outside a lock section (see Thread Safety).
- `delete_by_path(path)` — removes all rows for a document.
- `save(path)` / `load(path, provider)` — persist/restore sidecar files.

### `providers.py` -- Embedding Providers

Copied from ifcraftcorpus, adapted:
- Rename env var prefix `IFCRAFTCORPUS_` to `MARKDOWN_VAULT_MCP_`
- Fix any hardcoded package imports
- Keep the same provider ABC and implementations (Ollama, OpenAI,
  SentenceTransformers)

### `tracker.py` -- Change Detection

```python
class ChangeTracker:
    def __init__(self, state_path: Path): ...
    def detect_changes(self, source_dir: Path,
                       glob_pattern: str = "**/*.md") -> ChangeSet: ...
    def update_state(self, notes: list[ParsedNote],
                     skipped: dict[str, str] | None = None) -> None: ...
    def reset(self) -> None: ...
```

`tracker.py` is entirely new code (no ifcraftcorpus equivalent). State file
format (version 2, #665): `{"version": 2, "indexed": {"Journal/note.md":
"sha256hex", ...}, "skipped": {"CLAUDE.md": "sha256hex", ...}}` as JSON.
The legacy flat `{"Journal/note.md": "sha256hex", ...}` format loads with
every entry treated as indexed.

### `server.py` -- Generic MCP Server

Uses **FastMCP 3.0+** with lifespan hooks for Vault init/teardown.

**Tool surface** mirrors LLM file tool semantics (Claude Code Read/Write/Edit
pattern). Each tool is annotated with MCP `ToolAnnotations`:

| Tool | Description | `readOnlyHint` | `destructiveHint` | `idempotentHint` |
|------|-------------|:-:|:-:|:-:|
| `search` | Search the vault by query | `True` | `False` | `True` |
| `read` | Read a document's full content | `True` | `False` | `True` |
| `list_documents` | List documents, optionally filtered | `True` | `False` | `True` |
| `write` | Create or overwrite a document | `False` | `False` | `True` |
| `edit` | Patch a section (read-before-edit) | `False` | `False` | `False` |
| `rename` | Rename/move a document (Phase 2-3) | `False` | `False` | `False` |
| `delete` | Delete a document | `False` | **`True`** | `True` |
| `list_folders` | List all folders | `True` | `False` | `True` |
| `list_tags` | List tag values for a field | `True` | `False` | `True` |
| `stats` | Vault statistics | `True` | `False` | `True` |
| `reindex` | Incremental reindex | `False` | `False` | `True` |
| `build_embeddings` | Build/rebuild vector embeddings | `False` | `False` | `True` |
| `embeddings_status` | Check embedding provider status | `True` | `False` | `True` |
| `get_backlinks` | Find documents that link to a path | `True` | `False` | `True` |
| `get_outlinks` | Find links from a document (with exists flag) | `True` | `False` | `True` |
| `get_broken_links` | Find links to non-existent documents | `True` | `False` | `True` |
| `get_similar` | Find semantically similar notes by path | `True` | `False` | `True` |
| `get_recent` | Get most recently modified notes | `True` | `False` | `True` |
| `get_context` | Get consolidated context dossier for a note | `True` | `False` | `True` |
| `get_orphan_notes` | Find notes with no inbound or outbound links | `True` | `False` | `True` |
| `get_most_linked` | Find notes ranked by number of inbound links | `True` | `False` | `True` |
| `get_connection_path` | Shortest undirected path between two notes (BFS, max 10 hops) | `True` | `False` | `True` |
| `fetch` | Download from URL and save to vault (MCP-to-MCP transfer) | `False` | `False` | `True` |

**Tool name note**: the MCP tool is registered as `list_documents` (not `list`)
to avoid shadowing Python's built-in `list`. The underlying
`ReaderFacet.list_documents()` method matches the MCP name; both deliberately
avoid the bare name `list` so type annotations like `list[NoteInfo]` are not
mis-resolved against the method in class scope.

**Tag-based visibility**: `write`, `edit`, `delete`, `rename`, `fetch` are always
registered but tagged with ``tags={"write"}``. When ``read_only=True``, the
server calls ``mcp.disable(tags={"write"})`` to hide them from clients.
This also hides any prompts sharing the ``write`` tag (e.g. ``research``,
``discuss``, ``create_from_template``). The Vault still raises ``ReadOnlyError`` as a defence-in-depth
guard if a write method is somehow called on a read-only instance.

**Dynamic instructions**: the server's MCP `instructions` string varies with
`read_only` mode. When `read_only=True`, the instructions state this is a
read-only instance; when `read_only=False`, they describe write tool semantics.
This signals capability status to clients and reduces irrelevant prompting.

**Tool semantics**:
- `read(path)` returns full file content + frontmatter metadata
- `write(path, content, frontmatter?)` creates or overwrites entire file
- `edit(path, old_text, new_text)` reads file, verifies `old_text` exists
  exactly once, replaces, writes back. Fails on not-found or ambiguous match.
- `delete(path)` removes file, updates index, triggers `on_write`
- `fetch(url, path, frontmatter?, if_match?, timeout_s?)` downloads content
  from an HTTP/HTTPS URL and dispatches to `write()` (for `.md` paths) or
  `write_attachment()` (for other extensions). Requires `httpx` (included in
  `[all]` extra). Only `http` and `https` schemes are allowed (SSRF guard).

These semantics are intentionally close to Claude Code's file tools for
familiarity. LLMs that know how to read/write/edit files can use these tools
without special prompting.

**Dependency injection**: tools and resources use FastMCP's
``Depends(get_vault)`` to access the Vault instance from
lifespan context, eliminating module-level globals. Prompts are pure
template functions with no vault dependency.

**Resources**: the server exposes 6 read-only MCP resources:

| URI | Source | Description |
|-----|--------|-------------|
| ``config://vault`` | ``VaultConfig`` | Source dir, read-only flag, indexed fields, extensions |
| ``stats://vault`` | ``ReaderFacet.stats()`` | Document/chunk/folder counts, capabilities |
| ``tags://vault`` | ``ReaderFacet.list_tags()`` | All tags grouped by indexed field |
| ``tags://vault/{field}`` | ``ReaderFacet.list_tags(field)`` | Flat list for one field (template) |
| ``folders://vault`` | ``ReaderFacet.list_folders()`` | Sorted folder path list |
| ``toc://vault/{path}`` | ``ReaderFacet.get_toc(path)`` | Document headings with synthetic H1 title |

Resources return JSON (``mime_type="application/json"``). The ToC resource
queries the existing ``sections`` table — no file I/O.

**Prompts**: 6 built-in prompt templates, plus optional user-defined prompts:

| Prompt | Parameters | Tags | Description |
|--------|-----------|------|-------------|
| ``summarize`` | ``path`` | — | Summarize a document |
| ``research`` | ``topic`` | ``write`` | Search and consolidate as new note |
| ``discuss`` | ``path`` | ``write`` | Analyze and suggest improvements |
| ``create_from_template`` | ``template_name`` (optional) | ``write`` | Discover/read/fill/write from a template |
| ``related`` | ``path`` | — | Find related notes, suggest cross-references |
| ``compare`` | ``path1, path2`` | — | Compare two documents |

Write-tagged prompts are hidden in read-only mode by the same
``mcp.disable(tags={"write"})`` call that hides write tools.

**User-defined prompts**: when ``MARKDOWN_VAULT_MCP_PROMPTS_FOLDER`` is set,
``register_prompts()`` scans the directory for ``.md`` files at startup and
registers each as an MCP prompt. The file stem becomes the prompt name.
Frontmatter defines metadata:

```yaml
---
description: "One-line description shown to clients"
arguments:
  - name: path
    description: "Path to the note"
    required: true
  - name: topic
    description: "Optional topic focus"
    required: false
tags:
  - write   # optional: hidden in read-only mode
---

Prompt content here. Use $path and $topic as placeholders (string.Template syntax).
```

**Override semantics**: if a user-defined prompt has the same name as a
built-in, the built-in is skipped and the user's version is registered.
This allows domain-specific workflows (e.g. the ``zettelkasten`` or
``para-triage`` prompts) to live outside the core server and be mounted
at deployment time.

## Configuration

### Phase 1: Python API Only

Configuration is the `Vault` constructor. No config files.

### Phase 2: Environment Variables

For MCP server deployment:

| Variable | Description | Default |
|----------|-------------|---------|
| `MARKDOWN_VAULT_MCP_SERVER_NAME` | MCP server name shown to clients | `markdown-vault-mcp` |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | System-level instructions for LLM context | generic description |
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | Path to markdown files | required |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | Disable write tools | `true` |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | SQLite index path | in-memory |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | Embeddings directory | disabled |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | Comma-separated frontmatter fields to index | none |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | Comma-separated required frontmatter fields | none |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | Comma-separated glob patterns to exclude | none |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | Relative folder path for note templates used by `create_from_template` | `_templates` |
| `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER` | Path to a directory of user-defined `.md` prompt files; each file becomes an MCP prompt (user prompts override built-ins by name) | disabled |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | HTTPS URL for managed git mode (clone + remote validation) | disabled |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | Username for HTTPS token auth prompts | `x-access-token` |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | Token/password for HTTPS git auth | disabled |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | Seconds between ff-only pull ticks | `600` |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | Seconds of idle before git push (0 = push on shutdown only) | `30` |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | Committer name for auto-commits | `markdown-vault-mcp` |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | Committer email for auto-commits | `noreply@markdown-vault-mcp` |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | Run `git lfs pull` on startup to resolve LFS pointer files | `true` |
| `MARKDOWN_VAULT_MCP_BEARER_TOKEN` | Static bearer token for simple auth — clients send `Authorization: Bearer <token>` | none |
| `MARKDOWN_VAULT_MCP_BASE_URL` | Server's public URL — required for OIDC auth and MCP Apps domain computation (e.g. `https://mcp.example.com`) | none |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | OIDC discovery URL (`/.well-known/openid-configuration`) | none |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | OIDC client ID registered with the provider | none |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | OIDC client secret | none |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | Persistent JWT signing key — **required for Docker/Linux** to survive restarts | ephemeral on Linux |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | JWT audience claim (required by some providers) | none |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | Comma-separated OAuth scopes to request | `openid` |
| `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN` | Verify the upstream access token as JWT instead of the id token. Set `true` only when your provider issues JWT access tokens and you need audience-claim validation on that token | `false` (verify id token) |
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | Comma-separated allowlist of non-.md extensions (without dot), e.g. `pdf,png,docx`; use `*` to allow all non-.md files | common list (pdf, png, jpg, docx, …) |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | Maximum attachment size in MB, enforced by the `read` / `write` / `fetch` MCP tools (not the vault library); `0` disables the limit | `1.0` |
| `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES` | Maximum bytes returned by full-document `read()` for `.md` files; raises `ValueError` if exceeded. `read(path, section=...)` for partial reads bypasses the cap. `0` disables the limit | `262144` (256 KB) |
| `MARKDOWN_VAULT_MCP_APP_DOMAIN` | Claude app domain for MCP Apps iframe sandboxing; auto-computed from `BASE_URL` via `_compute_claude_app_domain()` | derived from `BASE_URL` |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | `openai`, `ollama`, `fastembed` | auto-detect |
| `OLLAMA_HOST` | Ollama server URL | `http://localhost:11434` |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | Ollama embedding model | `nomic-embed-text` |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | Force CPU-only inference | `false` |
| `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` | FastEmbed model | `BAAI/bge-small-en-v1.5` |
| `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` | FastEmbed model cache directory | FastEmbed default |
| `OPENAI_API_KEY` | OpenAI API key | none |
| `OPENAI_BASE_URL` / `MARKDOWN_VAULT_MCP_OPENAI_BASE_URL` | OpenAI-compatible API base URL (SiliconFlow, Together, internal gateways, …) | `https://api.openai.com/v1` |
| `OPENAI_EMBEDDING_MODEL` / `MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL` | OpenAI-compatible embedding model name | `text-embedding-3-small` |

#### Example Configurations

**Obsidian vault (read-only)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/home/user/Obsidian
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**
MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=ollama
```

**IF Craft Corpus (strict frontmatter)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/corpus
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_REQUIRED_FIELDS=title,cluster
MARKDOWN_VAULT_MCP_INDEXED_FIELDS=cluster,topics
```

**Obsidian vault (read-write, git-backed)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**,_templates/**
MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/acme/vault.git
MARKDOWN_VAULT_MCP_GIT_USERNAME=x-access-token
MARKDOWN_VAULT_MCP_GIT_TOKEN=ghp_xxx
```

**Bearer token auth (simple)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_BEARER_TOKEN=your-secret-token
```

**Obsidian vault with OIDC auth (Authelia)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-random-secret   # required on Linux/Docker
```

#### Authentication

The server supports four auth modes:

1. **Multi-auth** — both bearer token and OIDC configured simultaneously; either credential is accepted (`FastMCP.MultiAuth`)
2. **Bearer token** — simple static token via `MARKDOWN_VAULT_MCP_BEARER_TOKEN` (only OIDC vars absent)
3. **OIDC** — full OAuth 2.1 flow via `OIDCProxy` (only bearer token absent; requires `BASE_URL`, `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`)
4. **No auth** — server accepts all connections (default)

When both `BEARER_TOKEN` and the OIDC variables are set, the server uses `MultiAuth(server=oidc_auth, verifiers=[bearer_auth])` so that bearer-token clients and OIDC clients can connect to the same instance. `OIDCProxy` goes in `server=` so that `MultiAuth.get_routes()` delegates the OAuth authorization, token, and discovery endpoints to it; `StaticTokenVerifier` goes in `verifiers=`.

#### Bearer Token Authentication

Set `MARKDOWN_VAULT_MCP_BEARER_TOKEN` to a secret string. Clients must send an `Authorization: Bearer <token>` header with every request. Uses FastMCP's `StaticTokenVerifier` — no external dependencies or identity providers needed.

Best for deployments behind a VPN, in a Docker compose stack, or on a private network where full OIDC is unnecessary.

#### OIDC Authentication

When all four required vars are set (`BASE_URL`, `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`), the server uses FastMCP's `OIDCProxy` to authenticate MCP clients via OAuth 2.1 + PKCE. The server auto-discovers provider endpoints from the OIDC discovery URL so no additional endpoint configuration is needed.

**Token verification:** By default the server verifies the upstream `id_token` (always a standard JWT per OIDC Core) rather than the `access_token`. This works with all providers, including those that issue opaque (non-JWT) access tokens (e.g. Authelia). Set `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN=true` to revert to access-token JWT verification when audience-claim validation on that token is required.

**Token lifetime recommendations:** MCP clients do not reliably refresh tokens (see [Known Limitations](guides/authentication.md#known-limitations-mcp-oauth-token-refresh)). Configure all token lifetimes on your identity provider: `access_token: '8h'`, `id_token: '8h'`, `refresh_token: '30d'`. The `id_token` lifetime is critical when using `verify_id_token` mode — if shorter than `access_token`, the session dies at the `id_token` expiry regardless of the access token setting. Include `offline_access` in provider-side scopes for when clients support refresh.

**Authelia client registration** (in your Authelia `configuration.yml`):
```yaml
identity_providers:
  oidc:
    lifespans:
      custom:
        mcp_long_lived:
          access_token: '8h'
          id_token: '8h'
          refresh_token: '30d'
    clients:
      - client_id: markdown-vault-mcp
        client_secret: '$pbkdf2-sha512$...'   # hashed secret
        lifespan: 'mcp_long_lived'
        redirect_uris:
          - https://mcp.example.com/auth/callback
        grant_types:
          - authorization_code
          - refresh_token
        response_types:
          - code
        pkce_challenge_method: S256
        scopes:
          - openid
          - profile
          - email
          - offline_access
        token_endpoint_auth_method: client_secret_post
```

**Linux/Docker note:** FastMCP uses an ephemeral JWT signing key on Linux by default — every restart invalidates all client tokens and forces re-authentication. Set `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` to a stable random secret (e.g. `openssl rand -hex 32`) to persist tokens across restarts.

#### Attachment Support

The server supports reading and writing non-markdown binary files (PDFs, images, etc.) by overloading the existing MCP tools — no new tool registrations.

**Extension-based dispatch**: `.md` files always follow the markdown path. All other extensions are treated as attachments if they appear in the configured allowlist.

**Default allowlist**: `pdf png jpg jpeg gif webp svg bmp tiff docx xlsx pptx odt ods odp zip tar gz mp3 mp4 wav ogg txt csv tsv json yaml toml xml html css js ts`. The `.md` extension is always excluded regardless of configuration.

**Tool behaviour changes**:

| Tool | .md path | Attachment path |
|------|----------|-----------------|
| `read(path)` | returns markdown body + frontmatter | returns `{path, mime_type, size_bytes, content_base64, modified_at}` |
| `write(path, content, frontmatter, content_base64)` | uses `content` + `frontmatter` | uses `content_base64` (base64-encoded bytes) |
| `list_documents(include_attachments=False)` | notes only (unchanged) | also returns `AttachmentInfo` entries with `kind="attachment"`, `mime_type` |
| `delete(path)` | removes file + index entries | removes file only (no index) |
| `rename(old, new)` | moves file + updates index | moves file only (no index) |
| `stats()` | includes `attachment_extensions` list | — |

Attachments are **not indexed or searched** — only direct path-based read/write/delete/rename. MIME type is detected via Python's `mimetypes.guess_type()` (no extra dependencies).

The cap is enforced by the `read`, `write`, and `fetch` MCP tools — the layer where attachment bytes flow through the LLM context as base64. The vault library's `read_attachment()` / `write_attachment()` accept any size, so out-of-band byte movement (e.g. an HTTP transfer route) is not gated by it. Set `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB=0` to disable the limit. The default was tightened from 10 MB to 1 MB in #442 to keep LLM context bounded — most contexts can't survive a 10 MB base64-encoded attachment, so the old default was a silent context-blow-up. The error message names `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` as the knob to raise if the bytes are genuinely needed in context.

A parallel cap on whole-document `read()` for `.md` files (`MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES`, default 256 KB) raises `ValueError` with a message pointing at `read(path, section=heading)` for partial reads. Section reads bypass the cap because they only load one chunk.

#### MCP Apps

MCP Apps are browser-based views that MCP clients supporting the protocol can render inline (in a sidebar or panel) or fullscreen. They augment tool-based interaction with direct visual exploration.

**Resource URI**: `ui://vault/app.html` — the entire application is a single HTML resource registered with `visibility="app"`. This keeps it out of the standard tool list and exposes it only to clients that support the MCP Apps protocol.

**Display modes**:
- **Inline**: rendered in a client sidebar or panel alongside the conversation
- **Fullscreen**: rendered in a dedicated tab or window

**Four views** are bundled in the single resource:

| View | `view` value | Description |
|------|-------------|-------------|
| Context Card | `context` | Note dossier — backlinks, outlinks, similar notes, tags, and last-modified time for the note in focus |
| Graph Explorer | `graph` | Interactive force-directed link graph of the entire vault; nodes are notes, edges are links |
| Vault Browser | `browse` | Searchable, filterable file tree for direct vault navigation without issuing tool calls |
| Note Preview | `note` | Full-width rendered markdown preview with frontmatter table and action buttons |

**Primary tools** (visible to LLM, launch apps):

| Tool | Description |
|------|-------------|
| `browse_vault` | Opens the SPA with optional `path` and `view` (`context`, `graph`, `browse`, `note`); returns text summary for non-Apps clients |
| `show_context` | Opens the Context Card for a path; returns text summary for non-Apps clients |

**App-only tools** (`visibility="app"`): registered but hidden from the standard MCP tool list, serving MCP Apps clients only:

| Tool | Description |
|------|-------------|
| `_vault_context` | Returns full NoteContext JSON for the Context Card view |
| `_vault_graph_neighborhood` | Returns `{nodes, edges}` for a note's link neighborhood |
| `_vault_graph_hubs` | Returns `{nodes, edges}` for the most-linked hub notes |
| `_vault_list` | Returns `{folders, notes}` for a vault directory |
| `_vault_read` | Returns note content, frontmatter, and metadata |
| `_vault_search` | Returns search results with snippets (default mode `hybrid`) |

**View navigation behavior**: backlinks, outlinks, and similar-note items rendered inside the Context Card are clickable, and a click loads that note's context **in the same Context Card view** rather than switching the active view. A dedicated "Open in Browser" button (`ctx-browse-btn`) is the only path that calls `navigateTo('browse', {path})` to switch into the Vault Browser. This supersedes any earlier wording that suggested item clicks themselves perform cross-view navigation — keeping the click in-view preserves an exploration flow without losing scroll position or the surrounding dossier.

**Host context updates**: the Graph Explorer calls `app.updateContext(...)` whenever the active note or the visible neighborhood changes, supplying the active path, title, visible node count, and visible link count. The exact wording of the string is an implementation detail of the SPA; clients should read the structured fields (path/title/counts) rather than parse the string.

**Vendored dependencies** (bundled inline at build time via `scripts/vendor_spa.py`):
- `vis-network` — force-directed graph rendering for Graph Explorer
- `marked.js` — markdown-to-HTML rendering for note previews
- `DOMPurify` — XSS sanitization for all rendered HTML
- `@modelcontextprotocol/ext-apps` — MCP Apps SDK lifecycle and theming

**Domain configuration**: MCP Apps iframes are sandboxed to a specific Claude app domain. The server computes it from `MARKDOWN_VAULT_MCP_BASE_URL` via `_compute_claude_app_domain()`. Override with `MARKDOWN_VAULT_MCP_APP_DOMAIN` when `BASE_URL` does not reflect the actual hostname visible to the Claude client (e.g. behind a proxy, or on a custom domain).

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_APP_DOMAIN` | (derived from `BASE_URL`) | Override Claude app domain for MCP Apps iframe sandboxing |

### Phase 3: Evaluate YAML

If multi-vault or complex per-vault settings are needed, add YAML
config using `pydantic-settings` for type validation and env var overlay.
Evaluate at deploy time, not before.

## Packaging

```toml
[project]
name = "markdown-vault-mcp"
requires-python = ">=3.10"
dependencies = [
    "python-frontmatter>=1.0",
]

[project.optional-dependencies]
mcp = ["fastmcp>=3.0,<4"]
embeddings-api = ["httpx>=0.25", "numpy>=1.20"]  # httpx also used by fetch tool
embeddings = ["fastembed>=0.3", "numpy>=1.20"]
all = ["fastmcp>=3.0,<4", "httpx>=0.25", "fastembed>=0.3", "numpy>=1.20"]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff>=0.1", "mypy>=1.0"]

[project.scripts]
markdown-vault-mcp = "markdown_vault_mcp.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Note: `pyyaml` is not listed as a direct dependency; it is a transitive
dependency of `python-frontmatter`.

## Deployment

### Docker

Same pattern as ifcraftcorpus: `python:3.12-slim` base, `uv` for installs.
CI/CD, GitHub Actions, and PyPI publishing adapted from ifcraftcorpus with
minimal changes.

**Volume layout**: two volumes — `/data/vault` (user content, bind-mount or
named volume) and `/data/state` (all internal state: SQLite index, embeddings,
FastEmbed model cache, OIDC proxy state via `FASTMCP_HOME`). The Dockerfile
sets `FASTMCP_HOME=/data/state/fastmcp` so OIDCProxy persists JTI mappings
and upstream tokens across restarts.

**Entrypoint + gosu privilege drop**: the container starts as root and runs
`docker-entrypoint.sh`, which chowns `/data/*` to `appuser` (fixing
root-owned named volumes), then drops to non-root via `gosu` before
executing the application. Runtime UID/GID override via `PUID`/`PGID`
env vars (default 1000/1000). This is the same pattern used by official
PostgreSQL, Redis, and MySQL images.

Deployed behind **litellm MCP gateway + mcp-auth-proxy** (same as
ifcraftcorpus).

### Write + Git Integration

Three git modes:

1. **Managed mode** (`GIT_REPO_URL` set): server owns git lifecycle.
   Startup clones into `SOURCE_DIR` if empty, or verifies existing `origin`
   matches the configured repo URL. Pull loop + commit + deferred push enabled.
2. **Unmanaged / commit-only mode** (no `GIT_REPO_URL`): server commits local writes if
   `SOURCE_DIR` is already a git repo, but never pulls or pushes.
3. **No-git mode**: when `SOURCE_DIR` is not a git repo, git callbacks no-op.

Backward compatibility: `GIT_TOKEN` without `GIT_REPO_URL` keeps previous
pull+push behavior against the existing checkout but logs a deprecation warning.

In managed/legacy push-enabled modes, `GitWriteStrategy` commits per-write and
defers push to a background timer (`MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S`,
default 30s). After the idle period elapses with no writes, all accumulated
local commits are pushed in a single `git push`. On shutdown,
`Vault.close()` flushes any pending push.

Startup recovery: `GitWriteStrategy` checks for unpushed local commits
(`git log @{upstream}..HEAD`) on first invocation and pushes them before
accepting new writes.

**Deferred push mechanics**: `GitWriteStrategy` uses a `threading.Timer` that
resets on each write. After `push_delay_s` seconds of idle, all accumulated
local commits are pushed in a single `git push`. `flush()` cancels the timer
and pushes synchronously. `close()` calls `flush()` and marks the strategy as
closed (subsequent writes are ignored).

For private repos, HTTPS token auth uses:

- `MARKDOWN_VAULT_MCP_GIT_USERNAME` (default `x-access-token`)
- `MARKDOWN_VAULT_MCP_GIT_TOKEN`

Provider-specific usernames:

- GitHub: `x-access-token`
- GitLab: `oauth2`
- Bitbucket: account username

**Git credential security**: when a token is supplied, `GitWriteStrategy` uses
a `GIT_ASKPASS` temporary script rather than embedding the token in any
command-line argument. This means the token is never visible in
`/proc/<pid>/cmdline` or process listings. The script is created with `0o700`
permissions (owner-only) and deleted in a `finally` block regardless of push
outcome. Tokens are also **redacted** from all error log messages (replaced
with `***`).

**Git LFS support**: when `MARKDOWN_VAULT_MCP_GIT_LFS=true` (default),
`GitWriteStrategy` calls `_lfs_pull()` once during lazy initialisation — after
startup recovery (`_push_if_unpushed()`), outside the init lock — to resolve
LFS pointer files before the first write is committed. `_lfs_pull()` runs
`git lfs pull` in the vault root; failures (including `git lfs` not installed
or any non-zero exit) are logged at ERROR and never propagated to the caller.
Set `MARKDOWN_VAULT_MCP_GIT_LFS=false` for repos that do not use LFS, or when
`git-lfs` is not available on PATH.

**Periodic pull (ff-only)**: in push-enabled managed/legacy modes, when
`MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S > 0` (default `600`), the server:

- Runs one `git fetch` + ff-only update **before** the initial `build_index()`
  so the index scans the freshest working tree.
- Starts a daemon thread that repeats `fetch + ff-only update` every interval.
- After a successful fast-forward that advanced `HEAD`, triggers
  `IndexFacet.reindex()` to incrementally update the index.
- Blocks write operations during the **reindex phase** of each pull tick
  (not during fetch/ff-only merge) by acquiring the Vault write lock.
  Read/search operations are not blocked at the Python level (SQLite WAL
  enables concurrent readers during index writes).
- If `MARKDOWN_VAULT_MCP_GIT_LFS=true`, each successful pull tick ends with
  `git lfs pull` so LFS pointer files are resolved before reads and indexing.

Safety branch mode for push failures is tracked separately (see #119).

**Git history queries**: `GitWriteStrategy` exposes two read-only methods for querying the git commit log without modifying any state:

- `get_file_history(repo_path, path, since, limit, until=None)` — runs `git log` with a sentinel-delimited format string to enumerate commits touching a note or the entire vault. Uses ASCII Record Separator (`\x1e`) as a block delimiter so commit records can be parsed reliably regardless of commit message content. Vault-wide queries append `--name-only` to include changed file paths per commit. Both `since` and `until` are passed through verbatim to `git log` and are inclusive at the boundary.
- `get_file_diff(repo_path, path, ref, per_commit, since_timestamp=None, limit=None)` — runs `git diff` or `git show` to produce unified diffs. When `since_sha` is provided (validated as `[0-9a-f]{4,40}`), it is used directly as the ref. When `since_timestamp` is provided, `git rev-list --before=<ts> -1 HEAD` resolves it to a SHA (boundary **inclusive**: `--before` returns the most recent commit at or before that instant — a commit whose committer date equals the timestamp IS the resolved ref). When `per_commit=True` and `limit` is set, the inner `git log` adds `-n{clamped_limit}` (clamped to `[1, 100]`) to cap the number of commits walked — useful for keeping per-commit responses within LLM context budgets. Output exceeding 50 KB is truncated with a `[diff truncated: N bytes omitted]` note. `CalledProcessError` from an unknown ref is re-raised as `ValueError`.

Both methods use the existing `_git_env()` / `_cleanup_git_env()` pattern for credential forwarding and cleanup. Path arguments are always validated via `Vault._validate_path()` before being passed to the git layer. No shell injection is possible because all subprocess calls use list arguments with `shell=False`.

**MCP response envelope**: the MCP wrappers for `get_history` and `get_diff(per_commit=True)` return a `{"commits": [...], "total": N}` envelope rather than a bare list, so the structured payload is self-describing on the wire. FastMCP otherwise auto-wraps list-typed tool returns under a synthetic `"result"` key (`x-fastmcp-wrap-result: true` in the output schema), which forces clients re-reading persisted MCP content to know FastMCP's wrapping convention to find the data. The Python facade (`ReaderFacet.get_history`, `ReaderFacet.get_diff`) stays list-returning — only the MCP-tool wrapper transforms to the envelope. `get_diff(per_commit=False)` keeps its existing `{"diff": str}` shape since it is already self-describing.

**Attachment history & diff (#342).** `get_history` and `get_diff` accept a
`.md` note OR a configured attachment, validated by `validate_history_path`
(distinct from the strict-`.md` `validate_path` that the write/edit/read paths
use). `get_diff` lets git classify each attachment: a binary file (git
`--numstat` reports `-\t-`) returns a `git diff --stat` size/rename summary,
while a text attachment (`.svg`, `.csv`, …) returns a full unified diff. `.md`
notes are unchanged. The per-commit path (`per_commit=True`) is rename-aware
per commit (#683): for each commit it resolves the path's rename against
that commit's parent (`resolve_path_at_ref(git_root, "{sha}^", commit_path, …,
to_ref=sha)`) and diffs the two blobs (`git diff {sha}^:old {sha}:new`),
classifying binariness per commit — so a renamed binary pairs into
`{old => new} | Bin OLD -> NEW` instead of an add/text stat. A copied file
renders as a plain add (binary classification still correct; copy records are
skipped by `resolve_path_at_ref`). Non-rename commits use
`git diff {sha}^..{sha} -- path` (behaviour-preserving), and a parent-less
(root) commit falls back to the add-form `git show`. A pure rename with
byte-identical content produces an empty two-blob diff; the per-commit path
synthesizes a `{old} => {new} (renamed, no content change)` marker in that case
(#683). Known bounds: per-commit rename pairing relies on git's
`--find-renames=30` similarity threshold (a binary rename with a very large edit
may render as separate add/delete rather than a paired `{old => new}` stat), and
a per-commit diff of a merge commit renders the first-parent diff rather than a
combined diff.

### Release channels

The release workflow (`.github/workflows/release.yml`) publishes two
distinct channels via a single `workflow_dispatch` trigger:

- **Stable** (`prerelease: false`): full pipeline. semantic-release
  cuts a `vX.Y.Z` tag, PyPI receives the wheel + sdist, the Docker
  image publishes `:latest`, `:vX.Y.Z`, `:vX.Y`, `:vX`, `.deb`/`.rpm`
  packages attach to the GitHub Release, the Claude Code catalog PR
  opens in `pvliesdonk/claude-plugins`, and the MCP Registry receives
  the new `server.json`. Intended for promoting a verified build to
  every distribution surface.

- **Pre-release** (`prerelease: true`, the dispatch default):
  exercises the full pipeline without touching public catalogs.
  semantic-release cuts a `vX.Y.Z-rc.N` tag and marks the GitHub
  Release as a pre-release. The Docker image publishes `:unstable`
  and `:vX.Y.Z-rc.N` only — `:latest`, `:vX.Y`, `:vX` never move on
  a pre-release. The mcpb bundle is built and attached to the
  pre-release for manual smoke-test in Claude Desktop. PyPI, linux
  packages, the Claude Code catalog PR, and the MCP Registry publish
  are all skipped. This is the default dispatch mode so real releases
  require an explicit opt-out.

The `build-mcpb` and `publish-mcpb` jobs run unchanged in both modes:
`build-mcpb` reads `needs.release.outputs.version` (which already
carries the rc suffix on pre-release) and threads it through
`envsubst '${VERSION}'` into `packaging/mcpb/manifest.json.in` and
`pyproject.toml.in`, then `publish-mcpb` uploads the resulting
`.mcpb` to the GitHub Release. No committed manifest bump is needed
for the bundle.

### Future Work

- **FastMCP OAuth**: implemented via `OIDCProxy` — see OIDC Authentication section above.

## Implementation Plan

### Phase 1: Core Library + API Validation

**API surface**: `Vault.__init__`, `search`, `read`, `list`,
`build_index`, `reindex`, `build_embeddings`, `embeddings_status`,
`list_folders`, `list_tags`, `stats`.

1. Create repo structure, packaging, CI/CD (adapted from ifcraftcorpus)
2. Copy + adapt `providers.py` and `embeddings.py` (rename to `vector_index.py`)
3. Implement `scanner.py` -- frontmatter parsing, heading-based chunking,
   `ChunkStrategy` protocol
4. Implement `fts_index.py` -- generic FTS5 with `document_tags`, RRF hybrid
5. Implement `tracker.py` -- hash-based change detection
6. Implement `vault.py` -- thin facade tying it all together
7. Tests for all modules (fixtures with sample .md files covering: no
   frontmatter, partial frontmatter, malformed YAML, deep headings, unicode,
   invalid UTF-8)
8. **Validate API**: configure `Vault` with ifcraftcorpus settings
   (`required_frontmatter=["title", "cluster"]`,
   `indexed_frontmatter_fields=["cluster", "topics"]`). Build index, run
   search, verify tag filtering works. If the API doesn't accommodate the
   corpus use case, fix it before Phase 2.

### Phase 2: MCP Server + CI/CD

**API surface adds**: MCP tools, CLI.

9. Implement `server.py` with all read-only tools, `ToolAnnotations`,
   lifespan hooks
10. Implement `cli.py` -- `serve`, `index`, `search`, `reindex` commands
11. Configuration loading (env vars)
12. Docker + GitHub Actions + PyPI (adapted from ifcraftcorpus)
13. Validate against Obsidian vault (`pvliesdonk/obsidian.md`) as read-only
    vault
14. MCP tool integration tests using FastMCP test client

### Phase 3: Deploy + Write Support

**API surface adds**: `write`, `edit`, `delete`, `rename`.

15. Deploy to homelab (Traefik + mcp-auth-proxy)
16. Write support: `write`, `edit`, `delete` tools
17. `on_write` callback with git strategy
18. `rename` tool
19. Test with Obsidian vault in read-write mode
20. Evaluate YAML config need

### Phase 4: Publish + ifcraftcorpus Refactor

21. Publish markdown-vault-mcp 1.0 to PyPI
22. Refactor ifcraftcorpus to depend on markdown-vault-mcp
23. ifcraftcorpus becomes thin wrapper + domain tools + subagent prompts

## Testing Strategy

- **Fixtures**: `tests/fixtures/` directory with sample vault documents in
  several shapes: no frontmatter, minimal frontmatter, full frontmatter,
  malformed YAML, deeply nested headings, unicode, empty files, invalid UTF-8.
- **Unit tests**: scanner (frontmatter parsing, chunking, required_frontmatter
  filtering, UTF-8 fault tolerance), FTS index (CRUD, search, tag filtering,
  RRF hybrid), change tracker (detect changes, update state), vector index
  (add, search, save/load, metadata consistency).
- **Integration tests**: Vault end-to-end (scan -> index -> search ->
  reindex), write + reindex roundtrip (write makes content searchable),
  MCP server tool invocations via FastMCP test client.
- **Regression tests**: hybrid score ordering (search for a query that matches
  in both FTS5 and semantic; verify RRF merges ranks so neither signal
  dominates), document identity (same filename in different folders produces
  distinct results), frontmatter-less documents indexed correctly.
- **API validation**: Phase 1 includes a test that configures `Vault`
  with ifcraftcorpus settings and verifies search + tag filtering work.
- **Coverage**: enforce with `coverage.py` `fail_under` (same pattern as
  ifcraftcorpus).

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| VRAM contention (Ollama on RTX 4060 8GB) | `cpu_only` mode, batch embeddings |
| Vault scale (numpy in-memory) | Fine for thousands of documents. If tens of thousands, evaluate Qdrant. |
| Concurrent writes (Obsidian + MCP) | Use git as sync layer. MCP server should not write directly to live Obsidian vault without git in between. |
| FastMCP breaking changes | Pin `>=3.0,<4`. Monitor for 4.0 migration. |
| `Vault` API doesn't fit ifcraftcorpus | Validate in Phase 1 before building MCP server. |

## Appendix: Decision Log

Decisions made during design review (2026-03-07):

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| 1 | Document identity | Relative path with `.md` extension | Avoids collisions between same-name files in different folders |
| 2 | Frontmatter handling | Optional by default; `required_frontmatter` config | Obsidian vaults rarely have frontmatter; ifcraftcorpus requires it |
| 3 | Hybrid scoring | Reciprocal Rank Fusion (RRF) | Fixes latent bug: raw BM25 vs cosine comparison |
| 4 | Phasing | Validate API against ifcraftcorpus in Phase 1 | Discover API mismatches before shipping |
| 5 | Code reuse | Copy + adapt (not move) | ifcraftcorpus stays as-is; temporary duplication accepted |
| 6 | Module structure | Thin facade + specialized modules | Avoid fat modules; prefer focused abstractions |
| 7 | Frontmatter filtering | `document_tags` table (indexed) + raw JSON blob | Performant multi-value filtering without `json_extract()` |
| 8 | Configuration | Phase 1: API. Phase 2: env vars. Phase 3: evaluate YAML | Avoid premature config complexity |
| 9 | Chunking | `heading` + `whole` with `ChunkStrategy` protocol | Extensible without over-engineering; `sliding` deferred |
| 10 | FastMCP | Pin `>=3.0,<4`; lifespan hooks; follow conventions | Proper init/teardown; forward-compatible |
| 11 | Write support | Separate frontmatter param; generic `on_write` callback | Git strategy as built-in; extensible for future strategies |
| 12 | Docker/CI | Bring early (Phase 2); adapt from ifcraftcorpus | Proven infrastructure, minimal changes needed |
| 13.1 | Error handling | Library raises; MCP catches and returns structured | Clean separation of concerns |
| 13.2 | Logging | Follow FastMCP conventions; `logging.getLogger(__name__)` | Standardized, no `print()` |
| 13.3 | Concurrency | Library sync; `asyncio.to_thread()` in MCP layer | Appropriate for single-user; async provider as future work |
| 13.4 | FTS5 schema | `path`, `title`, `folder`, `heading`, `content` | Generic; domain filtering via `document_tags` |
| 13.5 | File extension | Include `.md` in document identifier | Unambiguous, matches filesystem |
| 14 | Python library use | Document as use case; `Vault` is primary API | MCP is one consumer; LangChain wrapper is downstream |
| 15 | Rename | Include in design, defer to Phase 2-3 | Touches every layer; not critical for initial release |
| 16 | Tool semantics | Mirror Claude Code Read/Write/Edit; MCP `ToolAnnotations` | Familiar to LLMs; `delete` marked destructive |
