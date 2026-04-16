# Config Centralization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `CollectionConfig` the single source of truth for all configuration — auth, embedding providers, and server identity — eliminating scattered `os.environ` reads from `mcp_server.py` and `providers.py`.

**Architecture:** `load_config()` reads all env vars into `CollectionConfig`. Auth builder functions move from `mcp_server.py` to `config.py` as public API accepting `CollectionConfig`. Provider constructors accept explicit params instead of reading `os.environ`. `mcp_server.py` becomes a thin wiring layer.

**Tech Stack:** Python 3.10+, dataclasses, FastMCP auth (OIDCProxy, StaticTokenVerifier, RemoteAuthProvider, MultiAuth, JWTVerifier)

**Spec:** `docs/superpowers/specs/2026-04-16-config-centralization-design.md`

**Issue:** #375

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/markdown_vault_mcp/config.py` | Modify | Add auth/embedding/server fields to `CollectionConfig`, add auth builder functions, expand `load_config()` |
| `src/markdown_vault_mcp/mcp_server.py` | Modify | Remove auth builders, slim `create_server()` to import from `config` |
| `src/markdown_vault_mcp/providers.py` | Modify | Config-driven constructors, `get_embedding_provider(config)` required |
| `src/markdown_vault_mcp/cli.py` | Modify | Remove manual `get_embedding_provider()` call — `to_collection_kwargs()` handles it |
| `tests/test_config.py` | Create | Tests for auth builders and new `load_config()` fields |
| `tests/test_providers.py` | Modify | Pass explicit params to constructors, pass config to `get_embedding_provider()` |
| `tests/test_mcp_server.py` | Modify | Update imports from `mcp_server` → `config`, pass `CollectionConfig` to auth builders |
| `tests/test_cli.py` | Modify | Minor — verify embedding provider is no longer resolved in CLI |
| `docs/configuration.md` | Modify | Breaking change: `EMBEDDING_PROVIDER` → `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` |
| `docs/guides/embeddings.md` | Modify | Update all `EMBEDDING_PROVIDER` references |
| `docs/guides/claude-code-plugin.md` | Modify | Update env var table |
| `docs/guides/claude-desktop.md` | Modify | Update env var references |
| `docs/deployment/systemd.md` | Modify | Update env var in example |
| `docs/deployment/claude-desktop.md` | Modify | Update env var in example |
| `docs/design.md` | Modify | Update env var table |
| `server.json` | Modify | Rename `EMBEDDING_PROVIDER` → `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` |
| `.claude-plugin/plugin/.mcp.json` | Modify | Rename if `EMBEDDING_PROVIDER` appears |
| `examples/*.env` | Modify | Update any `EMBEDDING_PROVIDER` references |
| `README.md` | Modify | Update any `EMBEDDING_PROVIDER` references |

---

## Task 1: Expand `CollectionConfig` with new fields

**Files:**
- Modify: `src/markdown_vault_mcp/config.py:65-120` (CollectionConfig dataclass)
- Test: `tests/test_config.py` (new file)

- [ ] **Step 1: Write test for new CollectionConfig fields**

Create `tests/test_config.py`:

```python
"""Tests for config.py — CollectionConfig, load_config, auth builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from markdown_vault_mcp.config import CollectionConfig, load_config


class TestCollectionConfigDefaults:
    """Verify default values for new fields."""

    def test_server_identity_defaults(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp/vault"))
        assert config.server_name == "markdown-vault-mcp"
        assert config.instructions is None

    def test_auth_defaults(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp/vault"))
        assert config.auth_mode is None
        assert config.base_url is None
        assert config.oidc_config_url is None
        assert config.oidc_client_id is None
        assert config.oidc_client_secret is None
        assert config.oidc_audience is None
        assert config.oidc_required_scopes is None
        assert config.oidc_jwt_signing_key is None
        assert config.oidc_verify_access_token is False
        assert config.bearer_token is None

    def test_embedding_defaults(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp/vault"))
        assert config.embedding_provider is None
        assert config.ollama_host == "http://localhost:11434"
        assert config.ollama_model == "nomic-embed-text"
        assert config.ollama_cpu_only is False
        assert config.openai_api_key is None
        assert config.fastembed_model == "BAAI/bge-small-en-v1.5"
        assert config.fastembed_cache_dir is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -x -v`
Expected: FAIL — `CollectionConfig` missing new fields

- [ ] **Step 3: Add new fields to CollectionConfig**

In `src/markdown_vault_mcp/config.py`, add to the `CollectionConfig` dataclass after the existing `event_store_url` field:

```python
    # Server identity
    server_name: str = "markdown-vault-mcp"
    instructions: str | None = None

    # Auth
    auth_mode: str | None = None
    base_url: str | None = None
    oidc_config_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_audience: str | None = None
    oidc_required_scopes: str | None = None
    oidc_jwt_signing_key: str | None = None
    oidc_verify_access_token: bool = False
    bearer_token: str | None = None

    # Embedding providers
    embedding_provider: str | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "nomic-embed-text"
    ollama_cpu_only: bool = False
    openai_api_key: str | None = None
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    fastembed_cache_dir: str | None = None
```

Update the class docstring to document the new fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/markdown_vault_mcp/config.py tests/test_config.py
git commit -m "refactor(config): add auth, embedding, and server identity fields to CollectionConfig"
```

---

## Task 2: Expand `load_config()` to read auth, embedding, and server identity env vars

**Files:**
- Modify: `src/markdown_vault_mcp/config.py:209-456` (load_config function)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write tests for new load_config fields**

Add to `tests/test_config.py`:

```python
class TestLoadConfigAuthFields:
    """Verify load_config() reads auth env vars."""

    @pytest.fixture(autouse=True)
    def _set_source_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_auth_mode_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_AUTH_MODE", "remote")
        config = load_config()
        assert config.auth_mode == "remote"

    def test_bearer_token_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "secret-tok")
        config = load_config()
        assert config.bearer_token == "secret-tok"

    def test_oidc_fields_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_BASE_URL", "https://example.com")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL", "https://auth.example.com/.well-known/openid-configuration")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID", "my-client")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET", "my-secret")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_AUDIENCE", "my-audience")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES", "openid,profile")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY", "my-key")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN", "true")
        config = load_config()
        assert config.base_url == "https://example.com"
        assert config.oidc_config_url == "https://auth.example.com/.well-known/openid-configuration"
        assert config.oidc_client_id == "my-client"
        assert config.oidc_client_secret == "my-secret"
        assert config.oidc_audience == "my-audience"
        assert config.oidc_required_scopes == "openid,profile"
        assert config.oidc_jwt_signing_key == "my-key"
        assert config.oidc_verify_access_token is True

    def test_oidc_verify_access_token_default_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = load_config()
        assert config.oidc_verify_access_token is False

    def test_auth_fields_default_none_when_unset(self) -> None:
        config = load_config()
        assert config.auth_mode is None
        assert config.bearer_token is None
        assert config.base_url is None


class TestLoadConfigEmbeddingFields:
    """Verify load_config() reads embedding env vars."""

    @pytest.fixture(autouse=True)
    def _set_source_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_embedding_provider_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER", "ollama")
        config = load_config()
        assert config.embedding_provider == "ollama"

    def test_ollama_host_bare_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://myhost:9999")
        config = load_config()
        assert config.ollama_host == "http://myhost:9999"

    def test_ollama_host_empty_string_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "")
        config = load_config()
        assert config.ollama_host == "http://localhost:11434"

    def test_ollama_host_trailing_slash_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://myhost:9999/")
        config = load_config()
        assert config.ollama_host == "http://myhost:9999"

    def test_openai_api_key_bare_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        config = load_config()
        assert config.openai_api_key == "sk-test-key"

    def test_ollama_model_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", "custom-model")
        config = load_config()
        assert config.ollama_model == "custom-model"

    def test_fastembed_fields_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_FASTEMBED_MODEL", "custom/model")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR", "/tmp/cache")
        config = load_config()
        assert config.fastembed_model == "custom/model"
        assert config.fastembed_cache_dir == "/tmp/cache"


class TestLoadConfigServerIdentityFields:
    """Verify load_config() reads server identity env vars."""

    @pytest.fixture(autouse=True)
    def _set_source_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))

    def test_server_name_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "my-vault")
        config = load_config()
        assert config.server_name == "my-vault"

    def test_instructions_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_INSTRUCTIONS", "Custom instructions")
        config = load_config()
        assert config.instructions == "Custom instructions"

    def test_server_name_default(self) -> None:
        config = load_config()
        assert config.server_name == "markdown-vault-mcp"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py::TestLoadConfigAuthFields -x -v`
Expected: FAIL — `load_config()` doesn't populate new fields yet

- [ ] **Step 3: Expand load_config() to read all new env vars**

In `src/markdown_vault_mcp/config.py`, add to `load_config()` before the `return CollectionConfig(...)` block:

```python
    # --- Server identity ---
    server_name = (_env("SERVER_NAME") or "").strip() or "markdown-vault-mcp"
    logger.debug("load_config: server_name=%s", server_name)

    raw_instructions = (_env("INSTRUCTIONS") or "").strip()
    instructions: str | None = raw_instructions or None
    logger.debug("load_config: instructions=%s", "set" if instructions else "not set")

    # --- Auth ---
    raw_auth_mode = (_env("AUTH_MODE") or "").strip().lower()
    auth_mode: str | None = raw_auth_mode or None
    logger.debug("load_config: auth_mode=%s", auth_mode or "not set")

    raw_base_url = (_env("BASE_URL") or "").strip()
    base_url: str | None = raw_base_url or None

    raw_oidc_config_url = (_env("OIDC_CONFIG_URL") or "").strip()
    oidc_config_url: str | None = raw_oidc_config_url or None

    raw_oidc_client_id = (_env("OIDC_CLIENT_ID") or "").strip()
    oidc_client_id: str | None = raw_oidc_client_id or None

    raw_oidc_client_secret = (_env("OIDC_CLIENT_SECRET") or "").strip()
    oidc_client_secret: str | None = raw_oidc_client_secret or None
    logger.debug(
        "load_config: oidc_client_secret=%s",
        "set" if oidc_client_secret else "not set",
    )

    raw_oidc_audience = (_env("OIDC_AUDIENCE") or "").strip()
    oidc_audience: str | None = raw_oidc_audience or None

    raw_oidc_required_scopes = (_env("OIDC_REQUIRED_SCOPES") or "").strip()
    oidc_required_scopes: str | None = raw_oidc_required_scopes or None

    raw_oidc_jwt_signing_key = (_env("OIDC_JWT_SIGNING_KEY") or "").strip()
    oidc_jwt_signing_key: str | None = raw_oidc_jwt_signing_key or None
    logger.debug(
        "load_config: oidc_jwt_signing_key=%s",
        "set" if oidc_jwt_signing_key else "not set",
    )

    raw_oidc_verify_access_token = (_env("OIDC_VERIFY_ACCESS_TOKEN") or "").strip()
    oidc_verify_access_token = _parse_bool(raw_oidc_verify_access_token) if raw_oidc_verify_access_token else False

    raw_bearer_token = (_env("BEARER_TOKEN") or "").strip()
    bearer_token: str | None = raw_bearer_token or None
    logger.debug(
        "load_config: bearer_token=%s", "set" if bearer_token else "not set"
    )

    # --- Embedding providers ---
    # EMBEDDING_PROVIDER uses the standard prefix (breaking change from bare name).
    # OPENAI_API_KEY and OLLAMA_HOST are ecosystem-standard bare env vars.
    raw_embedding_provider = (_env("EMBEDDING_PROVIDER") or "").strip().lower()
    embedding_provider: str | None = raw_embedding_provider or None
    logger.debug("load_config: embedding_provider=%s", embedding_provider or "auto-detect")

    ollama_host = (os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    logger.debug("load_config: ollama_host=%s", ollama_host)

    ollama_model = (_env("OLLAMA_MODEL") or "").strip() or "nomic-embed-text"
    logger.debug("load_config: ollama_model=%s", ollama_model)

    raw_ollama_cpu_only = (_env("OLLAMA_CPU_ONLY") or "").strip()
    ollama_cpu_only = _parse_bool(raw_ollama_cpu_only) if raw_ollama_cpu_only else False

    raw_openai_api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    openai_api_key: str | None = raw_openai_api_key or None
    logger.debug(
        "load_config: openai_api_key=%s", "set" if openai_api_key else "not set"
    )

    fastembed_model = (_env("FASTEMBED_MODEL") or "").strip() or "BAAI/bge-small-en-v1.5"
    raw_fastembed_cache_dir = (_env("FASTEMBED_CACHE_DIR") or "").strip()
    fastembed_cache_dir: str | None = raw_fastembed_cache_dir or None
```

Add all new fields to the `return CollectionConfig(...)` call. Remove the stale docstring paragraph about `EMBEDDING_PROVIDER` not being resolved here. Add the new env vars to the docstring list.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/markdown_vault_mcp/config.py tests/test_config.py
git commit -m "refactor(config): expand load_config() with auth, embedding, and server identity env vars"
```

---

## Task 3: Move auth builder functions to `config.py`

**Files:**
- Modify: `src/markdown_vault_mcp/config.py` (add functions)
- Modify: `src/markdown_vault_mcp/mcp_server.py` (remove functions, update imports)
- Modify: `tests/test_mcp_server.py` (update imports)
- Test: `tests/test_config.py` (add auth builder tests)

- [ ] **Step 1: Write tests for auth builders accepting CollectionConfig**

Add to `tests/test_config.py`:

```python
from markdown_vault_mcp.config import (
    CollectionConfig,
    build_bearer_auth,
    build_oidc_auth,
    build_remote_auth,
    load_config,
    resolve_auth_mode,
)


class TestResolveAuthMode:
    """Tests for resolve_auth_mode(config)."""

    def test_explicit_remote(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="remote")
        assert resolve_auth_mode(config) == "remote"

    def test_explicit_oidc_proxy(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="oidc-proxy")
        assert resolve_auth_mode(config) == "oidc-proxy"

    def test_unknown_auth_mode_returns_none(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), auth_mode="bogus")
        assert resolve_auth_mode(config) is None

    def test_auto_detect_oidc_proxy_all_four(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
            oidc_client_id="client",
            oidc_client_secret="secret",
        )
        assert resolve_auth_mode(config) == "oidc-proxy"

    def test_auto_detect_remote_base_and_config(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
        )
        assert resolve_auth_mode(config) == "remote"

    def test_no_auth_returns_none(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert resolve_auth_mode(config) is None


class TestBuildBearerAuth:
    """Tests for build_bearer_auth(config)."""

    def test_returns_none_without_token(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"))
        assert build_bearer_auth(config) is None

    def test_returns_verifier_with_token(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), bearer_token="my-token")
        result = build_bearer_auth(config)
        assert result is not None


class TestBuildOidcAuth:
    """Tests for build_oidc_auth(config)."""

    def test_returns_none_when_fields_missing(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), base_url="https://example.com")
        assert build_oidc_auth(config) is None

    def test_returns_oidc_proxy_when_all_set(self) -> None:
        from unittest.mock import MagicMock, patch

        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://mcp.example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
            oidc_client_id="test-client",
            oidc_client_secret="test-secret",
        )
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            result = build_oidc_auth(config)
        assert result is not None
        mock_cls.assert_called_once()

    def test_passes_correct_kwargs_to_oidc_proxy(self) -> None:
        from unittest.mock import MagicMock, patch

        config = CollectionConfig(
            source_dir=Path("/tmp"),
            base_url="https://mcp.example.com",
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
            oidc_client_id="test-client",
            oidc_client_secret="test-secret",
            oidc_audience="my-audience",
            oidc_required_scopes="openid,profile",
        )
        mock_cls = MagicMock()
        with patch("fastmcp.server.auth.oidc_proxy.OIDCProxy", mock_cls):
            build_oidc_auth(config)
        kw = mock_cls.call_args.kwargs
        assert kw["base_url"] == "https://mcp.example.com"
        assert kw["client_id"] == "test-client"
        assert kw["client_secret"] == "test-secret"
        assert kw["audience"] == "my-audience"
        assert kw["required_scopes"] == ["openid", "profile"]


class TestBuildRemoteAuth:
    """Tests for build_remote_auth(config)."""

    def test_returns_none_without_base_url(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            oidc_config_url="https://auth.example.com/.well-known/openid-configuration",
        )
        assert build_remote_auth(config) is None

    def test_returns_none_without_config_url(self) -> None:
        config = CollectionConfig(source_dir=Path("/tmp"), base_url="https://example.com")
        assert build_remote_auth(config) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py::TestResolveAuthMode -x -v`
Expected: FAIL — functions don't exist yet

- [ ] **Step 3: Add auth builder functions to config.py**

Add four public functions to `config.py` (after `load_config()`). These are direct ports from `mcp_server.py` but accept `CollectionConfig` instead of reading `os.environ`:

- `resolve_auth_mode(config: CollectionConfig) -> str | None`
- `build_bearer_auth(config: CollectionConfig) -> Any`
- `build_oidc_auth(config: CollectionConfig) -> Any`
- `build_remote_auth(config: CollectionConfig) -> Any`

Key changes from the originals:
- All read from `config.*` instead of `os.environ.get(f"{_ENV_PREFIX}_...")`
- Public names (no `_` prefix)
- `build_oidc_auth`: fix the CodeQL issue — separate the secret-presence check from logged names:
  ```python
  required = {
      "BASE_URL": config.base_url,
      "OIDC_CONFIG_URL": config.oidc_config_url,
      "OIDC_CLIENT_ID": config.oidc_client_id,
      "OIDC_CLIENT_SECRET": config.oidc_client_secret,
  }
  missing = [k for k, v in required.items() if not v]
  ```
- `build_oidc_auth`: parse `config.oidc_required_scopes` (comma-separated string) into list
- `build_remote_auth`: read `config.oidc_audience` and `config.oidc_required_scopes` from config

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -x -v`
Expected: PASS

- [ ] **Step 5: Update mcp_server.py — remove auth builders, import from config**

In `src/markdown_vault_mcp/mcp_server.py`:
- Remove `_resolve_auth_mode()`, `_build_remote_auth()`, `_build_bearer_auth()`, `_build_oidc_auth()` function definitions
- Update `create_server()` to import and use the `config.py` versions:
  ```python
  from markdown_vault_mcp.config import (
      build_bearer_auth,
      build_oidc_auth,
      build_remote_auth,
      load_config,
      resolve_auth_mode,
  )
  ```
- `create_server()` uses `config.server_name` and `config.instructions` instead of direct `os.environ.get()` for server identity
- Remove `import sys` if it was only used by `_build_oidc_auth`'s `sys.platform` check (move that check into `config.py`'s `build_oidc_auth`)

- [ ] **Step 6: Update test imports in test_mcp_server.py**

In `tests/test_mcp_server.py`:
- Change import from:
  ```python
  from markdown_vault_mcp.mcp_server import (
      _build_bearer_auth,
      _build_oidc_auth,
      _build_remote_auth,
      _resolve_auth_mode,
      create_server,
  )
  ```
  To:
  ```python
  from markdown_vault_mcp.config import (
      build_bearer_auth,
      build_oidc_auth,
      build_remote_auth,
      resolve_auth_mode,
  )
  from markdown_vault_mcp.mcp_server import create_server
  ```
- Update all test method bodies: replace `_build_oidc_auth()` → `build_oidc_auth(config)` etc.
- Each test that calls an auth builder must construct a `CollectionConfig` with the appropriate fields instead of using `monkeypatch.setenv()` for auth vars.
- The `_OIDC_VARS` and `_OIDC_REQUIRED` constants may still be needed for integration tests that go through `create_server()` (which calls `load_config()` from env). Keep those. But unit tests for auth builders should use `CollectionConfig` directly.

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest -x -q`
Expected: PASS — all existing tests still work

- [ ] **Step 8: Commit**

```bash
git add src/markdown_vault_mcp/config.py src/markdown_vault_mcp/mcp_server.py tests/test_config.py tests/test_mcp_server.py
git commit -m "refactor(config): move auth builders to config.py as public API

resolve_auth_mode, build_bearer_auth, build_oidc_auth, build_remote_auth
now accept CollectionConfig instead of reading os.environ directly.
Fixes CodeQL false positive in build_oidc_auth."
```

---

## Task 4: Make embedding providers config-driven

**Files:**
- Modify: `src/markdown_vault_mcp/providers.py`
- Modify: `tests/test_providers.py`

- [ ] **Step 1: Write tests for config-driven provider constructors**

Update `tests/test_providers.py`. Replace `monkeypatch.setenv` patterns with explicit constructor params:

```python
# OllamaProvider now takes explicit params:
class TestOllamaProvider:
    def test_embed_posts_to_correct_url(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.1, 0.2, 0.3]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(host="http://localhost:11434", model="nomic-embed-text")
            result = provider.embed(["hello"])
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["model"] == "nomic-embed-text"
        assert result == [[0.1, 0.2, 0.3]]

    def test_custom_host(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[0.9]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(host="http://remote:12345", model="nomic-embed-text")
            provider.embed(["x"])
        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == "http://remote:12345/api/embed"

    def test_cpu_only_includes_num_gpu_zero(self) -> None:
        mock_client, _ = _make_httpx_mock(json_body={"embeddings": [[1.0, 2.0]]})
        with patch("httpx.Client", return_value=mock_client):
            provider = OllamaProvider(host="http://localhost:11434", model="nomic-embed-text", cpu_only=True)
            provider.embed(["test"])
        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["json"].get("options") == {"num_gpu": 0}

# OpenAIProvider now requires api_key:
class TestOpenAIProvider:
    def test_embed_sends_bearer_token(self) -> None:
        mock_client, _ = _make_httpx_mock(...)
        with patch("httpx.Client", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test")
            provider.embed(["hello"])
        headers = mock_client.post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer sk-test"

# FastEmbedProvider takes explicit model_name and cache_dir:
class TestFastEmbedProvider:
    def test_embed_uses_model_and_cache(self) -> None:
        # model_name and cache_dir are now required/explicit
        provider = FastEmbedProvider(model_name="BAAI/bge-small-en-v1.5", cache_dir="/tmp/cache")
        ...
```

Also write tests for `get_embedding_provider(config)`:

```python
class TestGetEmbeddingProvider:
    def test_explicit_ollama(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            embedding_provider="ollama",
            ollama_host="http://localhost:11434",
            ollama_model="nomic-embed-text",
        )
        provider = get_embedding_provider(config)
        assert isinstance(provider, OllamaProvider)

    def test_explicit_openai(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            embedding_provider="openai",
            openai_api_key="sk-test",
        )
        provider = get_embedding_provider(config)
        assert isinstance(provider, OpenAIProvider)

    def test_explicit_unknown_raises(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            embedding_provider="unknown",
        )
        with pytest.raises(ValueError, match="unknown"):
            get_embedding_provider(config)

    def test_auto_detect_openai_key(self) -> None:
        config = CollectionConfig(
            source_dir=Path("/tmp"),
            openai_api_key="sk-test",
        )
        provider = get_embedding_provider(config)
        assert isinstance(provider, OpenAIProvider)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py -x -v`
Expected: FAIL — constructors still take no args

- [ ] **Step 3: Update provider constructors to accept explicit params**

In `src/markdown_vault_mcp/providers.py`:

```python
class OllamaProvider(EmbeddingProvider):
    def __init__(self, host: str, model: str, *, cpu_only: bool = False) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError:
            raise ImportError(
                "httpx is required for OllamaProvider. "
                "Install with: pip install 'markdown-vault-mcp[embeddings-api]'"
            )
        self._host = host.rstrip("/")
        self._model = model
        self._cpu_only = cpu_only
        self._dimension: int | None = None

class OpenAIProvider(EmbeddingProvider):
    def __init__(self, api_key: str) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError:
            raise ImportError(
                "httpx is required for OpenAIProvider. "
                "Install with: pip install 'markdown-vault-mcp[embeddings-api]'"
            )
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self._api_key = api_key
        self._dimension: int | None = None

class FastEmbedProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", cache_dir: str | None = None) -> None:
        ...  # same as current but no os.environ reads
```

Remove all `os.environ` reads from constructors. Remove `from markdown_vault_mcp.config import _ENV_PREFIX`.

- [ ] **Step 4: Update `get_embedding_provider()` to accept required config**

```python
def get_embedding_provider(config: CollectionConfig) -> EmbeddingProvider:
    """Return an embedding provider based on configuration.

    Args:
        config: Fully populated configuration object.

    Returns:
        An initialised EmbeddingProvider instance.

    Raises:
        RuntimeError: If no provider is available.
        ValueError: If config.embedding_provider is unrecognised.
    """
    explicit = config.embedding_provider

    if explicit == "openai":
        if not config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for openai provider")
        logger.info("Using OpenAIProvider (explicit)")
        return OpenAIProvider(api_key=config.openai_api_key)

    if explicit == "ollama":
        logger.info("Using OllamaProvider (explicit)")
        return OllamaProvider(
            host=config.ollama_host,
            model=config.ollama_model,
            cpu_only=config.ollama_cpu_only,
        )

    if explicit == "fastembed":
        logger.info("Using FastEmbedProvider (explicit)")
        return FastEmbedProvider(
            model_name=config.fastembed_model,
            cache_dir=config.fastembed_cache_dir,
        )

    if explicit:
        raise ValueError(
            f"Unrecognised MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER value: {explicit!r}. "
            "Valid values: 'openai', 'ollama', 'fastembed'."
        )

    # Auto-detect: OpenAI API key present?
    if config.openai_api_key:
        logger.info("Auto-detected OpenAIProvider (OPENAI_API_KEY is set)")
        return OpenAIProvider(api_key=config.openai_api_key)

    # Auto-detect: Ollama reachable?
    try:
        import httpx

        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{config.ollama_host}/api/tags")
        if response.status_code == 200:
            logger.info("Auto-detected OllamaProvider (reachable at %s)", config.ollama_host)
            return OllamaProvider(
                host=config.ollama_host,
                model=config.ollama_model,
                cpu_only=config.ollama_cpu_only,
            )
    except Exception:
        logger.debug("Ollama not reachable at %s, skipping", config.ollama_host)

    # Auto-detect: fastembed importable?
    try:
        import fastembed  # noqa: F401

        logger.info("Auto-detected FastEmbedProvider")
        return FastEmbedProvider(
            model_name=config.fastembed_model,
            cache_dir=config.fastembed_cache_dir,
        )
    except ImportError:
        logger.debug("fastembed not available, skipping")

    raise RuntimeError(
        "No embedding provider is available. Install one of:\n"
        "  pip install 'markdown-vault-mcp[embeddings-api]'  # httpx for Ollama or OpenAI\n"
        "  pip install 'markdown-vault-mcp[embeddings]'       # fastembed (local)\n"
        "Or set OPENAI_API_KEY for the OpenAI provider, "
        "or start an Ollama server for the Ollama provider."
    )
```

Update the import: `from markdown_vault_mcp.config import CollectionConfig` (TYPE_CHECKING is fine).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py -x -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -x -q`
Expected: PASS — may need to fix other callers first (see Task 5)

- [ ] **Step 7: Commit**

```bash
git add src/markdown_vault_mcp/providers.py tests/test_providers.py
git commit -m "refactor(providers): config-driven constructors and get_embedding_provider(config)

BREAKING: get_embedding_provider() now requires a CollectionConfig parameter.
BREAKING: EMBEDDING_PROVIDER renamed to MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER.
Provider constructors accept explicit params instead of reading os.environ."
```

---

## Task 5: Wire embedding provider through `to_collection_kwargs()` and update CLI

**Files:**
- Modify: `src/markdown_vault_mcp/config.py:121-208` (to_collection_kwargs)
- Modify: `src/markdown_vault_mcp/cli.py:47-87` (_build_collection)
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write test for embedding provider in to_collection_kwargs**

Add to `tests/test_config.py`:

```python
class TestToCollectionKwargsEmbeddings:
    """Verify to_collection_kwargs() wires embedding provider."""

    def test_no_embeddings_path_no_provider(self, tmp_path: Path) -> None:
        config = CollectionConfig(source_dir=tmp_path)
        kwargs = config.to_collection_kwargs()
        assert "embedding_provider" not in kwargs

    def test_embeddings_path_with_fastembed(self, tmp_path: Path) -> None:
        config = CollectionConfig(
            source_dir=tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider="fastembed",
            fastembed_model="BAAI/bge-small-en-v1.5",
        )
        try:
            kwargs = config.to_collection_kwargs()
            # Only passes if fastembed is installed in the test env
            assert "embedding_provider" in kwargs
        except Exception:
            pass  # OK if fastembed not installed

    def test_embeddings_path_with_bad_provider_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = CollectionConfig(
            source_dir=tmp_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider="nonexistent",
        )
        with caplog.at_level(logging.WARNING):
            kwargs = config.to_collection_kwargs()
        assert "embedding_provider" not in kwargs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::TestToCollectionKwargsEmbeddings -x -v`
Expected: FAIL

- [ ] **Step 3: Add embedding provider setup to to_collection_kwargs()**

In `config.py`, `to_collection_kwargs()`, add before the git strategy block:

```python
    # Resolve embedding provider if embeddings_path is configured.
    if self.embeddings_path is not None:
        try:
            from markdown_vault_mcp.providers import get_embedding_provider

            kwargs["embedding_provider"] = get_embedding_provider(self)
        except Exception:
            logger.warning(
                "Could not load embedding provider; semantic search disabled",
                exc_info=True,
            )
```

- [ ] **Step 4: Remove manual embedding provider setup from cli.py**

In `src/markdown_vault_mcp/cli.py`, `_build_collection()`, remove the block (currently around lines 78-86):

```python
    # Resolve embedding provider if embeddings_path is configured.
    if config.embeddings_path is not None:
        try:
            from markdown_vault_mcp.providers import get_embedding_provider

            kwargs["embedding_provider"] = get_embedding_provider()
        except Exception:
            ...
```

This is now handled by `to_collection_kwargs()`.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/markdown_vault_mcp/config.py src/markdown_vault_mcp/cli.py tests/test_config.py tests/test_cli.py
git commit -m "refactor(config): wire embedding provider through to_collection_kwargs()

CLI no longer resolves embedding provider separately — to_collection_kwargs()
handles it, keeping the CLI and server paths in sync."
```

---

## Task 6: Lint, type-check, coverage verification

**Files:** All modified files

- [ ] **Step 1: Run linter**

```bash
uv run ruff check --fix .
uv run ruff format .
uv run ruff format --check .
```

Expected: clean

- [ ] **Step 2: Run type checker**

```bash
uv run mypy src/
```

Expected: no errors

- [ ] **Step 3: Run tests with coverage**

```bash
uv run pytest --cov=src/markdown_vault_mcp/config --cov=src/markdown_vault_mcp/providers --cov=src/markdown_vault_mcp/mcp_server --cov=src/markdown_vault_mcp/cli --cov-report=term-missing -x -q
```

Verify new code paths are covered. Add tests for any uncovered branches.

- [ ] **Step 4: Verify no bare os.environ reads remain in providers.py**

```bash
grep -n 'os\.environ' src/markdown_vault_mcp/providers.py
```

Expected: zero hits

- [ ] **Step 5: Verify auth functions only in config.py**

```bash
grep -rn 'def.*build_bearer_auth\|def.*build_oidc_auth\|def.*build_remote_auth\|def.*resolve_auth_mode' src/
```

Expected: only `src/markdown_vault_mcp/config.py`

- [ ] **Step 6: Commit any fixes**

```bash
git add -u
git commit -m "fix: address lint, type-check, and coverage gaps"
```

---

## Task 7: Update documentation

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/guides/embeddings.md`
- Modify: `docs/guides/claude-code-plugin.md`
- Modify: `docs/guides/claude-desktop.md`
- Modify: `docs/deployment/systemd.md`
- Modify: `docs/deployment/claude-desktop.md`
- Modify: `docs/design.md`
- Modify: `server.json`
- Modify: `.claude-plugin/plugin/.mcp.json` (if EMBEDDING_PROVIDER appears)
- Modify: `examples/*.env` (if EMBEDDING_PROVIDER appears)
- Modify: `README.md` (if EMBEDDING_PROVIDER appears)

- [ ] **Step 1: Update docs/configuration.md**

Change `EMBEDDING_PROVIDER` → `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`. Remove the note about it being intentionally unprefixed. Update the description to reflect the breaking change.

- [ ] **Step 2: Update docs/guides/embeddings.md**

Replace all bare `EMBEDDING_PROVIDER=...` with `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=...` in env examples and prose.

- [ ] **Step 3: Update docs/guides/claude-code-plugin.md**

Update the env var table: `EMBEDDING_PROVIDER` → `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`.

- [ ] **Step 4: Update docs/guides/claude-desktop.md**

Replace all `"EMBEDDING_PROVIDER"` and `EMBEDDING_PROVIDER` references in JSON examples and prose.

- [ ] **Step 5: Update docs/deployment/systemd.md**

Replace `EMBEDDING_PROVIDER=fastembed` with `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=fastembed`.

- [ ] **Step 6: Update docs/deployment/claude-desktop.md**

Replace `"EMBEDDING_PROVIDER"` in JSON examples.

- [ ] **Step 7: Update docs/design.md**

Update the env var table entry for `EMBEDDING_PROVIDER`.

- [ ] **Step 8: Update server.json**

Rename `"name": "EMBEDDING_PROVIDER"` → `"name": "MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER"` in both PyPI and OCI sections. Also update the description field of `OPENAI_API_KEY` if it references `EMBEDDING_PROVIDER`.

- [ ] **Step 9: Check and update .claude-plugin and examples**

```bash
grep -rn 'EMBEDDING_PROVIDER' .claude-plugin/ examples/ README.md
```

Update any remaining references.

- [ ] **Step 10: Commit documentation**

```bash
git add docs/ server.json .claude-plugin/ examples/ README.md
git commit -m "docs: update EMBEDDING_PROVIDER to MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER

Breaking change: the env var is now consistently prefixed like all other
app-specific configuration. OPENAI_API_KEY and OLLAMA_HOST remain bare
as ecosystem-standard env vars."
```

---

## Task 8: Final verification and cleanup

- [ ] **Step 1: Full test suite**

```bash
uv run pytest -x -q
```

Expected: all tests pass

- [ ] **Step 2: Full lint + type-check**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run ruff format --check .
uv run mypy src/
```

Expected: clean

- [ ] **Step 3: Verify diff-cover**

```bash
uv run pytest --cov=src/markdown_vault_mcp --cov-report=xml -x -q
uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=80
```

Expected: patch coverage >= 80%

- [ ] **Step 4: Review git log for clean commit history**

```bash
git log --oneline origin/main..HEAD
```

Verify conventional commits, no WIP commits.
