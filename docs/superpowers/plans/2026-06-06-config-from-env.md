# Config `from_env` + frozen + validation hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish #579 by converting `config.py`'s 526-line `load_config()` into per-section `VaultConfig.from_env()` classmethods on frozen sub-configs (PR C1, behaviour-neutral), then adopt pvl-core 3.x `env_int`/`env_float` + harden validation (PR C2, behaviour-changing).

**Architecture:** Each sub-config dataclass in `config_sections/` gains a `from_env(prefix)` classmethod that reads its own env vars; `VaultConfig.from_env(prefix)` reads the top-level fields, delegates to each sub-config's `from_env`, and composes `ServerConfig.from_env`. `load_config()` is retired; the 3 prod callsites move to `VaultConfig.from_env()`. All domain dataclasses become `frozen=True`.

**Tech stack:** Python 3.12, `uv`, `ruff`, `mypy`, `pytest`, `fastmcp-pvl-core==3.2.0` (provides `env`, `parse_bool`, `parse_list`, `env_int`, `env_float`, `ConfigurationError`, `ServerConfig.from_env`).

**Two PRs, executed back-to-back (user: "split but do not defer"):**
- **PR C1 — Closes #579.** Pure structural relocation. Parsing logic moves *verbatim* (same `ValueError`s, same clamp/reset, same bare-vs-prefixed OpenAI/Ollama reads). Existing `tests/test_config.py` must pass **unchanged**; add per-section `from_env` tests. config.py drops < 400 LOC.
- **PR C2 — Closes a new issue (file during C2).** Adopt `env_int`/`env_float`; harden validation. Behaviour changes (documented per item); tests updated.

**Branch:** `refactor/config-from-env-3x` (already cut off `main` 179f632). C2 continues on a branch off C1's merge.

---

## Behaviour-neutrality ground truth (verified — do not deviate in C1)

- `fastmcp_pvl_core.ConfigurationError` is **NOT** a `ValueError` (direct `Exception` subclass). `env_int(strict=True)` raises `ConfigurationError`. The `search.*` fields raise `ValueError` today and `tests/test_config.py` asserts `pytest.raises(ValueError)`. **C1 keeps the explicit `int()`/`float()` + `raise ValueError` for `search.*`** — do NOT use `env_int(strict=True)` in C1 (that is a C2 change that flips the exception type).
- Per-field parse/validation semantics (from `load_config`, reproduce exactly in C1):
  | env suffix | parse | bounds today | default |
  |---|---|---|---|
  | `GIT_PUSH_DELAY_S` | `float()` | invalid → warn+default (no negative check) | 30.0 |
  | `GIT_PULL_INTERVAL_S` | `int()` | invalid → warn+600; **negative → warn+clamp to 0** | 600 |
  | `FILE_WATCHER_DEBOUNCE_S` | `float()` | invalid → warn+2.0; **`<= 0` → warn+reset 2.0** | 2.0 |
  | `MAX_ATTACHMENT_SIZE_MB` | `float()` | invalid → warn+1.0; **negative → warn+reset 1.0** (0 allowed = disable) | 1.0 |
  | `MAX_NOTE_READ_BYTES` | `int()` | invalid → warn+262144; **negative → warn+reset** (0 allowed = disable) | 262144 |
  | `CHUNKS_PER_FILE` | `int()` | invalid → **raise ValueError**; `< 1` → **raise ValueError** | 2 |
  | `SNIPPET_WORDS` | `int()` | invalid/`< 0` → **raise ValueError** | 200 |
  | `LENGTH_DOWNWEIGHT_ALPHA` | `float()` | invalid/`< 0` → **raise ValueError** | 0.25 |
  | `MAX_CHUNK_WORDS` | `int()` | invalid/`< 1` → **raise ValueError** | 400 |
  | `TRANSFER_TTL_DEFAULT_S` / `TTL_MAX_S` / `MAX_UPLOAD_BYTES` | `_parse_int_env` | invalid → warn+default; bounds enforced by `TransferConfig.__post_init__` (raises) | 3600 / 86400 / 104857600 |
