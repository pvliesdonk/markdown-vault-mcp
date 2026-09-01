# Module map

Every file under `src/markdown_vault_mcp/`, one line each, with what it owns.
This is the "where does X live" lookup: read it before adding a module, and
before assuming a responsibility has no home yet.

Two neighbours cover different ground. [`design.md`](design.md)'s **Module
Design** section explains a handful of modules in depth — why the package root
is empty, why `vault.py` is a thin composition root — and is the authoritative
specification. This file is the complete index, at one line of detail.

`tests/test_module_map.py` asserts the paths below match `src/` exactly, in
both directions, so a new module cannot land unlisted and a deleted one cannot
linger. The annotations are not machine-checked; they are the point of the
file, and keeping them true is part of the change that moves the code.

```
src/markdown_vault_mcp/
  __init__.py        -- minimal template-skeleton root (docstring + __version__); import from submodules, not the root (#665, #903)
  utils/
    __init__.py        -- shared path-traversal guard (resolve_inside + validate_path variants) + attachment/exclusion helpers re-exported for managers and facets (#876)
    text.py            -- text normalization, position mapping, fuzzy matching
    links.py           -- link target computation and replacement
    serialization.py   -- toc_payload: TocEntry/SubtreeToc → JSON-able dicts
    content_kind.py    -- is_note/has_md_suffix/artifact_suffix/is_allowed_artifact(_suffix): single owner of the note-vs-artifact boundary + the registry of the three '.md' axes (#1235)
    fs.py              -- filesystem traversal helpers: symlink-aware iteration, directory pruning (#508, #835)
    fts.py             -- fts_row_to_note_info: FTS row → NoteInfo conversion shared across managers
  managers/
    __init__.py        -- package aggregator: re-exports DocumentManager/IndexManager/LinkManager/SearchManager
    link.py            -- LinkManager: backlinks, outlinks, broken, orphans, hubs, paths
    search.py          -- SearchManager: keyword/semantic/hybrid search, list, context, stats
    index.py           -- IndexManager: build_index, reindex, dirty-path FTS refresh; delegates embeddings to the composed EmbeddingsManager (#1157)
    embeddings.py      -- EmbeddingsManager: vector lifecycle — cold build, convergence, inline embed, deferred flush, status (#1157); internal collaborator, not re-exported
    document.py        -- DocumentManager: note CRUD, backlinks, delete/rename/move orchestration; artifact CRUD delegated to ArtifactStore (#1235)
    artifacts.py       -- ArtifactStore: validate/size/read/write/unlink/move for non-.md artifacts over the manager's shared lock + notifier (#1235); internal collaborator, not re-exported
    _write_notifier.py -- WriteNotifier: single owner of write-callback dispatch shape incl. the old_path opt-in probe (#894, #1235)
    _write_kernel.py   -- atomic_write / check_if_match / umask cache: the pure write primitives both the note and artifact paths use (#1235)
    git_query.py       -- GitQueryManager: git history/diff reads (#610)
    summarize.py       -- SummarizeManager: LLM-backed note/subtree summarization, map-reduce batching (#922)
    okf_migrate.py     -- OkfMigrationManager: one-shot OKF transforms — link conversion, index generation, log seeding (#963)
    _ranking.py        -- pure ranking pipeline: downweight/boost/grouping/snippets (#759)
    _vector_loader.py  -- shared load-or-self-heal routine for the vector sidecar (#736)
  indexing/
    __init__.py        -- package aggregator: re-exports IndexWriteCoordinator, IndexWriter + writer job dataclasses, ReadinessState
    index_writer.py    -- IndexWriter: single-owner FIFO writer thread + job dataclasses/runners
    readiness.py       -- ReadinessState: build-readiness state machine (#576)
    coordinator.py     -- IndexWriteCoordinator: owns the writer + build/async orchestration (#576)
  facets/
    __init__.py        -- package aggregator: re-exports the five facets (Reader/Writer/Graph/Index/Summarize)
    reader.py          -- ReaderFacet: search/read/list/toc/similar/context/stats/history (#604)
    writer.py          -- WriterFacet: write/edit/delete/rename/attachments (#604)
    graph.py           -- GraphFacet: backlinks/outlinks/broken/orphans/most-linked/paths (#604); neighborhood/hub graph views (#880)
    index.py           -- IndexFacet: build/reindex/embeddings, readiness, writer + embeddings status (#604)
    summarize.py       -- SummarizeFacet: readiness-gated summarization surface; present only when a backend is configured (#925)
  git/
    __init__.py        -- package facade preserving the historical single-module import surface (incl. test patch targets)
    _run.py            -- low-level git subprocess + credential plumbing
    interfaces.py      -- HistorySource/Syncer/Versioner/VersionedStore: the versioning seam; GitWriteStrategy is the one implementation (#1229)
    strategy.py        -- GitWriteStrategy: auto-commit per write; composes RepoBootstrap + PushScheduler over one shared lock (#893)
    bootstrap.py       -- RepoBootstrap: managed-clone bootstrap, remote-protocol validation, memoised git-root discovery (#893)
    push_scheduler.py  -- PushScheduler: deferred-push timer + pending-flag mechanics and push execution (#893)
    conflict.py        -- rebase-conflict resolution mechanics (caller holds the strategy lock)
    query.py           -- read-only git history/diff queries; lock-free pure functions
    types.py           -- PullResult/PushResult + pull/push reason-code constants
  scanner.py           -- file discovery, frontmatter parsing, chunking
  interfaces.py        -- KeywordIndex/GraphStore/KeywordGraphIndex/VectorStore: the search/index storage seam (#1230)
  fts_index.py         -- SQLite FTS5 schema, BM25 search
  _fts_connection.py   -- per-thread sqlite connection registry + SQLITE_LOCKED retry (#760)
  vector_index.py      -- numpy embeddings, cosine similarity
  embed_text.py        -- EmbedTextBuilder: single shared builder for (context-enriched) embedding input text
  providers.py         -- embedding provider ABC + implementations
  tracker.py           -- hash-based change detection
  hashing.py           -- compute_etag / compute_file_hash: shared SHA-256 helpers
  types.py             -- shared dataclasses and result types (NoteInfo, SkippedFile, SKIP_CATEGORIES, ...)
  exceptions.py        -- exception hierarchy; re-exports pvl-core's ConfigurationError as the canonical config error (#638)
  conventions.py       -- ConventionsResolver: per-folder _conventions.md authoring policy, accumulated root-first
  okf.py               -- OKF detection probe + pure read-side annotations: type/status/staleness/trust (#961)
  okf_bundle.py        -- OKF bundle-zip export from live vault state, served via an okf-bundle download ref (#963)
  _okf_convention.py   -- OKF reserved-file maintenance after enforced writes: log.md bullet + index.md refresh (#964)
  _okf_write.py        -- OKF enforced-write runtime: contextvar actor + provenance stamp / verified clear (#964)
  _identity.py         -- Principal write identity: tool-edge resolution, contextvar carry, claim-key registration (#1160); sole owner of the subject rules (#1231)
  summarizer.py        -- Summarizer ABC + OpenAI-compatible chat-completions backend (#915)
  vault.py             -- thin composition root: settings-first dual-mode construction (#1158), lifecycle, wiring, facet accessors (index-write → indexing/coordinator.py)
  write_callback.py    -- WriteCallbackDispatcher: deferred git-commit callback worker (#599)
  _write_tools.py      -- WRITE_TOOL_NAMES + write_tools_phrase: single source for the user-facing write-tool enumeration (#1009)
  config.py            -- template-owned skeleton: flat metadata-carrying ProjectConfig fields + section-view properties + from_env, all inside CONFIG-* sentinels (#900, #952)
  config_sections/
    __init__.py          -- package aggregator: re-exports the seven section configs (Content/Embeddings/Git/Indexing/Search/Summarize/Sync) + VaultSettings
    _assembly.py         -- domain config-assembly kept out of template-owned config.py: to_vault_settings/to_vault_instances (+ deprecated to_vault_kwargs bridge, #1158), derive_max_chunk_chars, git-strategy builder, from_env value resolvers (#900, #952)
    vault_settings.py    -- VaultSettings: frozen config-derived Vault construction settings + pure effective_* derivations (#1158)
    _helpers.py          -- shared env-reading helpers for the sections' from_env classmethods (no config.py import)
    content.py           -- ContentConfig: attachment/read limits, template/prompt folders, conventions file
    embeddings.py        -- EmbeddingsConfig: provider selection + per-provider and shared embedding knobs
    git.py               -- GitConfig: git auth, identity, sync cadence
    indexing.py          -- IndexingConfig: paths, frontmatter, exclusions
    search.py            -- SearchConfig: ranking weights + snippet-truncation knobs
    summarize.py         -- SummarizeConfig: OpenAI-compatible summarization backend settings
    sync.py              -- SyncConfig: file-watcher + GitHub-webhook settings
  domain.py            -- Service: owns the Vault lifecycle (build, boot index/reindex/embeddings jobs, file watcher); get_vault/get_config DI + vault singleton (#902)
  _instructions.py     -- contribute_instructions: domain snippets added to pvl-core's instructions builder from server.py's DOMAIN-WIRING (#901)
  _server_apps.py      -- template-owned MCP Apps scaffold; vault SPA + app-tools confined to DOMAIN-APP-TOOL-NAMES/DOMAIN-APP-RESOURCE/DOMAIN-APP-TOOLS sentinels (#905)
  _vault_apps.py       -- domain helpers backing _server_apps sentinels: Claude sandbox-domain compute + CDN CSP + GraphView→SPA wire serializer (#905)
  _server_deps.py      -- server_lifespan + LifespanState: Service lifecycle and vault DI for request handlers
  _server_tools/
    __init__.py        -- register_tools: single entry point delegating to the per-facet groups (#578)
    _common.py         -- shared tool plumbing: staleness-annotated results, drain waits
    reader.py          -- read-side tool registrations (search/read/list/toc/context/...)
    writer.py          -- write-side tool registrations (write/edit/delete/rename/attachments)
    graph.py           -- link-graph tool registrations
    index.py           -- index/reindex/embeddings-status tool registrations
    git.py             -- git sync/history/diff tool registrations
    summarize.py       -- dual-mode summarize job tool; registered from make_server's DOMAIN-WIRING (#1033)
  _server_resources.py -- register_resources: MCP resource registrations
  _server_prompts.py   -- register_prompts: MCP prompt registrations from static/prompts templates (#609)
  _server_queryable.py -- needs_queryable decorator: MCP-layer wait/block on index readiness (#513)
  _transfer_sink.py    -- VaultTransferSink: domain sink + validator hooks for pvl-core's transfer routes (#979)
  _file_watcher.py     -- watchdog external-change watcher with debounce; used when git pull and webhook are off (#558)
  _github_webhook.py   -- GitHub push-webhook route: HMAC verify → force_pull + reindex (#530)
  _http_logging.py     -- quiet_http_loggers: pin httpx/httpcore to WARNING unless root level is DEBUG (#792)
  _icons.py            -- Lucide SVG tool icons from static/icons/ as data URIs
  server.py            -- generic FastMCP server factory (make_server); template-owned, domain customization confined to DOMAIN-UPSTREAM/DOMAIN-WIRING (#901)
  cli.py               -- CLI entry point
```
