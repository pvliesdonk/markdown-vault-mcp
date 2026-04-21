---
title: fastmcp-server-template — Copier Scaffold Design
date: 2026-04-20
status: draft
relates-to:
  - docs/superpowers/specs/2026-04-20-fastmcp-core-and-copier-template-design.md
  - docs/superpowers/plans/2026-04-20-fastmcp-pvl-core-extraction.md
---

# fastmcp-server-template — Copier Scaffold Design

This spec covers **Step 3** of the 8-step extraction sequence defined in
[`2026-04-20-fastmcp-core-and-copier-template-design.md`](2026-04-20-fastmcp-core-and-copier-template-design.md):
converting `pvliesdonk/fastmcp-server-template` from a GitHub template
repo (with `scripts/rename.sh` bootstrap) to a
[copier](https://copier.readthedocs.io/) template that scaffolds new MCP
servers depending on the `fastmcp-pvl-core` PyPI package.

Steps 1 + 2 (extract core, adopt in MV) completed 2026-04-20:
`fastmcp-pvl-core` v1.0.0 is on PyPI, and `markdown-vault-mcp` v1.25.0
ships with the full adoption. Step 4 (bootstrap-replay validation
against MV) and Steps 5–8 (migrate IG/scholar/kroki + retire SYNC.md)
each get their own spec + plan after this lands.

## Problem

The existing `fastmcp-server-template` repo duplicates ~770 lines of
FastMCP infrastructure (auth, middleware, logging, config, server
factory, artifact store, CLI helpers) across `src/fastmcp_server_template/`.
Users ran `scripts/rename.sh` to substitute identifiers after clicking
"Use this template". The bundle-then-rename model has two structural
problems:

1. **No update path.** Once a downstream ran `rename.sh`, they were
   forked off — template bug fixes never propagated. `SYNC.md` existed
   to manually track which patches had been hand-ported.
2. **Duplication.** Four downstreams (MV, IG, scholar, kroki) all
   carried near-identical copies of the same infrastructure, drifting
   apart file by file.

`fastmcp-pvl-core` v1.0.0 solves the duplication (via `pip install`).
This spec solves the update path by swapping the GitHub-template
workflow for a copier template — downstreams get `copier update` as
a first-class operation, hybrid files with sentinel blocks preserve
domain-owned sections across updates, and the template itself is
version-tagged so downstreams can pin their upgrade cadence.

## Goals

- Replace the inline-infra template with a copier scaffold that
  depends on `fastmcp-pvl-core>=1.0,<2` rather than re-hosting it.
- Scaffolded projects boot as working MCP servers on day one (stdio +
  HTTP transports) with one working tool, resource, and prompt as
  L2-style examples an LLM can imitate.
- Generated CI + release machinery matches what MV ships today: PyPI,
  Docker (multi-arch, GHCR), Linux packaging (nfpm .deb/.rpm +
  systemd), Claude Plugin PR publishing, MCP Registry manifest.
- Hybrid files (`pyproject.toml`, `CLAUDE.md`) use textual sentinel
  blocks so `copier update` can merge template-owned and domain-owned
  content without clobbering either.
- Template repo's own CI self-tests every PR by rendering with dummy
  answers and gating the generated project exactly like MV gates
  itself.
- Step 4+ retrofit path: `copier copy --overwrite --trust` against an
  existing MV/IG/scholar/kroki tree must be able to land a
  `.copier-answers.yml` and reconcile cleanly, without clobbering
  domain code.

## Non-goals

- Migrating MV / IG / scholar / kroki themselves onto the copier
  scaffold — that's Steps 4–7, separate specs + plans.
- Retiring `SYNC.md` across the four repos — Step 8, separate plan.
- Automating `copier update` PRs on a schedule — explicitly deferred
  in the original extraction design.
- Preserving backward compatibility with existing "Use this template"
  users — they forked pre-extraction and own their diverged tree;
  no migration path is provided.
- Promoting IG-specific features (ResourcesAsTools, long-operation
  keepalives) to core — evaluated after Step 5.

## Approach

One git repo, `pvliesdonk/fastmcp-server-template`, reused by name
with its entire current tree rewritten. The repo ships a copier
template at its root: `copier.yml` + jinja-ized files. The old
`scripts/rename.sh` and the inline `src/fastmcp_server_template/`
module are deleted.

**Single invocation path — copier only.** The GitHub
template-repository setting is turned off so the "Use this template"
button disappears; `copier copy gh:pvliesdonk/fastmcp-server-template`
is the sole supported entry point.

The repo uses its own GitHub Actions CI to self-test: every PR
renders the template with fixed dummy answers and runs the generated
project's full gate (`ruff` + `mypy` + `pytest`) across Python
3.11–3.14, plus an idempotence check to catch non-round-tripping
templates.

First release after the rewrite lands is **v1.0.0** — hard break
from whatever pre-copier tag history the repo had. Prior tags are
preserved for git archaeology but not supported.

## Copier variables

Eight variables (`copier.yml`):

| Variable | Default | Purpose |
|---|---|---|
| `project_name` | — | Repo name, CLI command, Docker image basename (e.g. `markdown-vault-mcp`). Lowercase letters/digits/hyphens; must not start with hyphen. |
| `pypi_name` | `{{ project_name }}` | PyPI distribution name override for cases where it differs from `project_name`. |
| `python_module` | `{{ pypi_name \| replace("-", "_") }}` | Python import name. Lowercase letters/digits/underscores; must start with a letter. |
| `env_prefix` | — | Env var prefix, no trailing underscore (e.g. `MARKDOWN_VAULT_MCP`). Uppercase letters/digits/underscores; must start and end with letter/digit. |
| `human_name` | — | Human-readable project name for docs (e.g. `Markdown Vault MCP`). |
| `domain_description` | — | One-line blurb for README and the default MCP instructions. |
| `github_org` | `pvliesdonk` | Used for all URLs, Docker registry path, and GitHub references. |
| `docker_registry` | `ghcr.io/{{ github_org }}` | Override for alternative registries (docker.io, private). |

Validators reject invalid git/Python/env-var identifiers at prompt
time. No feature flags — MCP Apps, Linux packaging, and Claude
Plugin publishing are always generated, matching the consistent
release pipeline decision for Steps 4–7.

`_skip_if_exists` in `copier.yml` lists the starter files so
`copier update` never touches them:

```yaml
_skip_if_exists:
  - "src/{{python_module}}/tools.py"
  - "src/{{python_module}}/resources.py"
  - "src/{{python_module}}/prompts.py"
  - "src/{{python_module}}/domain.py"
  - "tests/test_tools.py"
  - "tests/test_smoke.py"
  - "tests/conftest.py"
  - "docs/design.md"
  - "docs/index.md"
  - "docs/tools/index.md"
  - "docs/configuration.md"
  - "docs/installation.md"
  - "README.md"
  - "CHANGELOG.md"
  - "LICENSE"
  - ".env.example"
```

## File layout

### Synced (re-renders on every `copier update`)

```
.github/workflows/
  ci.yml
  release.yml
  codeql.yml
  claude-code-review.yml
  claude.yml
  docs.yml
.github/dependabot.yml
Dockerfile
docker-entrypoint.sh
compose.yml
packaging/nfpm.yaml
packaging/systemd.service
packaging/test-install.sh
mkdocs.yml
.ruff.toml
.gitignore
.gitattributes
.pre-commit-config.yaml
codecov.yml
server.json                                   # MCP Registry manifest
pyproject.toml                                # hybrid — see below
CLAUDE.md                                     # hybrid — see below
src/{{python_module}}/__init__.py
src/{{python_module}}/cli.py
src/{{python_module}}/server.py
src/{{python_module}}/config.py
src/{{python_module}}/_server_deps.py
src/{{python_module}}/_server_apps.py         # MCP Apps scaffolding (inert until used)
src/{{python_module}}/static/app.html         # SPA shell placeholder
scripts/vendor_spa.py                         # vendors SPA CDN deps inline
```

### Starter (written once on `copier copy`; preserved on update)

```
src/{{python_module}}/tools.py                # L2 example tool
src/{{python_module}}/resources.py            # L2 example resource
src/{{python_module}}/prompts.py              # L2 example prompt
src/{{python_module}}/domain.py               # domain logic placeholder
tests/conftest.py
tests/test_tools.py                           # golden-path smoke test
tests/test_smoke.py                           # make_server() builds without crashing
docs/design.md
docs/index.md
docs/tools/index.md
docs/configuration.md
docs/installation.md
README.md
CHANGELOG.md
LICENSE                                       # MIT default; starter so projects can switch
.env.example
```

### Deliberately not in the template

- `uv.lock` — each project generates its own from its dep set.
- `SYNC.md` — retired by Step 8; no point scaffolding a file we're
  about to delete.

## Hybrid files

Two files have template-owned and domain-owned sections:
`pyproject.toml` and `CLAUDE.md`. The strategy is a single file, copier's
default 3-way merge on update, and textual sentinel comments to mark
ownership. No JSON-ish merge rules; no per-key tracking.

### `pyproject.toml`

Template owns: `[build-system]`, `[tool.ruff]`,
`[tool.pytest.ini_options]`, `[tool.mypy]`,
`[tool.hatch.build.targets.wheel]`, `[tool.semantic_release]` + its
subtables, `[tool.pyright]`, `[project.scripts]`,
`[project.classifiers]`, `requires-python`, and the dev-deps
dependency group. Shared-infra dep `fastmcp-pvl-core>=1.0,<2` is
rendered by the template.

Sentinel block pair marks the domain-owned dep list:

```toml
dependencies = [
    "fastmcp-pvl-core>=1.0,<2",
    # PROJECT-DEPS-START — add domain dependencies below; kept across copier update
    # PROJECT-DEPS-END
]

[project.optional-dependencies]
# PROJECT-EXTRAS-START — add domain-specific optional-dependency groups below; kept across copier update
# PROJECT-EXTRAS-END
```

Copier's 3-way merge preserves lines between the sentinels; the
template can still add or remove entries above and below the block.

### `CLAUDE.md`

Domain-owned sections are marked with `<!-- DOMAIN-START --> / <!-- DOMAIN-END -->`
comment pairs; template-owned sections are bracketed by a prominent
banner:

```markdown
# {{ human_name }}

{{ domain_description }}

## Design
<!-- DOMAIN-START -->
<!-- Describe your service's design here. Kept across copier update. -->
<!-- DOMAIN-END -->

## Project Structure
<!-- DOMAIN-START -->
<!-- Kept across copier update. -->
<!-- DOMAIN-END -->

<!-- ===== TEMPLATE-OWNED SECTIONS BELOW — DO NOT EDIT; CHANGES WILL BE OVERWRITTEN ON COPIER UPDATE ===== -->

## Conventions
## Config & Customization Contract
## Hard PR Acceptance Gates
## GitHub Review Types
## Documentation Discipline
## Logging Standard

<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->

## Key Design Decisions
<!-- DOMAIN-START -->
<!-- Kept across copier update. -->
<!-- DOMAIN-END -->
```

Shared sections (Conventions, PR gates, Logging Standard, Doc
Discipline, GitHub Review Types, Config & Customization Contract) are
sourced verbatim from MV's current `CLAUDE.md`. The cross-repo sync
section is omitted — it's going away in Step 8.

## Example scaffold contents (L2)

The four starter Python files under `src/{{python_module}}/` are
each under 60 lines — minimum viable working examples that show the
registration patterns without committing to a domain shape.
FastMCP doc pointers live **inline in module + function docstrings**
where an LLM copying an existing tool sees them in-context (rather
than in a separate `llm-quickstart.md` an agent might never open).

### `tools.py` (starter)

```python
"""Tool registrations for {{ human_name }}.

See FastMCP tool docs: https://gofastmcp.com/servers/tools
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from {{python_module}}._server_deps import get_service
from {{python_module}}.domain import Service

logger = logging.getLogger(__name__)


def register_tools(mcp: FastMCP) -> None:
    """Register all domain tools on *mcp*.

    FastMCP tool reference: https://gofastmcp.com/servers/tools
    """

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ping(service: Service = Depends(get_service)) -> str:
        """Health-check tool — returns ``"pong"`` if the service is alive.

        Pattern: declare domain args, take the shared service via
        ``Depends``, return a JSON-serialisable value. See
        https://gofastmcp.com/servers/tools#async-tools for async + DI.
        """
        return await service.ping()
```

### `resources.py` (starter)

```python
"""Resource registrations for {{ human_name }}.

See FastMCP resource docs: https://gofastmcp.com/servers/resources
"""

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from {{python_module}}._server_deps import get_service
from {{python_module}}.domain import Service


def register_resources(mcp: FastMCP) -> None:
    @mcp.resource("status://{{ project_name }}")
    async def status(service: Service = Depends(get_service)) -> dict:
        """Service status resource.

        Templated resources take path parameters in the URI; static
        resources don't. See
        https://gofastmcp.com/servers/resources#templates.
        """
        return await service.status()
```

### `prompts.py` (starter)

```python
"""Prompt registrations for {{ human_name }}.

See FastMCP prompt docs: https://gofastmcp.com/servers/prompts
"""

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt()
    async def summarize(context: str) -> str:
        """Summarize ``context`` in one paragraph.

        See https://gofastmcp.com/servers/prompts#prompt-arguments for
        the full signature surface.
        """
        return f"Summarize the following in one paragraph:\n\n{context}"
```

### `domain.py` (starter)

```python
"""Domain logic placeholder for {{ human_name }}.

Real projects replace :class:`Service` with their actual business
logic. Keep FastMCP types out of this module — domain code should be
plain Python, easy to unit-test without a server.
"""


class Service:
    """Placeholder service. Replace with real domain logic."""

    def __init__(self) -> None:
        self._ready = False

    async def start(self) -> None:
        self._ready = True

    async def stop(self) -> None:
        self._ready = False

    async def ping(self) -> str:
        return "pong" if self._ready else "not ready"

    async def status(self) -> dict:
        return {"ready": self._ready}
```

### `_server_deps.py` (synced)

Synced because the lifespan + DI pattern is infrastructure; the body
wires a domain `Service` but doesn't embed domain-specific state:

```python
"""Service lifespan + DI for {{ human_name }}."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypedDict

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from {{python_module}}.domain import Service

logger = logging.getLogger(__name__)


class LifespanState(TypedDict):
    service: Service


@asynccontextmanager
async def server_lifespan(_mcp: object) -> AsyncIterator[LifespanState]:
    service = Service()
    await service.start()
    logger.info("Service started")
    try:
        yield {"service": service}
    finally:
        await service.stop()
        logger.info("Service stopped")


def get_service(ctx: Context = CurrentContext()) -> Service:
    return ctx.lifespan_context["service"]
```

### `server.py`, `config.py`, `cli.py`, `_server_apps.py` (synced)

`_server_apps.py` ships as an **inert placeholder**: a `register_apps(mcp)`
function that conditionally registers the SPA shell resource and
iframe-CSP config only when `{{ env_prefix }}_APP_DOMAIN` or
`{{ env_prefix }}_BASE_URL` are set. A generated project that never
uses MCP Apps sees the module once and never touches it again.

`server.py`, `config.py`, `cli.py` are a combined ~90 lines that
compose `fastmcp-pvl-core`:

- `config.py`: `@dataclass(frozen=True) class ProjectConfig` wrapping
  `ServerConfig` via composition, one `from_env()` classmethod
  calling `ServerConfig.from_env(ENV_PREFIX)`.
- `server.py`: `make_server(config: ProjectConfig | None = None) -> FastMCP`
  that calls `build_auth(config.server)`, `build_instructions(...)`,
  `build_event_store(ENV_PREFIX, config.server)`, wraps the
  ArtifactStore lifecycle (HTTP transports only),
  `wire_middleware_stack(mcp)`, and calls `register_tools`,
  `register_resources`, `register_prompts`, `register_apps`.
- `cli.py`: uses core's `make_serve_parser(_PROG, ...)` for the
  standard flags, adds domain-free `serve` command, delegates
  logging to `configure_logging_from_env`.

## Template self-test CI + release

### `.github/workflows/template-ci.yml`

Runs on every PR and push to main:

1. Install copier + uv on a clean Ubuntu runner.
2. Render: `copier copy --trust --defaults --data-file tests/fixtures/smoke-answers.yml . /tmp/smoke`
   where `smoke-answers.yml` supplies `project_name: smoke-mcp`,
   `env_prefix: SMOKE_MCP`, `human_name: "Smoke MCP"`, etc.
3. Gate the generated project:
   ```
   cd /tmp/smoke
   uv sync --all-extras --dev
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy src/
   uv run pytest -x -q
   ```
4. Matrix across Python 3.11–3.14 — catches syntax-level divergence.
5. Idempotence: run `copier update --trust --defaults` in the same
   tree and assert `git diff` is empty.

No PyPI publishing (the template repo is not a package). No Docker
build of the generated project (too slow for PR gate).

### `.github/workflows/release.yml`

Manual `workflow_dispatch`:

- Input: `bump` (`patch` / `minor` / `major`) — required.
- Action: compute next version, update `CHANGELOG.md` from PR titles
  since the last tag (simple gh-api script), create annotated tag
  `vX.Y.Z`, create GitHub release.
- No PyPI, no Docker, no registry — consumers reference tags via
  `copier update --vcs-ref vX.Y.Z`.

PSR is explicitly not used: template commits don't map cleanly to
feat/fix semantics because they're changes to infrastructure shared
across all downstreams.

### Versioning vs `fastmcp-pvl-core`

Template and core release independently. The template's generated
`pyproject.toml` pins `fastmcp-pvl-core>=1.0,<2`. When core ships
2.0, the template gets a `v2.0.0` release that bumps the pin; until
then the template iterates at `v1.x.y` pace.

## Migration / scope boundary

### Old GitHub-template-repo users

**No migration path.** Forks are pinned to the commit at fork time;
the rewrite doesn't touch them. Asking users to run
`copier copy --overwrite` against an existing pre-extraction tree
would clobber their diverged infra and domain code with no gain. If
they want to adopt `fastmcp-pvl-core`, they follow MV's 7-PR
migration as a reference (documented in the extraction plan doc)
and then optionally diff against a fresh `copier copy` into a
sibling directory.

The v1.0.0 CHANGELOG entry notes this stance plainly.

### MV, IG, scholar, kroki — retrofit in Steps 4–7

Step 4+ turns MV into the first real copier downstream, then IG,
scholar, kroki. Each Step 4/5/6/7 plan will run `copier copy
--overwrite --trust` against the existing tree. Step 3's design has
to support this retrofit without losing domain code. The design
choices that make retrofit safe:

1. **Synced files must already match** what MV has after the
   fastmcp-pvl-core adoption (PRs #396–#402). The synced list above
   is sized to match MV's current tree; Step 4 verifies this
   empirically.
2. **Hybrid-file sentinel blocks** slot around MV's existing domain
   content without overwriting it on first adoption.
3. **`_skip_if_exists` starter list** protects MV's existing
   `tools.py` / `resources.py` / `prompts.py` / `domain.py` +
   `README.md` / `CHANGELOG.md` / `docs/design.md` / etc.
4. **No required file MV doesn't already have** — the first
   `copier copy --overwrite` on MV is strictly additive for synced
   files and no-op for starter files.

Concretely, each sibling's Step 4/5/6/7 PR does:

```
cd /mnt/code/<project>
copier copy --overwrite --trust \
  --data project_name=<project> --data env_prefix=<PREFIX> ... \
  gh:pvliesdonk/fastmcp-server-template .
git diff   # should be <project>-specific vs template drift only
git add .copier-answers.yml
# commit as "chore: adopt fastmcp-server-template v1.0.0"
```

## Success criteria

1. `pvliesdonk/fastmcp-server-template` tree rewritten to this spec,
   tagged `v1.0.0`. GitHub template-repo setting disabled.
2. Template self-test CI passes: `copier copy` with dummy answers →
   generated project's `ruff` + `mypy` + `pytest` pass on Python
   3.11–3.14. Idempotent re-render (`copier update` produces empty
   diff).
3. A fresh `copier copy gh:pvliesdonk/fastmcp-server-template /tmp/foo`
   with realistic answers produces a buildable MCP server. Both stdio
   and HTTP transports start cleanly.
4. Template file list + hybrid sentinels are shaped to support
   retrofit of MV in Step 4 — empirically verified there, but the
   Step 3 design explicitly commits to the retrofit-safe shape.
5. Old `scripts/rename.sh`, `src/fastmcp_server_template/` module,
   and `TEMPLATE.md` deleted.

## Out of scope for this spec

- **Step 4** — bootstrap-replay validation against MV.
- **Steps 5–7** — migrate IG, scholar, kroki onto the copier scaffold.
- **Step 8** — retire `SYNC.md` across all four repos.
- **Scheduled `copier update` PR automation** — deferred.
- **IG feature promotion to core** (`ResourcesAsTools`, long-op
  keepalives) — post-Step 5 evaluation.

Each of the deferred items gets its own spec + plan after the
predecessor ships.
