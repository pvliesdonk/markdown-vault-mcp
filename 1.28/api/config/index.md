# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

```
import os
from markdown_vault_mcp import Collection, load_config

os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/path/to/vault"
config = load_config()
collection = Collection(**config.to_collection_kwargs())
```

## API Reference

## `CollectionConfig(source_dir, read_only=True, index_path=None, embeddings_path=None, state_path=None, indexed_frontmatter_fields=None, required_frontmatter=None, exclude_patterns=None, git_token=None, git_repo_url=None, git_username='x-access-token', git_push_delay_s=30.0, git_commit_name='markdown-vault-mcp', git_commit_email='noreply@markdown-vault-mcp', git_lfs=True, git_pull_interval_s=600, attachment_extensions=None, max_attachment_size_mb=10.0, templates_folder='_templates', prompts_folder=None, event_store_url=None, server_name='markdown-vault-mcp', instructions=None, auth_mode=None, base_url=None, oidc_config_url=None, oidc_client_id=None, oidc_client_secret=None, oidc_audience=None, oidc_required_scopes=None, oidc_jwt_signing_key=None, oidc_verify_access_token=False, bearer_token=None, embedding_provider=None, ollama_host='http://localhost:11434', ollama_model='nomic-embed-text', ollama_cpu_only=False, openai_api_key=None, fastembed_model='BAAI/bge-small-en-v1.5', fastembed_cache_dir=None, chunks_per_doc=2, snippet_words=200, length_downweight_alpha=0.25, max_chunk_words=400, server=ServerConfig())`

Configuration for a :class:`~markdown_vault_mcp.collection.Collection`.

Attributes:

| Name                         | Type           | Description                                                                                                                                                                                                                                                                                                                                |
| ---------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `source_dir`                 | `Path`         | Root directory of the markdown collection.                                                                                                                                                                                                                                                                                                 |
| `read_only`                  | `bool`         | When True (default), write operations raise :exc:~markdown_vault_mcp.exceptions.ReadOnlyError.                                                                                                                                                                                                                                             |
| `index_path`                 | \`Path         | None\`                                                                                                                                                                                                                                                                                                                                     |
| `embeddings_path`            | \`Path         | None\`                                                                                                                                                                                                                                                                                                                                     |
| `state_path`                 | \`Path         | None\`                                                                                                                                                                                                                                                                                                                                     |
| `indexed_frontmatter_fields` | \`list[str]    | None\`                                                                                                                                                                                                                                                                                                                                     |
| `required_frontmatter`       | \`list[str]    | None\`                                                                                                                                                                                                                                                                                                                                     |
| `exclude_patterns`           | \`list[str]    | None\`                                                                                                                                                                                                                                                                                                                                     |
| `git_token`                  | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `git_repo_url`               | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `git_username`               | `str`          | Username used with token auth (default "x-access-token", GitHub-compatible).                                                                                                                                                                                                                                                               |
| `git_push_delay_s`           | `float`        | Seconds of write-idle time before flushing local commits to the remote (default 30.0). 0 means push only on shutdown.                                                                                                                                                                                                                      |
| `git_commit_name`            | `str`          | Git committer name for auto-commits (default "markdown-vault-mcp").                                                                                                                                                                                                                                                                        |
| `git_commit_email`           | `str`          | Git committer e-mail for auto-commits (default "noreply@markdown-vault-mcp").                                                                                                                                                                                                                                                              |
| `git_lfs`                    | `bool`         | When True (default), run git lfs pull during git strategy initialisation so LFS pointers are resolved before reads.                                                                                                                                                                                                                        |
| `git_pull_interval_s`        | `int`          | Interval in seconds for periodic git fetch + fast-forward-only updates (default 600). Set to 0 to disable.                                                                                                                                                                                                                                 |
| `server_name`                | `str`          | Display name for the MCP server (default "markdown-vault-mcp").                                                                                                                                                                                                                                                                            |
| `instructions`               | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `attachment_extensions`      | \`list[str]    | None\`                                                                                                                                                                                                                                                                                                                                     |
| `max_attachment_size_mb`     | `float`        | Maximum attachment file size in megabytes (default 10.0). 0 means unlimited.                                                                                                                                                                                                                                                               |
| `templates_folder`           | `str`          | Vault-relative folder that holds note templates (default "\_templates").                                                                                                                                                                                                                                                                   |
| `prompts_folder`             | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `event_store_url`            | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `auth_mode`                  | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `base_url`                   | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `oidc_config_url`            | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `oidc_client_id`             | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `oidc_client_secret`         | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `oidc_audience`              | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `oidc_required_scopes`       | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `oidc_jwt_signing_key`       | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `oidc_verify_access_token`   | `bool`         | When True, verify the OIDC access token instead of the id_token (default False).                                                                                                                                                                                                                                                           |
| `bearer_token`               | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `embedding_provider`         | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `ollama_host`                | `str`          | Base URL for the Ollama API (default "http://localhost:11434").                                                                                                                                                                                                                                                                            |
| `ollama_model`               | `str`          | Ollama model name for embeddings (default "nomic-embed-text").                                                                                                                                                                                                                                                                             |
| `ollama_cpu_only`            | `bool`         | When True, request CPU-only inference from Ollama (default False).                                                                                                                                                                                                                                                                         |
| `openai_api_key`             | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `fastembed_model`            | `str`          | FastEmbed model name (default "BAAI/bge-small-en-v1.5").                                                                                                                                                                                                                                                                                   |
| `fastembed_cache_dir`        | \`str          | None\`                                                                                                                                                                                                                                                                                                                                     |
| `server`                     | `ServerConfig` | Shared server-level configuration (transport, host/port, auth, base URL, event store URL, MCP App domain) populated from MARKDOWN_VAULT_MCP\_\* env vars by :meth:fastmcp_pvl_core.ServerConfig.from_env. Domain-specific duplicates above remain on :class:CollectionConfig until MV-PR2/3 migrate consumers to read from self.server.\*. |

Example::

```
config = load_config()
collection = Collection(**config.to_collection_kwargs())
```

### `__post_init__()`

Normalize fields that must not be empty strings.

### `to_collection_kwargs()`

Return keyword arguments suitable for `Collection(**kwargs)`.

Resolves the embedding provider (when `embeddings_path` is set) and creates a :class:`~markdown_vault_mcp.git.GitWriteStrategy`.

Returns:

| Type             | Description                                               |
| ---------------- | --------------------------------------------------------- |
| `dict[str, Any]` | Dict of keyword arguments accepted by                     |
| `dict[str, Any]` | class:~markdown_vault_mcp.collection.Collection.__init__. |

Example::

```
config = load_config()
collection = Collection(**config.to_collection_kwargs())
```

## `load_config()`

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
- `MARKDOWN_VAULT_MCP_GIT_LFS`: run `git lfs pull` during git strategy init to resolve LFS pointers; default `true`.
- `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S`: seconds between periodic git fetch + ff-only updates (default `600`). Set to `0` to disable.

**Attachments and templates:**

- `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS`: comma-separated list of allowed attachment extensions (without dot, e.g. `pdf,png,jpg`); use `*` to allow all non-.md files; default: common document and image types.
- `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB`: maximum attachment size in megabytes for read and write; `0` disables the limit; default `10.0`.
- `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER`: relative folder path where template markdown files are stored; default `_templates`.
- `MARKDOWN_VAULT_MCP_PROMPTS_FOLDER`: relative folder path where user-defined prompt markdown files are stored; default `None` (disabled).
- `MARKDOWN_VAULT_MCP_EVENT_STORE_URL`: event store backend for HTTP session persistence; `file:///path` (default `/data/state/events`) or `memory://` (in-memory, lost on restart).

