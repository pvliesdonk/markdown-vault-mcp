# fastmcp-server-template → Copier Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `pvliesdonk/fastmcp-server-template` from a GitHub template repo (with `scripts/rename.sh`) into a copier template that depends on `fastmcp-pvl-core>=1.0,<2` rather than re-hosting the extracted infrastructure. Tag the rewrite as v1.0.0, disable the GitHub template-repo setting, ship a self-test CI.

**Architecture:** Single repo, reused by name, rewritten top-to-bottom. `copier.yml` at the root defines 8 variables; files with jinja placeholders use the `.jinja` suffix so GitHub Actions `${{ ... }}` syntax can coexist with copier `{{ variable }}` expansions (jinja-ized files wrap GHA blocks in `{% raw %}...{% endraw %}` where needed). Copier's default 3-way merge handles hybrid `pyproject.toml` + `CLAUDE.md` updates via textual sentinel blocks. A self-test CI renders the template with dummy answers and runs the generated project's full gate on Python 3.11–3.14.

**Tech Stack:** Copier 9+, uv, FastMCP 3, `fastmcp-pvl-core>=1.0,<2`, GitHub Actions, python-semantic-release (for downstream release.yml only; the template's own releases use a manual `bump` input).

**Spec:** [`docs/superpowers/specs/2026-04-20-fastmcp-copier-scaffold-design.md`](../specs/2026-04-20-fastmcp-copier-scaffold-design.md)

**Working directory:** `/mnt/code/fastmcp-server-template/` (local clone of `pvliesdonk/fastmcp-server-template`)

**Reference tree (synced file sources):** `/mnt/code/markdown-mcp/` — MV's current state after the fastmcp-pvl-core adoption (PRs #396–#402) is the golden reference for workflows, Dockerfile, packaging, etc.

---

## File structure

After rewrite, the repo tree looks like:

```
fastmcp-server-template/
├── copier.yml                                                 # variables, validators, _skip_if_exists
├── README.md                                                  # (template repo's own, not generated)
├── CHANGELOG.md                                               # (template repo's own)
├── LICENSE
├── .github/workflows/
│   ├── template-ci.yml                                        # self-test (render + gate rendered project)
│   └── release.yml                                            # manual tag bump for the template itself
├── tests/fixtures/
│   └── smoke-answers.yml                                      # fixed answers for self-test
├── pyproject.toml.jinja                                       # hybrid — template + PROJECT-DEPS sentinel
├── CLAUDE.md.jinja                                            # hybrid — domain + template sections
├── .github/workflows/ci.yml.jinja                             # synced (generated project)
├── .github/workflows/release.yml.jinja                        # synced (generated project's PSR-driven release)
├── .github/workflows/codeql.yml.jinja
├── .github/workflows/claude-code-review.yml.jinja
├── .github/workflows/claude.yml.jinja
├── .github/workflows/docs.yml.jinja
├── .github/dependabot.yml
├── Dockerfile.jinja
├── docker-entrypoint.sh.jinja
├── compose.yml.jinja
├── packaging/nfpm.yaml.jinja
├── packaging/systemd.service.jinja
├── packaging/test-install.sh.jinja
├── mkdocs.yml.jinja
├── .ruff.toml
├── .gitignore
├── .gitattributes
├── .pre-commit-config.yaml
├── codecov.yml
├── server.json.jinja
├── src/{{python_module}}/__init__.py.jinja                    # synced
├── src/{{python_module}}/cli.py.jinja                         # synced
├── src/{{python_module}}/server.py.jinja                      # synced
├── src/{{python_module}}/config.py.jinja                      # synced
├── src/{{python_module}}/_server_deps.py.jinja                # synced
├── src/{{python_module}}/_server_apps.py.jinja                # synced
├── src/{{python_module}}/static/app.html                      # synced — vendored SPA shell
├── scripts/vendor_spa.py                                      # synced
├── src/{{python_module}}/tools.py.jinja                       # starter (in _skip_if_exists)
├── src/{{python_module}}/resources.py.jinja                   # starter
├── src/{{python_module}}/prompts.py.jinja                     # starter
├── src/{{python_module}}/domain.py.jinja                      # starter
├── tests/conftest.py.jinja                                    # starter
├── tests/test_tools.py.jinja                                  # starter
├── tests/test_smoke.py.jinja                                  # starter
├── docs/design.md.jinja                                       # starter
├── docs/index.md.jinja                                        # starter
├── docs/tools/index.md.jinja                                  # starter
├── docs/configuration.md.jinja                                # starter
├── docs/installation.md.jinja                                 # starter
├── README.md.jinja                                            # starter (generated project's README)
├── CHANGELOG.md.jinja                                         # starter
└── .env.example.jinja                                         # starter
```

**Convention:** any template-input file with jinja substitutions gets the `.jinja` suffix; copier strips it on render. Files without substitutions (e.g. `.gitignore`, `.ruff.toml`, `codecov.yml`) have no suffix and pass through untouched.

**Note on copier path tokens:** `{{python_module}}` in a **path** (e.g. `src/{{python_module}}/`) is rendered by copier's path-jinja. Inside file **contents**, `{{python_module}}`, `{{env_prefix}}`, etc. are rendered by standard jinja.

---

## Phase A — Repo prep + delete old scaffolding

### Task 1: Branch and inspect the current state

**Files:**
- Inspect: `/mnt/code/fastmcp-server-template/` tree

- [ ] **Step 1: Switch to main and pull the latest**

```bash
cd /mnt/code/fastmcp-server-template
git checkout main
git pull --ff-only
```

- [ ] **Step 2: Create the rewrite branch**

```bash
git checkout -b feat/copier-scaffold-v1
```

- [ ] **Step 3: List what will be deleted**

```bash
ls scripts/ src/fastmcp_server_template/ TEMPLATE.md SYNC.md 2>&1
```

Expected: shows `scripts/rename.sh`, the full `src/fastmcp_server_template/` module, `TEMPLATE.md`, and possibly `SYNC.md`. Confirm these are the targets.

- [ ] **Step 4: Delete the old template scaffolding**

```bash
git rm -r scripts/rename.sh src/fastmcp_server_template/ TEMPLATE.md SYNC.md 2>&1 || true
# SYNC.md may not exist; `|| true` is fine
```

- [ ] **Step 5: Commit the deletion**

```bash
git commit -m "refactor: remove pre-copier template scaffolding

Clears the way for the copier-template rewrite landing in subsequent
commits on this branch.  scripts/rename.sh and the inline
src/fastmcp_server_template/ module duplicate what now lives in
fastmcp-pvl-core on PyPI.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase B — Copier framework bootstrap

### Task 2: Write `copier.yml`

**Files:**
- Create: `/mnt/code/fastmcp-server-template/copier.yml`

- [ ] **Step 1: Write copier.yml**

```yaml
# Copier configuration for fastmcp-server-template.
#
# Spec: docs/superpowers/specs/2026-04-20-fastmcp-copier-scaffold-design.md
# (markdown-vault-mcp repo).

_min_copier_version: "9.0"
_answers_file: .copier-answers.yml
_templates_suffix: .jinja

_skip_if_exists:
  - "src/{{python_module}}/tools.py"
  - "src/{{python_module}}/resources.py"
  - "src/{{python_module}}/prompts.py"
  - "src/{{python_module}}/domain.py"
  - "tests/conftest.py"
  - "tests/test_tools.py"
  - "tests/test_smoke.py"
  - "docs/design.md"
  - "docs/index.md"
  - "docs/tools/index.md"
  - "docs/configuration.md"
  - "docs/installation.md"
  - "README.md"
  - "CHANGELOG.md"
  - "LICENSE"
  - ".env.example"

_exclude:
  - "copier.yml"
  - "tests/fixtures"
  - ".github/workflows/template-ci.yml"       # self-test — template-repo only
  - ".github/workflows/template-release.yml"  # template's own release — template-repo only
  - "docs/superpowers"
  - "README.md"                               # this repo's own README (generated projects get README.md.jinja)
  - "CHANGELOG.md"                            # this repo's own CHANGELOG
  - "LICENSE"                                 # this repo's own LICENSE

# --- Variables ---

project_name:
  type: str
  help: "Repo name, CLI command, Docker image basename (e.g. markdown-vault-mcp)"
  validator: >-
    {% if not (project_name | regex_search('^[a-z][a-z0-9-]*$')) %}
    Lowercase letters, digits, and hyphens only; must start with a letter.
    {% endif %}

pypi_name:
  type: str
  default: "{{ project_name }}"
  help: "PyPI distribution name (override when it differs from project_name)"

python_module:
  type: str
  default: "{{ pypi_name | replace('-', '_') }}"
  help: "Python import name (underscore-separated)"
  validator: >-
    {% if not (python_module | regex_search('^[a-z][a-z0-9_]*$')) %}
    Lowercase letters, digits, and underscores only; must start with a letter.
    {% endif %}

env_prefix:
  type: str
  help: "Env var prefix, no trailing underscore (e.g. MARKDOWN_VAULT_MCP)"
  validator: >-
    {% if not (env_prefix | regex_search('^[A-Z][A-Z0-9_]*[A-Z0-9]$')) %}
    Uppercase letters, digits, and underscores only; must start and end with letter/digit.
    {% endif %}

human_name:
  type: str
  help: "Human-readable project name for docs (e.g. 'Markdown Vault MCP')"

domain_description:
  type: str
  help: "One-line blurb for README and the default MCP instructions"

github_org:
  type: str
  default: "pvliesdonk"
  help: "Used for URLs, Docker registry path, and GitHub references"

docker_registry:
  type: str
  default: "ghcr.io/{{ github_org }}"
  help: "Docker registry (override for docker.io or a private registry)"
```

**Why `_exclude` lists the template's own `README.md` / `CHANGELOG.md` / `LICENSE`:** copier copies everything in the template repo by default. We don't want the template repo's *own* README/CHANGELOG/LICENSE to end up as the generated project's README/CHANGELOG/LICENSE — those have starter `.jinja` files. `_exclude` keeps the template's own ones out of the render.

- [ ] **Step 2: Commit**

```bash
git add copier.yml
git commit -m "feat(copier): add copier.yml with 8 variables and _skip_if_exists

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Create smoke-answers fixture

**Files:**
- Create: `/mnt/code/fastmcp-server-template/tests/fixtures/smoke-answers.yml`

- [ ] **Step 1: Write the fixture**

```yaml
# Fixed answers used by template-ci.yml for self-test rendering.
# Values are deliberately boring — not intended to match any real project.
project_name: smoke-mcp
pypi_name: smoke-mcp
python_module: smoke_mcp
env_prefix: SMOKE_MCP
human_name: "Smoke MCP"
domain_description: "Smoke-test scaffold generated by template-ci.yml."
github_org: pvliesdonk
docker_registry: ghcr.io/pvliesdonk
```

- [ ] **Step 2: Commit**

```bash
mkdir -p tests/fixtures
git add tests/fixtures/smoke-answers.yml
git commit -m "test(template): add smoke-answers fixture for self-test CI

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Create the minimum-viable jinja'd Python skeleton

Goal: after this task, `copier copy . /tmp/smoke` + `cd /tmp/smoke && uv sync --all-extras --dev && uv run pytest` works, even though the server is minimal.

**Files:**
- Create: `src/{{python_module}}/__init__.py.jinja`
- Create: `src/{{python_module}}/server.py.jinja`
- Create: `pyproject.toml.jinja`
- Create: `tests/test_smoke.py.jinja`

- [ ] **Step 1: Write `__init__.py.jinja`**

```python
"""{{ human_name }}.

{{ domain_description }}
"""

__version__ = "0.1.0"
```

- [ ] **Step 2: Write the minimal `server.py.jinja`**

```python
"""{{ human_name }} — FastMCP server entry point.

Composes the primitives from ``fastmcp-pvl-core`` into a
project-specific ``make_server()``.  See
https://gofastmcp.com/servers for the FastMCP server surface.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp_pvl_core import (
    build_auth,
    build_instructions,
    configure_logging_from_env,
    resolve_auth_mode,
    wire_middleware_stack,
)

from {{ python_module }}.config import ProjectConfig

_ENV_PREFIX = "{{ env_prefix }}"


def make_server(config: ProjectConfig | None = None) -> FastMCP:
    """Construct the {{ human_name }} FastMCP server."""
    config = config or ProjectConfig.from_env()
    configure_logging_from_env()

    mcp = FastMCP(
        name="{{ project_name }}",
        instructions=build_instructions(
            read_only=True,
            env_prefix=_ENV_PREFIX,
            domain_line="{{ domain_description }}",
        ),
        auth=build_auth(config.server),
    )
    _ = resolve_auth_mode  # re-exported for downstream logging use; flake8 silencer
    wire_middleware_stack(mcp)
    return mcp
```

- [ ] **Step 3: Write a bare `config.py.jinja` stub (fleshed out in Task 7)**

```python
"""Configuration for {{ human_name }}.

Composes :class:`fastmcp_pvl_core.ServerConfig` via the domain
:class:`ProjectConfig` dataclass — never inherits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastmcp_pvl_core import ServerConfig

_ENV_PREFIX = "{{ env_prefix }}"


@dataclass(frozen=True)
class ProjectConfig:
    """Domain config for {{ human_name }}.  Compose — don't inherit."""

    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_env(cls) -> "ProjectConfig":
        return cls(server=ServerConfig.from_env(_ENV_PREFIX))
```

- [ ] **Step 4: Write a minimal `pyproject.toml.jinja`**

```toml
[project]
name = "{{ pypi_name }}"
version = "0.1.0"
description = "{{ domain_description }}"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [{ name = "{{ github_org }}" }]

dependencies = [
    "fastmcp-pvl-core>=1.0,<2",
    # PROJECT-DEPS-START — add domain dependencies below; kept across copier update
    # PROJECT-DEPS-END
]

[project.optional-dependencies]
# PROJECT-EXTRAS-START — add domain-specific optional-dependency groups below; kept across copier update
# PROJECT-EXTRAS-END

dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5",
    "diff-cover>=9",
    "ruff>=0.6",
    "mypy>=1.11",
]

