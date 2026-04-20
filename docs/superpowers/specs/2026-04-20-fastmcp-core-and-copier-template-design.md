---
date: 2026-04-20
status: draft
topic: fastmcp-core-and-copier-template
scope: cross-repo — markdown-vault-mcp, image-generation-mcp, scholar-mcp, kroki-mcp, fastmcp-server-template (+ new fastmcp-pvl-core)
---

# Shared FastMCP Infrastructure: Core Library + Copier Template

## Problem

Four MCP server projects — `markdown-vault-mcp` (MV), `image-generation-mcp` (IG), `scholar-mcp`, `kroki-mcp` — plus a `fastmcp-server-template` scaffold repo all share the same non-domain infrastructure on top of FastMCP: auth, middleware, logging, CLI, Docker, CI/CD, packaging, conventions. Each also has its own domain logic (markdown indexing, image generation, scholarly search, diagram rendering).

Today, fixes and improvements to the shared infrastructure land in whichever project happens to hit the problem first. Propagating to the others is manual and lossy:

- **Template is always stale** — improvements never flow back.
- **SYNC.md cross-repo porting is labor-intensive and re-interpretive** — when a coding agent ports a fix from one repo to another based on an issue description, the port is a re-derivation, not a transplant. Subtle details get lost.
- **Pain rotates across three buckets** — Python logic, scaffolding files (Dockerfile/CI/packaging), conventions (CLAUDE.md rules). Each bucket goes through bursts of drift; long-tail repos (kroki, IG, template) lag MV by weeks or months.

The current mechanism cannot keep five repos aligned. The more infrastructure improves, the more drift accumulates.

## Goals

- One authoritative home for shared Python infrastructure, imported by every project.
- One authoritative home for shared non-Python artifacts (Dockerfile, CI, packaging, CLAUDE.md, mkdocs), applied by every project with deterministic update semantics.
- Improvements propagate by version bump / `copier update`, not by re-interpretation.
- Each downstream repo retains independent issue tracker, release cadence, and domain autonomy. No monorepo.
- Bootstrapping a brand-new MCP server takes minutes, not hours, and produces a repo that passes CI on day one.
- The AI coding agent (Claude Code) can execute the full bootstrap flow from a single user instruction.

## Non-goals

- Merging the four downstream repos into a monorepo.
- Abstracting over FastMCP itself. Core depends on FastMCP directly; we don't hide it.
- Supporting non-Python MCP servers. Template and library are Python/FastMCP-specific.
- Cross-project feature sharing beyond infrastructure. Domain code (Collection, Scholar search, image generation) stays per-project.

## Approach

Split the shared surface along its natural seam — runnable Python vs. file artifacts — and give each half the right delivery mechanism.