- **Bare (un-prefixed) env reads** that C1 must preserve in `EmbeddingsConfig.from_env`:
  - `OLLAMA_HOST`: `os.environ.get("OLLAMA_HOST")` (bare only), then `or default`, then `.rstrip("/")`.
  - `OPENAI_API_KEY`: `os.environ.get("OPENAI_API_KEY")` (bare only).
  - `OPENAI_BASE_URL`: `_env("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")` (prefixed wins, bare fallback), then `or default`, then `.rstrip("/")`.
  - `OPENAI_EMBEDDING_MODEL`: `_env("OPENAI_EMBEDDING_MODEL") or os.environ.get("OPENAI_EMBEDDING_MODEL")` (prefixed wins, bare fallback).
- **Frozen + `__post_init__`:** `frozen=True` dataclasses cannot assign `self.x = ...` in `__post_init__`. `EmbeddingsConfig.__post_init__` normalizes `ollama_host` by assignment → must switch to `object.__setattr__(self, "ollama_host", ...)`. `TransferConfig.__post_init__` only raises (no assignment) → frozen-safe as-is.
- **Shared helpers:** the sub-config `from_env` methods (in `config_sections/`) need an env reader + the warn-fallback int parser. Create `config_sections/_helpers.py` (imports only `fastmcp_pvl_core` + stdlib — no import of `config.py`, so no cycle). `config.py` imports from it too.

---

## File structure

- **Create** `src/markdown_vault_mcp/config_sections/_helpers.py` — `env(prefix, name, default=None)` (thin wrapper over `fastmcp_pvl_core.env`), `parse_int_env(prefix, name, default)`, `parse_float_env(prefix, name, default)` (warn-and-default float). C1: relocate `_parse_int_env`; add the float variant matching the inline `GIT_PUSH_DELAY_S` pattern.
- **Modify** `config_sections/{git,indexing,embeddings,search,sync,content,transfer}.py` — add `@classmethod from_env(cls, prefix)`; add `frozen=True`.
- **Modify** `src/markdown_vault_mcp/config.py` — add `VaultConfig.from_env`; make `VaultConfig` frozen; delete `load_config()` body (retire); keep/trim module helpers (`_env`, `_parse_int_env` removed once relocated). Target < 400 LOC.
- **Modify** `_cli_impl.py` (2 callsites), `server.py` (1 callsite) — `load_config()` → `VaultConfig.from_env()`.
- **Modify** `tests/test_config.py` — keep all existing tests (behaviour identical); add `Test<Section>FromEnv` classes.
- **Docs** (C1): `docs/design.md` config section note (`from_env` is the entrypoint); CLAUDE.md "Config & Customization Contract" already references `from_env` — verify the sentinel text still matches.

---

# PR C1 — `from_env` + frozen + retire `load_config` (behaviour-neutral, Closes #579)

### Task 1: Shared helpers module

**Files:** Create `src/markdown_vault_mcp/config_sections/_helpers.py`; Test `tests/test_config.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_config.py`)
```python
class TestConfigHelpers:
    def test_parse_int_env_valid(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import parse_int_env
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_X", "7")
        assert parse_int_env("MARKDOWN_VAULT_MCP", "X", 3) == 7

    def test_parse_int_env_invalid_falls_back(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import parse_int_env
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_X", "nope")
        assert parse_int_env("MARKDOWN_VAULT_MCP", "X", 3) == 3

    def test_parse_float_env_invalid_falls_back(self, monkeypatch):
        from markdown_vault_mcp.config_sections._helpers import parse_float_env
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_X", "nope")
        assert parse_float_env("MARKDOWN_VAULT_MCP", "X", 1.5) == 1.5
```
- [ ] **Step 2: Run** `python -m pytest tests/test_config.py::TestConfigHelpers -q` → FAIL (module missing)
- [ ] **Step 3: Create `_helpers.py`**
```python
"""Shared env-reading helpers for config_sections from_env classmethods.

Imports only fastmcp_pvl_core + stdlib (never config.py) so config.py can
import these without a cycle.
"""
from __future__ import annotations

import logging

from fastmcp_pvl_core import env as _core_env

logger = logging.getLogger(__name__)


def env(prefix: str, name: str, default: str | None = None) -> str | None:
    """Read ``{prefix}_{name}`` (whitespace-stripped, empty-as-unset)."""
    return _core_env(prefix, name, default=default)


def parse_int_env(prefix: str, name: str, default: int) -> int:
    """Read an int env var; warn-and-default on absence/parse error."""
    raw = (env(prefix, name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s_%s=%r, using default %s", prefix, name, raw, default)
        return default


def parse_float_env(prefix: str, name: str, default: float) -> float:
    """Read a float env var; warn-and-default on absence/parse error."""
    raw = (env(prefix, name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s_%s=%r, using default %s", prefix, name, raw, default)
        return default
```
- [ ] **Step 4: Run** the test → PASS
- [ ] **Step 5: Commit** `git add src/markdown_vault_mcp/config_sections/_helpers.py tests/test_config.py && git commit -m "feat(config): add config_sections/_helpers env parsers (refs #579)"`