[project.scripts]
{{ project_name }} = "{{ python_module }}.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{{ python_module }}"]

[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "N", "D"]
ignore = ["D203", "D213"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["D"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.mypy]
python_version = "3.11"
strict = true
```

This pyproject.toml is fleshed out further in Task 11.  Keep the
skeleton minimal for now so the smoke render + gate succeeds.

- [ ] **Step 5: Write a minimal `cli.py.jinja` stub**

```python
"""CLI entry point for {{ human_name }}."""

from __future__ import annotations


def main() -> None:
    """CLI entry point."""
    from {{ python_module }}.server import make_server

    make_server()  # just verifies the module imports cleanly for now
```

- [ ] **Step 6: Write a minimal `tests/test_smoke.py.jinja`**

```python
"""Smoke tests for {{ human_name }}."""

from __future__ import annotations


def test_make_server_constructs() -> None:
    """make_server() returns a FastMCP instance without raising."""
    import os

    os.environ.setdefault("{{ env_prefix }}_BEARER_TOKEN", "")  # no auth needed
    from {{ python_module }}.server import make_server

    server = make_server()
    assert server is not None
```

- [ ] **Step 7: Local smoke — render and gate**

```bash
cd /mnt/code/fastmcp-server-template
rm -rf /tmp/smoke
uv run --no-project --with copier copier copy --trust --defaults \
    --data-file tests/fixtures/smoke-answers.yml . /tmp/smoke
ls /tmp/smoke/src/smoke_mcp/
# Expected: __init__.py, server.py, config.py, cli.py
```

- [ ] **Step 8: Build + test the rendered project**

```bash
cd /tmp/smoke
uv sync --all-extras --dev
uv run pytest tests/test_smoke.py -v
```

Expected: `PASSED  test_make_server_constructs`

- [ ] **Step 9: Commit**

```bash
cd /mnt/code/fastmcp-server-template
git add copier.yml pyproject.toml.jinja src/ tests/
git commit -m "feat(copier): minimum-viable jinja skeleton (renders + passes smoke test)

Bootstrap: copier copy with smoke-answers produces a buildable project
that imports fastmcp-pvl-core and constructs a FastMCP instance.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Template self-test CI workflow

**Files:**
- Create: `/mnt/code/fastmcp-server-template/.github/workflows/template-ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: template-ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  render-and-gate:
    strategy:
      fail-fast: false
      matrix:
        python: ["3.11", "3.12", "3.13", "3.14"]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - name: Install uv
        uses: astral-sh/setup-uv@v8
        with:
          version: "0.6"

      - name: Set up Python ${{ '{{' }} matrix.python {{ '}}' }}
        run: uv python install ${{ '{{' }} matrix.python {{ '}}' }}

      - name: Render smoke project with copier
        run: |
          uv run --no-project --with copier \
            copier copy --trust --defaults \
            --data-file tests/fixtures/smoke-answers.yml \
            . /tmp/smoke

      - name: Install rendered project
        working-directory: /tmp/smoke
        run: uv sync --all-extras --dev

      - name: ruff check
        working-directory: /tmp/smoke
        run: uv run ruff check .

      - name: ruff format --check
        working-directory: /tmp/smoke
        run: uv run ruff format --check .

      - name: mypy
        working-directory: /tmp/smoke
        run: uv run mypy src/

      - name: pytest
        working-directory: /tmp/smoke
        run: uv run pytest -x -q

      - name: Idempotence check (copier update yields empty diff)
        working-directory: /tmp/smoke
        run: |
          git init -q
          git config user.email "ci@example.com"
          git config user.name "CI"
          git add -A
          git commit -q -m "initial render"
          uv run --no-project --with copier \
            copier update --trust --defaults --vcs-ref HEAD
          if [ -n "$(git status --porcelain)" ]; then
            echo "::error::copier update produced a non-empty diff — template is not idempotent"
            git --no-pager diff
            exit 1
          fi
```

**Why the `${{ '{{' }} matrix.python {{ '}}' }}` dance:** this workflow is in the **template repo itself**, not a jinja-processed file, but we want to avoid any chance that a future copier-process step (e.g. someone reorganizing) breaks the syntax. Belt and braces.

**Why `--vcs-ref HEAD` for the idempotence update:** copier needs a git ref; we initialise a throwaway git repo in the rendered tree purely so `copier update` has a reference point.

- [ ] **Step 2: Commit (can't run this locally; it'll exercise on first push)**

```bash
git add .github/workflows/template-ci.yml
git commit -m "ci: add template self-test workflow

Renders the template with the smoke-answers fixture on Python 3.11-3.14,
runs the rendered project's full PR gate (ruff + mypy + pytest), then
verifies copier update yields an empty diff for idempotence.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — Flesh out synced Python module

Each file below replaces the minimal stubs from Task 4.

### Task 6: Complete `server.py.jinja`

**Files:**
- Modify: `src/{{python_module}}/server.py.jinja`

- [ ] **Step 1: Rewrite `server.py.jinja` with the full composition**

```python
"""{{ human_name }} — FastMCP server entry point.

Composes the primitives from ``fastmcp-pvl-core`` into a
project-specific ``make_server()``.  See
https://gofastmcp.com/servers for the FastMCP server surface and
``fastmcp-pvl-core``'s README for the composable helpers used below.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastmcp import FastMCP
from fastmcp_pvl_core import (
    ArtifactStore,
    ServerConfig,
    build_auth,
    build_event_store,
    build_instructions,
    configure_logging_from_env,
    resolve_auth_mode,
    wire_middleware_stack,
)

from {{ python_module }}._server_apps import register_apps
from {{ python_module }}._server_deps import server_lifespan
from {{ python_module }}.config import ProjectConfig
from {{ python_module }}.prompts import register_prompts
from {{ python_module }}.resources import register_resources
from {{ python_module }}.tools import register_tools

logger = logging.getLogger(__name__)

_ENV_PREFIX = "{{ env_prefix }}"


def make_server(
    *,
    transport: str = "stdio",
    config: ProjectConfig | None = None,
) -> FastMCP:
    """Construct the {{ human_name }} FastMCP server.

    Args:
        transport: ``"stdio"`` / ``"http"`` / ``"sse"``.  HTTP-only
            features (artifact downloads) are wired only when transport
            != ``"stdio"``.
        config: Optional pre-loaded config; default loads from env.

    Returns:
        A configured :class:`fastmcp.FastMCP` instance.
    """
    config = config or ProjectConfig.from_env()
    configure_logging_from_env()

    auth = build_auth(config.server)
    auth_mode = resolve_auth_mode(config.server) if auth is not None else "none"
    if auth_mode == "none":
        logger.warning(
            "No auth configured — server accepts unauthenticated connections"
        )
    else:
        logger.info("Auth enabled: mode=%s", auth_mode)

    try:
        pkg_ver = _pkg_version("{{ pypi_name }}")
    except PackageNotFoundError:
        pkg_ver = "unknown"

    logger.info(
        "Server config: version=%s name={{ project_name }} auth=%s",
        pkg_ver,
        auth_mode,
    )

    mcp = FastMCP(
        name="{{ project_name }}",
        instructions=build_instructions(
            read_only=True,
            env_prefix=_ENV_PREFIX,
            domain_line="{{ domain_description }}",
        ),
        lifespan=server_lifespan,
        auth=auth,
    )

    wire_middleware_stack(mcp)

    register_tools(mcp)
    register_resources(mcp)
    register_prompts(mcp)
    register_apps(mcp)

    if transport != "stdio":
        artifact_store = ArtifactStore(ttl_seconds=3600)
        ArtifactStore.register_route(mcp, artifact_store)

    return mcp


def _unused_imports_silencer() -> None:
    _ = ServerConfig, build_event_store  # re-exported for downstream use
```

- [ ] **Step 2: Rendering smoke — no gate yet (will fail until other modules exist)**

Skip testing for now; Task 10 verifies end-to-end.

- [ ] **Step 3: Commit**

```bash
git add src/{{python_module}}/server.py.jinja
# Note: literal `{{python_module}}` in the path — shell needs quotes
git add 'src/{{python_module}}/server.py.jinja'
git commit -m "feat(template): full make_server() composing core helpers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Complete `config.py.jinja`

**Files:**
- Modify: `src/{{python_module}}/config.py.jinja`

- [ ] **Step 1: Extend with a domain-owned sentinel block for fields**

```python
"""Configuration for {{ human_name }}.

Composes :class:`fastmcp_pvl_core.ServerConfig` via the domain
:class:`ProjectConfig` dataclass — never inherits.

Add domain-specific fields between the CONFIG-FIELDS sentinels; copier
update preserves that block across template updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastmcp_pvl_core import ServerConfig, env

_ENV_PREFIX = "{{ env_prefix }}"


@dataclass(frozen=True)
class ProjectConfig:
    """Domain config for {{ human_name }}.  Compose — don't inherit."""

    server: ServerConfig = field(default_factory=ServerConfig)

    # CONFIG-FIELDS-START — add domain fields below; kept across copier update
    # (example)
    # vault_path: Path = Path("/data/vault")
    # CONFIG-FIELDS-END

    @classmethod
    def from_env(cls) -> "ProjectConfig":
        """Load :class:`ProjectConfig` from ``{{ env_prefix }}_*`` env vars."""
        return cls(
            server=ServerConfig.from_env(_ENV_PREFIX),
            # CONFIG-FROM-ENV-START — populate domain fields below; kept across copier update
            # (example)
            # vault_path=Path(env(_ENV_PREFIX, "VAULT_PATH", "/data/vault")),
            # CONFIG-FROM-ENV-END
        )


def _unused_imports_silencer() -> None:
    _ = env  # re-exported so downstream from_env additions don't need a new import
```

- [ ] **Step 2: Commit**

```bash
git add 'src/{{python_module}}/config.py.jinja'
git commit -m "feat(template): ProjectConfig with domain sentinel blocks

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Write `cli.py.jinja`

**Files:**
- Modify: `src/{{python_module}}/cli.py.jinja`

- [ ] **Step 1: Rewrite with full CLI dispatch**

```python
"""CLI entry point for {{ human_name }}.

Uses ``fastmcp_pvl_core.make_serve_parser`` for the standard
``-v``/``--transport``/``--host``/``--port``/``--http-path`` flags;
add project-specific subcommands below if needed (see MV's CLI as a
reference).
"""

from __future__ import annotations

import logging
import os
import sys

from fastmcp_pvl_core import (
    configure_logging_from_env,
    make_serve_parser,
    normalise_http_path,
)

logger = logging.getLogger(__name__)

_PROG = "{{ project_name }}"
_ENV_PREFIX = "{{ env_prefix }}"


def main() -> None:
    """CLI entry point."""
    parser = make_serve_parser(
        prog=_PROG,
        description="{{ domain_description }}",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the MCP server")
    # Add more subcommands here (index, search, reindex, ...) as needed.

    args = parser.parse_args()

    configure_logging_from_env(verbose=args.verbose)
    # Root handler for {{ python_module }}.* — FastMCP's configure_logging
    # only covers its own logger tree.
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)

    # Silence httpx/httpcore at DEBUG — kept inline, core doesn't own these deps.
    if args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.command == "serve":
        _cmd_serve(args)
    else:
        parser.error(f"unknown command: {args.command}")


def _cmd_serve(args) -> None:  # type: ignore[no-untyped-def]
    from {{ python_module }}.server import make_server

    transport = args.transport
    server = make_server(transport=transport)
    env_http_path = os.environ.get(f"{_ENV_PREFIX}_HTTP_PATH")
    http_path = normalise_http_path(args.http_path or env_http_path)

    if transport == "http":
        import uvicorn

        app = server.http_app(path=http_path, transport="http")
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            timeout_graceful_shutdown=3,
            lifespan="on",
        )
    else:
        server.run(transport=transport)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit**

```bash
git add 'src/{{python_module}}/cli.py.jinja'
git commit -m "feat(template): cli.py using core's make_serve_parser

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Write `_server_deps.py.jinja`

**Files:**
- Create: `src/{{python_module}}/_server_deps.py.jinja`

- [ ] **Step 1: Write the lifespan + DI wiring**

```python
"""Service lifespan + dependency injection for {{ human_name }}."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypedDict

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from {{ python_module }}.domain import Service

logger = logging.getLogger(__name__)


class LifespanState(TypedDict):
    """Shape of the lifespan context yielded to request handlers."""

    service: Service


@asynccontextmanager
async def server_lifespan(_mcp: object) -> AsyncIterator[LifespanState]:
    """Start the service on startup; stop it on shutdown."""
    service = Service()
    await service.start()
    logger.info("Service started")
    try:
        yield {"service": service}
    finally:
        await service.stop()
        logger.info("Service stopped")


def get_service(ctx: Context = CurrentContext()) -> Service:
    """Resolve the running :class:`Service` from the request context.

    Use as a ``Depends`` default in tool/resource/prompt handlers.
    """
    return ctx.request_context.lifespan_context["service"]
```

- [ ] **Step 2: Commit**

```bash
git add 'src/{{python_module}}/_server_deps.py.jinja'
git commit -m "feat(template): lifespan + DI with domain Service placeholder

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Write `_server_apps.py.jinja` + SPA shell + vendor script

**Files:**
- Create: `src/{{python_module}}/_server_apps.py.jinja`
- Create: `src/{{python_module}}/static/app.html`
- Create: `scripts/vendor_spa.py`

- [ ] **Step 1: Write the inert-by-default `_server_apps.py.jinja`**

```python
"""MCP Apps scaffolding for {{ human_name }}.

Ships as an inert placeholder: ``register_apps`` is a no-op unless the
``{{ env_prefix }}_APP_DOMAIN`` or ``{{ env_prefix }}_BASE_URL`` env
vars are set.  Adopt MCP Apps by copying MV's ``_server_apps.py`` as a
reference once you need a real UI resource.
"""

from __future__ import annotations

import logging
import os

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_ENV_PREFIX = "{{ env_prefix }}"


def register_apps(_mcp: FastMCP) -> None:
    """Register MCP Apps resources on *mcp*.

    This scaffold intentionally registers nothing; check the env var
    and log that the scaffold is inactive.  Real projects replace the
    body with resource + tool registrations following MV's pattern.
    """
    app_domain = (
        os.environ.get(f"{_ENV_PREFIX}_APP_DOMAIN", "").strip()
        or os.environ.get(f"{_ENV_PREFIX}_BASE_URL", "").strip()
    )
    if app_domain:
        logger.info("MCP Apps scaffold present but not wired — app_domain=%s", app_domain)
    else:
        logger.debug("MCP Apps scaffold inactive (no app_domain configured)")
```

- [ ] **Step 2: Create the SPA shell placeholder**

```bash
mkdir -p 'src/{{python_module}}/static'
```

Create `src/{{python_module}}/static/app.html` (no jinja suffix; literal file):

```html
<!DOCTYPE html>
<!-- {{ human_name }} SPA shell placeholder.
     Run scripts/vendor_spa.py to hydrate this file with the vendored
     SDK once you're ready to ship MCP Apps UI. -->
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ human_name }}</title>
</head>
<body>
  <p>SPA placeholder — populate via scripts/vendor_spa.py.</p>
