# markdown-vault-mcp

Generic markdown vault MCP server with FTS5 + semantic search, frontmatter-aware indexing, and incremental reindexing.

## Design
<!-- DOMAIN-START -->
The authoritative design specification lives at [`docs/design.md`](docs/design.md). All implementation must conform to this spec. When in doubt, the design doc wins.
<!-- DOMAIN-END -->

## Project Structure
<!-- DOMAIN-START -->
```
src/markdown_vault_mcp/
  __init__.py        -- lazy (PEP 562) public API root; must stay import-light (#665)
  utils/
    text.py            -- text normalization, position mapping, fuzzy matching
    links.py           -- link target computation and replacement
    serialization.py   -- toc_payload: TocEntry/SubtreeToc → JSON-able dicts
  managers/
    link.py            -- LinkManager: backlinks, outlinks, broken, orphans, hubs, paths
    search.py          -- SearchManager: keyword/semantic/hybrid search, list, context, stats
    index.py           -- IndexManager: build_index, reindex, embeddings, flush
    document.py        -- DocumentManager: CRUD, attachments, path validation, backlinks
    git_query.py       -- GitQueryManager: git history/diff reads (#610)
    _vector_loader.py  -- shared load-or-self-heal routine for the vector sidecar (#736)
  indexing/
    index_writer.py    -- IndexWriter: single-owner FIFO writer thread + job dataclasses/runners
    readiness.py       -- ReadinessState: build-readiness state machine (#576)
    coordinator.py     -- IndexWriteCoordinator: owns the writer + build/async orchestration (#576)
  facets/
    reader.py          -- ReaderFacet: search/read/list/toc/similar/context/stats/history (#604)
    writer.py          -- WriterFacet: write/edit/delete/rename/attachments (#604)
    graph.py           -- GraphFacet: backlinks/outlinks/broken/orphans/most-linked/paths (#604)
    index.py           -- IndexFacet: build/reindex/embeddings, readiness, writer + embeddings status (#604)
  transfer/
    store.py           -- TransferStore: in-memory one-time transfer-token state machine (#622)
    routes.py          -- ASGI handler for the /transfer/{token} download/upload route (#622)
  scanner.py           -- file discovery, frontmatter parsing, chunking
  fts_index.py         -- SQLite FTS5 schema, BM25 search
  vector_index.py      -- numpy embeddings, cosine similarity
  providers.py         -- embedding provider ABC + implementations
  tracker.py           -- hash-based change detection
  vault.py             -- thin composition root: lifecycle, wiring, facet accessors (index-write → indexing/coordinator.py)
  write_callback.py    -- WriteCallbackDispatcher: deferred git-commit callback worker (#599)
  config.py            -- configuration loading
  server.py            -- generic FastMCP server factory (make_server) with tool annotations
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
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- Google-style docstrings on all public functions
- `logging.getLogger(__name__)` throughout, no `print()`
- Type hints everywhere
- Tests: `pytest` with fixtures in `tests/fixtures/`

## Hard PR Acceptance Gates

Every PR must pass **all** of the following locally before push. These are mechanical preconditions:

1. **Tests pass** — `uv run pytest -x -q` all tests pass
2. **Lint passes** — run in this exact order: `uv run ruff check --fix .` then `uv run ruff format .` then verify with `uv run ruff format --check .`. Always run format *after* check --fix because check --fix can leave files needing reformatting.
3. **Type-check passes** — `uv run mypy src/ tests/` reports no errors
4. **Patch coverage ≥ 80%** — Codecov measures only lines added/changed in the PR diff. Run `uv run pytest --cov=<changed_module> --cov-report=term-missing` and verify new code is exercised. Add tests for every uncovered branch before pushing.

5. **Structural quality (diff) passes** — new/changed code must introduce no new structural violations (complexity, too-many-*, security). Enforced on the diff only, so pre-existing code is never blocked. Run before pushing:
   ```bash
   uv run diff-quality --violations=ruff.check \
     --options="--extend-select=C901,PLR0911,PLR0912,PLR0913,PLR0915,S" \
     --compare-branch=origin/main --fail-under=100
   ```
   `# noqa: C901` (etc.) with a one-line justification is the escape hatch for genuinely irreducible new code.
