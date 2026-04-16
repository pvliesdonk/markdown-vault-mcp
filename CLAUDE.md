# markdown-vault-mcp

Generic markdown collection MCP server with FTS5 + semantic search, frontmatter-aware indexing, and incremental reindexing.

## Design

The authoritative design specification lives at [`docs/design.md`](docs/design.md). All implementation must conform to this spec. When in doubt, the design doc wins.

## Project Structure

```
src/markdown_vault_mcp/
  utils/
    text.py            -- text normalization, position mapping, fuzzy matching
    links.py           -- link target computation and replacement
  managers/
    link.py            -- LinkManager: backlinks, outlinks, broken, orphans, hubs, paths
    search.py          -- SearchManager: keyword/semantic/hybrid search, list, context
    index.py           -- IndexManager: build_index, reindex, embeddings, flush
    document.py        -- DocumentManager: CRUD, attachments, path validation, backlinks
  scanner.py           -- file discovery, frontmatter parsing, chunking
  fts_index.py         -- SQLite FTS5 schema, BM25 search
  vector_index.py      -- numpy embeddings, cosine similarity
  providers.py         -- embedding provider ABC + implementations
  tracker.py           -- hash-based change detection
  collection.py        -- thin facade: lifecycle, wiring, delegation
  config.py            -- configuration loading
  mcp_server.py        -- generic FastMCP server with tool annotations
  cli.py               -- CLI entry point
```

## Conventions

- Python 3.10+
- `uv` for package management, `ruff` for linting/formatting (line length 88)
- `hatchling` build backend
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- Google-style docstrings on all public functions
- `logging.getLogger(__name__)` throughout, no `print()`
- Type hints everywhere
- Tests: `pytest` with fixtures in `tests/fixtures/`

## Hard PR Acceptance Gates

Every PR must pass **all** of the following before merge. Do not open or push a PR until these are green locally:

1. **CI passes** — `uv run pytest -x -q` all tests pass
2. **Lint passes** — run in this exact order: `uv run ruff check --fix .` then `uv run ruff format .` then verify with `uv run ruff format --check .`. Always run format *after* check --fix because check --fix can leave files needing reformatting.
3. **Type-check passes** — `uv run mypy src/` reports no errors
4. **Patch coverage ≥ 80%** — Codecov measures only lines added/changed in the PR diff. Run `uv run pytest --cov=<changed_module> --cov-report=term-missing` and verify new code is exercised. Add tests for every uncovered branch before pushing.
5. **Docs updated** — `README.md` and `docs/**` reflect any user-facing changes in the same commit
6. **Manifest version lockstep** — `server.json`, `.claude-plugin/plugin/.claude-plugin/plugin.json`, and `.claude-plugin/plugin/.mcp.json` must all carry the same version. The release workflow bumps them atomically, but if you manually touch any of them, update all three.

## GitHub Review Types

GitHub has two distinct review mechanisms — **both must be read and addressed**:

- **Inline review comments** (`get_review_comments`): attached to specific lines of the diff. Appear in the "Files changed" tab. Use `get_review_comments` to fetch these.
- **PR-level comments** (`get_comments`): posted on the Conversation tab, not tied to a line. Review summary posts, bot analysis, and blocking issues are often posted here. Use `get_comments` to fetch these.

Always fetch both before declaring a review round complete.

## Reference

This project is extracted from [`pvliesdonk/if-craft-corpus`](https://github.com/pvliesdonk/if-craft-corpus). See the design doc's Reference Code section for the mapping between source files.

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

## Cross-Repo Sync

This repo shares domain-independent infrastructure with [`pvliesdonk/image-generation-mcp`](https://github.com/pvliesdonk/image-generation-mcp). See [`SYNC.md`](SYNC.md) for the shared file mapping, known divergences, and port history.

**Rule:** When fixing or improving shared infrastructure (auth, CI/CD, Docker entrypoint, release pipeline, packaging, CLI logging), create a corresponding issue in the other repo to port the change. Reference the source PR in the issue body. Domain-specific code (vault logic, image generation, tools, resources) does not need cross-posting.

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

## Key Design Decisions

- Document identity: relative path with `.md` extension
- Frontmatter: optional by default, `required_frontmatter` config to enforce
- Hybrid search: Reciprocal Rank Fusion (RRF)
- Tool semantics: mirror Claude Code Read/Write/Edit patterns
- Library is sync; MCP layer uses `asyncio.to_thread()`
- Full decision log in `docs/design.md` appendix
