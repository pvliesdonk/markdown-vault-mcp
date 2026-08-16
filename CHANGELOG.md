# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `summarize` accepts a per-call `max_notes` argument, clamped to the
  server's `SUMMARIZE_MAX_NOTES` cap, and the response now reports the
  effective limit as `notes_limit`. When notes were omitted the response
  also carries a `hint` string telling the calling model to summarize
  subfolders in separate calls for full coverage (#925). The live
  configured limit is substituted into the tool description and the server
  instructions at startup, so calling models can plan folder splits before
  their first call.

### Changed

- `summarize` now handles inputs larger than one model request map-reduce
  style (#922): notes are packed into batches of at most
  `SUMMARIZE_MAX_INPUT_CHARS` characters, each batch is summarized (with
  bounded parallelism), and the partial summaries are combined into the
  final result. `SUMMARIZE_MAX_INPUT_CHARS` is therefore now a per-request
  budget rather than a coverage cap — content that previously fell off the
  end of one request is now covered, at the cost of additional model calls
  on large selections. Coverage remains capped by `SUMMARIZE_MAX_NOTES`.
  The response gains `notes_included` / `notes_omitted` counts so callers
  can see exactly how much of the selection the summary covers.

### Fixed

- `summarize` output no longer ends with assistant-style offers of further
  help ("If you want I can..."): the system prompts now instruct the model
  to output only the summary itself — a tool result is terminal, so such
  offers are unanswerable noise (#921).

- `summarize` failed with `finish_reason=length` and empty output on
  reasoning models (the default `gpt-5-mini`): the token budget covers
  internal reasoning, so the old 2048 default was exhausted before any
  summary text was emitted (#919). The server now requests
  `reasoning_effort="low"` (dropped automatically on OpenAI-compatible
  servers that reject the parameter), the `SUMMARIZE_MAX_TOKENS` default is
  raised to 8192, and the exhausted-budget error now names the variable to
  raise.

### Changed

- Embedding providers are unified on the OpenAI-compatible wire protocol
  via the official `openai` SDK (#916): `OpenAIProvider` now embeds through
  the SDK, and `OllamaProvider` targets Ollama's OpenAI-compatible endpoint
  (`{OLLAMA_HOST}/v1`) through the same shared client. No configuration
  changes: `EMBEDDING_PROVIDER=ollama`, `OLLAMA_HOST`, `OLLAMA_MODEL`,
  `OLLAMA_CPU_ONLY`, and auto-detection behave exactly as before.
  `OLLAMA_CPU_ONLY` keeps using Ollama's native API (the compatibility
  layer cannot express CPU-only inference). The `embeddings-api` extra now
  installs the `openai` SDK alongside `httpx` and `numpy`.

### Changed (BREAKING)

- The `summarize` tool's Anthropic-SDK backend was replaced by a generic
  OpenAI-compatible backend (official `openai` SDK; works with OpenAI,
  Ollama, Anthropic's OpenAI-compat endpoint, vLLM, ...) (#915).
  - Bare `ANTHROPIC_API_KEY` and
    `MARKDOWN_VAULT_MCP_SUMMARIZE_ANTHROPIC_MODEL` are no longer read; a
    setup relying on them has the `summarize` tool silently hidden until
    reconfigured.
  - `MARKDOWN_VAULT_MCP_SUMMARIZE_PROVIDER=anthropic` now fails at startup
    with migration instructions when summarize credentials are present.
  - New configuration: `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY`
    (falls back to bare `OPENAI_API_KEY`),
    `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL` (setting it enables the
    tool even without a key, for keyless local endpoints such as Ollama),
    and `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL` (default `gpt-5-mini`).
  - Migration for Claude users:
    `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL=https://api.anthropic.com/v1`,
    the Anthropic key in `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY`, and
    `MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_MODEL=claude-haiku-4-5`.
  - The `summarize` extra now installs `openai` instead of `anthropic`.

- `Collection.get_index_status` `status` field value renamed from
  `"ready"` to `"queryable"`. MCP clients pattern-matching on the
  old value will silently treat the new value as unknown until
  updated.
- `Collection.get_index_status` priority order flipped: a built
  index with a captured background error from a prior attempt now
  reports `status="queryable"` (with the diagnostic in `error`),
  not `status="failed"`. `"failed"` now means "preconditions do not
  hold AND a captured error exists" — i.e., the index is not
  readable.
- `Collection.is_index_ready()` renamed to `Collection.is_queryable()`.
- `Collection._require_index_ready()` (private) renamed to
  `Collection._require_built()` — matches what it actually checks
  (only `_index_built`, single field).
- `Collection.wait_for_index_ready()` renamed to
  `Collection.wait_until_queryable()`.
- MCP decorator `needs_index_ready` renamed to `needs_queryable`.
  Module `_server_readiness.py` renamed to `_server_queryable.py`.
- Public exception `IndexNotReadyError` renamed to
  `IndexUnavailableError`.
- Environment variable `MARKDOWN_VAULT_MCP_READY_TIMEOUT_S` renamed
  to `MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S`. Running deployments that
  set the old variable will silently fall back to the 60-second
  default after upgrading; update operator configs (compose files,
  systemd units, `.env` files) accordingly.

External consumers that previously imported `IndexNotReadyError`,
called `is_index_ready()`/`wait_for_index_ready()`, or set the old
env var must rename their references. No deprecation shims ship.

### Added

- Curated-ranking configuration knobs (all defaults are exact behavioural
  no-ops):
  - `MARKDOWN_VAULT_MCP_TITLE_FIELD` — frontmatter field used as the
    document title (falls back to `title` → first H1 → filename stem).
  - `MARKDOWN_VAULT_MCP_SEARCHABLE_FIELDS` — frontmatter fields whose
    scalar values are indexed into a new FTS `summary` column (chunk-0 row
    per document), making them keyword-searchable including `summary:term`
    column filters. Legacy five-column FTS tables are migrated in place
    with no filesystem rescan.
  - `MARKDOWN_VAULT_MCP_FTS_WEIGHTS` — per-column BM25 weights persisted
    into the FTS5 rank configuration (`path`, `title`, `folder`,
    `heading`, `content`, `summary`; unset/all-`1.0` keeps the default).
  - `MARKDOWN_VAULT_MCP_FOLDER_WEIGHTS` — folder-prefix score multipliers
    applied to all three search modes just before per-file grouping
    (deepest matching prefix wins; query-time only).
  - `MARKDOWN_VAULT_MCP_EMBED_CONTEXT` — enriches embedding input with the
    note title, chunk heading, and first-chunk searchable-field preamble
    via a single shared builder used by every embedding site; the format
    token is persisted in the vector sidecar and a mismatch re-embeds the
    vault once on next startup.
- Changing `TITLE_FIELD` or `SEARCHABLE_FIELDS` is recorded in the FTS
  chunking provenance and triggers a one-time automatic cold rebuild on
  the next startup; pre-upgrade indexes with default config warm-restart
  untouched.

<!-- version list -->