</body>
</html>
```

Note: this file IS processed by jinja because `{{ human_name }}` is inside it. Make sure to include it.

- [ ] **Step 3: Create `scripts/vendor_spa.py` as a no-op starter**

```python
"""Vendor SPA dependencies inline (starter).

Copy MV's scripts/vendor_spa.py once you're ready to ship a real MCP
Apps UI.  Until then, this is a documented no-op.
"""

from __future__ import annotations


def main() -> None:
    """No-op placeholder — replace with real vendor pipeline when needed."""
    print("vendor_spa.py: no-op placeholder — see MV's scripts/vendor_spa.py for the real pipeline.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Commit**

```bash
git add 'src/{{python_module}}/_server_apps.py.jinja' \
        'src/{{python_module}}/static/app.html' \
        scripts/vendor_spa.py
git commit -m "feat(template): inert MCP Apps scaffold + SPA shell placeholder

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Flesh out `pyproject.toml.jinja`

**Files:**
- Modify: `pyproject.toml.jinja`

- [ ] **Step 1: Use MV's pyproject.toml as the reference**

Open `/mnt/code/markdown-mcp/pyproject.toml`.  Copy the content, make these substitutions:

- `markdown-vault-mcp` → `{{ pypi_name }}`
- `Generic markdown vault MCP server with FTS5 + semantic search` → `{{ domain_description }}`
- `markdown_vault_mcp` → `{{ python_module }}`
- `markdown-vault-mcp = "markdown_vault_mcp.cli:main"` → `{{ project_name }} = "{{ python_module }}.cli:main"`
- Replace the `dependencies = [...]` block with the sentinel-bracketed version shown below.
- Replace the `[project.optional-dependencies]` block with the sentinel-bracketed version shown below.
- Remove MV-domain-specific entries: `python-frontmatter`, `requests`, `httpx`, `fastembed`, `numpy`, the override-dependencies block, the `docs` extras, `tool.uv.override-dependencies`.
- Remove MV-specific `[tool.ruff.lint.per-file-ignores]` entries (keep the structure, leave it empty).
- Remove MV-specific `[[tool.mypy.overrides]]` modules (keep the shape, leave the module list empty).
- Keep `[tool.semantic_release]`, `[tool.hatch.build]`, `[tool.pyright]`, `[tool.ruff]`, `[tool.pytest]`, `[tool.mypy]`, `[tool.coverage]` wholesale.

Dependency sentinel form:

```toml
dependencies = [
    "fastmcp-pvl-core>=1.0,<2",
    # PROJECT-DEPS-START — add domain dependencies below; kept across copier update
    # PROJECT-DEPS-END
]

[project.optional-dependencies]
# PROJECT-EXTRAS-START — add domain-specific optional-dependency groups below; kept across copier update
# PROJECT-EXTRAS-END

dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5",
    "diff-cover>=9",
    "ruff>=0.6",
    "mypy>=1.11",
    "pre-commit>=3",
    "pip-audit>=2.7",
]
```

PSR section:

```toml
[tool.semantic_release]
version_toml = ["pyproject.toml:project.version"]
version_variables = ["src/{{ python_module }}/__init__.py:__version__"]
commit_parser = "angular"
tag_format = "v{version}"
build_command = ""
major_on_zero = false