### Task 2: `GitConfig.from_env` + frozen

**Files:** Modify `config_sections/git.py`; Test `tests/test_config.py`

- [ ] **Step 1: Write the failing test**
```python
class TestGitConfigFromEnv:
    def test_defaults(self, monkeypatch):
        from markdown_vault_mcp.config_sections import GitConfig
        for k in ("GIT_TOKEN","GIT_REPO_URL","GIT_PULL_INTERVAL_S","GIT_PUSH_DELAY_S"):
            monkeypatch.delenv(f"MARKDOWN_VAULT_MCP_{k}", raising=False)
        g = GitConfig.from_env("MARKDOWN_VAULT_MCP")
        assert g == GitConfig()

    def test_pull_interval_negative_clamps_to_zero(self, monkeypatch):
        from markdown_vault_mcp.config_sections import GitConfig
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "-5")
        assert GitConfig.from_env("MARKDOWN_VAULT_MCP").pull_interval_s == 0

    def test_frozen(self):
        from markdown_vault_mcp.config_sections import GitConfig
        import dataclasses
        with pytest.raises(dataclasses.FrozenInstanceError):
            GitConfig().token = "x"  # type: ignore[misc]
```
- [ ] **Step 2: Run** `python -m pytest tests/test_config.py::TestGitConfigFromEnv -q` → FAIL
- [ ] **Step 3: Implement** — change `@dataclass` to `@dataclass(frozen=True)`; add the classmethod (reproduces `load_config` git logic verbatim — see the behaviour table):
```python
    @classmethod
    def from_env(cls, prefix: str) -> "GitConfig":
        from markdown_vault_mcp.config_sections._helpers import env, parse_float_env

        push_delay_s = parse_float_env(prefix, "GIT_PUSH_DELAY_S", 30.0)

        raw_pull = (env(prefix, "GIT_PULL_INTERVAL_S") or "").strip()
        pull_interval_s = 600
        if raw_pull:
            try:
                pull_interval_s = int(raw_pull)
            except ValueError:
                logger.warning("invalid GIT_PULL_INTERVAL_S=%r, using 600", raw_pull)
            else:
                if pull_interval_s < 0:
                    logger.warning("negative GIT_PULL_INTERVAL_S=%r, clamping to 0", raw_pull)
                    pull_interval_s = 0

        raw_lfs = env(prefix, "GIT_LFS")
        return cls(
            token=env(prefix, "GIT_TOKEN") or None,
            repo_url=env(prefix, "GIT_REPO_URL") or None,
            username=env(prefix, "GIT_USERNAME") or "x-access-token",
            push_delay_s=push_delay_s,
            commit_name=env(prefix, "GIT_COMMIT_NAME") or "markdown-vault-mcp",
            commit_email=env(prefix, "GIT_COMMIT_EMAIL") or "noreply@markdown-vault-mcp",
            commit_name_claim=env(prefix, "GIT_COMMIT_NAME_CLAIM") or None,
            commit_email_claim=env(prefix, "GIT_COMMIT_EMAIL_CLAIM") or None,
            lfs=parse_bool(raw_lfs) if raw_lfs is not None else True,
            pull_interval_s=pull_interval_s,
        )
```
(Add `import logging; logger = logging.getLogger(__name__)` and `from fastmcp_pvl_core import parse_bool` at module top.) **Note:** the `token`-set-without-`repo_url` deprecation WARNING stays in `VaultConfig.from_env` (Task 9), not here — it needs both fields.
- [ ] **Step 4: Run** the test → PASS; also run the existing `TestGitCommitterConfig`/`TestGitLfsConfig` → PASS (behaviour identical)
- [ ] **Step 5: Commit**

### Task 3: `IndexingConfig.from_env` + frozen

**Files:** Modify `config_sections/indexing.py`; Test `tests/test_config.py`

