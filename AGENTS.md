# Markdown Vault MCP

Generic markdown vault MCP with hybrid search

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
    __init__.py        -- shared path-traversal guard (resolve_inside + validate_path variants) + attachment/exclusion helpers re-exported for managers and facets (#876)
    text.py            -- text normalization, position mapping, fuzzy matching
    links.py           -- link target computation and replacement
    serialization.py   -- toc_payload: TocEntry/SubtreeToc → JSON-able dicts
    fs.py              -- filesystem traversal helpers: symlink-aware iteration, directory pruning (#508, #835)
    fts.py             -- fts_row_to_note_info: FTS row → NoteInfo conversion shared across managers
  managers/
    __init__.py        -- package aggregator: re-exports DocumentManager/IndexManager/LinkManager/SearchManager
    link.py            -- LinkManager: backlinks, outlinks, broken, orphans, hubs, paths
    search.py          -- SearchManager: keyword/semantic/hybrid search, list, context, stats
    index.py           -- IndexManager: build_index, reindex, dirty-path FTS refresh; delegates embeddings to the composed EmbeddingsManager (#1157)
    embeddings.py      -- EmbeddingsManager: vector lifecycle — cold build, convergence, inline embed, deferred flush, status (#1157); internal collaborator, not re-exported
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
    strategy.py        -- GitWriteStrategy: auto-commit per write; composes RepoBootstrap + PushScheduler over one shared lock (#893)
    bootstrap.py       -- RepoBootstrap: managed-clone bootstrap, remote-protocol validation, memoised git-root discovery (#893)
    push_scheduler.py  -- PushScheduler: deferred-push timer + pending-flag mechanics and push execution (#893)
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
  _identity.py         -- Principal write identity: tool-edge resolution, contextvar carry, claim-key registration (#1160)
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



## Skills

Detailed guidance lives in skills under `.agents/skills/` (portable; Claude Code reaches them through `.claude/skills/` symlinks). They load only when invoked, so invoke them explicitly:

- `releasing` — before any release, release-candidate, unstable-channel, plugin-channel, or release-notes work.
- `config-contract` — before adding a config field, env var, Dockerfile extension point, mcpb install-screen entry, or release-manifest stamp.
- `logging-standard` — before adding or changing a logging call.
- `tool-registration` — before adding, renaming, or documenting an MCP tool, `get_server_info`, icons, or the public import surface.
- `repository-protection` — before changing rulesets, required checks, or the bootstrap workflow.
- `authoring-issues-prs` — when filing an issue or opening a PR.
- `code-review` — before opening a PR, marking one ready, or pushing further commits to a branch with an open PR: self-review the cumulative diff.
- `applying-template-updates` — when working through the weekly template update PR (`copier/update` branch) or after running `copier update`.
- `writing-release-notes` — when drafting a `docs/releases/` page.

Project-owned skills follow the same shape: a directory under `.agents/skills/` plus a relative symlink in `.claude/skills/`.

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

Every PR must pass **all** of the following before merge. Do not open or push a PR until these are green locally:

1. **CI passes** — `uv run pytest -x -q` all tests pass
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
Request Claude selectively on a pull request or issue with an explicit `@claude`
mention.


Automatic agent review is disabled. Request Claude selectively with an
`@claude` mention; deterministic CI remains the merge gate.

<!-- TEMPLATE-TRACKING-END -->

## GitHub Review Types

GitHub has two distinct review mechanisms — **both must be read and addressed**:

- **Inline review comments** (`get_review_comments`): attached to specific lines of the diff. Appear in the "Files changed" tab. Use `get_review_comments` to fetch these.
- **PR-level comments** (`get_comments`): posted on the Conversation tab, not tied to a line. Review summary posts, bot analysis, and blocking issues are often posted here. Use `get_comments` to fetch these.

Always fetch both before declaring a review round complete.

## Documentation Discipline

Every issue, PR, and code change must consider documentation impact. Before closing any issue or creating any PR, check whether the following need updating:

- **`docs/design/`** — internal design specs and architecture decisions (the authoritative dev reference). Any new feature, changed behavior, or architectural decision must be reflected here. Not part of the published site.
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

<!-- TEMPLATE-TRACKING-START -->
## Shared Infrastructure

Shared infrastructure (auth providers, middleware stack, logging bootstrap, event store factory, CLI scaffolding, release pipeline, Docker entrypoint, nfpm packaging, mcpb bundle) lives upstream in two places:

- [`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core) — the Python library that provides `ServerConfig`, auth builders, middleware helpers, and the `make_serve_parser` / `configure_logging_from_env` / `normalise_http_path` CLI helpers.
- [`fastmcp-server-template`](https://github.com/pvliesdonk/fastmcp-server-template) — the copier template this project was generated from. Ships the CI/release workflows, `knope.toml`, `Dockerfile`, `packaging/nfpm.yaml`, `packaging/mcpb/*`, `scripts/stamp_manifests.py`, server.py skeleton, and this very section of AGENTS.md.

Fixes and improvements to shared code land in those repos and propagate here via `copier update` against the template's latest tag — run manually or via the weekly `.github/workflows/copier-update.yml` cron. Starter files listed in `_skip_if_exists` (e.g. `packaging/mcpb/*`, the `tools.py` / `resources.py` / `prompts.py` / `domain.py` scaffolds, `CHANGELOG.md`, `LICENSE`) are written once and require manual reconciliation on template updates; `AGENTS.md`, `README.md`, `.pre-commit-config.yaml` and `scripts/stamp_manifests.py` are deliberately *not* among them — all four are re-rendered on update, and only content inside their domain sentinels survives (`DOMAIN-START` / `DOMAIN-END` in the two Markdown files, `DOMAIN-HOOKS` in the pre-commit config, `DOMAIN-MANIFESTS-HELPERS` / `DOMAIN-MANIFESTS` in the stamp script) — review `_skip_if_exists` in the template's `copier.yml` if you need to force-sync a file. Domain-specific code (tools, resources, prompts, and the fields and logic inside the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END`, `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END`, and `CONFIG-VALIDATE-START` / `CONFIG-VALIDATE-END` sentinels) stays in this repo.

## Contributing fixes upstream

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the three-tier routing (library to `fastmcp-pvl-core`, template to `fastmcp-server-template`, domain to this repo), the issue/PR discipline, and the uncertainty rule. CONTRIBUTING.md is the single source; this section is a pointer.

The provider-neutral release-notes skill lives at `.agents/skills/writing-release-notes/SKILL.md`; invoke it explicitly when preparing or revising release narrative. Claude Code reaches the same skills through `.claude/skills/` symlinks.

The `authoring-issues-prs` skill (`.agents/skills/authoring-issues-prs/SKILL.md`, reachable by Claude Code via `.claude/skills/`) fires when filing issues or PRs: it walks the routing, picks the issue form, and performs the follow-up steps forms cannot (native sub-issue links for epics, the release milestone or `ships-atomically` label). The skill is template-owned and re-rendered on `copier update`; project-specific authoring conventions belong inside its `DOMAIN-AUTHORING` sentinel block.

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
- Indexing is hash-based, so an unchanged file is never re-parsed: any change to how a note's stored rows are derived from its bytes (link extraction, chunking, tag/alias/heading derivation) must bump `INDEX_SEMANTICS_VERSION` in `fts_index.py` in the same commit, or deployed vaults keep serving the rows the old code produced (#1124)
- Full decision log in `docs/design/design.md` appendix
<!-- DOMAIN-END -->