[tool.semantic_release.changelog]
changelog_file = "CHANGELOG.md"

[tool.semantic_release.commit_parser_options]
minor_tags = ["feat"]
patch_tags = ["fix", "perf"]
```

Script entry:

```toml
[project.scripts]
{{ project_name }} = "{{ python_module }}.cli:main"
```

Keep MV's `[project.urls]` form, substituting `{{ github_org }}/{{ project_name }}` for the MV-specific URL.

- [ ] **Step 2: Render and gate the smoke project again**

```bash
cd /mnt/code/fastmcp-server-template
rm -rf /tmp/smoke
uv run --no-project --with copier copier copy --trust --defaults \
    --data-file tests/fixtures/smoke-answers.yml . /tmp/smoke

cd /tmp/smoke
uv sync --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest -x -q
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
cd /mnt/code/fastmcp-server-template
git add pyproject.toml.jinja
git commit -m "feat(template): full pyproject.toml with sentinel-bracketed deps + PSR config

Sourced from MV's post-adoption pyproject.toml; MV-specific domain
deps and per-file-ignores stripped.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Write `CLAUDE.md.jinja`

**Files:**
- Create: `CLAUDE.md.jinja`

- [ ] **Step 1: Source-and-edit from MV's CLAUDE.md**

Open `/mnt/code/markdown-mcp/CLAUDE.md`.  Keep these sections wholesale (they're the shared infrastructure standard):

- `## Conventions`
- `## Hard PR Acceptance Gates`
- `## GitHub Review Types`
- `## Documentation Discipline`
- `## Logging Standard`
- `## Config & Customization Contract` (if present; draft one if not — see MV's config.py header docstring for content)

Remove MV-specific sections: `## Design`, `## Project Structure`, `## Cross-Repo Sync`, `## Key Design Decisions`.

Wrap the whole file as follows:

```markdown
# {{ human_name }}

{{ domain_description }}

## Design
<!-- DOMAIN-START -->
<!-- Describe your service's design here. Kept across copier update. -->
<!-- DOMAIN-END -->

## Project Structure
<!-- DOMAIN-START -->
<!-- Document your project layout here. Kept across copier update. -->
<!-- DOMAIN-END -->

<!-- ===== TEMPLATE-OWNED SECTIONS BELOW — DO NOT EDIT; CHANGES WILL BE OVERWRITTEN ON COPIER UPDATE ===== -->

## Conventions

<Copy MV's Conventions section verbatim — Python 3.11+, uv, ruff line-length 88, hatchling, conventional commits, Google docstrings, logging.getLogger(__name__), type hints, pytest.>

## Hard PR Acceptance Gates

<Copy MV's numbered 1–6 gate list verbatim.  Replace the version-lockstep bullet with the following:

6. **Manifest version lockstep** — `server.json`, `.claude-plugin/plugin/.claude-plugin/plugin.json`, and `.claude-plugin/plugin/.mcp.json` must all carry the same version.  The release workflow bumps them atomically; manual touches require updating all three.>

## GitHub Review Types

<Copy MV's section verbatim.>

## Documentation Discipline

<Copy MV's section verbatim, then replace the path-specific doc list with this generic version:

- `docs/design.md` — authoritative spec; update on feature/behaviour/architecture change.
- `README.md` — user-facing docs; new env vars, tools, resources, prompts, CLI flags go here.
- `docs/` site pages — documented tools/resources/prompts, configuration, installation, guides.
- `CHANGELOG.md` — managed by semantic-release from conventional commits.
- Inline docstrings — new/changed public API methods need accurate Google-style docstrings.

**Rule: code without matching docs is incomplete.**>

## Logging Standard

<Copy MV's section verbatim — stdlib logging, levels table, exception handling rules, message format.>

## Config & Customization Contract

<New section — write this from scratch:>

Domain configuration composes :class:`fastmcp_pvl_core.ServerConfig` inside :class:`ProjectConfig` (see `src/{{ python_module }}/config.py`).  Add domain fields between the `CONFIG-FIELDS-START` / `CONFIG-FIELDS-END` sentinels and populate them in `from_env` between the `CONFIG-FROM-ENV-START` / `CONFIG-FROM-ENV-END` sentinels.  Never inherit from `ServerConfig`; always compose.

Env var prefix is `{{ env_prefix }}_` — all env reads go through `fastmcp_pvl_core.env(_ENV_PREFIX, "SUFFIX", default)` so naming stays consistent.

<!-- ===== TEMPLATE-OWNED SECTIONS END ===== -->

## Key Design Decisions
<!-- DOMAIN-START -->
<!-- Document your service's design decisions here. Kept across copier update. -->
<!-- DOMAIN-END -->
```

- [ ] **Step 2: Render and confirm CLAUDE.md renders to valid markdown**

```bash
cd /mnt/code/fastmcp-server-template
rm -rf /tmp/smoke
uv run --no-project --with copier copier copy --trust --defaults \
    --data-file tests/fixtures/smoke-answers.yml . /tmp/smoke
cat /tmp/smoke/CLAUDE.md | head -40
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md.jinja
git commit -m "feat(template): CLAUDE.md with domain/template sentinel sections

Shared sections sourced from MV's post-adoption CLAUDE.md; domain
sections delimited by DOMAIN-START/END for copier update safety.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — Workflows, Docker, packaging

These tasks copy MV's equivalent files and substitute identifiers.  Each file needs three kinds of substitution:

1. **Repo/module identifiers**: `markdown-vault-mcp` → `{{ project_name }}`, `markdown_vault_mcp` → `{{ python_module }}`, `MARKDOWN_VAULT_MCP` → `{{ env_prefix }}`.
2. **Org/registry**: `pvliesdonk` → `{{ github_org }}`, `ghcr.io/pvliesdonk` → `{{ docker_registry }}`.
3. **GHA `${{ }}` escaping**: wrap ALL occurrences of GitHub Actions `${{ ... }}` expressions in `{% raw %}${{ ... }}{% endraw %}` — jinja processes `{{ }}` and we don't want it touching GHA syntax.

A shell helper for the GHA escaping:

```bash
# One-liner: wrap every `${{ ... }}` in `{% raw %}...{% endraw %}`
sed -i 's|\${{\([^}]*\)}}|{% raw %}${{\1}}{% endraw %}|g' <file>
```

(Run once per file; verify with `grep '{% raw %}' <file>` that the expected expressions got wrapped.  Review manually because edge cases like nested `}}` exist.)

### Task 13: Jinja-ize `.github/workflows/ci.yml`

**Files:**
- Create: `.github/workflows/ci.yml.jinja`

- [ ] **Step 1: Copy MV's ci.yml**

```bash
cd /mnt/code/fastmcp-server-template
mkdir -p .github/workflows
cp /mnt/code/markdown-mcp/.github/workflows/ci.yml .github/workflows/ci.yml.jinja
```

- [ ] **Step 2: Apply identifier substitutions**

```bash
sed -i 's|markdown-vault-mcp|{{ project_name }}|g; s|markdown_vault_mcp|{{ python_module }}|g; s|MARKDOWN_VAULT_MCP|{{ env_prefix }}|g' .github/workflows/ci.yml.jinja
```

- [ ] **Step 3: Wrap GHA `${{ ... }}` in raw blocks**

```bash
python3 - <<'PY'
import re
from pathlib import Path

