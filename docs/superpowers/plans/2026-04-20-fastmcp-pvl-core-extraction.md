# fastmcp-pvl-core Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract shared FastMCP infrastructure from `markdown-vault-mcp` into a new standalone PyPI package `fastmcp-pvl-core`, then adopt it back in MV as the first consumer, publishing both releases.

**Architecture:** New repo `pvliesdonk/fastmcp-pvl-core` ships a narrow library of generic FastMCP primitives (auth, middleware, logging, config, server-factory helpers, artifact store, CLI helpers). MV imports from it using composition (never inheritance) via a ~30-line `make_server()` that wires building blocks together. No god-factory, no project-specific hooks in core.

**Tech Stack:** Python 3.10+, FastMCP, uv, hatchling, ruff, mypy, pytest, python-semantic-release, PSR → PyPI. Identical toolchain to MV — this is a behavior-preserving extraction, not a rewrite.

**Scope:** Steps 1 + 2 of the design spec only. Template conversion (Step 3), replay validation (Step 4), and migration of IG/scholar/kroki (Steps 5–7) become separate plans once the core API is proven stable.

**Design reference:** `docs/superpowers/specs/2026-04-20-fastmcp-core-and-copier-template-design.md`

**Where the work happens:**
- New repo: clone to `/mnt/code/fastmcp-pvl-core/` after creation
- MV: continue in `/mnt/code/markdown-mcp/` (current working copy)
- Recommended: run this plan in a worktree off MV's `main` to isolate the MV adoption branch

---

## Phase A — Bootstrap the core library repo

> **Gotcha — workflow-on-main requirement:** GitHub only runs a workflow on a PR if that workflow file already exists on the repo's default branch. This means every workflow (`ci.yml`, `claude-code-review.yml`, `claude.yml`, `codeql.yml`, `release.yml`, `docs.yml`) must land on `main` *before* any feature branch or PR is opened. Phase A lands everything on `main` directly — no PR gate until Phase B.
>
> **Gotcha — secrets must exist before workflows run:** the workflows reference `CLAUDE_CODE_OAUTH_TOKEN`, `CODECOV_TOKEN`, `RELEASE_TOKEN`. Set these before the first push so the initial `main` CI run has them available.

### Task 1: Create the GitHub repo and configure secrets

**Files:** none (repo + settings only)

- [ ] **Step 1: Create the GitHub repo via `gh`**

Run (from anywhere):

```bash
gh repo create pvliesdonk/fastmcp-pvl-core \
  --public \
  --description "Shared FastMCP infrastructure: auth, middleware, logging, server-factory helpers" \
  --add-readme=false \
  --license=mit
```

Expected: prints the repo URL.

- [ ] **Step 2: Clone the new repo to `/mnt/code/fastmcp-pvl-core`**

Run:

```bash
cd /mnt/code
gh repo clone pvliesdonk/fastmcp-pvl-core
cd fastmcp-pvl-core
```

Expected: empty repo with no files other than `.git/`.

- [ ] **Step 3: Configure required secrets**

Prerequisite: get the token values from an existing MV-family repo's settings (or generate fresh ones where appropriate — see each note below).

Run the following (substituting real values for each `<…>`):

```bash
cd /mnt/code/fastmcp-pvl-core

# Claude Code review bot. Value: same OAuth token MV uses — reuse it.
gh secret set CLAUDE_CODE_OAUTH_TOKEN --body "<value from MV repo settings>"

# Codecov (used by coverage upload in ci.yml). Value: get from codecov.io after
# adding the repo there. If you skip Codecov entirely, also remove the relevant
# step from ci.yml in Task 2 — the diff-cover-only gate still works.
gh secret set CODECOV_TOKEN --body "<codecov.io upload token for this repo>"

# PSR needs write access to contents + tags. GITHUB_TOKEN alone cannot push
# signed tags or trigger downstream workflows. Use a fine-grained PAT scoped
# to this repo with contents:write + actions:write + pull-requests:write.
gh secret set RELEASE_TOKEN --body "<fine-grained PAT>"
```

- [ ] **Step 4: Verify secrets are set**

```bash
gh secret list
```

Expected: prints three rows (`CLAUDE_CODE_OAUTH_TOKEN`, `CODECOV_TOKEN`, `RELEASE_TOKEN`). Values are redacted — only names shown. `GITHUB_TOKEN` is provided automatically by Actions and does not appear here.

- [ ] **Step 5: Configure branch protection on `main` (one-time)**

```bash
# Require PR review + passing status checks before merge. Status checks
# will only appear after the first workflow run — add them in a follow-up
# after Phase A main lands.
gh api repos/pvliesdonk/fastmcp-pvl-core/branches/main/protection \
  --method PUT \
  --field required_pull_request_reviews[required_approving_review_count]=0 \
  --field enforce_admins=false \
  --field required_status_checks=null \
  --field restrictions=null
```

Add status-check requirements after Task 3 (once CI has run once on main and the check name is registered).

**Note:** if you prefer configuring branch protection via the web UI, that works equivalently. The goal is: admin-bypass allowed, PR review preferred, status checks enforced once they exist.

---

### Task 2: Initial project structure (everything lands on main)

Phase A commits **directly to `main`** — no PR. This is the only way to satisfy GitHub's "workflow must exist on default branch" rule for subsequent PRs. All infrastructure files go in one initial commit so the first CI run on `main` is definitive.

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/pyproject.toml`
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/__init__.py`
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/py.typed`
- Create: `/mnt/code/fastmcp-pvl-core/tests/__init__.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/conftest.py`
- Create: `/mnt/code/fastmcp-pvl-core/.github/workflows/ci.yml`
- Create: `/mnt/code/fastmcp-pvl-core/.github/workflows/claude-code-review.yml`
- Create: `/mnt/code/fastmcp-pvl-core/.github/workflows/claude.yml`
- Create: `/mnt/code/fastmcp-pvl-core/.github/workflows/codeql.yml`
- Create: `/mnt/code/fastmcp-pvl-core/.github/workflows/release.yml` (stub — real PSR wiring in a later task)
- Create: `/mnt/code/fastmcp-pvl-core/.gitignore`
- Create: `/mnt/code/fastmcp-pvl-core/README.md`
- Create: `/mnt/code/fastmcp-pvl-core/LICENSE` (MIT — generated by `gh repo create --license=mit`; verify present)
- Create: `/mnt/code/fastmcp-pvl-core/CHANGELOG.md` (empty stub)

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "fastmcp-pvl-core"
version = "0.0.0"  # PSR-managed post-release
description = "Shared FastMCP infrastructure: auth, middleware, logging, server-factory helpers"
readme = "README.md"
license = {text = "MIT"}
authors = [{name = "Peter van Liesdonk", email = "peter@liesdonk.nl"}]
requires-python = ">=3.10"
dependencies = [
  "fastmcp>=2.3",
]

[project.optional-dependencies]
remote-auth = ["httpx>=0.27"]

[project.urls]
Homepage = "https://github.com/pvliesdonk/fastmcp-pvl-core"
Issues = "https://github.com/pvliesdonk/fastmcp-pvl-core/issues"

[dependency-groups]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.24",
  "pytest-cov>=5",
  "ruff>=0.6",
  "mypy>=1.11",
  "diff-cover>=9",
  "httpx>=0.27",
]

[tool.hatch.build.targets.wheel]
packages = ["src/fastmcp_pvl_core"]

[tool.ruff]
line-length = 88
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "N", "D"]
ignore = ["D203", "D213"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["D"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.mypy]
python_version = "3.10"
strict = true
warn_return_any = true
warn_unused_ignores = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-ra"
testpaths = ["tests"]
```

- [ ] **Step 2: Write the empty package module**

Create `src/fastmcp_pvl_core/__init__.py`:

```python
"""Shared FastMCP infrastructure.

Imported by MCP server projects that want auth mode dispatch,
middleware wiring, logging setup, config helpers, and server
factory building blocks without duplicating them per repo.
"""

__version__ = "0.0.0"  # PSR overrides at build time
```

Create `src/fastmcp_pvl_core/py.typed` (empty file — PEP 561 marker):

```
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
"""Shared pytest fixtures for fastmcp-pvl-core tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip all env vars whose name starts with a common test prefix."""
    prefixes = ("TEST_", "PVLCORE_TEST_")
    for key in list(os.environ):
        if key.startswith(prefixes):
            monkeypatch.delenv(key, raising=False)
    yield
```

Create `tests/__init__.py` (empty file).

- [ ] **Step 4: Write `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.mypy_cache/
.ruff_cache/
.pytest_cache/
dist/
build/
.coverage
coverage.xml
```

- [ ] **Step 5: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - name: Set up Python
        run: uv python install ${{ matrix.python-version }}
      - name: Install deps
        run: uv sync --all-extras
      - name: Lint
        run: |
          uv run ruff check .
          uv run ruff format --check .
      - name: Type check
        run: uv run mypy src/
      - name: Test
        run: uv run pytest -x -q --cov=fastmcp_pvl_core --cov-report=xml
```

- [ ] **Step 5b: Copy `claude-code-review.yml` from MV and adapt**

Copy MV's version and change the path references (if any) to match this repo:

```bash
cp /mnt/code/markdown-mcp/.github/workflows/claude-code-review.yml \
   /mnt/code/fastmcp-pvl-core/.github/workflows/claude-code-review.yml
```

Review the file: ensure it references no MV-specific paths or check names. The workflow trigger (`on: pull_request`) and the `CLAUDE_CODE_OAUTH_TOKEN` secret usage carry over unchanged.

- [ ] **Step 5c: Copy `claude.yml` (manual @claude mentions) from MV**

```bash
cp /mnt/code/markdown-mcp/.github/workflows/claude.yml \
   /mnt/code/fastmcp-pvl-core/.github/workflows/claude.yml
