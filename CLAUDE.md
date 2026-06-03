# markdown-vault-mcp

Generic markdown collection MCP server with FTS5 + semantic search, frontmatter-aware indexing, and incremental reindexing.

## Design
<!-- DOMAIN-START -->
The authoritative design specification lives at [`docs/design.md`](docs/design.md). All implementation must conform to this spec. When in doubt, the design doc wins.
<!-- DOMAIN-END -->

## Project Structure
<!-- DOMAIN-START -->
```
src/markdown_vault_mcp/
  utils/
    text.py            -- text normalization, position mapping, fuzzy matching
    links.py           -- link target computation and replacement
  managers/
    link.py            -- LinkManager: backlinks, outlinks, broken, orphans, hubs, paths
    search.py          -- SearchManager: keyword/semantic/hybrid search, list, context, stats
    index.py           -- IndexManager: build_index, reindex, embeddings, flush
    document.py        -- DocumentManager: CRUD, attachments, path validation, backlinks
    git_query.py       -- GitQueryManager: git history/diff reads (#610)
  indexing/
    index_writer.py    -- IndexWriter: single-owner FIFO writer thread + job dataclasses/runners
    readiness.py       -- ReadinessState: build-readiness state machine (#576)
    coordinator.py     -- IndexWriteCoordinator: owns the writer + build/async orchestration (#576)
  facets/
    reader.py          -- ReaderFacet: search/read/list/toc/similar/context/stats/history (#604)
    writer.py          -- WriterFacet: write/edit/delete/rename/attachments (#604)
    graph.py           -- GraphFacet: backlinks/outlinks/broken/orphans/most-linked/paths (#604)
    index.py           -- IndexFacet: build/reindex/embeddings, readiness, writer + embeddings status (#604)
  scanner.py           -- file discovery, frontmatter parsing, chunking
  fts_index.py         -- SQLite FTS5 schema, BM25 search
  vector_index.py      -- numpy embeddings, cosine similarity
  providers.py         -- embedding provider ABC + implementations
  tracker.py           -- hash-based change detection
  collection.py        -- thin facade: lifecycle, wiring, delegation (index-write → indexing/coordinator.py)
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
4. **Patch coverage ≥ 80%** — Run `uv run pytest --cov=<changed_module> --cov-report=term-missing` and verify new code is exercised. Add tests for every uncovered branch before pushing.
5. **Docs updated** — `README.md` and `docs/**` reflect any user-facing changes in the same commit
6. **Manifest version lockstep** — `server.json`, `.claude-plugin/plugin/.claude-plugin/plugin.json`, and `.claude-plugin/plugin/.mcp.json` must all carry the same version. The release workflow bumps them atomically, but if you manually touch any of them, update all three.

## Pre-commit Hooks

This project ships a `.pre-commit-config.yaml` that runs ruff (check + format), mypy on `src/` and `tests/`, gitleaks secret scanning, and standard whitespace/YAML/JSON checks — aligned with the `ci.yml` lint/typecheck/secrets jobs so a clean pre-commit run implies a clean CI lane.

- **Install once per clone:** `uv run pre-commit install`.
- **Run on demand before pushing:** `uv run pre-commit run --all-files`. A green run is a precondition for gates #2 and #3 above.
- **Never bypass with `--no-verify`.** A failing hook means the same check will fail in CI; fix the underlying issue rather than silencing it.

The config is in `_skip_if_exists`, so domain-specific additions (shellcheck, yamllint, project-specific linters, additional file checks) on top of the shipped defaults survive `copier update`.

## PR Discipline

**Every PR must have at least one associated issue.** If the work doesn't have one yet — a bug found in the wild, an opportunistic cleanup, a small improvement — create the issue first, then open the PR with `Closes #N` (or `Refs #N`) in the body. A single PR may close multiple issues (`Closes #A, closes #B`) — bundling related fixes is fine; the rule is "no orphan PRs", not "one PR per issue". This keeps the changelog, release notes, and cross-repo history coherent.

Trivial exceptions: pure typo fixes and automated dependency bumps (Dependabot / Renovate) may skip the issue.

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

## Logging Standard

### Framework
- Standard library `logging` throughout. Every module: `logger = logging.getLogger(__name__)`.
- No `print()` for operational output. No third-party logging libraries.
- FastMCP middleware handles tool invocation, timing, and error logging automatically.
- All logging goes through FastMCP's `configure_logging()` for uniform output. `FASTMCP_LOG_LEVEL` is the single log level control; the `-v` CLI flag sets it to `DEBUG`. `FASTMCP_ENABLE_RICH_LOGGING=false` switches to plain/JSON output.

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

### Tool icons

Drop SVG / PNG / ICO / JPEG files into `src/markdown_vault_mcp/static/icons/` and bulk-attach them to registered tools via `fastmcp_pvl_core.register_tool_icons(mcp, {"tool_name": "filename.svg"}, static_dir=...)` at the end of `register_tools()` — or attach at decoration time with `@mcp.tool(icons=[make_icon(STATIC / "x.svg")])` (where `STATIC = Path(__file__).parent / "static" / "icons"` is a shorthand you define at module level). The scaffold ships an empty `static/icons/` directory; commented-out wiring lives in `tools.py`.

### Dockerfile extension points

These sentinel blocks in `Dockerfile` are preserved across `copier update`. Add domain-specific apt packages, uv extras, state subdirs, and volume mounts inside them:

- `# DOCKERFILE-APT-DEPS-START` / `-END` — extra apt packages installed into the runtime image
- `# DOCKERFILE-UV-EXTRAS-START` / `-END` — `--extra <name>` flags added to both `uv sync` invocations (deps cache layer + project install — adding only to one breaks the cache layer)
- `# DOCKERFILE-STATE-DIRS-START` / `-END` — state subdirectories created under `/data` (chowned to the runtime user)
- `# DOCKERFILE-VOLUMES-START` / `-END` — `VOLUME` declarations on the final image

## Server Info Tool (`get_server_info`)

`make_server()` registers `get_server_info` (via `fastmcp_pvl_core.register_server_info_tool`) so operators can answer "is the latest fix actually deployed?" with a single MCP call. The default response carries `server_name`, `server_version`, and `core_version`.

For services that talk to a remote upstream (e.g. paperless, an HTTP API), wire the upstream version inside the `DOMAIN-UPSTREAM-START` / `DOMAIN-UPSTREAM-END` sentinel in `src/markdown_vault_mcp/server.py`. Pass `upstream_version=` (a zero-arg callable returning a dict / str / None) and optionally `upstream_label="<service>"` (default `"upstream"`). The simplest pattern is a module-level upstream client (typically constructed from env vars at import time) whose version method is referenced from the callable — `CurrentContext()` is a FastMCP DI marker that only resolves inside parameter defaults, so it cannot be called directly from a zero-arg provider. The block is preserved across `copier update`.

## File Exchange (`register_file_exchange` + opt-in upload)

`make_server()` reserves a `DOMAIN-FILE-EXCHANGE-START` /
`DOMAIN-FILE-EXCHANGE-END` sentinel in `src/markdown_vault_mcp/server.py`
for the `fastmcp-pvl-core` file-exchange helpers. The block is preserved
across `copier update`, so opt-in customisations (`produces=`,
`consumer_sink=`, the upload receiver, or — once #431 lands — the
download-direction `register_file_exchange(...)` call itself) survive
subsequent template updates.

> **Project-specific override:** unlike the template default, the two
> directions are wired asymmetrically.
>
> - **Upload direction is wired** as of #443:
>   `register_file_exchange_upload(...)` is active in
>   `server.py`, with `_vault_upload_receiver` /
>   `_validate_upload_target` from `markdown_vault_mcp.uploads` as the
>   receiver and pre-link validator. The route auto-mounts only when
>   transport is HTTP/SSE *and* `MARKDOWN_VAULT_MCP_BASE_URL` is set.
> - **Download direction is still deferred to #431** because the
>   spec-compliant `create_download_link(origin_id, ttl_seconds)` tool
>   collides on name with MV's existing
>   `create_download_link(path, ttl_seconds)` (registered via
>   `ArtifactStore` in the `DOMAIN-WIRING` block). Do not add
>   `register_file_exchange(mcp, ...)` to the sentinel block until #431
>   resolves the collision.
>
> See [`docs/guides/file-exchange.md`](docs/guides/file-exchange.md)
> for the wiring pattern, and the
> [`create_upload_link` tool entry](docs/tools/index.md) for the
> agent-facing contract.

## Shared Infrastructure

Shared infrastructure (auth providers, middleware stack, logging bootstrap, event store factory, CLI scaffolding, release pipeline, Docker entrypoint, nfpm packaging, mcpb bundle) lives upstream in two places:

- [`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core) — the Python library that provides `ServerConfig`, auth builders, middleware helpers, MCP File Exchange (`register_file_exchange` + `register_file_exchange_upload`), and the `make_serve_parser` / `configure_logging_from_env` / `normalise_http_path` CLI helpers.
- [`fastmcp-server-template`](https://github.com/pvliesdonk/fastmcp-server-template) — the copier template this project was generated from. Ships the CI/release workflows, `Dockerfile`, `packaging/nfpm.yaml`, `packaging/mcpb/*`, `scripts/bump_manifests.py`, server.py skeleton, and this very section of CLAUDE.md.

Fixes and improvements to shared code land in those repos and propagate here via `copier update` against the template's latest tag — run manually or via the weekly `.github/workflows/copier-update.yml` cron. Starter files listed in `_skip_if_exists` (e.g. `scripts/bump_manifests.py`, `packaging/mcpb/*`, the `tools.py` / `resources.py` / `prompts.py` / `domain.py` scaffolds, `README.md`, `CHANGELOG.md`, `LICENSE`, `.env.example`) are written once and require manual reconciliation on template updates — review `_skip_if_exists` in the template's `copier.yml` if you need to force-sync a file. Domain-specific code (tools, resources, prompts, and the fields and logic inside the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` and `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END` sentinels) stays in this repo.

## Contributing fixes upstream

- **Library-level fix** (anything you'd change in `fastmcp_pvl_core`): open a PR on `pvliesdonk/fastmcp-pvl-core`. After merge + release, bump `fastmcp-pvl-core` in this project's `pyproject.toml`. (Copier update alone won't pick it up unless the template's version constraint in `pyproject.toml.jinja` is also bumped.)
- **Template-level fix** (anything template-owned — `Dockerfile`, workflows, `server.py` skeleton, `CLAUDE.md` sections): open a PR on `pvliesdonk/fastmcp-server-template`. After merge + release, this project gets the fix on the next weekly `copier update` cron (or dispatch the workflow manually).
- **Domain-only fix** (anything inside a `DOMAIN-*`, `CONFIG-*`, or `PROJECT-*` sentinel block, `tools.py`, `resources.py`, `prompts.py`, `domain.py`, `tests/`): PR on this repo directly.

If a conflict marker appears in a copier-update bot PR, the conflict itself often signals a template bug — investigate whether the template's version needs fixing before resolving locally.

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
