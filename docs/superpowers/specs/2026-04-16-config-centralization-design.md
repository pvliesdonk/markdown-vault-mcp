# Config Centralization Design

**Issue:** #375
**Date:** 2026-04-16

## Problem

Configuration is scattered across three modules:

- **`mcp_server.py`** (~200 lines): auth builder functions (`_resolve_auth_mode`, `_build_bearer_auth`, `_build_oidc_auth`, `_build_remote_auth`) read `os.environ` directly with `f"{_ENV_PREFIX}_..."` patterns
- **`providers.py`**: `get_embedding_provider()` takes no args and reads `os.environ` directly; provider constructors (`OllamaProvider`, `OpenAIProvider`, `FastEmbedProvider`) also read `os.environ` internally
- **`config.py`**: `CollectionConfig` covers collection/git/attachment settings but has no auth or embedding fields; `load_config()` explicitly says it does not resolve `EMBEDDING_PROVIDER`

This makes configuration hard to trace, hard to test (requires monkeypatching env vars), and couples modules to `os.environ` instead of to a config object.

## Design

### `CollectionConfig` becomes the single config object

New fields added to the dataclass:

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

### `load_config()` reads everything

All env vars — collection, git, auth, embedding, server identity — are read in `load_config()`. The stale docstring claiming `EMBEDDING_PROVIDER` is not resolved here is removed.

### Env var conventions

| Variable | Read via | Rationale |
|----------|----------|-----------|
| `OPENAI_API_KEY` | `os.environ.get()` | Ecosystem standard (OpenAI SDK) |
| `OLLAMA_HOST` | `os.environ.get()` | Ecosystem standard (Ollama) |
| `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` | `_env()` | Our var — **breaking change** from bare `EMBEDDING_PROVIDER` |
| All other app-specific vars | `_env()` | Consistent prefix convention |

The `OLLAMA_HOST` empty-string bug is fixed: `(os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")`.

### Auth builders move to `config.py` as public API

Functions are renamed (drop `_` prefix) since they're cross-module API:

| Old (in `mcp_server.py`) | New (in `config.py`) |
|--------------------------|----------------------|
| `_resolve_auth_mode()` | `resolve_auth_mode(config: CollectionConfig)` |
| `_build_bearer_auth()` | `build_bearer_auth(config: CollectionConfig)` |
| `_build_oidc_auth()` | `build_oidc_auth(config: CollectionConfig)` |
| `_build_remote_auth()` | `build_remote_auth(config: CollectionConfig)` |

All functions accept `CollectionConfig` instead of reading `os.environ`. The CodeQL finding in `build_oidc_auth` is fixed by separating the secret-presence check from the logged variable names:

```python
required = {
    "BASE_URL": config.base_url,
    "OIDC_CONFIG_URL": config.oidc_config_url,
    "OIDC_CLIENT_ID": config.oidc_client_id,
    "OIDC_CLIENT_SECRET": config.oidc_client_secret,
}
missing = [k for k, v in required.items() if not v]
```

### `mcp_server.py` becomes thin

`create_server()` calls `load_config()` then delegates:

```python
config = load_config()
bearer_auth = build_bearer_auth(config)
oidc_mode = resolve_auth_mode(config)
oidc_auth = build_remote_auth(config) if oidc_mode == "remote" else ...
# ... compose auth (~15 lines total, down from ~200)
```

Server identity (`server_name`, `instructions`) comes from `config` instead of direct `os.environ.get()`.

### Embedding providers: fully config-driven

**`get_embedding_provider(config: CollectionConfig) -> EmbeddingProvider`**
- `config` parameter is required (no `None` default, no backward-compat fallback)
- Uses `config.embedding_provider` for explicit selection
- Uses `config.openai_api_key`, `config.ollama_host` etc. for auto-detection
- No `os.environ` reads

**Provider constructors accept explicit params:**

```python
class OllamaProvider(EmbeddingProvider):
    def __init__(self, host: str, model: str, *, cpu_only: bool = False) -> None: ...

class OpenAIProvider(EmbeddingProvider):
    def __init__(self, api_key: str) -> None: ...

class FastEmbedProvider(EmbeddingProvider):
    def __init__(self, model_name: str, cache_dir: str | None = None) -> None: ...
```

No `os.environ` reads in constructors.

### `to_collection_kwargs()` wires embedding provider

The method already creates the `GitWriteStrategy`; it now also creates the embedding provider:

```python
def to_collection_kwargs(self) -> dict[str, Any]:
    # ... existing git strategy wiring ...
    if self.embeddings_path:
        try:
            from markdown_vault_mcp.providers import get_embedding_provider
            kwargs["embedding_provider"] = get_embedding_provider(self)
        except Exception:
            logger.warning("Failed to initialize embedding provider", exc_info=True)
    return kwargs
```

## Files Changed

| File | Change |
|------|--------|
| `src/markdown_vault_mcp/config.py` | Add auth/embedding/server fields to `CollectionConfig`, add auth builder functions, expand `load_config()` |
| `src/markdown_vault_mcp/mcp_server.py` | Remove auth builders, slim `create_server()` to delegate to `config.py` |
| `src/markdown_vault_mcp/providers.py` | `get_embedding_provider(config)` required param, constructors take explicit params, remove all `os.environ` reads |
| `src/markdown_vault_mcp/cli.py` | Minor — `_build_collection` already uses `load_config()` + `to_collection_kwargs()` |
| `tests/test_mcp_server.py` | Update imports, pass `CollectionConfig` to auth builders |
| `tests/test_providers.py` | Pass explicit params to constructors and `get_embedding_provider(config)` |
| `tests/test_cli.py` | Minimal changes if any |

## Documentation Updates

All documentation must be updated in the same PR:

- **`docs/configuration.md`** — breaking change: `EMBEDDING_PROVIDER` → `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`; add all new env vars
- **`README.md`** — configuration section env var references
- **`examples/`** — env example files for changed var names
- **`server.json`** / `.claude-plugin/plugin/.mcp.json` — `environmentVariables` entries if names change
- **Inline docstrings** — all moved/changed public functions
- **`load_config()` docstring** — remove stale "EMBEDDING_PROVIDER not resolved here" claim, add embedding vars to the env var list

## Verification

1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check --fix . && uv run ruff format . && uv run ruff format --check .` — lint clean
3. `uv run mypy src/` — no type errors
4. `uv run pytest --cov=src/markdown_vault_mcp/config --cov=src/markdown_vault_mcp/providers --cov=src/markdown_vault_mcp/mcp_server --cov-report=term-missing` — patch coverage >= 80%
5. Grep for bare `os.environ` in `providers.py` — should find zero hits
6. Grep for `_build_bearer_auth\|_build_oidc_auth\|_build_remote_auth\|_resolve_auth_mode` — should only appear in `config.py` (as public names without `_` prefix)