- **`fastmcp-pvl-core`** — new PyPI package. Contains the runnable shared code: auth mode dispatch, middleware wiring, logging config, `ServerConfig`, helpers (`build_event_store`, `compute_app_domain`, `ArtifactStore`, `run_cli`). Each downstream imports from it; fixes propagate via version bump.
- **`fastmcp-server-template`** — existing repo, repurposed as a [copier](https://copier.readthedocs.io/) template. Contains the file artifacts: Dockerfile, `docker-entrypoint.sh`, CI workflows, `release.yml`, `nfpm.yaml`, systemd unit, `compose.yml`, `mkdocs.yml`, `CLAUDE.md` skeleton, starter `src/` scaffolding. Each downstream uses `copier copy` to bootstrap and `copier update` to pull in template changes as reviewable diffs.

The two components are independent — the core library could be adopted without the copier template and vice versa — but they are designed and released together because infrastructure changes typically touch both.

### Alternatives considered

- **Monorepo with uv workspace** — rejected. Collapses independent GitHub presence, complicates per-project dep pinning, and the one-time migration cost is disproportionate to the benefit for four projects. Revisit if project count grows to ~10.
- **Library only, keep SYNC.md for files** — rejected. Pain rotates across all three buckets; fixing only Python drift leaves two-thirds of the problem.
- **Double down on SYNC.md with better tooling** — rejected. The fundamental issue is re-interpretation. Better diff tooling doesn't fix the fact that port authors must re-derive each fix from prose.

## Component 1: `fastmcp-pvl-core` library

### Surface

**Auth** (straight lift from MV):
- `resolve_auth_mode(env_prefix) -> Literal["multi","bearer","remote","oidc-proxy","none"]`
- `build_bearer_auth(env_prefix) -> StaticTokenVerifier`
- `build_remote_auth(env_prefix) -> RemoteAuthProvider` — with `ImportError` guard for optional httpx dep
- `build_oidc_proxy_auth(env_prefix) -> OIDCProxy`
- `build_auth(env_prefix, server_config) -> AuthProvider | None` — top-level dispatcher that returns the right provider or a `MultiAuth(server=..., verifiers=[...], required_scopes=[])` when both bearer and OIDC are configured

**Middleware & logging:**
- `wire_middleware_stack(mcp)` — installs ErrorHandling, Timing, and Logging middlewares (the MV#331 stack)
- `configure_logging_from_env()` — reads `FASTMCP_LOG_LEVEL`, supports `-v` flag override, toggles rich/plain via `FASTMCP_ENABLE_RICH_LOGGING`

**Config plumbing:**
- `env(prefix, name, default=None, cast=str)` helper — single entry point for all env var reads
- `ServerConfig` dataclass — universal fields: `transport`, `host`, `port`, `base_url`, `bearer_token`, OIDC vars (`oidc_config_url`, `oidc_client_id`, `oidc_client_secret`, `oidc_audience`, `oidc_required_scopes`, `oidc_jwt_signing_key`), `event_store_url`, `app_domain`
- `ServerConfig.from_env(env_prefix)` classmethod

**CLI:**
- `run_cli(make_server, prog_name)` — argparse skeleton with `-v`, `--transport`, `--host`, `--port`, `--version`

**Server factory building blocks:**
- `build_instructions(*, read_only, env_prefix, domain_line)` — read-only/read-write aware instructions template
- `build_event_store(env_prefix, server_config)` — file:// default at `<data>/state/events`, memory:// opt-in
- `compute_app_domain(server_config)` — MCP Apps CSP helper (MV#283)
- `ArtifactStore` class + `ArtifactStore.register_route(mcp, base_url)` — one-time artifact download endpoint (MV#261)

### Explicitly not in core

- No god-factory `create_server()`. Each project writes its own ~30-line `make_server()` that composes the building blocks with its own tools/resources/prompts.
- No domain semantics: no `Collection`, no `GitWriteStrategy`, no search indexes, no write tools.
- No hooks or escape hatches for project-specific variation. If a project needs something the core doesn't have, it implements it in the project first; it gets promoted to core only when a second project needs the same thing.

### Size estimate

~800–1200 lines of Python plus tests. Narrow surface, one job.

### Repository

New repo: `pvliesdonk/fastmcp-pvl-core`. PyPI: `fastmcp-pvl-core`. Semver-versioned via PSR from conventional commits, same pattern as MV. While 0.x, minor bumps may break API; `1.0.0` after API has been stable for one release cycle post-migration.

## Component 2: `fastmcp-server-template` copier template

### Variables (`copier.yml`)

| Variable | Default | Purpose |
|---|---|---|
| `project_name` | — | Repo name, CLI command, Docker image basename (e.g. `markdown-vault-mcp`) |
| `pypi_name` | `{{ project_name }}` | Override when the PyPI name differs (e.g. scholar-mcp) |
| `python_module` | `{{ pypi_name \| replace("-", "_") }}` | Python package name |
| `env_prefix` | — | Env var prefix, no trailing underscore (e.g. `MARKDOWN_VAULT_MCP`) |
| `human_name` | — | Human-readable project name for docs (e.g. `Markdown Vault MCP`) |
| `domain_description` | — | One-line blurb for README and MCP instructions |
| `github_org` | `pvliesdonk` | Used for all URLs, Docker registry path, and GitHub references |
| `docker_registry` | `ghcr.io/{{ github_org }}` | Override for alternative registries (docker.io, private) |

**Deliberately no feature flags.** MCP Apps scaffolding is always included (empty = inert). Linux packaging is always included. Domain-specific features (attachments, git backend, etc.) stay in the project's domain layer — template never knows about them.

### File layout: synced vs starter

**Synced files** — managed by copier on every `copier update`; changes in the template propagate on the next update:

```
.github/workflows/ci.yml
.github/workflows/release.yml
.github/workflows/codeql.yml
.github/workflows/claude-code-review.yml
.github/workflows/docs.yml
Dockerfile
docker-entrypoint.sh
compose.yml
packaging/nfpm.yaml
packaging/systemd.service
mkdocs.yml
.gitignore
.gitattributes
.ruff.toml
pyproject.toml               # see hybrid section
CLAUDE.md                    # see hybrid section
src/{{python_module}}/cli.py
src/{{python_module}}/server.py
src/{{python_module}}/config.py
```

**Starter files** — written once on `copier copy`, excluded from subsequent `copier update`:

```
src/{{python_module}}/tools.py
src/{{python_module}}/resources.py
src/{{python_module}}/prompts.py
src/{{python_module}}/domain.py
tests/test_tools.py
docs/design.md
docs/tools/index.md
README.md
CHANGELOG.md
```

Copier distinguishes them via the `_skip_if_exists` directive in `copier.yml`.

### Hybrid files: `pyproject.toml` and `CLAUDE.md`

Both files have template-owned and project-owned sections. Approach: single file, 3-way merge on update (copier's default), sentinel comments to mark intent for humans and agents.

**`pyproject.toml`** — template owns `[tool.ruff]`, `[tool.pytest]`, `[tool.mypy]`, `[tool.hatch.build]`, `[tool.semantic_release]`, python version requirement, shared dev deps. Project owns `[project.dependencies]` domain entries and domain-specific optional dependencies.

**`CLAUDE.md`** — structure:

```markdown
# {{ project_name }}
<!-- DOMAIN -->
{{ domain_description }}

## Design
<!-- DOMAIN -->

## Project Structure
<!-- DOMAIN -->

<!-- ===== TEMPLATE-OWNED SECTIONS BELOW — DO NOT EDIT; CHANGES WILL BE OVERWRITTEN ON COPIER UPDATE ===== -->

## Conventions
## Config & Customization Contract
## Hard PR Acceptance Gates
## GitHub Review Types
## Documentation Discipline
## Logging Standard

<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->

## Key Design Decisions
<!-- DOMAIN -->
```

Shared sections (PR gates, logging standard, doc discipline, GitHub review types, Python tooling conventions, config & customization contract) are sourced from MV's current `CLAUDE.md` as the authoritative version. The cross-repo sync section is deleted — it no longer has a job.

On `copier update`, template-owned sections are rewritten; domain sections are preserved by 3-way merge. Conflicts only surface if a downstream manually edited a template-owned section, which the sentinel banner discourages.

### Update semantics

```
$ copier update
Updating from v0.4.2 → v0.5.0
  modified: .github/workflows/ci.yml  (clean fast-forward)
  modified: Dockerfile                (clean fast-forward)
  conflict: CLAUDE.md                 (3-way merge, review <<<<<<< markers)
  unchanged: 38 files
```

Template versions are git tags in the template repo. Downstream's `.copier-answers.yml` records the applied version. Updates are opt-in per downstream.

## Config & customization contract

Downstream projects compose — never inherit from — core types. This is the single most important pattern in the design.

```python
# src/markdown_vault_mcp/config.py
from dataclasses import dataclass
from pathlib import Path
from fastmcp_pvl_core import ServerConfig, env

ENV_PREFIX = "MARKDOWN_VAULT_MCP"

@dataclass(frozen=True)
class VaultConfig:
    """Domain config. Composes ServerConfig — does not inherit."""
    server: ServerConfig
    vault_path: Path
    embedding_provider: str = "fastembed"
    git_token: str | None = None
    # ...domain-specific fields

    @classmethod
    def from_env(cls) -> "VaultConfig":
        return cls(
            server=ServerConfig.from_env(ENV_PREFIX),
            vault_path=Path(env(ENV_PREFIX, "VAULT_PATH", "/data/vault")),
            # ...
        )
```

```python
# src/markdown_vault_mcp/server.py
def make_server(config: VaultConfig | None = None) -> FastMCP:
    config = config or VaultConfig.from_env()
    collection = Collection.from_config(config)

    mcp = FastMCP(
        name="markdown-vault-mcp",
        instructions=build_instructions(
            read_only=config.git_token is None,
            env_prefix=ENV_PREFIX,
            domain_line="A searchable markdown document collection.",
        ),
        auth=build_auth(ENV_PREFIX, config.server),
        event_store=build_event_store(ENV_PREFIX, config.server),
    )
    wire_middleware_stack(mcp)
    register_tools(mcp, collection)
    register_resources(mcp, collection)
    register_prompts(mcp, collection)
    register_apps(mcp, collection, app_domain=compute_app_domain(config.server))
    if config.server.transport != "stdio":
        ArtifactStore.register_route(mcp, config.server.base_url)
    return mcp
```

**Why composition, not inheritance:**

- No dataclass MRO / default-value ordering issues.
- Core can add fields to `ServerConfig` without breaking downstream subclasses (there aren't any).
- Each project's config reads top-to-bottom as plain data.
- API stability is straightforward: adding fields and helpers is non-breaking; rename is breaking.

**No escape hatches.** If a project needs behavior core doesn't support, it writes it in the project first, proves it in production, and promotes to core only when a second project has the same need. The library never gets ahead of actual demand.

This contract is explained in the template's `CLAUDE.md` ("Config & Customization Contract" section) so coding agents working in downstream repos internalize the rule.

## Claude Code bootstrap UX

Target user experience:

> *"I want a new MCP server that does X and Y; bootstrap in a new repo using `gh` from the template repo."*

Claude Code executes:

1. `copier copy gh:pvliesdonk/fastmcp-server-template ./<project-name>` — answers the questionnaire based on the user's instruction (project name, env prefix, human name, domain description derived from "X and Y")
2. `cd <project-name> && uv sync && uv run pytest` — verify the starter passes
3. `git init && git add . && git commit -m "chore: bootstrap from fastmcp-server-template vX.Y.Z"`
4. `gh repo create pvliesdonk/<project-name> --source=. --remote=origin --push --public`
5. Report URL to user; propose first domain implementation based on "X and Y"

**Template deliverables supporting this UX:**

- Template `README.md` has a top-level **"Bootstrap via Claude Code"** section with the exact prompt pattern, the step-by-step checklist above, and troubleshooting notes (auth, naming conflicts).
- Template's own `CLAUDE.md` (meta — guiding work *on* the template) documents how to extend the template when a bootstrap reveals a gap.
- Optional future enhancement: a `bootstrap-mcp-server` skill in the user's personal Claude Code config that encodes the flow so "new MCP server for X" anywhere in the user's setup triggers the same procedure. Not part of the template itself.

## Migration sequence

Eight incremental steps. Each is independently shippable and reversible. Estimated ~5 weekends end-to-end.

**Step 1 — Extract core library**
- New repo: `pvliesdonk/fastmcp-pvl-core`
- Lift the surface listed above from MV; generalize every function to take `env_prefix: str`
- Port MV's relevant auth + middleware tests verbatim (they prove the extraction is behavior-preserving)
- Publish `v0.1.0` to PyPI

**Step 2 — Adopt core in MV**
- Add `fastmcp-pvl-core = "^0.1"` dependency
- Delete extracted code from MV; replace with imports
- Rewrite `mcp_server.py` as the ~30-line `make_server()` shape
- All MV tests (~1395) must pass unchanged
- Validates the library boundary before any other repo is touched

**Step 3 — Convert template repo to copier**
- Retire `scripts/rename.sh` bootstrap
- Add `copier.yml` with the 7 variables
- Jinja-ize all synced files; add sentinel blocks to hybrid files
- Tag template `v0.1.0`

**Step 4 — Validate template via bootstrap replay**
- From a clean sibling directory: `copier copy gh:pvliesdonk/fastmcp-server-template /tmp/mv-replay` with MV's parameters
- Diff against real MV — the only differences should be MV's domain content (tools, resources, prompts, `docs/design.md`, etc.). Any infra diff is a template bug.
- Fix bugs, retag, retry until diff is domain-only
- Then run `copier update` in real MV to confirm clean (or near-clean) update path

**Step 5 — Migrate image-generation-mcp**
- `copier copy` into a worktree with IG parameters
- IG-specific features (`ResourcesAsTools`, long-operation keepalives, `HTTP_PATH`) stay in IG's domain layer for now; candidates for core promotion later
- Switch IG to import from core; delete duplicated code
- Tests pass

**Step 6 — Migrate scholar-mcp**
- Same flow. Scholar likely wants middleware stack upgrade as part of this (currently behind MV#331).

**Step 7 — Migrate kroki-mcp**
- Last because least mature. Mechanism is battle-tested by this point.

**Step 8 — Retire SYNC.md**
- All four repos now on copier + core. SYNC.md has no job.
- Delete SYNC.md from all repos. Git history preserves it.

**Rollback stance:** if any step reveals a fatal flaw — a shared abstraction leaking domain knowledge, a copier limitation that can't be worked around — stop, keep the extracted pieces that work, revert the rest. No step commits to a subsequent step.

## Versioning

- **`fastmcp-pvl-core`**: semver via PSR from conventional commits. `0.x.y` during stabilization (breaking changes allowed on minor bumps). `1.0.0` when all four downstreams are on it and API has been stable for one release cycle. Downstream pin: `^0.N` during 0.x, `^1` post-1.0.
- **`fastmcp-server-template`**: git tags, independent of core. Template and core can be released independently, but coordinated waves are preferred when a change touches both.
- Coordinated release workflow: template's `release.yml` can optionally trigger a `repository_dispatch` to downstream repos (see "Deferred work" below). Not required for v1 of the template.

## Deferred / future work

Explicitly *not* in scope for the initial migration. Each is a candidate for follow-up work once the baseline is proven.

- **Auto-update automation.** A workflow in each downstream repo that runs `copier update` on a schedule (and optionally on `repository_dispatch` from template releases), opens a PR with the diff, and links to the template's release notes for context. Safeguards: never auto-merge; PR body includes template CHANGELOG diff; workflow no-ops if answers file is already at latest version. Issue to be filed against the template repo during Step 3.
- **IG feature promotion to core.** Evaluate `ResourcesAsTools`, long-operation keepalives, `HTTP_PATH` for streamable-http after Step 5 — promote to core if scholar or kroki would benefit.
- **Template release notes convention.** Each template release gets a dedicated `TEMPLATE_RELEASE_NOTES.md` entry so copier-update PRs can link to meaningful change summaries.
- **Bootstrap skill.** Personal Claude Code skill encoding the bootstrap flow. Lives in user's config, not the template.
- **Cross-language template.** If future MCP servers are written in TypeScript/Go, a separate template repo is more appropriate than generalizing this one.

## Open questions

- **Template's own CI:** should the template repo run `copier copy` + `uv run pytest` in CI as a smoke test, so every template change is proven to produce a working bootstrap? Proposed answer: yes, but scope to Step 3.
- **Core library's test strategy:** mock a full FastMCP instance for middleware tests, or spin up a real `FastMCP` in tests? Proposed answer: real FastMCP — tests should exercise the same wiring downstreams use. MV's existing auth/middleware tests already do this.
- **What happens to existing SYNC.md cross-repo issues** (pending ports in image-gen repo and MV)? Proposed answer: resolve in-flight ports the old way before Step 5; after Step 5, new drift is handled by `copier update`.