**Server identity:**

- `MARKDOWN_VAULT_MCP_SERVER_NAME`: display name for the MCP server; default `"markdown-vault-mcp"`.
- `MARKDOWN_VAULT_MCP_INSTRUCTIONS`: server-level instructions surfaced to clients; default `None`.

**Authentication:**

- `MARKDOWN_VAULT_MCP_AUTH_MODE`: explicit OIDC mode override (`"oidc-proxy"` or `"remote"`); default auto-detect. Bearer and multi-auth are determined by the presence of `BEARER_TOKEN` and OIDC fields.
- `MARKDOWN_VAULT_MCP_BASE_URL`: public base URL of the server.
- `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL`: OIDC discovery endpoint URL.
- `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID`: OIDC client identifier.
- `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET`: OIDC client secret.
- `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE`: expected `aud` claim in tokens.
- `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES`: comma-separated required OIDC scopes.
- `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY`: key for signing session JWTs.
- `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN`: verify access token instead of id_token; default `false`.
- `MARKDOWN_VAULT_MCP_BEARER_TOKEN`: static bearer token for simple auth.

**Embedding providers:**

- `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`: embedding provider name (`"ollama"`, `"openai"`, `"fastembed"`); default `None`.
- `OLLAMA_HOST`: Ollama API base URL (ecosystem standard, bare env var); default `"http://localhost:11434"`.
- `MARKDOWN_VAULT_MCP_OLLAMA_MODEL`: Ollama model name; default `"nomic-embed-text"`.
- `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY`: CPU-only Ollama inference; default `false`.
- `OPENAI_API_KEY`: OpenAI API key (ecosystem standard, bare env var).
- `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL`: FastEmbed model name; default `"BAAI/bge-small-en-v1.5"`.
- `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR`: FastEmbed model cache directory; default `None`.

Returns:

| Type               | Description                                         |
| ------------------ | --------------------------------------------------- |
| `CollectionConfig` | A fully populated :class:CollectionConfig instance. |

Raises:

| Type         | Description                                  |
| ------------ | -------------------------------------------- |
| `ValueError` | If MARKDOWN_VAULT_MCP_SOURCE_DIR is not set. |

Example::

```
import os
os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/home/user/vault"
config = load_config()
collection = Collection(**config.to_collection_kwargs())
```