p = Path(".github/workflows/ci.yml.jinja")
src = p.read_text()
# Match ${{ ... }} where ... doesn't contain another }}.
new = re.sub(r'\$\{\{\s*(.+?)\s*\}\}', r'{% raw %}${{ \1 }}{% endraw %}', src)
p.write_text(new)
print("Replacements applied.")
PY
```

- [ ] **Step 4: Strip MV-specific jobs**

Open `.github/workflows/ci.yml.jinja`.  Remove any steps that reference MV-specific directories (e.g. `docs/design.md` checks, if present) or MV-specific dependency extras beyond what the template ships (`markdown-vault-mcp[all]`).  Leave the standard matrix + ruff + mypy + pytest + diff-cover + codecov-patch steps.

- [ ] **Step 5: Render + gate check**

```bash
cd /mnt/code/fastmcp-server-template
rm -rf /tmp/smoke
uv run --no-project --with copier copier copy --trust --defaults \
    --data-file tests/fixtures/smoke-answers.yml . /tmp/smoke
# Spot-check the rendered workflow has no lingering {{ or {% raw %} markers
grep -E '^(  |    )?(- name|uses|run):' /tmp/smoke/.github/workflows/ci.yml | head -10
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml.jinja
git commit -m "feat(template): ci.yml workflow (jinja-ized from MV's current)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Jinja-ize `.github/workflows/release.yml`

**Files:**
- Create: `.github/workflows/release.yml.jinja`

- [ ] **Step 1: Copy + substitute**

```bash
cp /mnt/code/markdown-mcp/.github/workflows/release.yml .github/workflows/release.yml.jinja
sed -i 's|markdown-vault-mcp|{{ project_name }}|g; s|markdown_vault_mcp|{{ python_module }}|g; s|MARKDOWN_VAULT_MCP|{{ env_prefix }}|g; s|ghcr.io/pvliesdonk|{{ docker_registry }}|g; s|pvliesdonk|{{ github_org }}|g' .github/workflows/release.yml.jinja

python3 - <<'PY'
import re
from pathlib import Path
p = Path(".github/workflows/release.yml.jinja")
src = p.read_text()
new = re.sub(r'\$\{\{\s*(.+?)\s*\}\}', r'{% raw %}${{ \1 }}{% endraw %}', src)
p.write_text(new)
PY
```

- [ ] **Step 2: Review MV-specific bits**

Open the file.  Remove MV-specific references: the `scripts/bump_manifests.py` `build_command` (keep the empty `build_command = ""` line), the MV-specific `assets` list that points at MV's `server.json` + plugin manifests (keep the list, but generalize to `server.json` + `.claude-plugin/plugin/.claude-plugin/plugin.json` + `.claude-plugin/plugin/.mcp.json` — the template ships these paths too).

The publish-linux-packages and publish-claude-plugin-pr jobs come with MV's release.yml; keep them verbatim — the template also ships packaging + claude-plugin manifests.

Keep the `prerelease` input + `prerelease_token: rc` wiring.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml.jinja
git commit -m "feat(template): release.yml (PSR + PyPI + Docker + Linux packages + Claude Plugin PR)

Sourced from MV's post-adoption release.yml; manifest asset paths
generalized to the template's paths.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Jinja-ize the remaining workflows

**Files:**
- Create: `.github/workflows/codeql.yml.jinja`
- Create: `.github/workflows/claude-code-review.yml.jinja`
- Create: `.github/workflows/claude.yml.jinja`
- Create: `.github/workflows/docs.yml.jinja`
- Create: `.github/dependabot.yml`

- [ ] **Step 1: Copy all four workflow files**

```bash
for f in codeql claude-code-review claude docs; do
  cp /mnt/code/markdown-mcp/.github/workflows/$f.yml .github/workflows/$f.yml.jinja
  sed -i 's|markdown-vault-mcp|{{ project_name }}|g; s|markdown_vault_mcp|{{ python_module }}|g; s|MARKDOWN_VAULT_MCP|{{ env_prefix }}|g; s|pvliesdonk|{{ github_org }}|g' .github/workflows/$f.yml.jinja
  python3 -c "
import re, pathlib
p = pathlib.Path('.github/workflows/$f.yml.jinja')
s = p.read_text()
p.write_text(re.sub(r'\\\$\\\{\\\{\\s*(.+?)\\s*\\}\\}', r'{% raw %}\${{ \1 }}{% endraw %}', s))
"
done
```

- [ ] **Step 2: Copy dependabot.yml**

```bash
cp /mnt/code/markdown-mcp/.github/dependabot.yml .github/dependabot.yml
sed -i 's|markdown-vault-mcp|{{ project_name }}|g; s|pvliesdonk|{{ github_org }}|g' .github/dependabot.yml
```

Note: `dependabot.yml` does NOT get the `.jinja` suffix — it has no jinja vars that conflict with yaml after substitution.  Leave without suffix so copier treats as literal (once substitutions are done).

Actually wait — we DO have `{{ project_name }}` in it now.  Rename:

```bash
mv .github/dependabot.yml .github/dependabot.yml.jinja
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/codeql.yml.jinja \
        .github/workflows/claude-code-review.yml.jinja \
        .github/workflows/claude.yml.jinja \
        .github/workflows/docs.yml.jinja \
        .github/dependabot.yml.jinja
git commit -m "feat(template): remaining workflows + dependabot config

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Jinja-ize Dockerfile + docker-entrypoint.sh + compose.yml

**Files:**
- Create: `Dockerfile.jinja`
- Create: `docker-entrypoint.sh.jinja`
- Create: `compose.yml.jinja`

- [ ] **Step 1: Copy all three from MV**

```bash
cp /mnt/code/markdown-mcp/Dockerfile Dockerfile.jinja
cp /mnt/code/markdown-mcp/docker-entrypoint.sh docker-entrypoint.sh.jinja
cp /mnt/code/markdown-mcp/compose.yml compose.yml.jinja
```

- [ ] **Step 2: Substitute identifiers**

```bash
for f in Dockerfile.jinja docker-entrypoint.sh.jinja compose.yml.jinja; do
  sed -i 's|markdown-vault-mcp|{{ project_name }}|g; s|markdown_vault_mcp|{{ python_module }}|g; s|MARKDOWN_VAULT_MCP|{{ env_prefix }}|g; s|ghcr.io/pvliesdonk|{{ docker_registry }}|g; s|pvliesdonk|{{ github_org }}|g' $f
done
```

- [ ] **Step 3: Strip MV-specific references**

Open each file.  Remove MV-specific:
- Dockerfile: any `COPY` of files not in the template tree.  Remove references to MV domain extras like `[all]` — change to the template's default (no extras required at build time).
- compose.yml: remove any MV-specific env vars that aren't in the template's `.env.example` (added in Task 24).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.jinja docker-entrypoint.sh.jinja compose.yml.jinja
git commit -m "feat(template): Docker + compose (jinja-ized from MV)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Jinja-ize Linux packaging files

**Files:**
- Create: `packaging/nfpm.yaml.jinja`
- Create: `packaging/systemd.service.jinja`
- Create: `packaging/test-install.sh.jinja`

- [ ] **Step 1: Copy + substitute**

```bash
mkdir -p packaging
cp /mnt/code/markdown-mcp/packaging/nfpm.yaml packaging/nfpm.yaml.jinja
cp /mnt/code/markdown-mcp/packaging/systemd.service packaging/systemd.service.jinja
cp /mnt/code/markdown-mcp/packaging/test-install.sh packaging/test-install.sh.jinja