6. **Docs updated** — `README.md` and `docs/**` reflect any user-facing changes in the same commit
7. **Manifest version lockstep** — `server.json`, `.claude-plugin/plugin/.claude-plugin/plugin.json`, and `.claude-plugin/plugin/.mcp.json` must all carry the same version. The release workflow bumps them atomically via PSR; manual touches require updating all three.


## Pre-commit Hooks

This project ships a `.pre-commit-config.yaml` that runs ruff (check + format), mypy on `src/` and `tests/`, gitleaks secret scanning, and standard whitespace/YAML/JSON checks — aligned with the `ci.yml` lint/typecheck/secrets jobs so a clean pre-commit run implies a clean CI lane.

- **Install once per clone:** `uv run pre-commit install`.
- **Run on demand before pushing:** `uv run pre-commit run --all-files`. A green run is a precondition for gates #2 and #3 above.

- **Structural gate runs at push time:** the `structural-diff-gate` hook (pre-push stage) runs the command above automatically. `uv run pre-commit install` wires it via `default_install_hook_types`. A clean local push implies a clean CI `structure` job — same command, same range, as long as your PR targets `main` (the hook hardcodes `origin/main`; CI compares against the PR's actual base branch).

- **Never bypass with `--no-verify`.** A failing hook means the same check will fail in CI; fix the underlying issue rather than silencing it.

The config is in `_skip_if_exists`, so domain-specific additions (shellcheck, yamllint, project-specific linters, additional file checks) on top of the shipped defaults survive `copier update`.


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

**When you notice decay outside the current change's scope** — a god class forming, a dead branch, a leaking abstraction, a name that no longer matches behaviour, or an audit hotspot — do **not** fix it inline (scope creep) and do **not** pass over it silently. **Open an issue** using this template:

> **What:** the structural problem in one sentence.
> **Where:** file/symbol and the metric or observation that flagged it.
> **Why it compounds:** what gets harder or riskier if it's left.
> **Suggested direction:** a starting point, not a prescribed refactor.

Constrain issues to **decay that will compound**, not anything imperfect. The diff-gate blocks new debt; these issues are the refactor-later backlog for pre-existing debt — neither blocks the current PR.


## PR Discipline

**Every PR must have at least one associated issue.** If the work doesn't have one yet — a bug found in the wild, an opportunistic cleanup, a small improvement — create the issue first, then open the PR with `Closes #N` (or `Refs #N`) in the body. A single PR may close multiple issues (`Closes #A, closes #B`) — bundling related fixes is fine; the rule is "no orphan PRs", not "one PR per issue". This keeps the changelog, release notes, and cross-repo history coherent.

Trivial exceptions: pure typo fixes and automated dependency bumps (Dependabot / Renovate) may skip the issue.

<!-- TEMPLATE-TRACKING-START -->
**Bot reviewers (claude-review, gemini-code-assist) are merge gates, not pair reviewers.** Local review must be complete before the PR opens. If a bot finds anything on first run, the local review was incomplete — that is a discipline failure to investigate, not "address-and-move-on." Run a local code-review pass on the cumulative diff before `gh pr create`; the bots are not a substitute.
<!-- TEMPLATE-TRACKING-END -->

## GitHub Review Types

GitHub has two distinct review mechanisms — **both must be read and addressed**:

- **Inline review comments** (`get_review_comments`): attached to specific lines of the diff. Appear in the "Files changed" tab. Use `get_review_comments` to fetch these.
- **PR-level comments** (`get_comments`): posted on the Conversation tab, not tied to a line. Review summary posts, bot analysis, and blocking issues are often posted here. Use `get_comments` to fetch these.

Always fetch both before declaring a review round complete.
## Documentation Discipline

Every issue, PR, and code change must consider documentation impact. Before closing any issue or creating any PR, check whether the following need updating:

- **`docs/design.md`** — the authoritative spec. Any new feature, changed behavior, or architectural decision must be reflected here. If the code diverges from the spec, update the spec.
- **`README.md`** — user-facing documentation. New env vars, tools, resources, prompts, CLI flags, or configuration options must be documented here.
- **`docs/` site pages** — the published documentation site. These pages must stay in sync with the codebase:
    - `docs/tools/index.md` — new or changed MCP tools
    - `docs/resources.md` — new or changed MCP resources
    - `docs/prompts.md` — new or changed MCP prompts
    - `docs/configuration.md` — new or changed env vars
    - `docs/installation.md` — new installation methods or dependencies
    - `docs/guides/*.md` — new features that affect user workflows (e.g., new views, auth modes, deployment options)
    - `docs/index.md` — feature list and architecture overview
- **`examples/`** — example env files. New env vars or changed defaults should be reflected in relevant examples.
- **`CHANGELOG.md`** — managed by semantic-release from conventional commits, but verify entries are meaningful.
- **`.claude-plugin/plugin/README.md`** — if the env vars wired in `.mcp.json` change or new installation steps are needed
- **`docs/guides/claude-code-plugin.md`** — new guide for the Claude Code plugin channel; update when wired env vars or plugin behavior changes
- **Inline docstrings** — new or changed public API methods need accurate docstrings.

**Rule: code without matching docs is incomplete.** When writing issues, include a "Documentation" section listing which docs need updating. When reviewing PRs, verify documentation is included. A PR that adds a tool, env var, resource, or user-facing feature without updating the corresponding docs/ page and README section should not be merged.

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

Domain configuration composes `fastmcp_pvl_core.ServerConfig` inside your domain config class (see `src/markdown_vault_mcp/config.py`).  Add domain fields between the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` sentinels and populate them in `from_env` between the `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END` sentinels.  Never inherit from `ServerConfig`; always compose.

Env var prefix is `MARKDOWN_VAULT_MCP_` — all env reads go through `fastmcp_pvl_core.env(_ENV_PREFIX, "SUFFIX", default)` so naming stays consistent.

- **Domain CLI subcommands** go in the `# DOMAIN-COMMANDS-START` / `-END` block in `cli.py` (like `CONFIG-FIELDS` in `config.py`). Register them as `@app.command()` and use function-local imports for domain modules. The block is preserved across `copier update`.

### Config wizard

`docs/javascripts/config-wizard/wizard-spec.json` drives the guided-setup page. It is **domain-owned and write-once** (`_skip_if_exists`): the runtime (`wizard.js`, `generators.js`, `wizard-spec-schema.json`, the generic tests) is template-owned and re-rendered, but the spec itself is never re-rendered, so it does **not** auto-update when you add config or when the template grows new questions. Reconcile it by hand.

Two rules keep the spec honest:

- **Cover the `ServerConfig` surface.** The seed covers the full `ServerConfig` surface plus logging; the drift test enforces completeness, so no explicit field list needs hand-maintaining here. When you add a domain setting that an operator would plausibly configure, add a question for it. When you adopt a new upstream setting, surface it too.
- **Coverage is CI-enforced:** `tests/test_config_wizard_drift.py` fails if the wizard offers a var no read site consumes (orphan) or omits a setting the server reads: both `ServerConfig` (via `server_config_env_suffixes()`) and your `ProjectConfig` (via `domain_env_suffixes(ProjectConfig)`, which recurses into any sub-config sections it composes — not just the reads in `ProjectConfig.from_env` itself). Offer every setting; hide niche ones with `advancedGroup`, never by omission. For the coverage check, the only escape is `_COVERED_BY_INFERENCE`, for settings with no dedicated control by design (for example, `AUTH_MODE`, inferred from which auth vars are set). For the orphan check, `FASTMCP_*` vars are exempt: their prefix never matches `MARKDOWN_VAULT_MCP_`, so they sit outside the coverage check by construction, and they are read by FastMCP itself rather than by project code.
- **Only offer vars the server actually consumes.** Every `var` must resolve to a real read site (`ServerConfig.from_env`, your `ProjectConfig.from_env`, the CLI, or a native `FASTMCP_*` var) — *advertised but unread* env vars (e.g. a hint that mentions `MARKDOWN_VAULT_MCP_SERVER_NAME` while the scaffold hardcodes the name) must not appear. List secret-bearing vars in `secretKeys` so the wizard masks them and keeps them out of the shareable link. A question may legitimately have **no `var`** when it is a wizard-internal routing key — the seed's `auth` select drives `showIf` but maps to no single env var (auth mode is inferred by `ServerConfig` from which vars are set), so it is not an orphan. Gate `showIf`/`guards` on the questions that are actually visible, and make every `showIf` self-contained: because the runtime checks raw answers with no cascade, a question gated on `auth` must *also* gate on `deployment=server` (the gate on `auth` itself), or it leaks — and emits its var — when `auth` is hidden but its stale answer lingers.

### Tool icons

Drop SVG / PNG / ICO / JPEG files into `src/markdown_vault_mcp/static/icons/` and bulk-attach them to registered tools via `fastmcp_pvl_core.register_tool_icons(mcp, {"tool_name": "filename.svg"}, static_dir=...)` at the end of `register_tools()` — or attach at decoration time with `@mcp.tool(icons=[make_icon(STATIC / "x.svg")])` (where `STATIC = Path(__file__).parent / "static" / "icons"` is a shorthand you define at module level). The scaffold ships an empty `static/icons/` directory; commented-out wiring lives in `tools.py`.

### Dockerfile extension points

These sentinel blocks in `Dockerfile` are preserved across `copier update`. Add domain-specific apt packages, uv extras, state subdirs, and volume mounts inside them:

- `# DOCKERFILE-APT-DEPS-START` / `-END` — extra apt packages installed into the runtime image
- `# DOCKERFILE-UV-EXTRAS-START` / `-END` — `--extra <name>` flags added to both `uv sync` invocations (deps cache layer + project install — adding only to one breaks the cache layer)
- `# DOCKERFILE-STATE-DIRS-START` / `-END` — state subdirectories created under `/data` (chowned to the runtime user)
- `# DOCKERFILE-VOLUMES-START` / `-END` — `VOLUME` declarations on the final image

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

<!-- TEMPLATE-TRACKING-START -->
## Shared Infrastructure

Shared infrastructure (auth providers, middleware stack, logging bootstrap, event store factory, CLI scaffolding, release pipeline, Docker entrypoint, nfpm packaging, mcpb bundle) lives upstream in two places:

- [`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core) — the Python library that provides `ServerConfig`, auth builders, middleware helpers, and the `make_serve_parser` / `configure_logging_from_env` / `normalise_http_path` CLI helpers.
- [`fastmcp-server-template`](https://github.com/pvliesdonk/fastmcp-server-template) — the copier template this project was generated from. Ships the CI/release workflows, `Dockerfile`, `packaging/nfpm.yaml`, `packaging/mcpb/*`, `scripts/bump_manifests.py`, server.py skeleton, `.gemini/config.yaml` (gemini-code-assist scope control), and this very section of CLAUDE.md.

Fixes and improvements to shared code land in those repos and propagate here via `copier update` against the template's latest tag — run manually or via the weekly `.github/workflows/copier-update.yml` cron. Starter files listed in `_skip_if_exists` (e.g. `scripts/bump_manifests.py`, `packaging/mcpb/*`, the `tools.py` / `resources.py` / `prompts.py` / `domain.py` scaffolds, `README.md`, `CHANGELOG.md`, `LICENSE`, `.env.example`) are written once and require manual reconciliation on template updates — review `_skip_if_exists` in the template's `copier.yml` if you need to force-sync a file. Domain-specific code (tools, resources, prompts, and the fields and logic inside the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` and `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END` sentinels) stays in this repo.

## Contributing fixes upstream

- **Library-level fix** (anything you'd change in `fastmcp_pvl_core`): open a PR on `pvliesdonk/fastmcp-pvl-core`. After merge + release, bump `fastmcp-pvl-core` in this project's `pyproject.toml`. (Copier update alone won't pick it up unless the template's version constraint in `pyproject.toml.jinja` is also bumped.)
- **Template-level fix** (anything template-owned — `Dockerfile`, workflows, `server.py` skeleton, `CLAUDE.md` sections): open a PR on `pvliesdonk/fastmcp-server-template`. After merge + release, this project gets the fix on the next weekly `copier update` cron (or dispatch the workflow manually).
- **Domain-only fix** (anything inside a `DOMAIN-*`, `CONFIG-*`, or `PROJECT-*` sentinel block, `tools.py`, `resources.py`, `prompts.py`, `domain.py`, `tests/`): PR on this repo directly.

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
- Full decision log in `docs/design.md` appendix
<!-- DOMAIN-END -->
