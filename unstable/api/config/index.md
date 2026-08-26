# Configuration

The `config` module loads configuration from environment variables and provides a typed dataclass for all settings.

## Quick Start

```
import os
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.vault import Vault
from markdown_vault_mcp.config import to_vault_kwargs

os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/path/to/vault"
config = ProjectConfig.from_env()
vault = Vault(**to_vault_kwargs(config))
```

## API Reference

## `ProjectConfig(server=ServerConfig(), source_dir=Path('/data/vault'), read_only=False, write_protect_existing=False, server_name='markdown-vault-mcp', instructions=None, disable_apps_ui=False, index_path=None, state_path=None, embeddings_path=None, indexed_fields=None, required_fields=None, exclude=None, title_field='title', searchable_fields=None, templates_folder='_templates', prompts_folder=None, conventions_file='_conventions.md', okf_mode='auto', okf_write=False, okf_verify='elicit', attachment_extensions=None, max_attachment_size_mb=1.0, max_note_read_bytes=262144, chunks_per_file=2, snippet_words=200, length_downweight_alpha=0.25, max_chunk_words=400, max_chunk_chars=None, chunk_overlap_words=40, folder_weights=None, fts_weights=None, embedding_provider=None, ollama_host='http://localhost:11434', openai_api_key=None, voyage_api_key=None, voyage_model='voyage-4', ollama_model='nomic-embed-text', ollama_cpu_only=False, openai_base_url='https://api.openai.com/v1', openai_embedding_model='text-embedding-3-small', fastembed_model='BAAI/bge-small-en-v1.5', fastembed_cache_dir=None, embed_context=False, embed_timeout_s=30.0, embedding_batch_size=4, git_repo_url=None, git_token=None, git_username='x-access-token', git_pull_interval_s=600, git_push_delay_s=30.0, git_commit_name='markdown-vault-mcp', git_commit_email='noreply@markdown-vault-mcp', git_commit_name_claim=None, git_commit_email_claim=None, git_lfs=True, file_watcher=True, file_watcher_debounce_s=2.0, file_watcher_root_floor=True, github_webhook_secret=None, summarize_provider=None, summarize_openai_api_key=None, summarize_openai_base_url=None, summarize_openai_model='gpt-5-mini', summarize_max_tokens=8192, summarize_max_notes=50, summarize_max_input_chars=200000, summarize_timeout=120.0, transfer=TransferConfig(), jobs=JobsConfig())`

Domain config for Markdown Vault MCP. Compose — don't inherit.

### `git`

The git section assembled from the flat `git_*` fields.

A property rather than a composed field so the config-surface generator documents the flat fields' metadata. Construction runs `GitConfig.__post_init__` validation.

### `indexing`

The indexing section assembled from the flat index/frontmatter fields.

### `embeddings`

The embeddings section assembled from the flat embedding fields.

### `search`

The search section assembled from the flat ranking/chunking fields.

### `summarize`

The summarize section assembled from the flat `summarize_*` fields.

### `sync`

The sync section assembled from the flat watcher/webhook fields.

### `content`

The content section assembled from the flat attachment/folder fields.

A relative `prompts_folder` is resolved against `source_dir` here, so direct construction and `from_env` behave identically.

### `__post_init__()`

Validate composed domain fields. Raise `ValueError` when invalid.

Runs on EVERY construction path — `from_env` and a direct `ProjectConfig(field=...)` alike. That is what makes this the right home for a field invariant: `env_float` / `env_int` bounds check only the *env-sourced* value, never the default, so a direct construction slips past them. They also cannot express an exclusive bound (their `minimum` / `maximum` are inclusive, so "must be > 0" lets `0` through) or a cross-field rule (A requires B, mutually-exclusive pairs). All three belong here.

The dataclass is `frozen=True`: read fields freely, but plain assignment raises. To *normalise* rather than merely check, use `object.__setattr__(self, "name", value)`.

### `from_env()`

Load :class:`ProjectConfig` from `MARKDOWN_VAULT_MCP_*` env vars.