```

No adaptation needed — same secret, same triggers.

- [ ] **Step 5d: Copy `codeql.yml` from MV**

```bash
cp /mnt/code/markdown-mcp/.github/workflows/codeql.yml \
   /mnt/code/fastmcp-pvl-core/.github/workflows/codeql.yml
```

Verify language matrix is `python` only (no TypeScript components in core).

- [ ] **Step 5e: Write a minimal `release.yml` stub**

A full PSR release pipeline is not required for the initial v0.1.0 manual publish. Ship a stub so future PRs that touch release infrastructure have a file to modify, and so the workflow exists on `main` when needed:

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: write
  id-token: write

jobs:
  release:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Placeholder
        run: echo "Release pipeline to be wired in a follow-up task (Phase H)"
```

The real PSR + PyPI + GitHub-release wiring happens in Task 13. Until then, merges to `main` do not trigger a release; only manual `workflow_dispatch` runs this stub.

- [ ] **Step 5f: Write `CHANGELOG.md` stub**

```markdown
# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
```

- [ ] **Step 6: Write `README.md` (overwrite placeholder)**

```markdown
# fastmcp-pvl-core

Shared FastMCP infrastructure for the `pvliesdonk/*-mcp` server family:
auth, middleware, logging, config helpers, server-factory building blocks.

## Status

Early 0.x. API may change on minor bumps until 1.0.

## Install

```bash
uv add fastmcp-pvl-core
# If you use RemoteAuthProvider mode:
uv add "fastmcp-pvl-core[remote-auth]"
```

## Usage

See `src/fastmcp_pvl_core/` for the full surface. Typical usage:

```python
from fastmcp import FastMCP
from fastmcp_pvl_core import (
    ServerConfig, build_auth, build_instructions,
    wire_middleware_stack, env,
)

config = ServerConfig.from_env("MY_APP")
mcp = FastMCP(
    name="my-app",
    instructions=build_instructions(read_only=False, env_prefix="MY_APP", domain_line="…"),
    auth=build_auth("MY_APP", config),
)
wire_middleware_stack(mcp)
```

## License

MIT
```

- [ ] **Step 7: Commit everything to `main` in one initial commit**

Phase A commits directly to `main` — no PR. This is intentional: PRs cannot trigger workflows that do not yet exist on `main`. After this commit, every workflow file is registered on the default branch, and subsequent tasks (starting Phase B) can use normal feature-branch + PR flow.

```bash
cd /mnt/code/fastmcp-pvl-core
git add -A
git commit -m "chore: initial project structure with workflows"
git push -u origin main
```

- [ ] **Step 8: Wait for initial CI to run and confirm green**

Run:

```bash
gh run list --limit 5
gh run watch
```

Expected: `ci.yml` runs on the `main` push, passes all jobs (lint, format, type-check, test across Python 3.10–3.13). Codeql and claude workflows may also run; they should succeed with no findings.

- [ ] **Step 9: Add status-check requirements to branch protection**

Now that checks have run once and their names are registered:

```bash
gh api repos/pvliesdonk/fastmcp-pvl-core/branches/main/protection \
  --method PUT \
  --field 'required_status_checks[strict]=true' \
  --field 'required_status_checks[contexts][]=test (3.10)' \
  --field 'required_status_checks[contexts][]=test (3.11)' \
  --field 'required_status_checks[contexts][]=test (3.12)' \
  --field 'required_status_checks[contexts][]=test (3.13)' \
  --field enforce_admins=false \
  --field required_pull_request_reviews=null \
  --field restrictions=null
```

Expected: subsequent PRs require all four `test (…)` checks to pass before merge.

---

### Task 3: Smoke test — empty package installs and tests run

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_smoke.py`

- [ ] **Step 1: Write the smoke test**

```python
"""Smoke tests — verify the package installs and imports."""

import fastmcp_pvl_core


def test_package_imports():
    assert fastmcp_pvl_core is not None


def test_version_attribute():
    assert hasattr(fastmcp_pvl_core, "__version__")
    assert isinstance(fastmcp_pvl_core.__version__, str)
```

- [ ] **Step 2: Install deps and run tests**

Run:

```bash
cd /mnt/code/fastmcp-pvl-core
uv sync --all-extras
uv run pytest -v
```

Expected: 2 tests pass.

- [ ] **Step 3: Run lint and type-check**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_smoke.py
git commit -m "test: add smoke tests"
git push
```

---

## Phase B — Config foundations

> **Workflow from here on:** every task from Task 4 onward uses a feature branch + PR.
> Branch naming convention: `feat/<task-slug>` (e.g. `feat/env-helpers`, `feat/server-config`).
> PRs must pass CI + claude-review before merge. Use `gh pr merge --merge` (ruleset disallows squash).

### Task 4: Port `env()` helper and parse helpers

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_env.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_env.py`

**Source reference:** MV `src/markdown_vault_mcp/config.py` lines 21–64 (`_env`, `_parse_bool`, `_parse_list`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_env.py`:

```python
"""Tests for env var reading helpers."""

from __future__ import annotations

import pytest

from fastmcp_pvl_core import env, parse_bool, parse_list, parse_scopes


class TestEnv:
    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MYAPP_FOO", raising=False)
        assert env("MYAPP", "FOO", default="bar") == "bar"

    def test_returns_value_when_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_FOO", "hello")
        assert env("MYAPP", "FOO") == "hello"

    def test_empty_string_treated_as_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_FOO", "")
        assert env("MYAPP", "FOO", default="fallback") == "fallback"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_FOO", "  value  ")
        assert env("MYAPP", "FOO") == "value"

    def test_prefix_can_have_trailing_underscore(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_FOO", "x")
        assert env("MYAPP_", "FOO") == "x"
        assert env("MYAPP", "FOO") == "x"


class TestParseBool:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
    def test_truthy(self, value: str):
        assert parse_bool(value) is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", ""])
    def test_falsy(self, value: str):
        assert parse_bool(value) is False


class TestParseList:
    def test_empty(self):
        assert parse_list("") == []

    def test_comma_separated(self):
        assert parse_list("a,b,c") == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert parse_list(" a , b , c ") == ["a", "b", "c"]

    def test_drops_empty_items(self):
        assert parse_list("a,,b,") == ["a", "b"]


class TestParseScopes:
    def test_none_returns_none(self):
        assert parse_scopes(None) is None

    def test_empty_returns_empty_list(self):
        assert parse_scopes("") == []

    def test_space_separated(self):
        assert parse_scopes("read write") == ["read", "write"]

    def test_comma_separated(self):
        assert parse_scopes("read,write") == ["read", "write"]

    def test_mixed(self):
        assert parse_scopes("read, write profile") == ["read", "write", "profile"]
```

- [ ] **Step 2: Run the test to confirm it fails**

Run:

```bash
cd /mnt/code/fastmcp-pvl-core
uv run pytest tests/test_env.py -v
```

Expected: `ImportError: cannot import name 'env' from 'fastmcp_pvl_core'`.

- [ ] **Step 3: Write the implementation**

Create `src/fastmcp_pvl_core/_env.py`:

```python
"""Environment variable helpers.

All env var reads in the library and downstream projects route
through :func:`env` to keep naming consistent.
"""

from __future__ import annotations

import os


def env(prefix: str, name: str, default: str | None = None) -> str | None:
    """Read `{PREFIX}_{NAME}` from the environment.

    Args:
        prefix: Env var prefix (trailing underscore optional).
        name: Variable name (without prefix).
        default: Value to return if unset or empty after strip.

    Returns:
        The env var value stripped of whitespace, or ``default``.
    """
    key = f"{prefix.rstrip('_')}_{name}"
    raw = os.environ.get(key)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


def parse_bool(value: str) -> bool:
    """Parse common truthy strings to ``bool``."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_list(value: str) -> list[str]:
    """Parse a comma-separated list, trimming and dropping empties."""
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_scopes(value: str | None) -> list[str] | None:
    """Parse an OIDC/OAuth scopes string (space- or comma-separated)."""
    if value is None:
        return None
    # Normalize commas to spaces, then split on whitespace.
    normalized = value.replace(",", " ")
    return [s for s in normalized.split() if s]
```

- [ ] **Step 4: Export from the package root**

Replace `src/fastmcp_pvl_core/__init__.py` contents:

```python
"""Shared FastMCP infrastructure."""

from fastmcp_pvl_core._env import env, parse_bool, parse_list, parse_scopes

__version__ = "0.0.0"

__all__ = ["env", "parse_bool", "parse_list", "parse_scopes"]
```

- [ ] **Step 5: Run the test to confirm it passes**

Run:

```bash
uv run pytest tests/test_env.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run lint + type-check**

Run:

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add env and parse helpers"
git push
```

---

### Task 5: Port `ServerConfig` dataclass

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_config.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_config.py`

**Source reference:** MV `src/markdown_vault_mcp/config.py` lines 66–316 — pull only the universally-applicable server/auth/event-store fields; leave domain fields in MV.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
"""Tests for ServerConfig."""

from __future__ import annotations

import pytest

from fastmcp_pvl_core import ServerConfig


class TestServerConfigDefaults:
    def test_default_transport_is_stdio(self):
        config = ServerConfig()
        assert config.transport == "stdio"

    def test_default_host_port(self):
        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8000

    def test_auth_fields_default_to_none(self):
        config = ServerConfig()
        assert config.bearer_token is None
        assert config.oidc_config_url is None
        assert config.oidc_client_id is None
        assert config.oidc_required_scopes == ()


class TestServerConfigFromEnv:
    def test_reads_transport(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_TRANSPORT", "http")
        config = ServerConfig.from_env("MYAPP")
        assert config.transport == "http"

    def test_reads_host_port(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_HOST", "0.0.0.0")
        monkeypatch.setenv("MYAPP_PORT", "9000")
        config = ServerConfig.from_env("MYAPP")
        assert config.host == "0.0.0.0"
        assert config.port == 9000

    def test_reads_bearer_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_BEARER_TOKEN", "secret")
        config = ServerConfig.from_env("MYAPP")
        assert config.bearer_token == "secret"

    def test_reads_oidc_vars(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_BASE_URL", "https://x.example")
        monkeypatch.setenv("MYAPP_OIDC_CONFIG_URL", "https://idp.example/.well-known/openid-configuration")
        monkeypatch.setenv("MYAPP_OIDC_CLIENT_ID", "cid")
        monkeypatch.setenv("MYAPP_OIDC_CLIENT_SECRET", "csecret")
        monkeypatch.setenv("MYAPP_OIDC_REQUIRED_SCOPES", "openid profile")
        config = ServerConfig.from_env("MYAPP")
        assert config.base_url == "https://x.example"
        assert config.oidc_config_url == "https://idp.example/.well-known/openid-configuration"
        assert config.oidc_client_id == "cid"
        assert config.oidc_client_secret == "csecret"
        assert config.oidc_required_scopes == ("openid", "profile")

    def test_reads_event_store_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MYAPP_EVENT_STORE_URL", "file:///data/events")
        config = ServerConfig.from_env("MYAPP")
        assert config.event_store_url == "file:///data/events"

    def test_is_frozen(self):
        config = ServerConfig()
        with pytest.raises(AttributeError):
            config.transport = "http"  # type: ignore[misc]
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_config.py -v
```

Expected: `ImportError: cannot import name 'ServerConfig'`.

- [ ] **Step 3: Implement `ServerConfig`**

Create `src/fastmcp_pvl_core/_config.py`:

```python
"""Universal server configuration.

Downstream projects compose this into their own domain config dataclass
(they do not inherit). Core only owns fields that are universal to any
FastMCP server: transport, host, port, auth credentials, event store URL,
MCP Apps domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fastmcp_pvl_core._env import env, parse_scopes

Transport = Literal["stdio", "http", "sse"]


@dataclass(frozen=True)
class ServerConfig:
    """Universal fields every FastMCP server needs.

    Compose into a domain config; never inherit from this class.
    """

    transport: Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    base_url: str | None = None

    bearer_token: str | None = None

    oidc_config_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_audience: str | None = None
    oidc_required_scopes: tuple[str, ...] = field(default_factory=tuple)
    oidc_jwt_signing_key: str | None = None

    event_store_url: str | None = None
    app_domain: str | None = None

    @classmethod
    def from_env(cls, env_prefix: str) -> "ServerConfig":
        """Load all fields from ``{env_prefix}_*`` environment variables.

        Args:
            env_prefix: Env var prefix, no trailing underscore needed.

        Returns:
            A populated :class:`ServerConfig` instance.
        """
        transport_raw = env(env_prefix, "TRANSPORT", "stdio")
        if transport_raw not in ("stdio", "http", "sse"):
            transport_raw = "stdio"
        transport: Transport = transport_raw  # type: ignore[assignment]

        host = env(env_prefix, "HOST", "127.0.0.1")
        assert host is not None
        port_str = env(env_prefix, "PORT", "8000")
        assert port_str is not None

        scopes_raw = env(env_prefix, "OIDC_REQUIRED_SCOPES")
        scopes = tuple(parse_scopes(scopes_raw) or ())

        return cls(
            transport=transport,
            host=host,
            port=int(port_str),
            base_url=env(env_prefix, "BASE_URL"),
            bearer_token=env(env_prefix, "BEARER_TOKEN"),
            oidc_config_url=env(env_prefix, "OIDC_CONFIG_URL"),
            oidc_client_id=env(env_prefix, "OIDC_CLIENT_ID"),
            oidc_client_secret=env(env_prefix, "OIDC_CLIENT_SECRET"),
            oidc_audience=env(env_prefix, "OIDC_AUDIENCE"),
            oidc_required_scopes=scopes,
            oidc_jwt_signing_key=env(env_prefix, "OIDC_JWT_SIGNING_KEY"),
            event_store_url=env(env_prefix, "EVENT_STORE_URL"),
            app_domain=env(env_prefix, "APP_DOMAIN"),
        )
```

- [ ] **Step 4: Export from package root**

Update `src/fastmcp_pvl_core/__init__.py`:

```python
"""Shared FastMCP infrastructure."""

from fastmcp_pvl_core._config import ServerConfig, Transport
from fastmcp_pvl_core._env import env, parse_bool, parse_list, parse_scopes

__version__ = "0.0.0"

__all__ = [
    "ServerConfig",
    "Transport",
    "env",
    "parse_bool",
    "parse_list",
    "parse_scopes",
]
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_config.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run lint + type-check + full test suite**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
uv run pytest -x -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add ServerConfig dataclass with from_env loader"
git push
```

---

## Phase C — Auth

### Task 6: Port `resolve_auth_mode`

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_auth.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_auth_mode.py`

**Source reference:** MV `src/markdown_vault_mcp/config.py` lines 750–798 (`resolve_auth_mode`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth_mode.py`:

```python
"""Tests for resolve_auth_mode."""

from __future__ import annotations

import pytest

from fastmcp_pvl_core import ServerConfig, resolve_auth_mode


def _cfg(**kwargs) -> ServerConfig:
    return ServerConfig(**kwargs)


class TestResolveAuthMode:
    def test_none_when_no_auth_configured(self):
        assert resolve_auth_mode(_cfg()) == "none"

    def test_bearer_only(self):
        assert resolve_auth_mode(_cfg(bearer_token="x")) == "bearer"

    def test_oidc_proxy_when_all_four_oidc_vars_set(self):
        assert resolve_auth_mode(_cfg(
            base_url="https://x",
            oidc_config_url="https://idp/.well-known/openid-configuration",
            oidc_client_id="cid",
            oidc_client_secret="csecret",
        )) == "oidc-proxy"

    def test_remote_when_only_base_url_and_config_url(self):
        assert resolve_auth_mode(_cfg(
            base_url="https://x",
            oidc_config_url="https://idp/.well-known/openid-configuration",
        )) == "remote"

    def test_multi_when_bearer_and_oidc_proxy(self):
        assert resolve_auth_mode(_cfg(
            bearer_token="x",
            base_url="https://x",
            oidc_config_url="https://idp/.well-known/openid-configuration",
            oidc_client_id="cid",
            oidc_client_secret="csecret",
        )) == "multi"

    def test_multi_when_bearer_and_remote(self):
        assert resolve_auth_mode(_cfg(
            bearer_token="x",
            base_url="https://x",
            oidc_config_url="https://idp/.well-known/openid-configuration",
        )) == "multi"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_auth_mode.py -v
```

Expected: `ImportError: cannot import name 'resolve_auth_mode'`.

- [ ] **Step 3: Implement `resolve_auth_mode`**

Create `src/fastmcp_pvl_core/_auth.py`:

```python
"""Auth mode resolution and builders.

Inspect :class:`ServerConfig` to determine which auth flavor is
configured, then dispatch to the right FastMCP auth provider.
Five modes: ``none``, ``bearer``, ``remote``, ``oidc-proxy``, ``multi``.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastmcp_pvl_core._config import ServerConfig

logger = logging.getLogger(__name__)

AuthMode = Literal["none", "bearer", "remote", "oidc-proxy", "multi"]


def resolve_auth_mode(config: ServerConfig) -> AuthMode:
    """Decide which auth flavor to use based on configured fields.

    - ``multi``: both bearer and an OIDC flavor configured.
    - ``bearer``: only ``bearer_token`` set.
    - ``oidc-proxy``: all four OIDC client-credential vars set.
    - ``remote``: only ``base_url`` + ``oidc_config_url`` set.
    - ``none``: nothing configured.

    Args:
        config: The loaded server config.

    Returns:
        One of the five :data:`AuthMode` strings.
    """
    has_bearer = bool(config.bearer_token)
    has_oidc_proxy = all((
        config.base_url,
        config.oidc_config_url,
        config.oidc_client_id,
        config.oidc_client_secret,
    ))
    has_remote = bool(config.base_url and config.oidc_config_url) and not has_oidc_proxy

    oidc_mode: AuthMode | None
    if has_oidc_proxy:
        oidc_mode = "oidc-proxy"
    elif has_remote:
        oidc_mode = "remote"
    else:
        oidc_mode = None

    if has_bearer and oidc_mode is not None:
        return "multi"
    if has_bearer:
        return "bearer"
    if oidc_mode is not None:
        return oidc_mode
    return "none"
```

- [ ] **Step 4: Export**

Update `src/fastmcp_pvl_core/__init__.py` — add to imports and `__all__`:

```python
from fastmcp_pvl_core._auth import AuthMode, resolve_auth_mode
```

and append `"AuthMode", "resolve_auth_mode"` to `__all__`.

- [ ] **Step 5: Run tests + checks**

```bash
uv run pytest tests/test_auth_mode.py -v
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add resolve_auth_mode dispatcher"
git push
```

---

### Task 7: Port individual auth builders

**Files:**
- Modify: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_auth.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_auth_builders.py`

**Source reference:** MV `src/markdown_vault_mcp/config.py` lines 799–943 (`build_remote_auth`, `build_bearer_auth`, `build_oidc_auth`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auth_builders.py`:

```python
"""Tests for individual auth builders."""

from __future__ import annotations

from fastmcp_pvl_core import (
    ServerConfig,
    build_bearer_auth,
    build_oidc_proxy_auth,
    build_remote_auth,
)


class TestBuildBearerAuth:
    def test_returns_none_when_no_token(self):
        assert build_bearer_auth(ServerConfig()) is None

    def test_returns_verifier_when_token_set(self):
        from fastmcp.server.auth.providers.bearer import StaticTokenVerifier

        auth = build_bearer_auth(ServerConfig(bearer_token="secret"))
        assert isinstance(auth, StaticTokenVerifier)


class TestBuildOIDCProxyAuth:
    def test_returns_none_when_any_var_missing(self):
        assert build_oidc_proxy_auth(ServerConfig()) is None
        assert build_oidc_proxy_auth(ServerConfig(base_url="https://x")) is None

    def test_returns_proxy_when_all_vars_set(self):
        from fastmcp.server.auth.oidc_proxy import OIDCProxy

        auth = build_oidc_proxy_auth(ServerConfig(
            base_url="https://x.example",
            oidc_config_url="https://idp.example/.well-known/openid-configuration",
            oidc_client_id="cid",
            oidc_client_secret="csecret",
        ))
        assert isinstance(auth, OIDCProxy)


class TestBuildRemoteAuth:
    def test_returns_none_when_config_missing(self):
        assert build_remote_auth(ServerConfig()) is None

    def test_returns_none_when_httpx_unavailable(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "httpx", None)
        result = build_remote_auth(ServerConfig(
            base_url="https://x",
            oidc_config_url="https://idp/.well-known/openid-configuration",
        ))
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_auth_builders.py -v
```

Expected: ImportError on the builder names.

- [ ] **Step 3: Extend `_auth.py` with builders**

Append to `src/fastmcp_pvl_core/_auth.py`:

```python
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp.server.auth.oauth_proxy import OAuthProxy
    from fastmcp.server.auth.providers.bearer import StaticTokenVerifier


def build_bearer_auth(config: ServerConfig) -> "StaticTokenVerifier | None":
    """Return a ``StaticTokenVerifier`` when ``bearer_token`` is set."""
    if not config.bearer_token:
        return None
    from fastmcp.server.auth.providers.bearer import StaticTokenVerifier

    return StaticTokenVerifier(
        tokens={config.bearer_token: {"scopes": ["read", "write"]}},
    )


def build_oidc_proxy_auth(config: ServerConfig) -> Any:
    """Return an ``OIDCProxy`` when all four OIDC proxy vars are set."""
    if not all((
        config.base_url,
        config.oidc_config_url,
        config.oidc_client_id,
        config.oidc_client_secret,
    )):
        return None
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    return OIDCProxy(
        base_url=config.base_url,
        config_url=config.oidc_config_url,
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        audience=config.oidc_audience,
        required_scopes=list(config.oidc_required_scopes) or None,
        jwt_signing_key=config.oidc_jwt_signing_key,
    )


def build_remote_auth(config: ServerConfig) -> Any:
    """Return a ``RemoteAuthProvider`` when only BASE_URL + OIDC_CONFIG_URL are set.

    Returns ``None`` if httpx is not installed (optional ``remote-auth``
    extra) or the config is incomplete.
    """
    if not (config.base_url and config.oidc_config_url):
        return None
    try:
        import httpx  # noqa: F401
    except ImportError:
        logger.warning(
            "build_remote_auth skipped: httpx not installed. "
            "Install with `pip install fastmcp-pvl-core[remote-auth]`."
        )
        return None

    from fastmcp.server.auth.providers.jwt import JWTVerifier
    from fastmcp.server.auth.remote import RemoteAuthProvider

    verifier = JWTVerifier(
        jwks_uri=None,  # fetched from discovery by RemoteAuthProvider
        issuer=None,
        audience=config.oidc_audience,
        required_scopes=list(config.oidc_required_scopes) or None,
    )
    return RemoteAuthProvider(
        base_url=config.base_url,
        config_url=config.oidc_config_url,
        verifier=verifier,
    )
```

**Note:** the exact constructor signatures for `RemoteAuthProvider` / `OIDCProxy` / `JWTVerifier` must match MV's current usage — if they differ after porting, resolve by grepping MV's `config.py` lines 799–943 and mirroring exactly.

- [ ] **Step 4: Export**

Update `__init__.py`:

```python
from fastmcp_pvl_core._auth import (
    AuthMode,
    build_bearer_auth,
    build_oidc_proxy_auth,
    build_remote_auth,
    resolve_auth_mode,
)
```

Append builder names to `__all__`.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_auth_builders.py -v
```

Expected: all pass.

- [ ] **Step 6: Lint + type-check**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add individual auth builders (bearer, remote, oidc-proxy)"
git push
```

---

### Task 8: Port `build_auth` dispatcher with MultiAuth wiring

**Files:**
- Modify: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_auth.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_build_auth.py`

**Source reference:** MV `src/markdown_vault_mcp/mcp_server.py` in `create_server()` — the section that wires `MultiAuth(server=..., verifiers=[...], required_scopes=[])`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_build_auth.py`:

```python
"""Tests for build_auth dispatcher."""

from __future__ import annotations

from fastmcp_pvl_core import ServerConfig, build_auth


class TestBuildAuth:
    def test_none_when_no_auth_configured(self):
        assert build_auth(ServerConfig()) is None

    def test_returns_bearer_verifier_alone(self):
        from fastmcp.server.auth.providers.bearer import StaticTokenVerifier

        auth = build_auth(ServerConfig(bearer_token="x"))
        assert isinstance(auth, StaticTokenVerifier)

    def test_returns_oidc_proxy_alone(self):
        from fastmcp.server.auth.oidc_proxy import OIDCProxy

        auth = build_auth(ServerConfig(
            base_url="https://x.example",
            oidc_config_url="https://idp.example/.well-known/openid-configuration",
            oidc_client_id="cid",
            oidc_client_secret="csecret",
        ))
        assert isinstance(auth, OIDCProxy)

    def test_returns_multi_auth_with_empty_required_scopes(self):
        from fastmcp.server.auth.multi import MultiAuth

        auth = build_auth(ServerConfig(
            bearer_token="x",
            base_url="https://x.example",
            oidc_config_url="https://idp.example/.well-known/openid-configuration",
            oidc_client_id="cid",
            oidc_client_secret="csecret",
        ))
        assert isinstance(auth, MultiAuth)
        # CRITICAL: required_scopes must be [] to prevent OIDC's ["openid"]
        # from propagating to RequireAuthMiddleware and rejecting bearer tokens.
        assert auth.required_scopes == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_build_auth.py -v
```

Expected: `ImportError: cannot import name 'build_auth'`.

- [ ] **Step 3: Implement `build_auth`**

Append to `src/fastmcp_pvl_core/_auth.py`:

```python
def build_auth(config: ServerConfig) -> Any:
    """Dispatch to the correct FastMCP auth provider.

    Returns:
        - ``None`` when no auth is configured
        - a ``StaticTokenVerifier`` in bearer-only mode
        - an ``OIDCProxy`` in oidc-proxy-only mode
        - a ``RemoteAuthProvider`` in remote-only mode
        - a ``MultiAuth`` with ``required_scopes=[]`` in multi mode
          (the empty list is load-bearing — see comment in source)
    """
    mode = resolve_auth_mode(config)
    if mode == "none":
        return None
    if mode == "bearer":
        return build_bearer_auth(config)
    if mode == "oidc-proxy":
        return build_oidc_proxy_auth(config)
    if mode == "remote":
        return build_remote_auth(config)
    # mode == "multi"
    from fastmcp.server.auth.multi import MultiAuth

    oidc_auth = build_oidc_proxy_auth(config) or build_remote_auth(config)
    bearer_auth = build_bearer_auth(config)
    # required_scopes=[] is critical: otherwise OIDC's "openid" scope
    # propagates to RequireAuthMiddleware and rejects bearer tokens
    # with 403 insufficient_scope (MV PR #249).
    return MultiAuth(
        server=oidc_auth,
        verifiers=[bearer_auth] if bearer_auth else [],
        required_scopes=[],
    )
```

- [ ] **Step 4: Export**

Add `build_auth` to `__init__.py` imports and `__all__`.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_build_auth.py -v
```

Expected: all pass.

- [ ] **Step 6: Lint + type + commit**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
git add -A
git commit -m "feat: add build_auth dispatcher with MultiAuth wiring"
git push
```

---

## Phase D — Middleware and logging

### Task 9: Port `wire_middleware_stack` and `configure_logging_from_env`

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_middleware.py`
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_logging.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_middleware.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_logging.py`

**Source reference:** MV `src/markdown_vault_mcp/mcp_server.py` lines 241–260 (middleware stack), MV PR #331 notes.

- [ ] **Step 1: Write the failing middleware test**

Create `tests/test_middleware.py`:

```python
"""Tests for wire_middleware_stack."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import (
    LoggingMiddleware,
    StructuredLoggingMiddleware,
)
from fastmcp.server.middleware.timing import TimingMiddleware

from fastmcp_pvl_core import wire_middleware_stack


def _installed_types(mcp: FastMCP) -> list[type]:
    return [type(m) for m in mcp._middleware]  # internal attr; acceptable in tests


def test_installs_three_middlewares_in_order():
    mcp = FastMCP(name="t")
    wire_middleware_stack(mcp)
    types = _installed_types(mcp)
    assert types[0] is ErrorHandlingMiddleware
    assert types[1] is TimingMiddleware
    assert types[2] in (LoggingMiddleware, StructuredLoggingMiddleware)


def test_structured_when_rich_disabled(monkeypatch):
    monkeypatch.setenv("FASTMCP_ENABLE_RICH_LOGGING", "false")
    mcp = FastMCP(name="t")
    wire_middleware_stack(mcp)
    types = _installed_types(mcp)
    assert types[2] is StructuredLoggingMiddleware


def test_rich_when_rich_enabled_or_unset(monkeypatch):
    monkeypatch.delenv("FASTMCP_ENABLE_RICH_LOGGING", raising=False)
    mcp = FastMCP(name="t")
    wire_middleware_stack(mcp)
    types = _installed_types(mcp)
    assert types[2] is LoggingMiddleware
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_middleware.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `wire_middleware_stack`**

Create `src/fastmcp_pvl_core/_middleware.py`:

```python
"""FastMCP middleware stack installation."""

from __future__ import annotations

import os

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import (
    LoggingMiddleware,
    StructuredLoggingMiddleware,
)
from fastmcp.server.middleware.timing import TimingMiddleware

from fastmcp_pvl_core._env import parse_bool


def wire_middleware_stack(mcp: FastMCP) -> None:
    """Install the standard middleware stack on a FastMCP instance.

    Order matters:
      1. :class:`ErrorHandlingMiddleware` — catches unhandled exceptions
      2. :class:`TimingMiddleware` — records tool invocation duration
      3. :class:`LoggingMiddleware` (rich) or
         :class:`StructuredLoggingMiddleware` (JSON) — controlled by
         ``FASTMCP_ENABLE_RICH_LOGGING``.

    Args:
        mcp: The FastMCP server instance to configure.
    """
    mcp.add_middleware(
        ErrorHandlingMiddleware(
            include_traceback=True,
        )
    )
    mcp.add_middleware(TimingMiddleware())

    rich_raw = os.environ.get("FASTMCP_ENABLE_RICH_LOGGING", "true")
    if parse_bool(rich_raw):
        mcp.add_middleware(LoggingMiddleware())
    else:
        mcp.add_middleware(StructuredLoggingMiddleware())
```

- [ ] **Step 4: Write the failing logging test**

Create `tests/test_logging.py`:

```python
"""Tests for configure_logging_from_env."""

from __future__ import annotations

import logging

from fastmcp_pvl_core import configure_logging_from_env


def test_sets_debug_when_verbose_true(monkeypatch):
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    configure_logging_from_env(verbose=True)
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG


def test_respects_fastmcp_log_level(monkeypatch):
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "WARNING")
    configure_logging_from_env(verbose=False)
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_defaults_to_info_when_nothing_set(monkeypatch):
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    configure_logging_from_env(verbose=False)
    assert logging.getLogger().getEffectiveLevel() == logging.INFO
```

- [ ] **Step 5: Run to confirm failure**

```bash
uv run pytest tests/test_logging.py -v
```

Expected: ImportError.

- [ ] **Step 6: Implement `configure_logging_from_env`**

Create `src/fastmcp_pvl_core/_logging.py`:

```python
"""Logging setup — delegates to FastMCP's configure_logging().

The ``-v`` CLI flag forces ``DEBUG``; otherwise the ``FASTMCP_LOG_LEVEL``
env var wins; otherwise ``INFO``.
"""

from __future__ import annotations

import logging
import os

from fastmcp.utilities.logging import configure_logging


def configure_logging_from_env(*, verbose: bool = False) -> None:
    """Configure logging globally based on env + verbose flag.

    Args:
        verbose: If ``True``, force ``DEBUG`` and set
            ``FASTMCP_LOG_LEVEL=DEBUG`` in the environment so FastMCP's
            own logger picks up the same level.
    """
    if verbose:
        os.environ["FASTMCP_LOG_LEVEL"] = "DEBUG"
        level_name = "DEBUG"
    else:
        level_name = os.environ.get("FASTMCP_LOG_LEVEL", "INFO").upper()

    level = getattr(logging, level_name, logging.INFO)
    logging.getLogger().setLevel(level)
    configure_logging(level_name)
```

- [ ] **Step 7: Export**

Update `__init__.py`:

```python
from fastmcp_pvl_core._logging import configure_logging_from_env
from fastmcp_pvl_core._middleware import wire_middleware_stack
```

Append to `__all__`.

- [ ] **Step 8: Run all tests + lint + type**

```bash
uv run pytest -x -q
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: add middleware stack and logging helpers"
git push
```

---

## Phase E — Server-factory helpers

### Task 10: Port `build_instructions`, `build_event_store`, `compute_app_domain`

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_factory.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_factory.py`

**Source reference:**
- `build_instructions` — MV `mcp_server.py:113` (`_build_default_instructions`)
- `build_event_store` — MV `mcp_server.py:58`
- `compute_app_domain` — MV `_server_apps.py` (search for `_compute_claude_app_domain`)

- [ ] **Step 1: Check source for compute_app_domain signature**

Run:

```bash
grep -n "def.*compute_.*_domain" /mnt/code/markdown-mcp/src/markdown_vault_mcp/_server_apps.py
grep -n "APP_DOMAIN\|base_url" /mnt/code/markdown-mcp/src/markdown_vault_mcp/_server_apps.py | head -20
```

Expected: prints the function signature and usage. The implementation must accept a `ServerConfig` (or the two fields it reads: `base_url` and `app_domain` override) and return a string.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_factory.py`:

```python
"""Tests for server-factory helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastmcp_pvl_core import (
    ServerConfig,
    build_event_store,
    build_instructions,
    compute_app_domain,
)


class TestBuildInstructions:
    def test_read_only_line(self):
        text = build_instructions(
            read_only=True,
            env_prefix="MY_APP",
            domain_line="A widget service.",
        )
        assert "READ-ONLY" in text
        assert "A widget service." in text
        assert "MY_APP_INSTRUCTIONS" in text

    def test_read_write_line(self):
        text = build_instructions(
            read_only=False,
            env_prefix="MY_APP",
            domain_line="A widget service.",
        )
        assert "READ-WRITE" in text
        assert "READ-ONLY" not in text


class TestBuildEventStore:
    def test_memory_url(self):
        config = ServerConfig(event_store_url="memory://")
        store = build_event_store("MY_APP", config)
        # Memory store should not persist anywhere — type-check only.
        assert store is not None

    def test_file_url(self):
        with tempfile.TemporaryDirectory() as td:
            config = ServerConfig(event_store_url=f"file://{td}/events")
            store = build_event_store("MY_APP", config)
            assert store is not None

    def test_default_when_unset(self):
        config = ServerConfig(event_store_url=None)
        store = build_event_store("MY_APP", config)
        # Default: file-backed under a platform-appropriate path.
        assert store is not None


class TestComputeAppDomain:
    def test_override_wins(self):
        config = ServerConfig(
            base_url="https://x.example",
            app_domain="override.example",
        )
        assert compute_app_domain(config) == "override.example"

    def test_derives_from_base_url(self):
        config = ServerConfig(base_url="https://mcp.example.com")
        assert compute_app_domain(config) == "mcp.example.com"

    def test_none_when_no_base_url(self):
        assert compute_app_domain(ServerConfig()) is None
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/test_factory.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement the three helpers**

Create `src/fastmcp_pvl_core/_factory.py`:

```python
"""Server-factory building blocks.

Each function returns a piece of the FastMCP wiring so downstream
projects can compose a ``make_server()`` without inheriting from a
base class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from fastmcp_pvl_core._config import ServerConfig
from fastmcp_pvl_core._env import env

if TYPE_CHECKING:
    pass


def build_instructions(
    *, read_only: bool, env_prefix: str, domain_line: str
) -> str:
    """Build the default MCP ``instructions`` string.

    Args:
        read_only: Whether write tools are disabled on this instance.
        env_prefix: Env var prefix (no trailing underscore) so the hint
            about ``{PREFIX}_INSTRUCTIONS`` is accurate.
        domain_line: One sentence describing the service's domain,
            included verbatim.

    Returns:
        A multi-line instruction string suitable for ``FastMCP(instructions=...)``.
    """
    write_line = (
        "This instance is READ-ONLY — write tools are not available."
        if read_only
        else "This instance is READ-WRITE — write tools are available."
    )
    prefix = env_prefix.rstrip("_")
    return (
        f"{domain_line} "
        f"{write_line} "
        f"Operators: set {prefix}_INSTRUCTIONS to describe this "
        "service's domain and capabilities."
    )


def build_event_store(env_prefix: str, config: ServerConfig) -> Any:
    """Construct an MCP event store based on ``EVENT_STORE_URL``.

    - ``memory://...`` → in-memory (non-persistent)
    - ``file://...`` → file-backed under the given path
    - unset → file-backed under a default path (platform state dir)

    Args:
        env_prefix: Reserved for future prefix-scoped overrides.
        config: Server config providing ``event_store_url``.

    Returns:
        A FastMCP-compatible event store instance.
    """
    from fastmcp.server.event_store import EventStore, FileEventStore, MemoryEventStore

    url = config.event_store_url
    if url is None or url.startswith("file://"):
        path = url.removeprefix("file://") if url else "/data/state/events"
        return FileEventStore(path=path)
    if url.startswith("memory://"):
        return MemoryEventStore()
    raise ValueError(f"Unsupported event_store_url scheme: {url!r}")


def compute_app_domain(config: ServerConfig) -> str | None:
    """Derive the MCP Apps iframe domain for CSP sandboxing.

    Priority:
      1. ``config.app_domain`` (explicit override)
      2. Host portion of ``config.base_url``
      3. ``None`` if neither is set

    Args:
        config: Server config.

    Returns:
        The domain string (e.g. ``"mcp.example.com"``) or ``None``.
    """
    if config.app_domain:
        return config.app_domain
    if config.base_url:
        parsed = urlparse(config.base_url)
        return parsed.netloc or None
    return None
```

**Note:** the exact FastMCP event store classes (`FileEventStore`, `MemoryEventStore`) may have different names — check MV `mcp_server.py:58` and mirror. The imports must match.

- [ ] **Step 5: Export**

Update `__init__.py`:

```python
from fastmcp_pvl_core._factory import (
    build_event_store,
    build_instructions,
    compute_app_domain,
)
```

Append to `__all__`.

- [ ] **Step 6: Run tests + checks + commit**

```bash
uv run pytest tests/test_factory.py -v
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
git add -A
git commit -m "feat: add build_instructions, build_event_store, compute_app_domain"
git push
```

---

## Phase F — Artifact store

### Task 11: Port `ArtifactStore` with route registration

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_artifacts.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_artifacts.py`

**Source reference:** MV `src/markdown_vault_mcp/artifacts.py` (full file — `TokenRecord`, `ArtifactStore`, `make_artifact_handler`). Keep generic — no references to `Collection` or markdown-specific types in core.

- [ ] **Step 1: Read MV's artifacts.py for context**

Run:

```bash
head -220 /mnt/code/markdown-mcp/src/markdown_vault_mcp/artifacts.py
```

Confirm: the store operates on arbitrary bytes + filename + mime type — nothing markdown-specific. If `set_collection_store`/`_get_collection_from_store` exist, those stay in MV (domain-specific); only the store itself + handler factory + route registration move to core.

- [ ] **Step 2: Write the failing test**

Create `tests/test_artifacts.py`:

```python
"""Tests for ArtifactStore."""

from __future__ import annotations

import pytest

from fastmcp_pvl_core import ArtifactStore


class TestArtifactStore:
    def test_add_returns_token(self):
        store = ArtifactStore()
        token = store.add(b"hello", filename="a.txt", mime_type="text/plain")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_pop_returns_data_and_removes_token(self):
        store = ArtifactStore()
        token = store.add(b"hello", filename="a.txt", mime_type="text/plain")
        record = store.pop(token)
        assert record is not None
        assert record.content == b"hello"
        assert record.filename == "a.txt"
        assert record.mime_type == "text/plain"
        # One-time: second pop returns None
        assert store.pop(token) is None

    def test_pop_unknown_token_returns_none(self):
        store = ArtifactStore()
        assert store.pop("nonexistent") is None

    def test_expired_tokens_are_purged(self, monkeypatch):
        import time

        store = ArtifactStore(ttl_seconds=1)
        token = store.add(b"x", filename="x", mime_type="application/octet-stream")
        # Move time forward
        original_time = time.time()
        monkeypatch.setattr(time, "time", lambda: original_time + 10)
        assert store.pop(token) is None
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/test_artifacts.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement `ArtifactStore`**

Create `src/fastmcp_pvl_core/_artifacts.py`:

```python
"""One-time artifact download support.

Downstream tools can stash bytes in the store, return an HTTP URL
pointing at ``GET /artifacts/{token}``, and the next retrieval consumes
and removes the token. Useful for large generated outputs (images,
archives) that shouldn't pass through the MCP transport.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenRecord:
    """A one-time downloadable artifact."""

    content: bytes
    filename: str
    mime_type: str
    expires_at: float


class ArtifactStore:
    """In-memory one-time artifact store with TTL expiry."""

    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        """Create a new empty store.

        Args:
            ttl_seconds: Token lifetime after which unclaimed artifacts
                are purged on next access. Default: 1 hour.
        """
        self._records: dict[str, TokenRecord] = {}
        self._ttl = ttl_seconds

    def add(self, content: bytes, *, filename: str, mime_type: str) -> str:
        """Stash bytes and return a fresh opaque token."""
        self._purge_expired()
        token = uuid.uuid4().hex
        self._records[token] = TokenRecord(
            content=content,
            filename=filename,
            mime_type=mime_type,
            expires_at=time.time() + self._ttl,
        )
        return token

    def pop(self, token: str) -> TokenRecord | None:
        """Consume a token: return the record and remove it, or ``None``."""
        self._purge_expired()
        return self._records.pop(token, None)

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [t for t, r in self._records.items() if r.expires_at <= now]
        for t in expired:
            del self._records[t]

    @classmethod
    def register_route(cls, mcp: Any, store: "ArtifactStore", *, path: str = "/artifacts/{token}") -> None:
        """Register ``GET {path}`` on a FastMCP HTTP app to serve artifacts.

        Args:
            mcp: The FastMCP instance (must be HTTP/SSE transport).
            store: The shared :class:`ArtifactStore` holding tokens.
            path: URL template; default ``/artifacts/{token}``.
        """
        from starlette.requests import Request
        from starlette.responses import Response

        @mcp.custom_route(path, methods=["GET"])
        async def _handler(request: Request) -> Response:
            token = request.path_params["token"]
            record = store.pop(token)
            if record is None:
                return Response(status_code=404)
            return Response(
                content=record.content,
                media_type=record.mime_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{record.filename}"'
                },
            )
```

- [ ] **Step 5: Export**

Update `__init__.py`:

```python
from fastmcp_pvl_core._artifacts import ArtifactStore, TokenRecord
```

Append to `__all__`.

- [ ] **Step 6: Run tests + lint + type + commit**

```bash
uv run pytest tests/test_artifacts.py -v
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
git add -A
git commit -m "feat: add ArtifactStore with one-time token semantics"
git push
```

---

## Phase G — CLI helpers

### Task 12: Port `run_cli` helper

**Files:**
- Create: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/_cli.py`
- Create: `/mnt/code/fastmcp-pvl-core/tests/test_cli.py`

**Source reference:** MV `src/markdown_vault_mcp/cli.py` — port only `_normalise_http_path`, `_cmd_serve`'s generic parts, `_build_parser`'s generic args (`-v`, `--transport`, `--host`, `--port`, `--version`). Domain subcommands (`index`, `search`, `reindex`) stay in MV.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
"""Tests for run_cli helper."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastmcp import FastMCP

from fastmcp_pvl_core import make_serve_parser, normalise_http_path


class TestNormaliseHttpPath:
    def test_none_returns_default(self):
        assert normalise_http_path(None) == "/mcp"

    def test_empty_returns_default(self):
        assert normalise_http_path("") == "/mcp"

    def test_adds_leading_slash(self):
        assert normalise_http_path("mcp") == "/mcp"

    def test_strips_trailing_slash(self):
        assert normalise_http_path("/mcp/") == "/mcp"

    def test_preserves_multi_segment(self):
        assert normalise_http_path("/api/mcp") == "/api/mcp"


class TestMakeServeParser:
    def test_parses_verbose_flag(self):
        parser = make_serve_parser(prog="myapp")
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_default_transport_is_stdio(self):
        parser = make_serve_parser(prog="myapp")
        args = parser.parse_args([])
        assert args.transport == "stdio"

    def test_parses_http_transport_and_port(self):
        parser = make_serve_parser(prog="myapp")
        args = parser.parse_args(["--transport", "http", "--port", "9000"])
        assert args.transport == "http"
        assert args.port == 9000
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement CLI helpers**

Create `src/fastmcp_pvl_core/_cli.py`:

```python
"""CLI helpers.

Intentionally narrow: we ship ``normalise_http_path`` and
``make_serve_parser``. Each project builds its own ``main()`` using
these helpers plus its own domain subcommands.
"""

from __future__ import annotations

import argparse


def normalise_http_path(path: str | None, *, default: str = "/mcp") -> str:
    """Normalize an HTTP mount path.

    Args:
        path: Raw path from config or CLI; may be ``None`` or empty.
        default: Path to return when ``path`` is falsy.

    Returns:
        A path starting with ``/`` and without a trailing ``/`` (unless
        the path is exactly ``/``).
    """
    if not path:
        return default
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path


def make_serve_parser(*, prog: str, description: str = "") -> argparse.ArgumentParser:
    """Build the common ``serve`` argparse parser.

    Projects typically add domain-specific subparsers on top of this:

        parser = make_serve_parser(prog="myapp")
        subs = parser.add_subparsers(dest="cmd")
        subs.add_parser("index", help="Reindex the corpus")
        args = parser.parse_args()

    Args:
        prog: Program name (also used for ``--version``).
        description: Optional description line.

    Returns:
        A configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG logging (sets FASTMCP_LOG_LEVEL=DEBUG)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind when transport is http/sse (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind when transport is http/sse (default: 8000)",
    )
    parser.add_argument(
        "--http-path",
        default=None,
        help="HTTP mount path (default: /mcp)",
    )
    return parser
```

- [ ] **Step 4: Export**

Update `__init__.py`:

```python
from fastmcp_pvl_core._cli import make_serve_parser, normalise_http_path
```

Append to `__all__`.

- [ ] **Step 5: Run tests + lint + type + commit**

```bash
uv run pytest -x -q
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
git add -A
git commit -m "feat: add normalise_http_path and make_serve_parser CLI helpers"
git push
```

---

## Phase H — Core library release

### Task 13: Audit, tag, publish `fastmcp-pvl-core` v0.1.0

**Files:**
- Verify: `/mnt/code/fastmcp-pvl-core/src/fastmcp_pvl_core/__init__.py`
- Verify: `/mnt/code/fastmcp-pvl-core/pyproject.toml`
- Create: `/mnt/code/fastmcp-pvl-core/CHANGELOG.md`

- [ ] **Step 1: Audit the public surface**

Run:

```bash
cd /mnt/code/fastmcp-pvl-core
uv run python -c "import fastmcp_pvl_core; print(sorted(fastmcp_pvl_core.__all__))"
```

Expected output (sorted):

```
['ArtifactStore', 'AuthMode', 'ServerConfig', 'TokenRecord', 'Transport',
 'build_auth', 'build_bearer_auth', 'build_event_store',
 'build_instructions', 'build_oidc_proxy_auth', 'build_remote_auth',
 'compute_app_domain', 'configure_logging_from_env', 'env',
 'make_serve_parser', 'normalise_http_path', 'parse_bool', 'parse_list',
 'parse_scopes', 'resolve_auth_mode', 'wire_middleware_stack']
```

If anything is missing, revisit the relevant task and add the export before proceeding.

- [ ] **Step 2: Run the full test + lint + type gate**

```bash
uv run pytest -x -q --cov=fastmcp_pvl_core --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
```

Expected: all pass, coverage ≥ 85% on `src/fastmcp_pvl_core/`.

- [ ] **Step 3: Write `CHANGELOG.md`**

```markdown
# Changelog

## 0.1.0 (2026-04-20)

### Features

- Initial extraction from `markdown-vault-mcp`.
- `ServerConfig` dataclass + `from_env()` loader.
- `env()`, `parse_bool()`, `parse_list()`, `parse_scopes()` helpers.
- Auth: `resolve_auth_mode`, `build_bearer_auth`, `build_remote_auth`,
  `build_oidc_proxy_auth`, `build_auth` (with `MultiAuth(required_scopes=[])`).
- Middleware: `wire_middleware_stack` (ErrorHandling + Timing + rich/JSON Logging).
- Logging: `configure_logging_from_env`.
- Server factory: `build_instructions`, `build_event_store`, `compute_app_domain`.
- Artifacts: `ArtifactStore` + `register_route` with one-time token semantics.
- CLI: `normalise_http_path`, `make_serve_parser`.
```

- [ ] **Step 4: Bump version and tag**

Run:

```bash
cd /mnt/code/fastmcp-pvl-core
# Update version in pyproject.toml
sed -i 's/^version = "0.0.0"/version = "0.1.0"/' pyproject.toml
# Update __version__
sed -i 's/__version__ = "0.0.0"/__version__ = "0.1.0"/' src/fastmcp_pvl_core/__init__.py
git add -A
git commit -m "chore: release 0.1.0"
git tag v0.1.0
git push origin main
git push origin v0.1.0
```

- [ ] **Step 5: Build and publish to PyPI**

Run:

```bash
cd /mnt/code/fastmcp-pvl-core
uv build
uv publish --token "$PYPI_TOKEN"
```

Expected: `fastmcp-pvl-core 0.1.0` uploaded to PyPI. If you haven't set up PSR-based automated publishing yet, that can be added as a follow-up — manual publish is fine for v0.1.0.

- [ ] **Step 6: Verify install works**

In a fresh tmp directory:

```bash
cd /tmp
uv venv /tmp/verify
source /tmp/verify/bin/activate
uv pip install fastmcp-pvl-core==0.1.0
python -c "from fastmcp_pvl_core import ServerConfig, build_auth; print('ok')"
```

Expected: prints `ok`.

---

## Phase I — Adopt core in MV

### Task 14: Add `fastmcp-pvl-core` to MV's dependencies

**Files:**
- Modify: `/mnt/code/markdown-mcp/pyproject.toml`

- [ ] **Step 1: Create a branch for the adoption**

```bash
cd /mnt/code/markdown-mcp
git checkout -b feat/adopt-fastmcp-pvl-core
```

- [ ] **Step 2: Add the dependency**

Run:

```bash
cd /mnt/code/markdown-mcp
uv add "fastmcp-pvl-core>=0.1.0,<0.2"
```

Expected: `pyproject.toml` gains the dep; `uv.lock` updated.

- [ ] **Step 3: Verify full test suite still passes (baseline)**

```bash
uv run pytest -x -q
```

Expected: all ~1395 tests pass. This is the baseline — any test that fails after adoption is a regression.

- [ ] **Step 4: Commit the dep addition alone**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add fastmcp-pvl-core dependency"
```

---

### Task 15: Swap MV's env helpers and introduce `ServerConfig` composition

**Files:**
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/config.py`

**Strategy:** delete MV's `_env`, `_parse_bool`, `_parse_list`, `_parse_scopes` and replace with imports from core. Add a `ServerConfig` field to `CollectionConfig` composed from `ServerConfig.from_env(_ENV_PREFIX)`. Keep domain fields in `CollectionConfig`.

- [ ] **Step 1: Replace env helpers with imports**

Edit `src/markdown_vault_mcp/config.py`:

Find lines 21–64 (the `_env`, `_parse_bool`, `_parse_list` definitions) and replace with:

```python
from fastmcp_pvl_core import env as _core_env
from fastmcp_pvl_core import parse_bool as _parse_bool
from fastmcp_pvl_core import parse_list as _parse_list
from fastmcp_pvl_core import parse_scopes as _parse_scopes


def _env(name: str, default: str | None = None) -> str | None:
    """Backward-compat shim — reads ``{_ENV_PREFIX}_{name}``."""
    return _core_env(_ENV_PREFIX, name, default=default)
```

**Rationale:** the shim preserves the `_env(name, ...)` call shape used throughout MV, so downstream call sites don't need changes. The heavy lifting moves to core.

- [ ] **Step 2: Replace MV's auth builders and `resolve_auth_mode`**

Find lines 735–943 (`_parse_scopes`, `resolve_auth_mode`, `build_remote_auth`, `build_bearer_auth`, `build_oidc_auth`) and delete all of them.

Add at the top of `config.py` (near the imports):

```python
from fastmcp_pvl_core import (
    ServerConfig,
    build_auth as _core_build_auth,
    build_bearer_auth as _core_build_bearer_auth,
    build_oidc_proxy_auth as _core_build_oidc_proxy_auth,
    build_remote_auth as _core_build_remote_auth,
    resolve_auth_mode as _core_resolve_auth_mode,
)
```

Add compat wrappers near the bottom that accept `CollectionConfig` and delegate:

```python
def resolve_auth_mode(config: "CollectionConfig") -> str:
    """Shim: dispatch core resolver on the composed ServerConfig."""
    return _core_resolve_auth_mode(config.server)


def build_auth(config: "CollectionConfig") -> Any:
    return _core_build_auth(config.server)


def build_bearer_auth(config: "CollectionConfig") -> Any:
    return _core_build_bearer_auth(config.server)


def build_remote_auth(config: "CollectionConfig") -> Any:
    return _core_build_remote_auth(config.server)


def build_oidc_auth(config: "CollectionConfig") -> Any:
    """Legacy name preserved — delegates to core's oidc-proxy builder."""
    return _core_build_oidc_proxy_auth(config.server)
```

- [ ] **Step 3: Add `server: ServerConfig` field to `CollectionConfig`**

Locate the `CollectionConfig` dataclass (line 66) and add at the top of its field list:

```python
@dataclass(frozen=True)
class CollectionConfig:
    # Universal server fields (delegated to fastmcp_pvl_core.ServerConfig).
    server: ServerConfig = field(default_factory=ServerConfig)
    # ...existing domain fields below unchanged
```

- [ ] **Step 4: Populate `server` in `load_config()`**

Locate `load_config()` (line 317) and add at the construction site:

```python
def load_config() -> CollectionConfig:
    return CollectionConfig(
        server=ServerConfig.from_env(_ENV_PREFIX),
        # ...existing domain fields unchanged
    )
```

If existing code in `load_config()` reads fields that now live on `config.server` (e.g., `bearer_token`), update those lookups to `config.server.bearer_token`.

- [ ] **Step 5: Run the full MV test suite**

```bash
cd /mnt/code/markdown-mcp
uv run pytest -x -q
```

Expected: all tests pass. If any auth or config test fails, the shim/composition split is wrong — fix before committing.

- [ ] **Step 6: Run lint + type-check**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: compose ServerConfig from core and delegate auth builders"
```

---

### Task 16: Swap MV's middleware and logging wiring

**Files:**
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/mcp_server.py`
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/cli.py`

- [ ] **Step 1: Replace inline middleware wiring with `wire_middleware_stack`**

In `src/markdown_vault_mcp/mcp_server.py`, locate the middleware block around lines 241–260:

```python
    # --- Middleware stack ---
    mcp.add_middleware(ErrorHandlingMiddleware(include_traceback=True))
    mcp.add_middleware(TimingMiddleware())
    if rich_disabled:
        mcp.add_middleware(StructuredLoggingMiddleware())
    else:
        mcp.add_middleware(LoggingMiddleware())
```

Replace with:

```python
    from fastmcp_pvl_core import wire_middleware_stack

    wire_middleware_stack(mcp)
```

Delete the now-unused imports at the top of the file: `ErrorHandlingMiddleware`, `TimingMiddleware`, `LoggingMiddleware`, `StructuredLoggingMiddleware`, and any local `rich_disabled` computation.

- [ ] **Step 2: Replace logging setup in CLI**

In `src/markdown_vault_mcp/cli.py`, locate the place where logging is configured in `main()` and replace with:

```python
from fastmcp_pvl_core import configure_logging_from_env

configure_logging_from_env(verbose=args.verbose)
```

Delete any local logging-setup helpers now dead.

- [ ] **Step 3: Run tests**

```bash
uv run pytest -x -q
```

Expected: all pass.

- [ ] **Step 4: Lint + type + commit**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
git add -A
git commit -m "refactor: use core wire_middleware_stack and configure_logging_from_env"
```

---

### Task 17: Swap MV's server-factory helpers

**Files:**
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/mcp_server.py`
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/_server_apps.py`

- [ ] **Step 1: Replace `_build_default_instructions` call**

In `mcp_server.py`, find the call to `_build_default_instructions(read_only=...)` and replace with:

```python
from fastmcp_pvl_core import build_instructions

instructions = build_instructions(
    read_only=read_only,
    env_prefix=_ENV_PREFIX,
    domain_line="A searchable markdown document collection.",
)
```

Delete the local `_build_default_instructions` function (line 113).

- [ ] **Step 2: Replace `build_event_store` with core version**

In `mcp_server.py`, find the local `build_event_store` (line 58) and delete it. Replace call sites with:

```python
from fastmcp_pvl_core import build_event_store

event_store = build_event_store(_ENV_PREFIX, config.server)
```

- [ ] **Step 3: Replace `_compute_claude_app_domain` in `_server_apps.py`**

Find the local app-domain function in `_server_apps.py` and delete it. Replace call sites with:

```python
from fastmcp_pvl_core import compute_app_domain

app_domain = compute_app_domain(config.server)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest -x -q
```

Expected: all pass. MCP Apps tests in particular must still pass.

- [ ] **Step 5: Lint + type + commit**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
git add -A
git commit -m "refactor: use core instructions/event_store/app_domain helpers"
```

---

### Task 18: Swap MV's `ArtifactStore` to the core implementation

**Files:**
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/artifacts.py`
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/mcp_server.py`

**Strategy:** MV's `artifacts.py` contains both a generic store (moved to core) and domain glue (`set_collection_store`, `_get_collection_from_store`, `make_artifact_handler` wrapping vault-specific read logic). Keep the domain glue; replace the generic store with core's.

- [ ] **Step 1: Delete the generic store from MV's `artifacts.py`**

In `src/markdown_vault_mcp/artifacts.py`, delete lines 35–125 (the `TokenRecord` dataclass, `ArtifactStore` class, `set_artifact_store`, `get_artifact_store` helpers).

Replace with:

```python
from fastmcp_pvl_core import ArtifactStore, TokenRecord

# Module-level singleton for the artifact store.
_store: ArtifactStore | None = None


def set_artifact_store(store: ArtifactStore | None) -> None:
    global _store
    _store = store


def get_artifact_store() -> ArtifactStore:
    if _store is None:
        raise RuntimeError("ArtifactStore not configured")
    return _store
```

Keep the rest of the file (`set_collection_store`, `_get_collection_from_store`, `make_artifact_handler`) — these are vault-specific.

- [ ] **Step 2: Update `mcp_server.py` artifact route registration**

If `mcp_server.py` manually registered the `/artifacts/{token}` route via `@mcp.custom_route(...)`, replace with:

```python
from fastmcp_pvl_core import ArtifactStore

# In make_server() after `mcp = FastMCP(...)`:
if config.server.transport != "stdio":
    artifact_store = ArtifactStore()
    set_artifact_store(artifact_store)
    ArtifactStore.register_route(mcp, artifact_store)
```

- [ ] **Step 3: Run tests, focusing on artifact tests**

```bash
uv run pytest -x -q tests/test_artifacts.py tests/test_create_download_link.py -v
uv run pytest -x -q
```

Expected: all pass.

- [ ] **Step 4: Lint + type + commit**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
git add -A
git commit -m "refactor: use core ArtifactStore; keep vault-specific handler glue"
```

---

### Task 19: Swap MV's CLI scaffolding

**Files:**
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/cli.py`

- [ ] **Step 1: Replace `_normalise_http_path` with core version**

In `src/markdown_vault_mcp/cli.py`, find `_normalise_http_path` (line 29) and delete it. Replace call sites with:

```python
from fastmcp_pvl_core import normalise_http_path
```

- [ ] **Step 2: Replace `_build_parser`'s generic section with `make_serve_parser`**

Locate `_build_parser` (line 190). The current function likely has:

```python
parser = argparse.ArgumentParser(prog="markdown-vault-mcp", ...)
parser.add_argument("-v", "--verbose", ...)
parser.add_argument("--transport", ...)
# ... plus subcommands
```

Replace the universal arg setup with:

```python
from fastmcp_pvl_core import make_serve_parser

def _build_parser() -> argparse.ArgumentParser:
    parser = make_serve_parser(prog="markdown-vault-mcp")
    subparsers = parser.add_subparsers(dest="cmd")
    # domain subcommands preserved as-is
    index_p = subparsers.add_parser("index", help="Build or rebuild the index")
    # ...existing subcommand setup unchanged
    return parser
```

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: all pass.

- [ ] **Step 4: Smoke-test the CLI manually**

```bash
uv run markdown-vault-mcp --help
uv run markdown-vault-mcp --version
```

Expected: both produce sensible output; `--help` shows the generic args + MV-specific subcommands.

- [ ] **Step 5: Lint + type + commit**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
git add -A
git commit -m "refactor: use core normalise_http_path and make_serve_parser"
```

---

### Task 20: Rewrite `mcp_server.py` into the `make_server()` shape

**Files:**
- Modify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/mcp_server.py`

**Goal:** the `create_server()` function at line 147 currently does all wiring inline. Rewrite as a ~30-line `make_server()` that composes core helpers cleanly. Keep `create_server` as a backwards-compat alias to avoid breaking any external callers.

- [ ] **Step 1: Draft the new `make_server()`**

Replace the body of `create_server()` (line 147 onwards) with:

```python
def make_server(config: CollectionConfig | None = None) -> FastMCP:
    """Build a fully-wired FastMCP server from (optional) config.

    If ``config`` is ``None``, it is loaded from the environment.
    """
    from fastmcp_pvl_core import (
        build_auth,
        build_event_store,
        build_instructions,
        compute_app_domain,
        wire_middleware_stack,
    )

    config = config or load_config()
    read_only = config.git_token is None
    collection = Collection.from_config(config)

    mcp = FastMCP(
        name="markdown-vault-mcp",
        instructions=build_instructions(
            read_only=read_only,
            env_prefix=_ENV_PREFIX,
            domain_line="A searchable markdown document collection.",
        ),
        auth=build_auth(config.server),
        event_store=build_event_store(_ENV_PREFIX, config.server),
    )
    wire_middleware_stack(mcp)

    register_tools(mcp, collection, read_only=read_only)
    register_resources(mcp, collection)
    register_prompts(mcp, collection, read_only=read_only)
    register_apps(
        mcp, collection,
        app_domain=compute_app_domain(config.server),
    )

    if config.server.transport != "stdio":
        from markdown_vault_mcp.artifacts import set_artifact_store

        artifact_store = ArtifactStore()
        set_artifact_store(artifact_store)
        ArtifactStore.register_route(mcp, artifact_store)

    return mcp


def create_server(transport: str = "stdio") -> FastMCP:
    """Backwards-compat wrapper. New callers use :func:`make_server`."""
    # Transport arg preserved for compat; the real value comes from config.
    return make_server()
```

**Notes:**
- The exact shape of `Collection.from_config`, `register_tools`, etc. must match MV's current module layout. Verify the imports at the top of the file include `Collection`, the `register_*` symbols from `_server_tools`/`_server_resources`/`_server_prompts`/`_server_apps`, and `ArtifactStore`.
- `read_only = config.git_token is None` mirrors MV's existing logic. If MV uses a different heuristic, preserve that.

- [ ] **Step 2: Update `__init__.py` to expose `make_server`**

In `src/markdown_vault_mcp/__init__.py`:

```python
from markdown_vault_mcp.mcp_server import create_server, make_server
```

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest -x -q
```

Expected: all tests pass unchanged.

- [ ] **Step 4: Run lint + type-check + smoke test**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run mypy src/
uv run markdown-vault-mcp --help
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: rewrite create_server as make_server() using core helpers"
```

---

### Task 21: Final MV audit, CHANGELOG, and release

**Files:**
- Verify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/config.py`
- Verify: `/mnt/code/markdown-mcp/src/markdown_vault_mcp/mcp_server.py`
- Verify: `/mnt/code/markdown-mcp/CHANGELOG.md` (PSR updates on release)
- Verify: `/mnt/code/markdown-mcp/docs/design.md`

- [ ] **Step 1: Verify nothing generic is left in MV**

Run each command below; each should produce no hits (any match means code that should have moved to core):

```bash
cd /mnt/code/markdown-mcp
# Auth builders should no longer be defined locally
grep -n "^def build_bearer_auth\|^def build_remote_auth\|^def build_oidc_auth\|^def resolve_auth_mode" src/markdown_vault_mcp/config.py
# Middleware imports should be gone from mcp_server.py
grep -n "ErrorHandlingMiddleware\|TimingMiddleware\|LoggingMiddleware\|StructuredLoggingMiddleware" src/markdown_vault_mcp/mcp_server.py
# No local env helpers
grep -n "^def _parse_bool\|^def _parse_list\|^def _parse_scopes" src/markdown_vault_mcp/config.py
```

If any grep returns hits, go back to the corresponding task and finish the removal.

- [ ] **Step 2: Run full MV PR gate locally**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run ruff format --check .
uv run mypy src/
uv run pytest -x -q --cov=markdown_vault_mcp --cov-report=term-missing
```

Expected: all green. Coverage should not regress relative to pre-extraction baseline.

- [ ] **Step 3: Update `docs/design.md` — note the core library dependency**

Add a brief paragraph to the architecture overview:

```markdown
## Shared Infrastructure

Generic FastMCP infrastructure (auth, middleware, logging, server-factory
helpers, artifact store) lives in the `fastmcp-pvl-core` PyPI package.
See the package README and the cross-repo design spec at
`docs/superpowers/specs/2026-04-20-fastmcp-core-and-copier-template-design.md`.
```

- [ ] **Step 4: Push the branch and open a PR**

```bash
git push -u origin feat/adopt-fastmcp-pvl-core
gh pr create \
  --title "refactor: adopt fastmcp-pvl-core 0.1.0" \
  --body "$(cat <<'EOF'
## Summary

Adopt the newly-extracted \`fastmcp-pvl-core\` 0.1.0 library. Replaces
MV's inline auth / middleware / logging / config helpers with imports.
Rewrites \`create_server\` as a ~30-line \`make_server\` that composes
core building blocks.

Design spec: \`docs/superpowers/specs/2026-04-20-fastmcp-core-and-copier-template-design.md\`

## Test plan

- [ ] Full MV test suite passes (~1395 tests)
- [ ] \`uv run markdown-vault-mcp --help\` shows expected args
- [ ] Docker build succeeds
- [ ] Live smoke test: server starts, bearer auth works, OIDC discovery works

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Wait for CI, merge, release**

Once CI is green and review is clean, merge via `gh pr merge --merge`. PSR picks up the `refactor:` conventional commits and produces the next MV minor release automatically.

---

## Completion checklist

- [ ] `fastmcp-pvl-core` 0.1.0 published on PyPI
- [ ] MV branch merged to main with ~1395 tests passing
- [ ] MV release published (PSR auto-release on merge)
- [ ] `docs/design.md` in MV mentions the core dependency
- [ ] No generic infra code remains in MV (config.py, mcp_server.py, artifacts.py, cli.py grep checks clean)

Follow-up plans (separate documents):
- Step 3 — convert `fastmcp-server-template` repo to copier
- Step 4 — bootstrap-replay validation against MV
- Steps 5/6/7 — migrate IG, scholar, kroki
- Step 8 — retire SYNC.md
- Deferred — auto-update GitHub Action (issue to file against template repo)