for f in packaging/nfpm.yaml.jinja packaging/systemd.service.jinja packaging/test-install.sh.jinja; do
  sed -i 's|markdown-vault-mcp|{{ project_name }}|g; s|markdown_vault_mcp|{{ python_module }}|g; s|MARKDOWN_VAULT_MCP|{{ env_prefix }}|g; s|pvliesdonk|{{ github_org }}|g' $f
done
```

Also copy the pre/post install scripts (`preinstall`, `postinstall`, `preremove`, `postremove`) if present under `packaging/`:

```bash
for f in /mnt/code/markdown-mcp/packaging/{preinstall,postinstall,preremove,postremove}.sh; do
  [ -f "$f" ] && cp "$f" "packaging/$(basename $f).jinja" && sed -i 's|markdown-vault-mcp|{{ project_name }}|g; s|markdown_vault_mcp|{{ python_module }}|g; s|MARKDOWN_VAULT_MCP|{{ env_prefix }}|g; s|pvliesdonk|{{ github_org }}|g' "packaging/$(basename $f).jinja"
done
```

- [ ] **Step 2: Commit**

```bash
git add packaging/
git commit -m "feat(template): Linux packaging (nfpm + systemd + install scripts)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 18: Jinja-ize mkdocs, server.json, misc config

**Files:**
- Create: `mkdocs.yml.jinja`
- Create: `server.json.jinja`
- Create: `codecov.yml`
- Create: `.ruff.toml`
- Create: `.gitignore`
- Create: `.gitattributes`
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1: Copy + substitute the jinja'd files**

```bash
cp /mnt/code/markdown-mcp/mkdocs.yml mkdocs.yml.jinja
cp /mnt/code/markdown-mcp/server.json server.json.jinja

for f in mkdocs.yml.jinja server.json.jinja; do
  sed -i 's|markdown-vault-mcp|{{ project_name }}|g; s|markdown_vault_mcp|{{ python_module }}|g; s|MARKDOWN_VAULT_MCP|{{ env_prefix }}|g; s|ghcr.io/pvliesdonk|{{ docker_registry }}|g; s|pvliesdonk|{{ github_org }}|g; s|Generic markdown vault MCP server with FTS5 + semantic search|{{ domain_description }}|g; s|Markdown Vault MCP|{{ human_name }}|g' $f
done
```

- [ ] **Step 2: Copy the non-jinja'd config files literally**

```bash
cp /mnt/code/markdown-mcp/codecov.yml codecov.yml
cp /mnt/code/markdown-mcp/.ruff.toml .ruff.toml 2>/dev/null || true
cp /mnt/code/markdown-mcp/.gitignore .gitignore
cp /mnt/code/markdown-mcp/.gitattributes .gitattributes
cp /mnt/code/markdown-mcp/.pre-commit-config.yaml .pre-commit-config.yaml
```

- [ ] **Step 3: Strip MV-specific entries**

Open each file.  Remove MV-specific references:
- `mkdocs.yml.jinja`: remove nav entries for MV-specific guides (`docs/guides/zettelkasten.md`, etc.).  Keep structure: Home, Installation, Configuration, Tools, Resources, Prompts.
- `server.json.jinja`: remove MV-specific keywords beyond generic ones; keep the `runtimeHint`, `websiteUrl`, `environmentVariables` structure with TEMPLATE-owned shell.
- `.gitignore`: keep as-is (generic Python ignores).
- `.pre-commit-config.yaml`: keep MV's hooks (ruff, mypy, trailing whitespace, eof-fixer, yaml, large files, json).
- `.gitattributes`: keep MV's `linguist-generated` marks for vendored SPA files.

- [ ] **Step 4: Commit**

```bash
git add mkdocs.yml.jinja server.json.jinja codecov.yml .ruff.toml \
        .gitignore .gitattributes .pre-commit-config.yaml
git commit -m "feat(template): docs site + MCP registry manifest + lint config

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase E — Starter files

### Task 19: Starter Python files — tools, resources, prompts, domain

**Files:**
- Create: `src/{{python_module}}/tools.py.jinja`
- Create: `src/{{python_module}}/resources.py.jinja`
- Create: `src/{{python_module}}/prompts.py.jinja`
- Create: `src/{{python_module}}/domain.py.jinja`

- [ ] **Step 1: Write `tools.py.jinja`**

```python
"""Tool registrations for {{ human_name }}.

See FastMCP tool docs: https://gofastmcp.com/servers/tools
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from {{ python_module }}._server_deps import get_service
from {{ python_module }}.domain import Service

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

- [ ] **Step 2: Write `resources.py.jinja`**

```python
"""Resource registrations for {{ human_name }}.

See FastMCP resource docs: https://gofastmcp.com/servers/resources
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from {{ python_module }}._server_deps import get_service
from {{ python_module }}.domain import Service


def register_resources(mcp: FastMCP) -> None:
    """Register all domain resources on *mcp*."""

    @mcp.resource("status://{{ project_name }}")
    async def status(service: Service = Depends(get_service)) -> dict:
        """Service status resource — JSON-serialisable dict.

        Templated resources take path parameters in the URI; static
        resources don't. See
        https://gofastmcp.com/servers/resources#templates for the full
        pattern.
        """
        return await service.status()
```

- [ ] **Step 3: Write `prompts.py.jinja`**

```python
"""Prompt registrations for {{ human_name }}.

See FastMCP prompt docs: https://gofastmcp.com/servers/prompts
"""

from __future__ import annotations

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Register all domain prompts on *mcp*."""

    @mcp.prompt()
    async def summarize(context: str) -> str:
        """Summarize ``context`` in one paragraph.

        See https://gofastmcp.com/servers/prompts#prompt-arguments for
        the full signature surface.
        """
        return f"Summarize the following in one paragraph:\n\n{context}"
```

- [ ] **Step 4: Write `domain.py.jinja`**

```python
"""Domain logic placeholder for {{ human_name }}.

Real projects replace :class:`Service` with their actual business
logic (database client, API wrapper, file indexer, etc.).  Keep
FastMCP types out of this module — domain code should be plain
Python, easy to unit-test without a server.
"""

from __future__ import annotations


class Service:
    """Placeholder service.  Replace with real domain logic."""

    def __init__(self) -> None:
        self._ready = False

    async def start(self) -> None:
        """Start the service (connect to DB, warm caches, etc.)."""
        self._ready = True

    async def stop(self) -> None:
        """Stop the service (close connections, flush state, etc.)."""
        self._ready = False

    async def ping(self) -> str:
        """Health check."""
        return "pong" if self._ready else "not ready"

    async def status(self) -> dict:
        """Structured status payload."""
        return {"ready": self._ready}
```

- [ ] **Step 5: Commit**

```bash
git add 'src/{{python_module}}/tools.py.jinja' \
        'src/{{python_module}}/resources.py.jinja' \
        'src/{{python_module}}/prompts.py.jinja' \
        'src/{{python_module}}/domain.py.jinja'
git commit -m "feat(template): L2 starter Python files (tools, resources, prompts, domain)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 20: Starter tests

**Files:**
- Create: `tests/conftest.py.jinja`
- Modify: `tests/test_smoke.py.jinja` (already exists from Task 4; keep)
- Create: `tests/test_tools.py.jinja`

- [ ] **Step 1: Write `conftest.py.jinja`**

```python
"""Shared test fixtures for {{ human_name }}."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from fastmcp import Client

