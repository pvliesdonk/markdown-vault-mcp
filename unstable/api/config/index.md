# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

```
import os
from markdown_vault_mcp import Vault, VaultConfig

os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/path/to/vault"
config = VaultConfig.from_env()
vault = Vault(**config.to_vault_kwargs())
```

## API Reference

## `VaultConfig(source_dir, read_only=True, server_name='markdown-vault-mcp', instructions=None, git=GitConfig(), indexing=IndexingConfig(), embeddings=EmbeddingsConfig(), search=SearchConfig(), sync=SyncConfig(), content=ContentConfig(), transfer=TransferConfig(), disable_apps_ui=False, server=ServerConfig())`

Configuration for a :class:`~markdown_vault_mcp.vault.Vault`.

Attributes:

| Name              | Type               | Description                                                                                                                                                                                               |
| ----------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_dir`      | `Path`             | Root directory of the markdown vault.                                                                                                                                                                     |
| `read_only`       | `bool`             | When True (default), write operations raise :exc:~markdown_vault_mcp.exceptions.ReadOnlyError.                                                                                                            |
| `server_name`     | `str`              | Display name for the MCP server (default "markdown-vault-mcp").                                                                                                                                           |
| `instructions`    | \`str              | None\`                                                                                                                                                                                                    |
| `git`             | `GitConfig`        | Git auth, identity, and sync cadence settings.                                                                                                                                                            |
| `indexing`        | `IndexingConfig`   | SQLite/vector index paths and frontmatter/exclusion settings.                                                                                                                                             |
| `embeddings`      | `EmbeddingsConfig` | Embedding provider selection and per-provider settings.                                                                                                                                                   |
| `search`          | `SearchConfig`     | Search ranking and snippet-truncation knobs.                                                                                                                                                              |
| `sync`            | `SyncConfig`       | File-watcher and GitHub-webhook settings.                                                                                                                                                                 |
| `content`         | `ContentConfig`    | Attachment/note-read limits and template/prompt folder paths.                                                                                                                                             |
| `transfer`        | `TransferConfig`   | One-time upload/download transfer-link TTL and size settings.                                                                                                                                             |
| `disable_apps_ui` | `bool`             | When True, hides MCP-Apps UI tools (browse_vault, show_context) from the tool listing so clients that don't render MCP Apps panels don't see them (default False).                                        |
| `server`          | `ServerConfig`     | Shared server-level configuration (transport, host/port, auth, base URL, event store URL, MCP App domain) populated from MARKDOWN_VAULT_MCP\_\* env vars by :meth:fastmcp_pvl_core.ServerConfig.from_env. |

Example::

```
config = VaultConfig.from_env()
vault = Vault(**config.to_vault_kwargs())
```

### `to_vault_kwargs()`

Return keyword arguments suitable for `Vault(**kwargs)`.

Resolves the embedding provider (when `indexing.embeddings_path` is set) and creates a :class:`~markdown_vault_mcp.git.GitWriteStrategy`.

Returns:

| Type             | Description                                     |
| ---------------- | ----------------------------------------------- |
| `dict[str, Any]` | Dict of keyword arguments accepted by           |
| `dict[str, Any]` | class:~markdown_vault_mcp.vault.Vault.__init__. |

Example::

```
config = VaultConfig.from_env()
vault = Vault(**config.to_vault_kwargs())
```

### `from_env(prefix=_ENV_PREFIX)`

Load configuration from environment variables.

Reads the following environment variables:

**Core:**

- `MARKDOWN_VAULT_MCP_SOURCE_DIR` (required): path to markdown files.
- `MARKDOWN_VAULT_MCP_READ_ONLY`: disable write tools; default `true`.
- `MARKDOWN_VAULT_MCP_INDEX_PATH`: SQLite index path; default in-memory.
- `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH`: embeddings directory; default disabled.
- `MARKDOWN_VAULT_MCP_STATE_PATH`: state file path; default `{source_dir}/.markdown_vault_mcp/state.json`.
- `MARKDOWN_VAULT_MCP_INDEXED_FIELDS`: comma-separated frontmatter fields to index; default none.
- `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS`: comma-separated required frontmatter fields; default none.
- `MARKDOWN_VAULT_MCP_EXCLUDE`: comma-separated glob patterns to exclude; default none.

**Git:**

- `MARKDOWN_VAULT_MCP_GIT_TOKEN`: token for git write strategy; default disabled.
- `MARKDOWN_VAULT_MCP_GIT_REPO_URL`: HTTPS remote URL for managed git mode; when set, startup may clone into `SOURCE_DIR`.
- `MARKDOWN_VAULT_MCP_GIT_USERNAME`: username for token auth in managed mode; default `x-access-token`.
- `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S`: seconds of idle before pushing (default `30`). Set to `0` to push only on shutdown.
- `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME`: git committer name for auto-commits; default `markdown-vault-mcp`.
- `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL`: git committer email for auto-commits; default `noreply@markdown-vault-mcp`.
- `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM`: OIDC claim key whose value overrides the commit author name when an OIDC access token is present (e.g. `name`). Unset by default (static name used for all commits).
- `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM`: OIDC claim key whose value overrides the commit author e-mail when an OIDC access token is present (e.g. `email`). Unset by default.
- `MARKDOWN_VAULT_MCP_GIT_LFS`: run `git lfs pull` during git strategy init to resolve LFS pointers; default `true`.
- `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S`: seconds between periodic git fetch + ff-only updates (default `600`). Set to `0` to disable.

**Attachments and templates:**

- `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS`: comma-separated list of allowed attachment extensions (without dot, e.g. `pdf,png,jpg`); use `*` to allow all non-.md files; default: common document and image types.
- `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB`: maximum attachment size in megabytes for read and write; `0` disables the limit; default `1.0`.
- `MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES`: maximum bytes returned by full-document `read()` for `.md` files; `read(path, section=...)` bypasses the cap; `0` disables; default `262144` (256 KB).
- `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER`: relative folder path where template markdown files are stored; default `_templates`.
- `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER`: relative folder path where user-defined prompt markdown files are stored; default `None` (disabled).

**Server identity:**

- `MARKDOWN_VAULT_MCP_SERVER_NAME`: display name for the MCP server; default `"markdown-vault-mcp"`.
- `MARKDOWN_VAULT_MCP_INSTRUCTIONS`: server-level instructions surfaced to clients; default `None`.

**Embedding providers:**

- `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`: embedding provider name (`"ollama"`, `"openai"`, `"fastembed"`); default `None`.
- `OLLAMA_HOST`: Ollama API base URL (ecosystem standard, bare env var); default `"http://localhost:11434"`.
- `MARKDOWN_VAULT_MCP_OLLAMA_MODEL`: Ollama model name; default `"nomic-embed-text"`.
- `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY`: CPU-only Ollama inference; default `false`.
- `OPENAI_API_KEY`: OpenAI API key (ecosystem standard, bare env var).
- `MARKDOWN_VAULT_MCP_OPENAI_BASE_URL` or `OPENAI_BASE_URL`: OpenAI-compatible API base URL; default `"https://api.openai.com/v1"`.
- `MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL` or `OPENAI_EMBEDDING_MODEL`: embedding model name; default `"text-embedding-3-small"`.
- `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL`: FastEmbed model name; default `"BAAI/bge-small-en-v1.5"`.
- `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR`: FastEmbed model cache directory; default `None`.

**Transfer links:**

- `MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S`: token lifetime when the caller omits one; default `3600` (1 hour).
- `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S`: ceiling a requested TTL is clamped to; default `86400` (24 hours).
- `MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES`: per-upload size cap in bytes; default `104857600` (100 MiB).

Transport and auth variables (`TRANSPORT`, `HOST`, `PORT`, `BASE_URL`, `AUTH_MODE`, `OIDC_*`, `BEARER_TOKEN`, `KV_STORE_URL` (legacy `EVENT_STORE_URL`), `APP_DOMAIN`) are read into `config.server` by :meth:`fastmcp_pvl_core.ServerConfig.from_env`; see `docs/configuration.md` for the full list.

Parameters:

| Name     | Type  | Description                                       | Default       |
| -------- | ----- | ------------------------------------------------- | ------------- |
| `prefix` | `str` | Env var prefix; defaults to "MARKDOWN_VAULT_MCP". | `_ENV_PREFIX` |

Returns:

| Type          | Description                                    |
| ------------- | ---------------------------------------------- |
| `VaultConfig` | A fully populated :class:VaultConfig instance. |

Raises:

| Type                 | Description                                                                                                 |
| -------------------- | ----------------------------------------------------------------------------------------------------------- |
| `ConfigurationError` | If MARKDOWN_VAULT_MCP_SOURCE_DIR is not set, or if a search-ranking env var is non-numeric or out of range. |

Example::

```
import os
os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/home/user/vault"
config = VaultConfig.from_env()
vault = Vault(**config.to_vault_kwargs())
```
