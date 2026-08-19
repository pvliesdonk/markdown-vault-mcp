# markdown-vault-mcp

Generic markdown vault MCP server with FTS5 + semantic search, frontmatter-aware indexing, and incremental reindexing.

## Design
<!-- DOMAIN-START -->
The authoritative design specification lives at [`docs/design/design.md`](docs/design/design.md). All implementation must conform to this spec. When in doubt, the design doc wins.
<!-- DOMAIN-END -->

## Project Structure
<!-- DOMAIN-START -->
```
src/markdown_vault_mcp/
  __init__.py        -- minimal template-skeleton root (docstring + __version__); import from submodules, not the root (#665, #903)
  utils/
    __init__.py        -- path-traversal guard (validate_path) + attachment/exclusion helpers re-exported for managers and facets
    text.py            -- text normalization, position mapping, fuzzy matching
    links.py           -- link target computation and replacement
    serialization.py   -- toc_payload: TocEntry/SubtreeToc → JSON-able dicts
    fs.py              -- filesystem traversal helpers: symlink-aware iteration, directory pruning (#508, #835)
    fts.py             -- fts_row_to_note_info: FTS row → NoteInfo conversion shared across managers
  managers/
    __init__.py        -- package aggregator: re-exports DocumentManager/IndexManager/LinkManager/SearchManager
    link.py            -- LinkManager: backlinks, outlinks, broken, orphans, hubs, paths
    search.py          -- SearchManager: keyword/semantic/hybrid search, list, context, stats
    index.py           -- IndexManager: build_index, reindex, embeddings, flush
    document.py        -- DocumentManager: CRUD, attachments, backlinks; path checks delegate to utils.validate_path
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
    strategy.py        -- GitWriteStrategy: auto-commit per write, deferred background push
    conflict.py        -- rebase-conflict resolution mechanics (caller holds the strategy lock)
    query.py           -- read-only git history/diff queries; lock-free pure functions
    types.py           -- PullResult/PushResult + pull/push reason-code constants
  scanner.py           -- file discovery, frontmatter parsing, chunking
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
  summarizer.py        -- Summarizer ABC + OpenAI-compatible chat-completions backend (#915)
  vault.py             -- thin composition root: lifecycle, wiring, facet accessors (index-write → indexing/coordinator.py)
  write_callback.py    -- WriteCallbackDispatcher: deferred git-commit callback worker (#599)
  _write_tools.py      -- WRITE_TOOL_NAMES + write_tools_phrase: single source for the user-facing write-tool enumeration (#1009)
  config.py            -- template-owned skeleton: flat metadata-carrying ProjectConfig fields + section-view properties + from_env, all inside CONFIG-* sentinels (#900, #952)
  config_sections/
    __init__.py          -- package aggregator: re-exports the seven section configs (Content/Embeddings/Git/Indexing/Search/Summarize/Sync)
    _assembly.py         -- domain config-assembly kept out of template-owned config.py: to_vault_kwargs, derive_max_chunk_chars, git-strategy builder, from_env value resolvers (#900, #952)
    _helpers.py          -- shared env-reading helpers for the sections' from_env classmethods (no config.py import)
    content.py           -- ContentConfig: attachment/read limits, template/prompt folders, conventions file
    embeddings.py        -- EmbeddingsConfig: provider selection + per-provider and shared embedding knobs
    git.py               -- GitConfig: git auth, identity, sync cadence
    indexing.py          -- IndexingConfig: paths, frontmatter, exclusions
    search.py            -- SearchConfig: ranking weights + snippet-truncation knobs
    summarize.py         -- SummarizeConfig: OpenAI-compatible summarization backend settings
    sync.py              -- SyncConfig: file-watcher + GitHub-webhook settings
  domain.py            -- Service: owns the Vault lifecycle (build, boot index/reindex/embeddings jobs, file watcher); get_vault/get_config DI + vault singleton (#902)
  _instructions.py     -- build_default_instructions: domain server-instructions prose, applied in server.py's DOMAIN-WIRING (#901)
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
<!-- DOMAIN-END -->

## Reference
<!-- DOMAIN-START -->
This project is extracted from [`pvliesdonk/if-craft-corpus`](https://github.com/pvliesdonk/if-craft-corpus). See the design doc's Reference Code section for the mapping between source files.
<!-- DOMAIN-END -->

<!-- ===== TEMPLATE-OWNED SECTIONS BELOW — DO NOT EDIT; CHANGES WILL BE OVERWRITTEN ON COPIER UPDATE ===== -->

## Conventions

- Python 3.11+
- `uv` for package management, `ruff` for linting/formatting (line length 88)
- `hatchling` build backend
- Conventional commits, one type from `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test` — optionally scoped (`feat(search): ...`) and with `!` for a breaking change. Only `feat`, `fix`, and the `!` marker drive releases: `feat` cuts a minor, `fix` a patch, `!` a major. Every other type — `perf` included — cuts nothing and never reaches `CHANGELOG.md`; a performance change that must ship on its own is either honestly a `fix:` (it fixes a performance defect) or released with Release Prepare's explicit `override_version` input.
- Google-style docstrings on all public functions
- `logging.getLogger(__name__)` throughout, no `print()`
- Type hints everywhere
- Tests: `pytest` with fixtures in `tests/fixtures/`

**Pull-request titles are enforced, not merely encouraged.** A squash merge
takes the PR title as the commit subject, so CI's `PR Title` job checks it
against the type list above and fails the `CI Success` aggregate when it does
not match. This is not style policing: knope computes versions and writes
`CHANGELOG.md` from those subjects, and it silently ignores a subject whose
type it does not count — no fallback heading, no entry, no warning. That
silent-drop class is exactly what the gate exists for: it keeps the history
parseable and the accepted set deliberate. The changelog itself carries three
sections per release — Breaking Changes first, then Features, then Bug
Fixes — fed only by `!`/`feat`/`fix` subjects; the richness for everything
else lives on the `docs/releases/` notes page, not in the changelog.

Retitling is enough to clear a failure — the job reads the current title from
the API, so re-running it after a retitle needs no push.

**Reverts are the one accepted title with a caveat.** `Revert "..."`, the
shape `git revert` and GitHub's revert button generate, passes: it is the
ecosystem's convention, and Conventional Commits deliberately leaves reverts
unspecified. But **neither revert form reaches `CHANGELOG.md`** — knope
counts only `feat`/`fix`/`!`, so `revert: ORIGINAL SUBJECT` is just as
changelog-invisible as the quoted form. The job says so with a warning
annotation rather than letting you find out at release time. The revert is
narrated on the `docs/releases/` page instead, whose research runs off merged
pull requests and linked issues rather than commit subjects.

The accepted set lives in `scripts/check_pr_title.py`. That list, the
knope-counted subset (`feat`/`fix`/`!`), and this section's prose are checked
against each other by `tests/test_commit_conventions.py`, so no one of the
three can drift alone.

The automated Claude review runs **only after CI passes** — if CI is red, no
review is posted. Fix CI and push; the review runs on the next green run.

## Breaking Changes and the `!` Marker

The release version is computed by knope from commit subjects and lands in a reviewed release PR, so the `!` marker (or `BREAKING CHANGE:` footer) *is* the major-version decision — apply it deliberately, not by habit; the release-PR review is where a mis-typed `!` gets caught, but the reviewer should never have to. A change is **breaking** only if it breaks one of two surfaces:

- **Operator surface** — an environment variable, config file, CLI flag, deployment layout, or on-disk state format a human must change to upgrade.
- **Public library interface** — anything importable from `markdown_vault_mcp` that a downstream Python consumer uses. A mechanical guard for this tier is tracked at pvliesdonk/fastmcp-server-template#352.

A change to the **MCP tool surface** (tool names, parameters, return schemas, adding or removing tools) is **not** breaking on its own. The LLM client is stateless and re-discovers the surface over the protocol on connect, so a server restart resolves it with no user action.

Two refinements:

1. **Re-discovery covers a tool's *shape*, not its *semantics*.** A tool that keeps its signature but changes what it does can still break operator automation, prompts, or skills. If only the MCP surface changed and the previous behaviour is still reachable (additive / dual-mode), the change is not breaking; if the old behaviour is gone, treat it as breaking even though the schema re-discovers cleanly.
2. **Assess against the last stable release, not the previous commit.** A change to something introduced in the same unreleased range breaks nothing a user has, and does not earn a `!`. When a feature and its rework land in one release cycle, only the net effect on the released surface counts. Sanity-check any commit carrying `!` before it merges: if `git tag --contains` on the commit that introduced the surface comes back empty, the surface never shipped and the `!` is spurious.

The two-part test: (1) does an operator or a library consumer of the last stable release have to change something? → breaking. (2) If only the MCP surface changed, is the previous behaviour still reachable? → not breaking; if it is gone, breaking.

## Hard PR Acceptance Gates

Every PR must pass **all** of the following locally before push. These are mechanical preconditions:

1. **Tests pass** — `uv run pytest -x -q` all tests pass
2. **Lint passes** — run in this exact order: `uv run ruff check --fix .` then `uv run ruff format .` then verify with `uv run ruff format --check .`. Always run format *after* check --fix because check --fix can leave files needing reformatting.
3. **Type-check passes** — `uv run mypy src/ tests/` reports no errors
4. **Patch coverage ≥ 80%** — Codecov measures only lines added/changed in the PR diff. Run `uv run pytest --cov=src/markdown_vault_mcp --cov-report=term-missing` and verify new code is exercised. Use the path form for `--cov`: a dotted module target (e.g. `--cov=markdown_vault_mcp.config`) makes coverage.py import the module speculatively, which leaves an orphaned beartype import hook behind and aborts the whole session at conftest load. Add tests for every uncovered branch before pushing.

5. **Structural quality (diff) passes** — new/changed code must introduce no new structural violations (complexity, too-many-*, security). Enforced on the diff only, so pre-existing code is never blocked. Run before pushing:
   ```bash
   bash scripts/structural_gate.sh
   ```
   The script derives its compare branch (nearest of `origin/main` and `origin/release/*`; override with `STRUCTURAL_GATE_BASE`) and runs `diff-quality --violations=ruff.check --options="--extend-select=C901,PLR0911,PLR0912,PLR0913,PLR0915,S" --fail-under=100` against it. `# noqa: C901` (etc.) with a one-line justification is the escape hatch for genuinely irreducible new code.
6. **Docs updated** — `README.md` and `docs/**` reflect any user-facing changes in the same commit
7. **Manifest version lockstep** — `server.json`, `.claude-plugin/plugin/.claude-plugin/plugin.json`, and `.claude-plugin/plugin/.mcp.json` must all carry the same version: the latest *stable* release. A stable release PR stamps them atomically (knope invokes `scripts/stamp_manifests.py`); rc release PRs deliberately leave them untouched, because the versions they name are only published for stable releases. Manual touches require updating all three.


## Pre-commit Hooks

This project ships a `.pre-commit-config.yaml` that runs ruff (check + format), mypy on `src/` and `tests/`, gitleaks secret scanning, and standard whitespace/YAML/JSON checks — aligned with the `ci.yml` lint/typecheck/secrets jobs so a clean pre-commit run implies a clean CI lane.

- **Install once per clone:** `uv run pre-commit install`.
- **Run on demand before pushing:** `uv run pre-commit run --all-files`. A green run is a precondition for gates #2 and #3 above.

- **Structural gate runs at push time:** the `structural-diff-gate` hook (pre-push stage) runs `scripts/structural_gate.sh` automatically. `uv run pre-commit install` wires it via `default_install_hook_types`. A clean local push implies a clean CI `structure` job — CI runs the same script, pinning the compare branch to the PR's actual base, while the hook derives it (nearest of `origin/main` and `origin/release/*`), so backport branches targeting a release branch are measured against the right base locally too.

- **Never bypass with `--no-verify`.** A failing hook means the same check will fail in CI; fix the underlying issue rather than silencing it.

Domain-specific additions (shellcheck, yamllint, project-specific linters, additional file checks) belong between the `DOMAIN-HOOKS` markers at the end of the config, never outside them; hooks inside that block on top of the shipped defaults survive `copier update`.


## Structural health

The structural gate stops *new* debt; these practices and the advisory audit keep existing debt visible.

**Local-shape rules (checkable while you write):**

- Keep functions short and single-purpose; if a function needs a comment to explain a *section*, that section is a function.
- Nesting beyond ~3 levels is a smell — extract or invert/early-return.
- Five parameters is the ceiling; past it, pass an object or split the function.
- A new responsibility is a **new collaborator, not a longer class**. When a class grows a second reason to change, that reason belongs in its own unit.

**Advisory audit (on demand — run before substantial work in an unfamiliar area, or when about to touch a flagged module):**

```bash
uv run --with radon python -m radon cc -s -n C src/    # complexity hotspots (grade C+)
uv run --with radon python -m radon mi -s src/         # maintainability index
uv run --with vulture vulture src/                     # dead-code candidates
```

Each analyzer is optional and degrades gracefully if absent. `vulture` over-reports on importable/decorated/framework-registered code — **confirm before deleting** and keep a whitelist.

**When you notice decay outside the current change's scope** — a god class forming, a dead branch, a leaking abstraction, a name that no longer matches behaviour, or an audit hotspot — do **not** fix it inline (scope creep) and do **not** pass over it silently. **Open an issue** using the **Decay** form (`.github/ISSUE_TEMPLATE/decay.yml`): What / Where / Why it compounds / Suggested direction.

Constrain issues to **decay that will compound**, not anything imperfect. The diff-gate blocks new debt; these issues are the refactor-later backlog for pre-existing debt — neither blocks the current PR.


## PR Discipline

**Every PR must have at least one associated issue.** If the work doesn't have one yet — a bug found in the wild, an opportunistic cleanup, a small improvement — create the issue first, then open the PR with `Closes #N` (or `Refs #N`) in the body. A single PR may close multiple issues (`Closes #A, closes #B`) — bundling related fixes is fine; the rule is "no orphan PRs", not "one PR per issue". This keeps the changelog, release notes, and cross-repo history coherent.

Trivial exceptions: pure typo fixes and automated dependency bumps (Renovate) may skip the issue.

<!-- TEMPLATE-TRACKING-START -->
**The bot reviewer (claude-review) is a merge gate, not a pair reviewer.** Local review must be complete before the PR opens. If it finds anything on first run, the local review was incomplete — that is a discipline failure to investigate, not "address-and-move-on." Run a local code-review pass on the cumulative diff before `gh pr create`; it is not a substitute.
<!-- TEMPLATE-TRACKING-END -->

## GitHub Review Types

GitHub has two distinct review mechanisms — **both must be read and addressed**:

- **Inline review comments** (`get_review_comments`): attached to specific lines of the diff. Appear in the "Files changed" tab. Use `get_review_comments` to fetch these.
- **PR-level comments** (`get_comments`): posted on the Conversation tab, not tied to a line. Review summary posts, bot analysis, and blocking issues are often posted here. Use `get_comments` to fetch these.

Always fetch both before declaring a review round complete.
## Documentation Discipline

Every issue, PR, and code change must consider documentation impact. Before closing any issue or creating any PR, check whether the following need updating:

- **`docs/design/design.md`** — the authoritative spec. Any new feature, changed behavior, or architectural decision must be reflected here. If the code diverges from the spec, update the spec.
- **`README.md`** — user-facing documentation. New env vars, tools, resources, prompts, CLI flags, or configuration options must be documented here.
- **`docs/` site pages** — the published documentation site. New or changed MCP tools/resources/prompts, new env vars, new installation methods or deployment options.
- **`CHANGELOG.md`** — machine-generated audit trail: knope writes each release's version section (below the `<!-- version list -->` insertion flag) into the release PR's diff. Never hand-edit version sections or the flag line. If this project was generated before the flag existed, add the flag line to `CHANGELOG.md` once by hand — `tests/test_release_flow_contract.py` fails with the exact line until it is present.
- **Inline docstrings** — new or changed public API methods need accurate Google-style docstrings.

**Rule: code without matching docs is incomplete.**

## Documentation Conventions (user-facing vs internal)

`docs/` is the **published, user-facing documentation site** (mkdocs + mike).
Everything under `docs/`, plus **`README.md`**, is operator-facing prose and is
**Vale-linted** in CI and pre-commit — keep it clean.

**Internal / developer docs are not user-facing and are not linted.** They live
under a fixed set of subtrees, excluded from both the published site and Vale:

- `docs/design/` — design specs and architecture notes
- `docs/decisions/` — architecture decision records (ADRs)
- `docs/superpowers/` — agent working specs and plans (also gitignored)

This boundary is declared in three places that **must stay in lockstep** (the
`template-ci` "vale exclusion-scope lockstep" job asserts the CI glob and the
pre-commit exclude match): the mkdocs `exclude_docs:` block, the `vale` CI
step's `vale_flags` glob (`--glob=!docs/{superpowers,design,decisions}/**` — one
glob with brace alternation, because Vale honors only a single `--glob`), and
the `- id: vale` pre-commit hook's `exclude:` regex
(`^docs/(superpowers|design|decisions)/`). The set is fixed by convention — do
not add per-project exclusions; put internal docs in one of the subtrees above.

## Logging Standard

### Scope

This standard governs **first-party code only** — `src/markdown_vault_mcp/` and
`tests/`. Two categories of log output are explicitly **out of scope**; do not
try to make them conform:

- **FastMCP middleware stack** (`fastmcp-pvl-core` timing / logging /
  error-handling middleware): emits conforming, bare-event-name-first lines
  automatically. Tool-call lines carry `tool=<name>`. Governed by
  `fastmcp-pvl-core` itself (see pvliesdonk/fastmcp-pvl-core#90); no
  first-party code needed.
- **`uvicorn.access` and `mcp.server.lowlevel.server` log lines**: upstream
  transport / SDK output. `configure_logging_from_env` raises their effective
  level to `WARNING`, suppressing per-request `INFO` output at the default
  level; at `DEBUG` they are reset to `NOTSET` so the root logger governs
  their effective level via propagation (pvliesdonk/fastmcp-pvl-core#91).
  `uvicorn.error` is intentionally not suppressed — it carries startup and
  bind failures. Do not silence or reformat any of these — they are out of
  scope by design.

### Framework
- Standard library `logging` throughout. Every module: `logger = logging.getLogger(__name__)`.
- No `print()` for operational output. No third-party logging libraries.
- FastMCP middleware handles tool invocation, timing, and error logging automatically.
- All first-party logging goes through FastMCP's `configure_logging_from_env()` for uniform output. `FASTMCP_LOG_LEVEL` is the single log level control; the `-v` CLI flag sets it to `DEBUG`. `FASTMCP_ENABLE_RICH_LOGGING=false` switches to plain/JSON output.

### Log Levels
| Level | Use for |
|-------|---------|
| `DEBUG` | Detailed internals: cache hits, parameter values, config resolution |
| `INFO` | Significant operations: service startup, configuration decisions (tool calls logged by middleware) |
| `WARNING` | Degraded but continuing: API errors with fallback, missing optional config, unexpected data |
| `ERROR` | Failures affecting the primary result. Use `logger.error(..., exc_info=True)` when traceback is needed |

### Exception Handling
- All exceptions must be caught and handled. No bare `except:`. Always specify the exception type.
- Expected errors (HTTP 4xx, missing data): catch, log, return user-facing error string.
- Optional enrichment failures: catch, log at `DEBUG` with `exc_info=True`, continue.
- Primary result errors: catch, log at `WARNING` or `ERROR`, return error string.
- `ErrorHandlingMiddleware` is a safety net. If it catches something, that's a bug to fix.

### Message Format
- Pseudo-structured: `logger.info("event_name key=%s", value)`
- Event name as first token (snake_case), then key=value pairs via `%s` formatting.
- Never use f-strings in log calls (defeats lazy evaluation).

## Config & Customization Contract

Domain configuration composes `fastmcp_pvl_core.ServerConfig` inside your domain config class (see `src/markdown_vault_mcp/config.py`).  Add domain fields between the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` sentinels, populate them in `from_env` between the `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END` sentinels, and enforce their invariants in `__post_init__` between the `CONFIG-VALIDATE-START` / `CONFIG-VALIDATE-END` sentinels.  Validation belongs in `__post_init__` rather than `from_env` because it then also covers a direct `ProjectConfig(field=...)`; `env_float` / `env_int` bounds check only the env-sourced value, are inclusive-only, and cannot express cross-field rules.  The dataclass is frozen — read fields, don't assign (use `object.__setattr__` if a field must be normalised).  Never inherit from `ServerConfig`; always compose.

Env var prefix is `MARKDOWN_VAULT_MCP_` — all env reads go through `fastmcp_pvl_core.env(_ENV_PREFIX, "SUFFIX", default)` so naming stays consistent.

- **Domain CLI subcommands** go in the `# DOMAIN-COMMANDS-START` / `-END` block in `cli.py` (like `CONFIG-FIELDS` in `config.py`). Register them as `@app.command()` and use function-local imports for domain modules. The block is preserved across `copier update`.

### Config wizard

`docs/javascripts/config-wizard/wizard-spec.json` drives the guided-setup page. It is **generated**, produced by `scripts/gen_config_surface.py` on every `copier copy`/`copier update` and re-verified by `scripts/gen_config_surface.py --check` in CI — never hand-edit it. The runtime (`wizard.js`, `generators.js`, `wizard-spec-schema.json`, the generic tests) is template-owned and re-rendered the same way it always was.

To change what the wizard asks:

- **A domain setting your project reads** — give its `ProjectConfig` field (between the `CONFIG-FIELDS-START`/`-END` sentinels in `config.py`) a `metadata={"help": ..., "tags": (...)}`. The generator's AST scan discovers it from there; no wizard-spec edits needed.
- **A var the scan cannot see** (a deprecated alias no longer read inside `ProjectConfig.from_env`, or something read outside it entirely) — declare it in `config-presentation.domain.yml` instead.
- **Coverage is enforced, not automatic** — the generator fails loudly (`SystemExit`, naming the var and its tags) if a collected `Var`'s tags match no env-file section, rather than silently dropping it from every generated file. Giving a domain field a tag no section lists is a config-presentation bug, and this catches it at generation time instead of shipping an undocumented var. There is, however, **no orphan check**: a stale or mistaken entry in `config-presentation.domain.yml` that nothing actually reads will generate into the wizard and env files anyway. Keep that file's contents matched to real reads yourself.

### mcpb install screen

`packaging/mcpb/manifest.json.in`'s `user_config` and `server.mcp_config.env` objects are **generated** the same way (`kind: mcpb-user-config`): both derive from one curated `fields:` map, so a screen field and its env wiring cannot drift apart, and hand edits to those two objects are overwritten on the next generation — the rest of the manifest stays yours. The template baseline shows a deliberately minimal screen (server name, log level). Curate this project's screen in `config-presentation.domain.yml` under `files:` → `packaging/mcpb/manifest.json.in` → `fields:`: map an env var name to `{id: ..., title: ..., type: string|boolean|number|directory|file, required: ..., default: ..., sensitive: ...}` (everything but `id` falls back to the var's own metadata), or to `null` to drop a baseline field. A `files:` entry for a path the template does not declare is taken wholesale — that is how a project drives its own additional install-channel manifest from the same source of truth.

### Tool icons

Drop SVG / PNG / ICO / JPEG files into `src/markdown_vault_mcp/static/icons/` and bulk-attach them to registered tools via `fastmcp_pvl_core.register_tool_icons(mcp, {"tool_name": "filename.svg"}, static_dir=...)` at the end of `register_tools()` — or attach at decoration time with `@mcp.tool(icons=[make_icon(STATIC / "x.svg")])` (where `STATIC = Path(__file__).parent / "static" / "icons"` is a shorthand you define at module level). The scaffold ships an empty `static/icons/` directory; commented-out wiring lives in `tools.py`.

### Dockerfile extension points

These sentinel blocks in `Dockerfile` are preserved across `copier update`. Add domain-specific apt packages, uv extras, state subdirs, and volume mounts inside them:

- `# DOCKERFILE-APT-DEPS-START` / `-END` — extra apt packages installed into the runtime image
- `# DOCKERFILE-UV-EXTRAS-START` / `-END` — `--extra <name>` flags added to both `uv sync` invocations (deps cache layer + project install — adding only to one breaks the cache layer)
- `# DOCKERFILE-STATE-DIRS-START` / `-END` — state subdirectories created under `/data` (chowned to the runtime user)
- `# DOCKERFILE-VOLUMES-START` / `-END` — `VOLUME` declarations on the final image

### Release manifest extension points

`scripts/stamp_manifests.py` runs inside every release PR (knope's `prepare-release` workflow invokes it with the computed version; `pyproject.toml` is knope's own `versioned_files` entry and the script never touches it). It rewrites `uv.lock`'s self-version entry on every release, rc included, in the PEP 440 canonical spelling (`-rc.N` → `rcN`) — canonical because uv rewrites the entry to that spelling on any re-lock, and stamping it that way from the start means a mid-cycle re-lock changes nothing. The install-channel manifests — `server.json` and the Claude plugin pair — it stamps on *stable* versions only, so a stable tag never points at a manifest whose version lags it; on rc versions they stay at the last published stable — pre-releases skip PyPI, the MCP registry, and the marketplace, so stamping those pins would make the branch name a version that does not exist anywhere (`tests/test_release_flow_contract.py` asserts all of this). These sentinel blocks in that script are preserved across `copier update`. Add stamps for this project's own version-coupled manifests (another lockstep JSON/TOML) inside them:

- `# DOMAIN-MANIFESTS-HELPERS-START` / `-END` — module-level helpers (use `_load` / `_dump` for JSON so the byte format matches what `scripts/gen_config_surface.py` asserts; raise `StampError` for a pin that cannot be stamped — never warn and continue)
- `# DOMAIN-MANIFESTS-START` / `-END` — the calls, inside `main()`, where `version` is in scope; extend `stamped` with every path you rewrite so it is staged into the release commit

The script must actually be invoked by knope's stamp Command, or a stale manifest ships silently; `tests/test_release_flow_contract.py` asserts that invocation coupling (the successor of the PSR-era `assets` pairing rule), so a stamp declared in one half only fails the gate rather than shipping a release commit with a stale file in it.


## Claude Code plugin channel

`.claude-plugin/plugin/` ships this server as a Claude Code plugin: `.claude-plugin/plugin.json` (identity + version) and an exec-form `.mcp.json` that launches the released PyPI package with `uvx --from <pkg>==<version>`. Both manifests are version-coupled to the *stable* release stream — `scripts/stamp_manifests.py` stamps and stages them inside each stable release PR, so the marketplace entry published by the release workflow always installs the version it points at. That entry is written into the catalog's `.claude-plugin/marketplace.json`, the only path Claude Code loads a marketplace from — a manifest anywhere else is not a marketplace, and the publish job fails rather than bumping one. Pre-releases never reach PyPI, so on rc prepares the script leaves both files at the last published stable rather than pinning a version `uvx` cannot resolve (`tests/test_release_flow_contract.py` gates the invocation pairing and this skip). The `env` block in `.mcp.json`, the plugin README, and any `skills/` directories are project-owned content: the files are seeded once and never re-rendered by template updates. Values in `env` may reference plugin `userConfig` entries as `${user_config.<id>}` declared in `plugin.json` — exec-form fields only; shell-form command strings reject the substitution.

To generate the plugin's configuration screen from the config surface instead of hand-editing it, declare a `files:` pair in `config-presentation.domain.yml`: the plugin.json path with `kind: claude-plugin-user-config` and a `fields:` map (same field specs as the mcpb screen above), plus the `.mcp.json` path with `kind: claude-plugin-env` and `fields_from:` naming the first entry. One fields map then drives both the `userConfig` object and the `${user_config.<id>}` env wiring, and `scripts/gen_config_surface.py --check` gates the pair against drift.

## Public import surface guard

`tests/test_import_surface.py` (template-owned, re-rendered on template updates) asserts the set of public names importable from the `markdown_vault_mcp` package root against the project-owned snapshot `tests/public_import_surface.txt` (seeded once, never re-rendered by template updates — yours). The surface is enumerated in a fresh interpreter — every non-underscore name in `dir(package)` or `__all__` that resolves via `getattr` — so lazy `__getattr__` re-exports count, incidental submodule imports from earlier tests do not, and a root holding only a docstring and `__version__` has an empty surface. When the test fails:

- **A name disappeared** — that is a breaking change to the public library interface. Either restore the name, or regenerate the snapshot (`uv run python tests/test_import_surface.py --update`) **and** mark the commit/PR breaking (`feat!:` / `fix!:`, or a `BREAKING CHANGE:` footer) per the versioning policy's public-library-interface tier (pvliesdonk/fastmcp-server-template#342).
- **A name appeared** — not breaking; regenerate the snapshot and commit it alongside the change, so the snapshot diff stays the reviewable record of every surface change.

The guard covers the package root only — submodule paths, env vars, and CLI flags are out of its scope.

## Pre-release artifact smoke test

The `Pre-release check` workflow (Actions tab, `workflow_dispatch`) builds and validates the mcpb bundle from any branch at a caller-supplied version (default `0.0.0-dev`), uploads it as a workflow artifact for manual install testing, and can optionally attach it to a deletable `v<version>-rc` pre-release. It runs the exact same steps a real release runs — both call the shared `.github/actions/build-mcpb` composite — so a green pre-release check means the release path's bundle build is green too. Project-specific artifact assertions (extra bundles, plugin manifests) belong in `packaging/pre-release-checks.sh` — an executable script the workflow runs when present, with `VERSION` and `BUNDLE` exported; the file is project-owned, and template updates never touch it.

## Release model

`main` is trunk. Everything merges there, and **merging a feature is not releasing**: a merge feeds the rolling `edge` channel (next section) and nothing else. A release is a separate, deliberate event — dispatching the **Release Prepare** workflow, reviewing the release PR it opens, and merging that PR, which is what tags and publishes — and the sections below say when and from where.

**The default release path is trunk.** When a release is wanted and trunk is quiescent (no atomic epic mid-flight — the cut criterion below), dispatch Release Prepare on `main` and merge the release PR it opens. That cuts a stable: no branch, no ceremony. Most releases should look like this.

**The cut criterion.** An epic that ships atomically makes trunk unreleasable while it is open: releasing `main` ships HEAD, mid-story. Judge quiescence from the queryable signal the epic conventions record (see CONTRIBUTING.md's Epics section): preferably the **release milestone** — safe to cut means no open issues in the target release's milestone — with the **`ships-atomically` label** as the fallback when no milestone names a release yet. An open atomic epic with unclosed children means either release from a commit before the epic started (a `release/X.Y` branch cut from that commit) or wait. The Release Prepare workflow's advisory step surfaces both signals on every default-branch dispatch — a release-named milestone (`X.Y`) that still has open issues, and open `ships-atomically` epics (each with a count of its native sub-issues, so a cross-repo epic whose children live elsewhere stays visible); it warns and never blocks, because the cut may still be intentional.

**The `release/X.Y` branch is the exception tool**, for exactly two cases:

1. **Dirty trunk at cut time** — trunk carries unfinished work that must be excluded from the release. Cut the branch from the last quiescent commit behind HEAD, stabilise there (rcs), and finalize the stable.
2. **Patching a shipped release** — an already-released version needs a fix while `main` has moved on past it. The branch does not need to exist in advance: create it retroactively from the tag (`git branch release/X.Y vX.Y.Z`), land the fix, release.

Fixes flow trunk → branch only: land on `main` first, cherry-pick to the branch. There is no merge-back: after a branch release, the automated port-bookkeeping PR (see the release machinery section below) carries the changelog section to `main` as an ordinary reviewed PR — never features, and nothing deadlocks if it merges late.

**Branching cannot defer a structural refactor.** A refactor that changes existing behaviour makes every later cherry-pick across it conflict-prone, so a stabilisation branch buys no room for one. Land structural refactors early in a cycle, far from the next cut — not late, when a branch is about to be needed.

**Cadence: value-triggered, not time-boxed.** No fixed release train — a calendar cadence is wrong for a low-traffic project and adds nothing to a busy one. Release when unreleased user-visible work (features, fixes, and especially security patches) has accumulated, and never hold a release hostage to unfinished work: excluding that work is exactly what the branch tool is for. The failure mode to avoid is drift — weeks of unreleased commits held behind one open epic.

**The three channels and their promises:**

| Channel | Identity | Promise |
|---|---|---|
| `edge` | none — the commit is the identity | the newest merged code, rebuilt on every merge to `main`; rolling and disposable |
| rc | `vX.Y.Z-rc.N`, target computed and reviewed in the merged release PR | a stabilisation step toward exactly that version — normally cut from `release/X.Y`, or from quiescent trunk when continuing a series whose rc tags are reachable there. Its image also takes the rolling `rc` tag while the candidate is still ahead of the newest stable |
| stable | `vX.Y.Z` | a quiescent-trunk release (the default) or the promotion of an rc series (a plain, non-rc prepare over the same commits, guarded to a stamps-only diff) |

An rc is not "the latest build with a version number" — that job belongs to `edge`, which costs one build-and-push and leaves no tag, release, or version bump behind. Cut rcs only when genuinely stabilising a specific release.

## Unstable channel (rolling `edge` image)

The `Unstable channel` workflow (`.github/workflows/unstable.yml`) runs on every push to `main` (plus manual dispatch for seeding) and rebuilds two artifacts from that commit: the container image, pushed to `ghcr.io/pvliesdonk/markdown-vault-mcp:edge` — a fixed rolling tag whose contents each merge replaces — and an mcpb bundle built through the same shared `.github/actions/build-mcpb` composite the release path uses, uploaded as the `mcpb-bundle-edge` workflow artifact at the constant version `0.0.0-dev`. The channel is deliberately versionless: no git tag, no GitHub release, no version bump, no manifest change. "Run the latest merged code" costs one build-and-push and leaves nothing behind; the image's `org.opencontainers.image.revision` label carries the exact commit. The docs site's rolling `unstable` version deploys from the same trigger (`docs.yml`), so it tracks merged code the way `edge` does. Cutting a version-numbered pre-release (rc) remains the release-PR flow's job (see the release machinery section below); an rc image ships under its immutable `vX.Y.Z-rc.N` tag plus the rolling `rc` tag, which release.yml owns. The two rolling unstable tags answer different questions and have exactly one producer each — `edge` is the newest merged commit, `rc` the newest candidate — and neither workflow writes the other's tag. `rc` is ordering-gated the way `latest` is: it moves only while the candidate's version is still ahead of the newest stable, so a candidate for an already-released version, or one cut on an older `release/X.Y`, never pulls it backwards.

## Release machinery (prepare, release PRs, promotion)

**A release is a pull request.** Dispatching **Release Prepare** on the ref to release from (`main`, or a `release/X.Y` branch) has knope compute the version from the conventional commits since the last release in that ref's ancestry, stamp the version-coupled files (`pyproject.toml` and `CHANGELOG.md` natively; `uv.lock`'s self-version entry — every prepare — and the install-channel manifests — stable prepares only — via `scripts/stamp_manifests.py`), and open — or refresh — a release PR against that ref. The dispatch's `channel` input picks rc or stable (`auto` = rc on `release/*`, stable elsewhere), and the optional `override_version` input replaces the whole computation with an explicit version — the escape hatch for ranges knope counts nothing in, never the default. Only the default branch and `release/*` can be dispatched on or merged into; both workflows refuse anything else. Merging the release PR is the release decision: the **Release** workflow tags the merge commit, creates the GitHub release, and runs the publish fan-out, with every gate derived from the reviewed version string (`-rc.` marks a pre-release) and tag ordering — there is no dispatch flag to desync.

**Promotion is the plain run.** After an rc series, dispatching prepare with `channel: stable` on the same ref computes exactly `X.Y.Z` from the same commits — no finalize flag, no config override. The same-source guard (`scripts/promotion_guard.sh`) runs in **two layers**: at prepare time, inside `knope prepare-release` after the prep commit and before the push/PR steps, so a drifted promotion refuses without ever becoming a mergeable PR; and again **before** the tag is created, as the immutable-tag backstop for drift landing between prepare and merge. It refuses unless the diff since the last reachable rc touches only release stamps plus `docs/releases/**` (notes are release metadata); a refusal leaves no tag behind, and any other commit landing between rc and promotion forces a new rc.

Two operator rules are load-bearing: **never use GitHub's "Update branch" button on a release PR** — it merges the base into the prep branch behind the tool's back; the only refresh is re-dispatching Release Prepare, which recreates the prep branch from its base. And a stale release PR is already hard-blocked by the ruleset's strict required checks — re-dispatch instead of merging around staleness.

Rolling channels are ordering-aware: Docker `latest`/`vX`/`vX.Y` (and `rc` on the pre-release side), the GitHub latest-release pointer, the docs `latest` alias, the marketplace entry, and the registry entry only follow a release that is the newest in the relevant series, so a backport patch cut from an old `release/X.Y` never repoints them backwards — and the guarantee survives overlapping releases, because each mutable channel rechecks tag ordering inside its own serialized publish job.

**There is no merge-back.** What `main` still wants from a branch release is bookkeeping: after a *stable* branch release, the `port-bookkeeping` job in `release.yml` opens an ordinary PR carrying the release's changelog section and release-notes page to the default branch (rcs port nothing — knope's stable section is cumulative over the rc cycle) — reviewed CI-gated merge, no admin-bypass push, and nothing deadlocks if it merges late. Version stamps are deliberately not ported, so a later prepare on another branch can recompute an already-released version; Release Prepare catches exactly that — it refuses when the computed version already carries a repo-global tag, naming the remedy (`override_version`, or porting the version stamps first) — so the collision surfaces at prepare time with a clear message, never at tag time. When to cut a branch at all — and the default of releasing from a quiescent trunk without one — is the [release model](#release-model) above, not machinery.

<!-- TEMPLATE-TRACKING-START -->
## Release notes pages

`docs/releases/` is the canonical human-facing release narrative: one page per minor series, patch releases appending a dated section to their series page. The page is a document with exactly the changelog's lifecycle — it differs from `CHANGELOG.md` only in author and source material, and narrative prose never moves into the changelog. **Release preparation writes it**: Release Prepare's `draft-notes` job has an agent research the release range through the GitHub API (linked issues and PRs, never commit subjects; the `writing-release-notes` skill is the contract) and commits the page onto the prep branch, so every release PR — rc and stable alike — carries stamps, changelog, and notes in one reviewable diff. Merging the release PR lands the page in the tagged tree and on the base branch; `main` accumulates the pages for all releases (branch cuts reach it via the port-bookkeeping PR, like the changelog section). The release body takes its summary block and deep link from the page in the tag at publish time.

Rules for working with the notes:

- **The evidence contract governs the page.** Every causal claim must trace to a linked issue or PR; no evidence, no narrative. Reviewing a release PR includes this evidence check — follow the links and verify they support the claims — not prose polish; Vale (including the `ai-tells` pack) already gated the prose.
- **`skip_notes` is the only way to release without the refresh.** The release PR is held as a **draft** until the notes job lands its commit (or verifies the page's `notes-range-end` watermark already matches the range end), so a notes-less release PR is never mergeable; a failed drafting job fails the prepare run loudly and leaves the PR draft. Re-run it, or re-dispatch Release Prepare with `skip_notes: true` to consciously ship whatever page state the base carries. The **Release Notes** workflow's manual dispatch remains as the backfill/re-draft tool (its standalone notes PRs need no separate issue, same class as Renovate bumps), and later hand edits to a released page trigger the **Release Notes Publish** docs redeploy.
<!-- TEMPLATE-TRACKING-END -->

## Tool Registration Checklist

Every MCP tool you register must carry the full set of metadata below — not just the behaviour. A tool that works but lacks a title, hints, or docs is incomplete. When adding or changing a tool, verify each item:

- **Title** — a human-readable `annotations.title` (e.g. `"Search Vault"`). Title-aware clients (notably VS Code, which honours only `title` and `readOnlyHint` among annotations) render this as the tool's label; without it they fall back to the raw machine name. Set it inline in the tool's `annotations={...}` dict.
- **Behavioural hints** — `readOnlyHint`, and where they apply `destructiveHint` / `idempotentHint`, in the same `annotations` dict. These describe side effects accurately (a destructive tool must set `destructiveHint=True`).
- **Icon** — an entry wired via `register_tool_icons(...)` or `@mcp.tool(icons=[...])` (see [Tool icons](#tool-icons)).
- **Docstring** — a Google-style docstring; FastMCP surfaces it as the tool description and per-parameter docs.
- **Docs entry** — a row in your published tools reference (e.g. `docs/tools/index.md`) so the tool is documented for users (per [Documentation Discipline](#documentation-discipline)).
- **Enforcement test** — keep a test that enumerates the registered tools and asserts each carries the metadata above (at minimum a non-empty `annotations.title`). Enumerate the *full* registry, not just the client-facing listing, so app-only / hidden tools cannot slip past. Such a test turns this checklist into a CI gate: a future tool added without a title fails loudly rather than silently shipping its machine name.

## Server Info Tool (`get_server_info`)

`make_server()` registers `get_server_info` (via `fastmcp_pvl_core.register_server_info_tool`) so operators can answer "is the latest fix actually deployed?" with a single MCP call. The default response carries `server_name`, `server_version`, and `core_version`.

For services that talk to a remote upstream (e.g. paperless, an HTTP API), wire the upstream version inside the `DOMAIN-UPSTREAM-START` / `DOMAIN-UPSTREAM-END` sentinel in `src/markdown_vault_mcp/server.py`. Pass `upstream_version=` (a zero-arg callable returning a dict / str / None) and optionally `upstream_label="<service>"` (default `"upstream"`). The simplest pattern is a module-level upstream client (typically constructed from env vars at import time) whose version method is referenced from the callable — `CurrentContext()` is a FastMCP DI marker that only resolves inside parameter defaults, so it cannot be called directly from a zero-arg provider. The block is preserved across `copier update`.

## Repository protection (rulesets)

`.github/rulesets/*.json` are the source of truth for this repository's branch and tag rulesets — required PRs + the `CI Success` check on `main` and `release/*`, deletion/force-push protection for `v*` tags — and `bootstrap.yml` applies them (upsert by name) on pushes touching those files and on manual dispatch. Never adjust protection in the GitHub UI: the next bootstrap run resets it to the checked-in state. Change the JSON files instead. The release pipeline's `RELEASE_TOKEN` (a repository admin's PAT) bypasses these rules **by design** via the admin-role bypass entry — its remaining jobs are creating the `vX.Y.Z` tag + GitHub release after a release PR merges (the `v*` tag ruleset applies) and opening the release/notes/port PRs whose CI must run; direct release-commit and merge-back pushes to protected branches are gone with PSR. Posture and bypass model: `docs/deployment/repository-protection.md`.

<!-- TEMPLATE-TRACKING-START -->
## Shared Infrastructure

Shared infrastructure (auth providers, middleware stack, logging bootstrap, event store factory, CLI scaffolding, release pipeline, Docker entrypoint, nfpm packaging, mcpb bundle) lives upstream in two places:

- [`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core) — the Python library that provides `ServerConfig`, auth builders, middleware helpers, and the `make_serve_parser` / `configure_logging_from_env` / `normalise_http_path` CLI helpers.
- [`fastmcp-server-template`](https://github.com/pvliesdonk/fastmcp-server-template) — the copier template this project was generated from. Ships the CI/release workflows, `knope.toml`, `Dockerfile`, `packaging/nfpm.yaml`, `packaging/mcpb/*`, `scripts/stamp_manifests.py`, server.py skeleton, and this very section of CLAUDE.md.

Fixes and improvements to shared code land in those repos and propagate here via `copier update` against the template's latest tag — run manually or via the weekly `.github/workflows/copier-update.yml` cron. Starter files listed in `_skip_if_exists` (e.g. `packaging/mcpb/*`, the `tools.py` / `resources.py` / `prompts.py` / `domain.py` scaffolds, `CHANGELOG.md`, `LICENSE`) are written once and require manual reconciliation on template updates; `CLAUDE.md`, `README.md`, `.pre-commit-config.yaml` and `scripts/stamp_manifests.py` are deliberately *not* among them — all four are re-rendered on update, and only content inside their domain sentinels survives (`DOMAIN-START` / `DOMAIN-END` in the two Markdown files, `DOMAIN-HOOKS` in the pre-commit config, `DOMAIN-MANIFESTS-HELPERS` / `DOMAIN-MANIFESTS` in the stamp script) — review `_skip_if_exists` in the template's `copier.yml` if you need to force-sync a file. Domain-specific code (tools, resources, prompts, and the fields and logic inside the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END`, `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END`, and `CONFIG-VALIDATE-START` / `CONFIG-VALIDATE-END` sentinels) stays in this repo.

## Contributing fixes upstream

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the three-tier routing (library to `fastmcp-pvl-core`, template to `fastmcp-server-template`, domain to this repo), the issue/PR discipline, and the uncertainty rule. CONTRIBUTING.md is the single source; this section is a pointer.

The `authoring-issues-prs` skill (`.claude/skills/authoring-issues-prs/SKILL.md`) fires when filing issues or PRs: it walks the routing, picks the issue form, and performs the follow-up steps forms cannot (native sub-issue links for epics, the release milestone or `ships-atomically` label). The skill is template-owned and re-rendered on `copier update`; project-specific authoring conventions belong inside its `DOMAIN-AUTHORING` sentinel block.

If a conflict marker appears in a copier-update bot PR, the conflict itself often signals a template bug — investigate whether the template's version needs fixing before resolving locally.
<!-- TEMPLATE-TRACKING-END -->

<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->

## Key Design Decisions
<!-- DOMAIN-START -->
- Document identity: relative path with `.md` extension
- Frontmatter: optional by default, `required_frontmatter` config to enforce
- Hybrid search: Reciprocal Rank Fusion (RRF)
- Tool semantics: mirror Claude Code Read/Write/Edit patterns
- Library is sync; MCP layer uses `asyncio.to_thread()`
- Full decision log in `docs/design/design.md` appendix
<!-- DOMAIN-END -->