- [ ] **Step 1: Test** — `IndexingConfig.from_env` defaults all-None; `INDEX_PATH=/x` → `Path("/x")`; list fields via `parse_list`; frozen.
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — `@dataclass(frozen=True)`; classmethod (note `Path` is already a runtime import here per the TC003 fix):
```python
    @classmethod
    def from_env(cls, prefix: str) -> "IndexingConfig":
        from markdown_vault_mcp.config_sections._helpers import env
        from fastmcp_pvl_core import parse_list

        def _path(name: str) -> Path | None:
            raw = (env(prefix, name) or "").strip()
            return Path(raw) if raw else None

        return cls(
            index_path=_path("INDEX_PATH"),
            state_path=_path("STATE_PATH"),
            embeddings_path=_path("EMBEDDINGS_PATH"),
            indexed_frontmatter_fields=parse_list(env(prefix, "INDEXED_FIELDS") or "") or None,
            required_frontmatter=parse_list(env(prefix, "REQUIRED_FIELDS") or "") or None,
            exclude_patterns=parse_list(env(prefix, "EXCLUDE") or "") or None,
        )
```
(Verify `parse_list("")` returns `[]` so `or None` yields `None` — matches current `_parse_list(raw) or None`.)
- [ ] **Step 4: Run** → PASS; existing indexing-field tests → PASS
- [ ] **Step 5: Commit**

### Task 4: `EmbeddingsConfig.from_env` + frozen (bare reads + `object.__setattr__`)

**Files:** Modify `config_sections/embeddings.py`; Test `tests/test_config.py`

- [ ] **Step 1: Test** — defaults; `OLLAMA_HOST` bare read + trailing-slash strip; `OPENAI_BASE_URL` prefixed-wins-over-bare; `OPENAI_API_KEY` bare; frozen; `__post_init__` still normalizes (existing `TestEmbeddingsConfigNormalization` must pass).
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — `@dataclass(frozen=True)`; change `__post_init__` assignment to `object.__setattr__(self, "ollama_host", ...)`; add classmethod reproducing the bare/prefixed reads exactly:
```python
    def __post_init__(self) -> None:
        host = (self.ollama_host or "http://localhost:11434").rstrip("/")
        object.__setattr__(self, "ollama_host", host)

    @classmethod
    def from_env(cls, prefix: str) -> "EmbeddingsConfig":
        import os
        from markdown_vault_mcp.config_sections._helpers import env
        from fastmcp_pvl_core import parse_bool

        ollama_host = (os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        raw_cpu = env(prefix, "OLLAMA_CPU_ONLY")
        openai_base_url = (
            env(prefix, "OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or ""
        ).strip() or "https://api.openai.com/v1"
        openai_model = (
            env(prefix, "OPENAI_EMBEDDING_MODEL")
            or os.environ.get("OPENAI_EMBEDDING_MODEL")
            or ""
        ).strip() or "text-embedding-3-small"
        return cls(
            provider=env(prefix, "EMBEDDING_PROVIDER") or None,
            ollama_host=ollama_host,
            ollama_model=env(prefix, "OLLAMA_MODEL") or "nomic-embed-text",
            ollama_cpu_only=parse_bool(raw_cpu) if raw_cpu is not None else False,
            openai_api_key=(os.environ.get("OPENAI_API_KEY") or "").strip() or None,
            openai_base_url=openai_base_url.rstrip("/"),
            openai_embedding_model=openai_model,
            fastembed_model=env(prefix, "FASTEMBED_MODEL") or "BAAI/bge-small-en-v1.5",
            fastembed_cache_dir=env(prefix, "FASTEMBED_CACHE_DIR") or None,
        )
```
(`ollama_host` is passed already-stripped; `__post_init__` re-strip is idempotent. `from __future__ import annotations` already present.)
- [ ] **Step 4: Run** → PASS; existing `TestLoadConfigEmbeddingFields` + `TestEmbeddingsConfigNormalization` → PASS
- [ ] **Step 5: Commit**

### Task 5: `SearchConfig.from_env` + frozen (raises `ValueError`)

**Files:** Modify `config_sections/search.py`; Test `tests/test_config.py`