from {{ python_module }}.server import make_server


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all ``{{ env_prefix }}_*`` env vars before each test."""
    for key in list(os.environ):
        if key.startswith("{{ env_prefix }}_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    """Return an in-memory FastMCP client connected to a fresh server."""
    server = make_server()
    async with Client(server) as c:
        yield c
```

- [ ] **Step 2: Write `test_tools.py.jinja`**

```python
"""Golden-path smoke test for the example ``ping`` tool."""

from __future__ import annotations

from fastmcp import Client


async def test_ping_returns_pong(client: Client) -> None:
    """The example ``ping`` tool round-trips cleanly through the MCP client."""
    result = await client.call_tool("ping", {})
    assert result.content[0].text == "pong"
```

- [ ] **Step 3: Render + gate end-to-end**

```bash
cd /mnt/code/fastmcp-server-template
rm -rf /tmp/smoke
uv run --no-project --with copier copier copy --trust --defaults \
    --data-file tests/fixtures/smoke-answers.yml . /tmp/smoke

cd /tmp/smoke
uv sync --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest -x -q
```

Expected: 2 tests pass (smoke + ping).

- [ ] **Step 4: Commit**

```bash
cd /mnt/code/fastmcp-server-template
git add tests/conftest.py.jinja tests/test_tools.py.jinja
git commit -m "feat(template): starter conftest + ping integration test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 21: Starter docs

**Files:**
- Create: `docs/design.md.jinja`
- Create: `docs/index.md.jinja`
- Create: `docs/tools/index.md.jinja`
- Create: `docs/configuration.md.jinja`
- Create: `docs/installation.md.jinja`

- [ ] **Step 1: Write `docs/index.md.jinja`**

```markdown
# {{ human_name }}

{{ domain_description }}

## Getting started

- [Installation](installation.md)
- [Configuration](configuration.md)
- [Tools](tools/index.md)
```

- [ ] **Step 2: Write `docs/installation.md.jinja`**

```markdown
# Installation

## From PyPI

```bash
pip install {{ pypi_name }}
```

## From Docker

```bash
docker pull {{ docker_registry }}/{{ project_name }}:latest
```

## From source

```bash
git clone https://github.com/{{ github_org }}/{{ project_name }}
cd {{ project_name }}
uv sync --all-extras --dev
```
```

- [ ] **Step 3: Write `docs/configuration.md.jinja`**

```markdown
# Configuration

{{ human_name }} is configured via environment variables with the
``{{ env_prefix }}_`` prefix.

## Common variables

See `fastmcp-pvl-core`'s README for the full list of universal
variables (`{{ env_prefix }}_TRANSPORT`, `{{ env_prefix }}_HOST`,
`{{ env_prefix }}_PORT`, `{{ env_prefix }}_HTTP_PATH`,
`{{ env_prefix }}_BASE_URL`, auth vars, etc.).

## Domain variables

Document your project-specific variables here.
```

- [ ] **Step 4: Write `docs/tools/index.md.jinja`**

```markdown
# Tools

## ping

Health-check tool — returns `"pong"` if the service is alive.
Replace with real tools per the scaffold in
`src/{{ python_module }}/tools.py`.
```

- [ ] **Step 5: Write `docs/design.md.jinja`**

```markdown
# {{ human_name }} Design

<Document your service's design here.  See MV's design.md as a
structural reference.>

## Shared Infrastructure

Generic FastMCP infrastructure (auth providers, middleware stack,
logging bootstrap, server-factory helpers, artifact store, CLI helpers)
lives in the `fastmcp-pvl-core` PyPI package.  {{ human_name }}
composes this library via ``ServerConfig`` (never inheritance) — see
`src/{{ python_module }}/server.py:make_server` for the assembled call
graph.
```

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "feat(template): starter docs pages (index, installation, configuration, tools, design)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 22: Starter README + CHANGELOG + LICENSE + .env.example

**Files:**
- Create: `README.md.jinja`
- Create: `CHANGELOG.md.jinja`
- Create: `LICENSE.jinja`
- Create: `.env.example.jinja`

- [ ] **Step 1: Write `README.md.jinja`**

```markdown
# {{ human_name }}

{{ domain_description }}

## Quick start

```bash
pip install {{ pypi_name }}
{{ project_name }} serve                                # stdio
{{ project_name }} serve --transport http --port 8000   # HTTP
```

## Configuration

All configuration goes via `{{ env_prefix }}_*` env vars.  See
[docs/configuration.md](docs/configuration.md).

## Links

- [Documentation](https://{{ github_org }}.github.io/{{ project_name }}/)
- [FastMCP](https://gofastmcp.com)
- [fastmcp-pvl-core](https://pypi.org/project/fastmcp-pvl-core/)
```

- [ ] **Step 2: Write `CHANGELOG.md.jinja`**

```markdown
# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
```

- [ ] **Step 3: Write `LICENSE.jinja`**

```
MIT License

Copyright (c) 2026 {{ github_org }}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 4: Write `.env.example.jinja`**

```bash
# {{ human_name }} environment variables (copy to .env and fill in).

# --- Server ---
# {{ env_prefix }}_TRANSPORT=stdio   # stdio | http | sse
# {{ env_prefix }}_HOST=127.0.0.1
# {{ env_prefix }}_PORT=8000
# {{ env_prefix }}_HTTP_PATH=/mcp
# {{ env_prefix }}_BASE_URL=https://mcp.example.com

# --- Auth (pick one flavor) ---
# {{ env_prefix }}_BEARER_TOKEN=<secret>

# {{ env_prefix }}_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
# {{ env_prefix }}_OIDC_CLIENT_ID=<client-id>
# {{ env_prefix }}_OIDC_CLIENT_SECRET=<client-secret>
# {{ env_prefix }}_OIDC_AUDIENCE=
# {{ env_prefix }}_OIDC_REQUIRED_SCOPES=openid
# {{ env_prefix }}_OIDC_JWT_SIGNING_KEY=<hex-string>

# --- Domain (populate from your ProjectConfig) ---
```

- [ ] **Step 5: Commit**

```bash
git add README.md.jinja CHANGELOG.md.jinja LICENSE.jinja .env.example.jinja
git commit -m "feat(template): starter README, CHANGELOG, LICENSE, .env.example

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase F — Template's own release workflow

### Task 23: Write the template repo's release workflow

**Files:**
- Create: `.github/workflows/template-release.yml`

The template repo's own release workflow uses the name `template-release.yml` to mirror `template-ci.yml` and to avoid any visual confusion with the jinja'd `release.yml.jinja` (which renders into the *generated* project's `.github/workflows/release.yml`).  The `_exclude` list set up in Task 2 already covers `template-release.yml`, so copier won't render it into generated projects.

- [ ] **Step 1: Write `.github/workflows/template-release.yml`**

```yaml
name: template-release

on:
  workflow_dispatch:
    inputs:
      bump:
        description: "Version bump"
        type: choice
        required: true
        options: [patch, minor, major]

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0

      - name: Compute next version
        id: version
        run: |
          set -euo pipefail
          LATEST="$(git describe --tags --abbrev=0 --match 'v*' 2>/dev/null || echo 'v0.0.0')"
          VERSION="${LATEST#v}"
          IFS=. read -r MAJOR MINOR PATCH <<< "$VERSION"
          case "{% raw %}${{ inputs.bump }}{% endraw %}" in
            major) MAJOR=$((MAJOR+1)); MINOR=0; PATCH=0 ;;
            minor) MINOR=$((MINOR+1)); PATCH=0 ;;
            patch) PATCH=$((PATCH+1)) ;;
          esac
          NEXT="v${MAJOR}.${MINOR}.${PATCH}"
          echo "next=$NEXT" >> "$GITHUB_OUTPUT"
          echo "latest=$LATEST" >> "$GITHUB_OUTPUT"
          echo "Next version: $NEXT (from $LATEST)"

      - name: Collect merged PR titles since latest tag
        id: notes
        env:
          GH_TOKEN: {% raw %}${{ github.token }}{% endraw %}
        run: |
          set -euo pipefail
          LATEST="{% raw %}${{ steps.version.outputs.latest }}{% endraw %}"
          SINCE="$(git log -1 --format=%cI "$LATEST" 2>/dev/null || echo '1970-01-01')"
          PRS="$(gh pr list --base main --state merged \
            --search "merged:>${SINCE}" \
            --json number,title \
            --jq '.[] | "- #\(.number) \(.title)"')"
          if [ -z "$PRS" ]; then
            PRS="- (no PRs merged since $LATEST)"
          fi
          {
            echo "notes<<EOF"
            echo "$PRS"
            echo "EOF"
          } >> "$GITHUB_OUTPUT"

      - name: Update CHANGELOG.md
        env:
          NEXT: {% raw %}${{ steps.version.outputs.next }}{% endraw %}
          NOTES: {% raw %}${{ steps.notes.outputs.notes }}{% endraw %}
        run: |
          set -euo pipefail
          DATE="$(date -u +%Y-%m-%d)"
          HEADER="## ${NEXT} ({DATE})"
          HEADER="${HEADER/\{DATE\}/${DATE}}"
          {
            echo "# Changelog"
            echo
            echo "$HEADER"
            echo
            echo "$NOTES"
            echo
            tail -n +2 CHANGELOG.md 2>/dev/null || true
          } > CHANGELOG.md.new
          mv CHANGELOG.md.new CHANGELOG.md

      - name: Commit, tag, push
        env:
          NEXT: {% raw %}${{ steps.version.outputs.next }}{% endraw %}
        run: |
          set -euo pipefail
          git config user.email "actions@users.noreply.github.com"
          git config user.name "github-actions"
          git add CHANGELOG.md
          git commit -m "chore(release): $NEXT"
          git tag -a "$NEXT" -m "Release $NEXT"
          git push origin main
          git push origin "$NEXT"

      - name: Create GitHub release
        env:
          GH_TOKEN: {% raw %}${{ github.token }}{% endraw %}
          NEXT: {% raw %}${{ steps.version.outputs.next }}{% endraw %}
          NOTES: {% raw %}${{ steps.notes.outputs.notes }}{% endraw %}
        run: |
          gh release create "$NEXT" \
            --title "$NEXT" \
            --notes "$NOTES"
```

**Why no PSR:** the template's commits are infrastructure edits across all downstreams — they don't map to feat/fix semantics cleanly.  Manual `bump` is honest.

- [ ] **Step 2: Commit**

```bash
git add copier.yml .github/workflows/template-release.yml
git commit -m "ci: add template-release workflow (manual bump, no PSR)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase G — Template repo meta

### Task 24: Write the template repo's own README, CHANGELOG, LICENSE

**Files:**
- Overwrite: `README.md` (template repo's own)
- Overwrite: `CHANGELOG.md` (template repo's own)
- Overwrite: `LICENSE` (template repo's own)

- [ ] **Step 1: Write template's README.md**

```markdown
# fastmcp-server-template

Copier template that scaffolds a production-ready FastMCP server
depending on [`fastmcp-pvl-core`](https://pypi.org/project/fastmcp-pvl-core/)
for shared infrastructure (auth, middleware, logging, server factory,
artifact store, CLI helpers).

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- [copier](https://copier.readthedocs.io/) 9+

## Usage

```bash
uv run --no-project --with copier \
  copier copy gh:pvliesdonk/fastmcp-server-template my-new-service

# Answer the prompts, then:
cd my-new-service
uv sync --all-extras --dev
uv run pytest
uv run my-new-service serve
```

## Update flow

Downstreams updated via `copier update --trust` when a new template
tag lands.  `.copier-answers.yml` in your repo records the template
version; conflicts are surfaced as `<<<<<<< HEAD` markers in the
hybrid files (`pyproject.toml`, `CLAUDE.md`) on the rare occasion a
template-owned section and a domain edit collide.

## Spec

See [the copier scaffold design spec](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/docs/superpowers/specs/2026-04-20-fastmcp-copier-scaffold-design.md)
for the full rationale.
```

- [ ] **Step 2: Write template's CHANGELOG.md**

```markdown
# Changelog

All notable changes to this template are documented here.  Template
consumers see these in their `copier update` PRs.

## v1.0.0 (2026-04-20)

**Complete rewrite.**  The repo transitioned from a GitHub template
repo (with `scripts/rename.sh`) to a copier template that depends on
`fastmcp-pvl-core>=1.0,<2` for shared infrastructure rather than
re-hosting it inline.

### Migration

- Existing forks created via "Use this template" pre-v1.0.0 are NOT
  automatically upgraded.  To adopt the new shape, follow MV's
  7-PR migration (`refactor: adopt fastmcp-pvl-core ...` in
  `pvliesdonk/markdown-vault-mcp`) as a reference, then optionally
  run `copier copy gh:pvliesdonk/fastmcp-server-template ./sibling`
  into a sibling directory and diff against your hand-migrated repo.
- The "Use this template" button on GitHub is disabled — `copier copy`
  is the sole supported entry point.
- `scripts/rename.sh`, `src/fastmcp_server_template/`, `TEMPLATE.md`,
  and `SYNC.md` are removed.
```

- [ ] **Step 3: Write template's LICENSE**

Standard MIT (copy from the starter `LICENSE.jinja` without the jinja vars):

```
MIT License

Copyright (c) 2026 pvliesdonk
... (same body)
```

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md LICENSE
git commit -m "docs: rewrite template repo's own README, CHANGELOG, LICENSE for v1.0.0

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase H — End-to-end verification

### Task 25: Final local smoke — realistic answers

**Files:** none (verification)

- [ ] **Step 1: Render with realistic answers**

```bash
cd /mnt/code/fastmcp-server-template
rm -rf /tmp/realistic
uv run --no-project --with copier copier copy --trust \
  --data project_name=hello-mcp \
  --data pypi_name=hello-mcp \
  --data python_module=hello_mcp \
  --data env_prefix=HELLO_MCP \
  --data human_name="Hello MCP" \
  --data domain_description="Example MCP server generated from the copier template." \
  --data github_org=pvliesdonk \
  --data docker_registry=ghcr.io/pvliesdonk \
  . /tmp/realistic
```

- [ ] **Step 2: Run the full gate on the generated project**

```bash
cd /tmp/realistic
uv sync --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest -x -q
```

Expected: all green.  If mypy fails, address the specific error (usually a missing type hint in a starter — fix in the template and re-render).

- [ ] **Step 3: Verify CLI works**

```bash
cd /tmp/realistic
uv run hello-mcp serve --help
```

Expected: argparse help output showing `-v` / `--transport` / `--host` / `--port` / `--http-path` + the `serve` subcommand.

- [ ] **Step 4: Verify stdio serve doesn't crash immediately**

```bash
cd /tmp/realistic
HELLO_MCP_BEARER_TOKEN="" timeout 2s uv run hello-mcp serve < /dev/null || [ $? -eq 124 ]
```

(`timeout 124` is expected — the server is waiting for stdin; it got killed.)

- [ ] **Step 5: Verify idempotence locally**

```bash
cd /tmp/realistic
git init -q
git add -A
git -c user.email=t@t -c user.name=t commit -q -m "initial"
uv run --no-project --with copier copier update --trust --defaults
git diff --exit-code
```

Expected: no diff.

- [ ] **Step 6: No commit needed** — this is verification only.

---

### Task 26: Push + open PR

**Files:** none (git ops)

- [ ] **Step 1: Push the branch**

```bash
cd /mnt/code/fastmcp-server-template
git push -u origin feat/copier-scaffold-v1
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create \
  --title "feat!: rewrite as copier template depending on fastmcp-pvl-core (v1.0.0)" \
  --body "$(cat <<'EOF'
## Summary

Complete rewrite of `fastmcp-server-template` per [the Step 3 scaffold
design spec](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/docs/superpowers/specs/2026-04-20-fastmcp-copier-scaffold-design.md).

- Retires `scripts/rename.sh` + the inline `src/fastmcp_server_template/`
  module (~770 lines).
- Adds `copier.yml` with 8 variables + validators + `_skip_if_exists`
  for starter files.
- Jinja-izes `pyproject.toml`, `CLAUDE.md` (hybrid, with sentinel
  blocks), `server.py`, `config.py`, `cli.py`, `_server_deps.py`,
  `_server_apps.py`, all CI workflows, Dockerfile, docker-entrypoint,
  compose.yml, `packaging/` files, `mkdocs.yml`, `server.json`, and
  `.github/dependabot.yml`.
- Adds starter files (tools.py, resources.py, prompts.py, domain.py,
  conftest, tests, docs, README, CHANGELOG, LICENSE, .env.example) in
  `_skip_if_exists` so `copier update` never touches them.
- Adds `.github/workflows/template-ci.yml` self-test: renders the
  template with `tests/fixtures/smoke-answers.yml` on Python 3.11–3.14,
  runs the rendered project's ruff + mypy + pytest, plus idempotent
  re-render check.
- Adds `.github/workflows/template-release.yml` manual-bump workflow
  for the template repo's own version tags.

## Breaking changes

- The "Use this template" button will be disabled after this lands
  (manual repo-settings change, not in CI).
- Existing forks do NOT auto-upgrade.  Documented migration path in
  `CHANGELOG.md`.

## Test plan

- [x] Local render with smoke-answers and realistic answers.
- [x] Rendered project passes ruff + mypy + pytest.
- [x] Idempotent `copier update` yields empty diff.
- [ ] CI self-test passes on Python 3.11–3.14.
EOF
)"
```

- [ ] **Step 3: Wait for template-ci to pass**

```bash
gh pr checks --watch
```

If any step fails, fix in-branch, push, repeat.

---

### Task 27: Merge and ship v1.0.0

**Files:** none (git ops + GitHub settings)

- [ ] **Step 1: Merge the PR**

```bash
gh pr merge --merge   # merge commit, matches the project's ruleset convention
```

- [ ] **Step 2: Disable GitHub template-repo setting**

```bash
gh repo edit pvliesdonk/fastmcp-server-template --template=false
```

- [ ] **Step 3: Trigger v1.0.0 release**

```bash
gh workflow run template-release.yml --ref main -f bump=major
```

This takes the current `v0.x.y` tag up to `v1.0.0`.

(If the latest tag is already something higher than `v0.x.y`, use `bump=patch` and re-tag the exact v1.0.0 manually:

```bash
git checkout main
git pull --ff-only
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
gh release create v1.0.0 --title v1.0.0 --generate-notes
```

— but this is an edge case; the workflow should work.)

- [ ] **Step 4: Verify the release**

```bash
gh release view v1.0.0
gh repo view pvliesdonk/fastmcp-server-template --json isTemplate
```

Expected: release exists; `isTemplate` is `false`.

- [ ] **Step 5: Update MV's handoff memory**

```bash
# On the markdown-mcp clone:
cd /mnt/code/markdown-mcp
```

Edit `/home/peter/.claude/projects/-mnt-code-markdown-mcp/memory/fastmcp_pvl_core_extraction_handoff.md`:

Replace the "Step 3 — Build a copier scaffolding repo" bullet in the "Remaining work" section with:

```markdown
- **Step 3 — DONE 2026-04-21.**  `pvliesdonk/fastmcp-server-template`
  is a copier template depending on `fastmcp-pvl-core>=1.0,<2`.  Tag
  `v1.0.0` on PyPI-adjacent channels; GitHub template-repo setting
  disabled.  See
  [`docs/superpowers/specs/2026-04-20-fastmcp-copier-scaffold-design.md`](...)
  and the paired plan.
```

Update `~/.claude/projects/-mnt-code-markdown-mcp/memory/MEMORY.md` to flip Step 3 from pending to done in the status table.

- [ ] **Step 6: Commit the memory update to git-ignored memory**

Memory files aren't committed to MV's repo — they live in the auto-memory directory.  Just save the file; no git commit needed for MV.

---

## Completion checklist

- [ ] `pvliesdonk/fastmcp-server-template` tree rewritten to the spec shape.
- [ ] `copier.yml` with 8 variables + validators + `_skip_if_exists`.
- [ ] Self-test CI green on Python 3.11–3.14 including idempotence check.
- [ ] Realistic `copier copy` render passes full gate locally.
- [ ] GitHub template-repo setting disabled.
- [ ] v1.0.0 tag + release published.
- [ ] MV's handoff memory updated to reflect Step 3 done.

## Out of scope (future plans)

- **Step 4 — Bootstrap-replay validation against MV.**  Separate plan after this lands.
- **Steps 5–7 — Migrate IG, scholar, kroki** onto the copier scaffold.
- **Step 8 — Retire `SYNC.md`** across all four repos.
- **Auto-update automation** (scheduled `copier update` PR workflow) — deferred per design spec.