- [ ] **Step 1: Test** — defaults; overrides; `CHUNKS_PER_FILE=0` → `ValueError`; `CHUNKS_PER_FILE=nope` → `ValueError`; frozen.
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — `@dataclass(frozen=True)`; classmethod keeping the explicit `int()/float()` + `raise ValueError` (do NOT use `env_int` — that's C2):
```python
    @classmethod
    def from_env(cls, prefix: str) -> "SearchConfig":
        from markdown_vault_mcp.config_sections._helpers import env

        def _bounded_int(name: str, default: int, minimum: int) -> int:
            raw = (env(prefix, name) or "").strip()
            if not raw:
                return default
            try:
                val = int(raw)
            except ValueError as e:
                raise ValueError(f"{prefix}_{name} must be an integer, got {raw!r}") from e
            if val < minimum:
                raise ValueError(f"{prefix}_{name} must be >= {minimum}, got {val}")
            return val

        raw_alpha = (env(prefix, "LENGTH_DOWNWEIGHT_ALPHA") or "").strip()
        if raw_alpha:
            try:
                alpha = float(raw_alpha)
            except ValueError as e:
                raise ValueError(
                    f"{prefix}_LENGTH_DOWNWEIGHT_ALPHA must be a number, got {raw_alpha!r}"
                ) from e
            if alpha < 0:
                raise ValueError(f"{prefix}_LENGTH_DOWNWEIGHT_ALPHA must be >= 0, got {alpha}")
        else:
            alpha = 0.25

        return cls(
            chunks_per_file=_bounded_int("CHUNKS_PER_FILE", 2, 1),
            snippet_words=_bounded_int("SNIPPET_WORDS", 200, 0),
            length_downweight_alpha=alpha,
            max_chunk_words=_bounded_int("MAX_CHUNK_WORDS", 400, 1),
        )
```
(Match the exact current error-message wording from `config.py:627-685` so any message-asserting tests still pass — check those lines and copy the strings verbatim.)
- [ ] **Step 4: Run** → PASS; existing search-ranking tests (incl. `rejects_malformed_int/float`) → PASS
- [ ] **Step 5: Commit**

### Task 6: `SyncConfig.from_env` + frozen

**Files:** Modify `config_sections/sync.py`; Test `tests/test_config.py`

- [ ] **Step 1: Test** — defaults; `FILE_WATCHER=false`; `FILE_WATCHER_DEBOUNCE_S` invalid→2.0 and `<=0`→2.0; `GITHUB_WEBHOOK_SECRET`; frozen.
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — `@dataclass(frozen=True)`; classmethod:
```python
    @classmethod
    def from_env(cls, prefix: str) -> "SyncConfig":
        import logging
        from markdown_vault_mcp.config_sections._helpers import env
        from fastmcp_pvl_core import parse_bool

        _log = logging.getLogger(__name__)
        raw_fw = env(prefix, "FILE_WATCHER")
        raw_deb = (env(prefix, "FILE_WATCHER_DEBOUNCE_S") or "").strip()
        debounce = 2.0
        if raw_deb:
            try:
                debounce = float(raw_deb)
            except ValueError:
                _log.warning("invalid FILE_WATCHER_DEBOUNCE_S=%r, using 2.0", raw_deb)
            else:
                if debounce <= 0:
                    _log.warning("FILE_WATCHER_DEBOUNCE_S=%r <= 0, using 2.0", raw_deb)
                    debounce = 2.0
        return cls(
            file_watcher_enabled=parse_bool(raw_fw) if raw_fw is not None else True,
            file_watcher_debounce_s=debounce,
            github_webhook_secret=env(prefix, "GITHUB_WEBHOOK_SECRET") or None,
        )
```
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit**

### Task 7: `ContentConfig.from_env` + frozen (templates/prompts normalization)

**Files:** Modify `config_sections/content.py`; Test `tests/test_config.py`

- [ ] **Step 1: Test** — defaults; `ATTACHMENT_EXTENSIONS` list + `"*"`→`["*"]`; `MAX_ATTACHMENT_SIZE_MB` invalid/negative→1.0, 0 allowed; `MAX_NOTE_READ_BYTES` invalid/negative→default, 0 allowed; `TEMPLATES_FOLDER` backslash+trailing-slash normalize, empty→`_templates`; frozen.
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — `@dataclass(frozen=True)`; classmethod reproducing `config.py` content logic (attachment-extensions `"*"` handling, templates_folder normalization, max-* warn+reset). `prompts_folder` join-to-source_dir is done in `VaultConfig.from_env` (needs `source_dir`); `ContentConfig.from_env` reads the **raw** prompts path; OR pass `source_dir` into `ContentConfig.from_env(prefix, source_dir)`. **Decision:** pass `source_dir: Path` as a 2nd arg to `ContentConfig.from_env` so the join stays here. Copy the exact normalization from `config.py` lines ~500-560 (read them and reproduce verbatim, including `parse_float_env`/`parse_int_env` for the max-* fields with the negative-reset guard).
- [ ] **Step 4: Run** → PASS; existing `TestAttachmentConfig`/`TestMaxNoteReadBytesEnv`/templates tests → PASS
- [ ] **Step 5: Commit**

### Task 8: `TransferConfig.from_env` + frozen

**Files:** Modify `config_sections/transfer.py`; Test `tests/test_config.py`

- [ ] **Step 1: Test** — defaults; env overrides; `__post_init__` still raises on `ttl_default > ttl_max` / nonpositive cap; frozen (`__post_init__` raises-only → frozen-safe).
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — `@dataclass(frozen=True)`; classmethod:
```python
    @classmethod
    def from_env(cls, prefix: str) -> "TransferConfig":
        from markdown_vault_mcp.config_sections._helpers import parse_int_env
        return cls(
            ttl_default_s=parse_int_env(prefix, "TRANSFER_TTL_DEFAULT_S", 3600),
            ttl_max_s=parse_int_env(prefix, "TRANSFER_TTL_MAX_S", 86400),
            max_upload_bytes=parse_int_env(prefix, "TRANSFER_MAX_UPLOAD_BYTES", 104857600),
        )
```
- [ ] **Step 4: Run** → PASS; existing transfer tests → PASS
- [ ] **Step 5: Commit**

### Task 9: `VaultConfig.from_env` + frozen + retire `load_config` + migrate callsites

**Files:** Modify `config.py`, `_cli_impl.py`, `server.py`; Test `tests/test_config.py`

- [ ] **Step 1: Test** — `VaultConfig.from_env` reads `source_dir` (raise if blank), `read_only`, `server_name` (empty→default), `instructions`; composes all sub-configs + `ServerConfig.from_env`; token-without-repo-url WARNING fires; the existing whole-`load_config` tests pass when pointed at `from_env`. Add `test_load_config_is_from_env_alias` OR (if removing the shim) update all `load_config()` test calls to `VaultConfig.from_env()`. **Decision:** RETIRE `load_config` (no shim) — update test call sites. (Aligns with "retire load_config"; the existing assertions are behaviour, unchanged.)
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** —
  - `@dataclass(frozen=True)` on `VaultConfig`.
  - Add `@classmethod from_env(cls, prefix: str = _ENV_PREFIX) -> "VaultConfig"` between the `CONFIG-FROM-ENV-START`/`END` sentinels: read `SOURCE_DIR` (raise `ValueError` if blank — copy current message), `READ_ONLY` (parse_bool default True), `SERVER_NAME` (empty→default), `INSTRUCTIONS`; build `git=GitConfig.from_env(prefix)` … `content=ContentConfig.from_env(prefix, source_dir)` … `transfer=TransferConfig.from_env(prefix)`; `server=ServerConfig.from_env(prefix)`; emit the token-without-repo_url WARNING if `git.token and not git.repo_url`.
  - Delete the old `load_config()` function body and the now-unused module helpers (`_env`, `_parse_int_env`) — keep only what `from_env` uses (import shared helpers from `config_sections._helpers`).
  - `to_vault_kwargs` / `_build_git_strategy` unchanged (already `self.*`-based).
  - Migrate callsites: `_cli_impl.py:56` `load_config()`→`VaultConfig.from_env()`; `_cli_impl.py:99` same; `server.py:145` same. Update imports (`from markdown_vault_mcp.config import VaultConfig`).
- [ ] **Step 4: Run** full suite `python -m pytest -q` → all pass; `wc -l src/markdown_vault_mcp/config.py` → < 400
- [ ] **Step 5: Commit**

### Task 10: C1 cleanup + docs + gates

- [ ] Grep `load_config` across `src/` + `tests/` → only the retired-definition removal + migrated callsites; no stragglers.
- [ ] `docs/design.md`: update the config section to say `VaultConfig.from_env()` is the entrypoint (replace any `load_config` mention).
- [ ] Verify CLAUDE.md "Config & Customization Contract" sentinel text (`CONFIG-FROM-ENV-START/END`, `from_env`) still matches the code.
- [ ] Gates: `ruff check --fix . && ruff format . && ruff format --check . && mypy src/ tests/ && python -m pytest -q` (1909+ green).
- [ ] Preflight circus (full, all lenses — this is a real refactor, do NOT skip), then PR: **`Closes #579`**.

---

# PR C2 — adopt `env_int`/`env_float` + harden validation (behaviour-changing, new issue)

**Prereq:** C1 merged. Branch off updated `main`. **File the issue first** (observation-style): "Adopt pvl-core `env_int`/`env_float` + harden config validation" — body lists the behaviour changes below; each is a deliberate decision.

### Task C2-1: Adopt `env_int`/`env_float` for the warn-fallback numeric fields
Replace, in the sub-config `from_env` methods, the `parse_int_env`/`parse_float_env`/inline parsing for `GIT_PUSH_DELAY_S`, `GIT_PULL_INTERVAL_S`, `FILE_WATCHER_DEBOUNCE_S`, `MAX_ATTACHMENT_SIZE_MB`, `MAX_NOTE_READ_BYTES`, `TRANSFER_*` with `env_int`/`env_float(prefix, name, default, minimum=...)`. **Behaviour delta to document + test:** out-of-range now returns the **default** uniformly (e.g. negative `GIT_PULL_INTERVAL_S` → 600, was clamp-to-0; `DEBOUNCE <= 0` handled via `minimum`). Update the affected tests to the new uniform semantics. Remove `config_sections/_helpers.parse_int_env`/`parse_float_env` once unused.

### Task C2-2: `search.*` → `env_int(strict=True)` (ValueError → ConfigurationError)
Replace `SearchConfig.from_env`'s `_bounded_int`/alpha logic with `env_int`/`env_float(strict=True, minimum=...)`. **Behaviour delta:** raises `fastmcp_pvl_core.ConfigurationError` (not `ValueError`). Update `test_config.py` `pytest.raises(ValueError)` → `pytest.raises(ConfigurationError)` for the four search fields.

### Task C2-3: Explicit-provider fail-fast
In `to_vault_kwargs` (config.py), the `except (ImportError, RuntimeError)` around `get_embedding_provider` currently silently disables search. **Change:** when `self.embeddings.provider` is explicitly set, re-raise (surface the misconfig); keep the silent degrade only for the auto-detect path (`provider is None`). Add a test: explicit `EMBEDDING_PROVIDER=openai` + provider construction failure → raises (not silent).

### Task C2-4: Normalization symmetrization
`EmbeddingsConfig.__post_init__`: also normalize `openai_base_url` (rstrip "/") via `object.__setattr__`, matching `ollama_host`. `ContentConfig`: move `templates_folder` normalization into `__post_init__` (so direct construction normalizes too). Tests for direct-construction normalization.

### Task C2-5: C2 gates + docs + PR
- `docs/configuration.md`: note the validation changes (search fields raise `ConfigurationError`; explicit-provider misconfig now fails fast).
- Full gates + full preflight circus. PR: `Closes #<C2 issue>`.

---

## Self-review notes (run before dispatch)
- **Spec coverage:** #579 "from_env + frozen + retire load_config" → C1 Tasks 2-10. "validation-in-subconfig / env_int" → C2. ✓
- **No placeholders:** Task 6/7/9 say "copy the exact wording/normalization from config.py lines N" — the implementer MUST open those lines and reproduce verbatim; this is deliberate (the source is the spec for behaviour-neutrality), not a placeholder. Every code-bearing step has real code.
- **Type consistency:** all `from_env(cls, prefix)` signatures uniform; `ContentConfig.from_env(cls, prefix, source_dir)` is the one 2-arg variant (flagged in Tasks 7 + 9).
- **Frozen risk:** only `EmbeddingsConfig.__post_init__` assigns → `object.__setattr__` (Task 4). `TransferConfig.__post_init__` raises-only (Task 8). Confirm no other sub-config `__post_init__` assigns before freezing.
- **Behaviour-neutrality gate for C1:** the existing `tests/test_config.py` suite (minus call-site renames in Task 9) must pass UNCHANGED. If any existing test needs an assertion change in C1, that is a behaviour leak — stop and reconsider.
