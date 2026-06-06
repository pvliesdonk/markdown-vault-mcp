"""Configuration loading from environment variables for markdown-vault-mcp.

Reads env vars and returns a :class:`VaultConfig` suitable for
constructing a :class:`~markdown_vault_mcp.vault.Vault`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmcp_pvl_core import ServerConfig
from fastmcp_pvl_core import env as _core_env

# Direct re-exports: parse_bool/parse_list match MV's old call shape (take a
# str, return bool / list[str]) and call sites still gate with
# ``if raw_xxx is not None`` so no shim wrapper is needed.
from fastmcp_pvl_core import parse_bool as _parse_bool
from fastmcp_pvl_core import parse_list as _parse_list

from markdown_vault_mcp.config_sections import (
    ContentConfig,
    EmbeddingsConfig,
    GitConfig,
    IndexingConfig,
    SearchConfig,
    SyncConfig,
    TransferConfig,
)
from markdown_vault_mcp.git import GitWriteStrategy

logger = logging.getLogger(__name__)

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"


def _env(name: str, default: str | None = None) -> str | None:
    """Read ``{_ENV_PREFIX}_{name}`` via the shared core helper.

    Thin shim that preserves the historical one-arg call shape used
    throughout this module.  Behaviour comes from
    :func:`fastmcp_pvl_core.env`: whitespace is stripped and an empty
    value is treated as unset (returns *default*).
    """
    return _core_env(_ENV_PREFIX, name, default=default)


def _parse_int_env(name: str, default: int) -> int:
    """Read an integer env var, falling back to *default* on absence/parse error."""
    raw = (_env(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        full_name = f"{_ENV_PREFIX}_{name}"
        logger.warning(
            "load_config: invalid %s=%r, using default %s",
            full_name,
            raw,
            default,
        )
        return default


@dataclass
class VaultConfig:
    """Configuration for a :class:`~markdown_vault_mcp.vault.Vault`.

    Attributes:
        source_dir: Root directory of the markdown vault.
        read_only: When ``True`` (default), write operations raise
            :exc:`~markdown_vault_mcp.exceptions.ReadOnlyError`.
        server_name: Display name for the MCP server (default
            ``"markdown-vault-mcp"``).
        instructions: Optional server-level instructions surfaced to clients.
        git: Git auth, identity, and sync cadence settings.
        indexing: SQLite/vector index paths and frontmatter/exclusion settings.
        embeddings: Embedding provider selection and per-provider settings.
        search: Search ranking and snippet-truncation knobs.
        sync: File-watcher and GitHub-webhook settings.
        content: Attachment/note-read limits and template/prompt folder paths.
        transfer: One-time upload/download transfer-link TTL and size settings.
        server: Shared server-level configuration (transport, host/port,
            auth, base URL, event store URL, MCP App domain) populated
            from ``MARKDOWN_VAULT_MCP_*`` env vars by
            :meth:`fastmcp_pvl_core.ServerConfig.from_env`.

    Example::

        config = load_config()
        vault = Vault(**config.to_vault_kwargs())
    """

    # CONFIG-FIELDS-START — domain fields; kept across copier update
    source_dir: Path
    read_only: bool = True
    server_name: str = "markdown-vault-mcp"
    instructions: str | None = None
    git: GitConfig = field(default_factory=GitConfig)
    indexing: IndexingConfig = field(default_factory=IndexingConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    content: ContentConfig = field(default_factory=ContentConfig)
    transfer: TransferConfig = field(default_factory=TransferConfig)
    # CONFIG-FIELDS-END

    # Universal server fields delegated to fastmcp_pvl_core.ServerConfig.
    server: ServerConfig = field(default_factory=ServerConfig)

    def to_vault_kwargs(self) -> dict[str, Any]:
        """Return keyword arguments suitable for ``Vault(**kwargs)``.

        Resolves the embedding provider (when ``indexing.embeddings_path``
        is set) and creates a :class:`~markdown_vault_mcp.git.GitWriteStrategy`.

        Returns:
            Dict of keyword arguments accepted by
            :class:`~markdown_vault_mcp.vault.Vault.__init__`.

        Example::

            config = load_config()
            vault = Vault(**config.to_vault_kwargs())
        """
        kwargs: dict[str, Any] = {
            "source_dir": self.source_dir,
            "read_only": self.read_only,
            "index_path": self.indexing.index_path,
            "embeddings_path": self.indexing.embeddings_path,
            "state_path": self.indexing.state_path,
            "indexed_frontmatter_fields": self.indexing.indexed_frontmatter_fields,
            "required_frontmatter": self.indexing.required_frontmatter,
            "exclude_patterns": self.indexing.exclude_patterns,
            "attachment_extensions": self.content.attachment_extensions,
            "max_attachment_size_mb": self.content.max_attachment_size_mb,
            "max_note_read_bytes": self.content.max_note_read_bytes,
            "git_pull_interval_s": 0,
            "chunks_per_file": self.search.chunks_per_file,
            "snippet_words": self.search.snippet_words,
            "length_downweight_alpha": self.search.length_downweight_alpha,
            "max_chunk_words": self.search.max_chunk_words,
        }

        # Semantic search is gated by the storage path in config.indexing,
        # while the provider lives in config.embeddings (cross-section coupling).
        # ValueError propagates — it means the user set an invalid provider
        # name, which is a config mistake that should not be silenced.
        if self.indexing.embeddings_path is not None:
            try:
                from markdown_vault_mcp.providers import get_embedding_provider

                kwargs["embedding_provider"] = get_embedding_provider(self)
            except (ImportError, RuntimeError):
                logger.warning(
                    "Could not load embedding provider; semantic search disabled",
                    exc_info=True,
                )

        if self.git.repo_url is not None:
            git_strategy = self._build_git_strategy(
                token=self.git.token,
                repo_url=self.git.repo_url,
                managed=True,
                enable_pull=True,
                enable_push=True,
            )
            kwargs["git_pull_interval_s"] = self.git.pull_interval_s
            kwargs["git_strategy"] = git_strategy
            kwargs["on_write"] = git_strategy
            return kwargs

        # Backward compatibility mode: token without explicit repo URL keeps
        # pull+push semantics, using the existing local checkout's origin.
        if self.git.token is not None:
            git_strategy = self._build_git_strategy(
                token=self.git.token,
                managed=False,
                enable_pull=True,
                enable_push=True,
            )
            kwargs["git_pull_interval_s"] = self.git.pull_interval_s
            kwargs["git_strategy"] = git_strategy
            kwargs["on_write"] = git_strategy
            return kwargs

        # Unmanaged / commit-only mode: commit locally if repo exists, never pull/push.
        git_strategy = self._build_git_strategy(
            token=None,
            managed=False,
            enable_pull=False,
            enable_push=False,
        )
        kwargs["git_strategy"] = git_strategy
        kwargs["on_write"] = git_strategy
        return kwargs

    def _build_git_strategy(
        self,
        *,
        token: str | None,
        managed: bool,
        enable_pull: bool,
        enable_push: bool,
        repo_url: str | None = None,
    ) -> GitWriteStrategy:
        """Build a GitWriteStrategy with the kwargs shared across all three git modes."""
        return GitWriteStrategy(
            token=token,
            repo_url=repo_url,
            managed=managed,
            enable_pull=enable_pull,
            enable_push=enable_push,
            username=self.git.username,
            push_delay_s=self.git.push_delay_s,
            commit_name=self.git.commit_name,
            commit_email=self.git.commit_email,
            commit_name_claim=self.git.commit_name_claim,
            commit_email_claim=self.git.commit_email_claim,
            git_lfs=self.git.lfs,
            repo_path=self.source_dir,
        )


def load_config() -> VaultConfig:
    """Load configuration from environment variables.

    Reads the following environment variables:

    **Core:**

    - ``MARKDOWN_VAULT_MCP_SOURCE_DIR`` (required): path to markdown files.
    - ``MARKDOWN_VAULT_MCP_READ_ONLY``: disable write tools; default ``true``.
    - ``MARKDOWN_VAULT_MCP_INDEX_PATH``: SQLite index path; default in-memory.
    - ``MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH``: embeddings directory; default
      disabled.
    - ``MARKDOWN_VAULT_MCP_STATE_PATH``: state file path; default
      ``{source_dir}/.markdown_vault_mcp/state.json``.
    - ``MARKDOWN_VAULT_MCP_INDEXED_FIELDS``: comma-separated frontmatter
      fields to index; default none.
    - ``MARKDOWN_VAULT_MCP_REQUIRED_FIELDS``: comma-separated required
      frontmatter fields; default none.
    - ``MARKDOWN_VAULT_MCP_EXCLUDE``: comma-separated glob patterns to
      exclude; default none.

    **Git:**

    - ``MARKDOWN_VAULT_MCP_GIT_TOKEN``: token for git write strategy; default
      disabled.
    - ``MARKDOWN_VAULT_MCP_GIT_REPO_URL``: HTTPS remote URL for managed git mode;
      when set, startup may clone into ``SOURCE_DIR``.
    - ``MARKDOWN_VAULT_MCP_GIT_USERNAME``: username for token auth in managed
      mode; default ``x-access-token``.
    - ``MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S``: seconds of idle before pushing
      (default ``30``).  Set to ``0`` to push only on shutdown.
    - ``MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME``: git committer name for
      auto-commits; default ``markdown-vault-mcp``.
    - ``MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL``: git committer email for
      auto-commits; default ``noreply@markdown-vault-mcp``.
    - ``MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM``: OIDC claim key whose value
      overrides the commit author name when an OIDC access token is present
      (e.g. ``name``).  Unset by default (static name used for all commits).
    - ``MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM``: OIDC claim key whose value
      overrides the commit author e-mail when an OIDC access token is present
      (e.g. ``email``).  Unset by default.
    - ``MARKDOWN_VAULT_MCP_GIT_LFS``: run ``git lfs pull`` during git strategy
      init to resolve LFS pointers; default ``true``.
    - ``MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S``: seconds between periodic
      git fetch + ff-only updates (default ``600``). Set to ``0`` to disable.

    **Attachments and templates:**

    - ``MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS``: comma-separated list of
      allowed attachment extensions (without dot, e.g. ``pdf,png,jpg``); use
      ``*`` to allow all non-.md files; default: common document and image types.
    - ``MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB``: maximum attachment size in
      megabytes for read and write; ``0`` disables the limit; default ``1.0``.
    - ``MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES``: maximum bytes returned by
      full-document ``read()`` for ``.md`` files; ``read(path, section=...)``
      bypasses the cap; ``0`` disables; default ``262144`` (256 KB).
    - ``MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER``: relative folder path where
      template markdown files are stored; default ``_templates``.
    - ``MARKDOWN_VAULT_MCP_PROMPTS_FOLDER``: relative folder path where
      user-defined prompt markdown files are stored; default ``None`` (disabled).

    **Server identity:**

    - ``MARKDOWN_VAULT_MCP_SERVER_NAME``: display name for the MCP server;
      default ``"markdown-vault-mcp"``.
    - ``MARKDOWN_VAULT_MCP_INSTRUCTIONS``: server-level instructions surfaced
      to clients; default ``None``.

    **Embedding providers:**

    - ``MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER``: embedding provider name
      (``"ollama"``, ``"openai"``, ``"fastembed"``); default ``None``.
    - ``OLLAMA_HOST``: Ollama API base URL (ecosystem standard, bare env var);
      default ``"http://localhost:11434"``.
    - ``MARKDOWN_VAULT_MCP_OLLAMA_MODEL``: Ollama model name; default
      ``"nomic-embed-text"``.
    - ``MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY``: CPU-only Ollama inference;
      default ``false``.
    - ``OPENAI_API_KEY``: OpenAI API key (ecosystem standard, bare env var).
    - ``MARKDOWN_VAULT_MCP_OPENAI_BASE_URL`` or ``OPENAI_BASE_URL``:
      OpenAI-compatible API base URL; default ``"https://api.openai.com/v1"``.
    - ``MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL`` or
      ``OPENAI_EMBEDDING_MODEL``: embedding model name; default
      ``"text-embedding-3-small"``.
    - ``MARKDOWN_VAULT_MCP_FASTEMBED_MODEL``: FastEmbed model name; default
      ``"BAAI/bge-small-en-v1.5"``.
    - ``MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR``: FastEmbed model cache
      directory; default ``None``.

    **Transfer links:**

    - ``MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S``: token lifetime when the
      caller omits one; default ``3600`` (1 hour).
    - ``MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S``: ceiling a requested TTL is
      clamped to; default ``86400`` (24 hours).
    - ``MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES``: per-upload size cap in
      bytes; default ``104857600`` (100 MiB).

    Transport and auth variables (``TRANSPORT``, ``HOST``, ``PORT``,
    ``BASE_URL``, ``AUTH_MODE``, ``OIDC_*``, ``BEARER_TOKEN``,
    ``KV_STORE_URL`` (legacy ``EVENT_STORE_URL``), ``APP_DOMAIN``) are read
    into ``config.server`` by
    :meth:`fastmcp_pvl_core.ServerConfig.from_env`; see
    ``docs/configuration.md`` for the full list.

    Returns:
        A fully populated :class:`VaultConfig` instance.

    Raises:
        ValueError: If ``MARKDOWN_VAULT_MCP_SOURCE_DIR`` is not set.

    Example::

        import os
        os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/home/user/vault"
        config = load_config()
        vault = Vault(**config.to_vault_kwargs())
    """
    raw_source_dir = (_env("SOURCE_DIR") or "").strip()
    if not raw_source_dir:
        raise ValueError(
            "MARKDOWN_VAULT_MCP_SOURCE_DIR is required but not set. "
            "Set it to the path of your markdown vault."
        )
    source_dir = Path(raw_source_dir)
    logger.debug("load_config: source_dir=%s", source_dir)

    raw_read_only = _env("READ_ONLY")
    read_only = _parse_bool(raw_read_only) if raw_read_only is not None else True
    logger.debug("load_config: read_only=%s (raw=%r)", read_only, raw_read_only)

    raw_index_path = (_env("INDEX_PATH") or "").strip()
    index_path: Path | None = Path(raw_index_path) if raw_index_path else None
    logger.debug("load_config: index_path=%s", index_path)

    raw_embeddings_path = (_env("EMBEDDINGS_PATH") or "").strip()
    embeddings_path: Path | None = (
        Path(raw_embeddings_path) if raw_embeddings_path else None
    )
    logger.debug("load_config: embeddings_path=%s", embeddings_path)

    raw_state_path = (_env("STATE_PATH") or "").strip()
    state_path: Path | None = Path(raw_state_path) if raw_state_path else None
    logger.debug("load_config: state_path=%s", state_path)

    raw_indexed_fields = (_env("INDEXED_FIELDS") or "").strip()
    indexed_frontmatter_fields: list[str] | None = (
        _parse_list(raw_indexed_fields) or None
    )
    logger.debug(
        "load_config: indexed_frontmatter_fields=%s", indexed_frontmatter_fields
    )

    raw_required_fields = (_env("REQUIRED_FIELDS") or "").strip()
    required_frontmatter: list[str] | None = _parse_list(raw_required_fields) or None
    logger.debug("load_config: required_frontmatter=%s", required_frontmatter)

    raw_exclude = (_env("EXCLUDE") or "").strip()
    exclude_patterns: list[str] | None = _parse_list(raw_exclude) or None
    logger.debug("load_config: exclude_patterns=%s", exclude_patterns)

    raw_git_token = (_env("GIT_TOKEN") or "").strip()
    git_token: str | None = raw_git_token or None
    logger.debug("load_config: git_token=%s", "set" if git_token else "not set")

    raw_git_repo_url = (_env("GIT_REPO_URL") or "").strip()
    git_repo_url: str | None = raw_git_repo_url or None
    logger.debug("load_config: git_repo_url=%s", git_repo_url or "not set")

    raw_git_username = (_env("GIT_USERNAME") or "").strip()
    git_username: str = raw_git_username or "x-access-token"
    logger.debug("load_config: git_username=%s", git_username)

    if git_token and not git_repo_url:
        logger.warning(
            "load_config: MARKDOWN_VAULT_MCP_GIT_TOKEN is set without "
            "MARKDOWN_VAULT_MCP_GIT_REPO_URL. This legacy mode is deprecated; "
            "set GIT_REPO_URL to enable explicit managed mode."
        )

    raw_commit_name = (_env("GIT_COMMIT_NAME") or "").strip()
    git_commit_name: str = raw_commit_name or "markdown-vault-mcp"
    logger.debug("load_config: git_commit_name=%s", git_commit_name)

    raw_commit_email = (_env("GIT_COMMIT_EMAIL") or "").strip()
    git_commit_email: str = raw_commit_email or "noreply@markdown-vault-mcp"
    logger.debug("load_config: git_commit_email=%s", git_commit_email)

    raw_commit_name_claim = (_env("GIT_COMMIT_NAME_CLAIM") or "").strip()
    git_commit_name_claim: str | None = raw_commit_name_claim or None
    logger.debug("load_config: git_commit_name_claim=%s", git_commit_name_claim)

    raw_commit_email_claim = (_env("GIT_COMMIT_EMAIL_CLAIM") or "").strip()
    git_commit_email_claim: str | None = raw_commit_email_claim or None
    logger.debug("load_config: git_commit_email_claim=%s", git_commit_email_claim)

    raw_push_delay = (_env("GIT_PUSH_DELAY_S") or "").strip()
    if raw_push_delay:
        try:
            git_push_delay_s = float(raw_push_delay)
        except ValueError:
            logger.warning(
                "load_config: invalid GIT_PUSH_DELAY_S=%r, using default 30.0",
                raw_push_delay,
            )
            git_push_delay_s = 30.0
    else:
        git_push_delay_s = 30.0
    logger.debug("load_config: git_push_delay_s=%s", git_push_delay_s)

    raw_git_lfs = _env("GIT_LFS")
    git_lfs: bool = _parse_bool(raw_git_lfs) if raw_git_lfs is not None else True
    logger.debug("load_config: git_lfs=%s (raw=%r)", git_lfs, raw_git_lfs)

    raw_pull_interval = (_env("GIT_PULL_INTERVAL_S") or "").strip()
    if raw_pull_interval:
        try:
            git_pull_interval_s = int(raw_pull_interval)
        except ValueError:
            logger.warning(
                "load_config: invalid GIT_PULL_INTERVAL_S=%r, using default 600",
                raw_pull_interval,
            )
            git_pull_interval_s = 600
    else:
        git_pull_interval_s = 600
    if git_pull_interval_s < 0:
        logger.warning(
            "load_config: GIT_PULL_INTERVAL_S=%r is negative, using 0 (disabled)",
            git_pull_interval_s,
        )
        git_pull_interval_s = 0
    logger.debug("load_config: git_pull_interval_s=%s", git_pull_interval_s)

    github_webhook_secret: str | None = _env("GITHUB_WEBHOOK_SECRET") or None
    logger.debug(
        "load_config: github_webhook_secret=%s",
        "set" if github_webhook_secret else "unset",
    )

    raw_file_watcher = _env("FILE_WATCHER")
    file_watcher_enabled: bool = (
        _parse_bool(raw_file_watcher) if raw_file_watcher is not None else True
    )
    logger.debug("load_config: file_watcher_enabled=%s", file_watcher_enabled)

    raw_debounce = (_env("FILE_WATCHER_DEBOUNCE_S") or "").strip()
    if raw_debounce:
        try:
            file_watcher_debounce_s = float(raw_debounce)
        except ValueError:
            logger.warning(
                "load_config: invalid FILE_WATCHER_DEBOUNCE_S=%r, using default 2.0",
                raw_debounce,
            )
            file_watcher_debounce_s = 2.0
    else:
        file_watcher_debounce_s = 2.0
    if file_watcher_debounce_s <= 0:
        logger.warning(
            "load_config: FILE_WATCHER_DEBOUNCE_S=%r must be > 0, using default 2.0",
            file_watcher_debounce_s,
        )
        file_watcher_debounce_s = 2.0
    logger.debug("load_config: file_watcher_debounce_s=%s", file_watcher_debounce_s)

    raw_attachment_extensions = (_env("ATTACHMENT_EXTENSIONS") or "").strip()
    attachment_extensions: list[str] | None
    if not raw_attachment_extensions:
        attachment_extensions = None  # use default list in Vault
    elif raw_attachment_extensions == "*":
        attachment_extensions = ["*"]
    else:
        attachment_extensions = _parse_list(raw_attachment_extensions) or None
    logger.debug("load_config: attachment_extensions=%s", attachment_extensions)

    raw_max_attachment_size = (_env("MAX_ATTACHMENT_SIZE_MB") or "").strip()
    if raw_max_attachment_size:
        try:
            max_attachment_size_mb = float(raw_max_attachment_size)
        except ValueError:
            logger.warning(
                "load_config: invalid MAX_ATTACHMENT_SIZE_MB=%r, using default 1.0",
                raw_max_attachment_size,
            )
            max_attachment_size_mb = 1.0
        else:
            if max_attachment_size_mb < 0:
                logger.warning(
                    "load_config: MAX_ATTACHMENT_SIZE_MB=%r is negative, using default 1.0",
                    max_attachment_size_mb,
                )
                max_attachment_size_mb = 1.0
    else:
        max_attachment_size_mb = 1.0
    logger.debug("load_config: max_attachment_size_mb=%s", max_attachment_size_mb)

    raw_max_note_read_bytes = (_env("MAX_NOTE_READ_BYTES") or "").strip()
    if raw_max_note_read_bytes:
        try:
            max_note_read_bytes = int(raw_max_note_read_bytes)
        except ValueError:
            logger.warning(
                "load_config: invalid MAX_NOTE_READ_BYTES=%r, using default 262144",
                raw_max_note_read_bytes,
            )
            max_note_read_bytes = 262144
        else:
            if max_note_read_bytes < 0:
                logger.warning(
                    "load_config: MAX_NOTE_READ_BYTES=%r is negative, using default 262144",
                    max_note_read_bytes,
                )
                max_note_read_bytes = 262144
    else:
        max_note_read_bytes = 262144
    logger.debug("load_config: max_note_read_bytes=%s", max_note_read_bytes)

    raw_templates_folder = (_env("TEMPLATES_FOLDER") or "").strip()
    templates_folder = (
        raw_templates_folder.replace("\\", "/").strip("/") or "_templates"
    )
    logger.debug("load_config: templates_folder=%s", templates_folder)

    raw_prompts_folder = (_env("PROMPTS_FOLDER") or "").strip()
    if raw_prompts_folder:
        pf = Path(raw_prompts_folder.replace("\\", "/"))
        if not pf.is_absolute():
            pf = source_dir / pf
        prompts_folder: str | None = str(pf)
    else:
        prompts_folder = None
    logger.debug("load_config: prompts_folder=%s", prompts_folder)

    # --- Server identity ---
    raw_server_name = (_env("SERVER_NAME") or "").strip()
    server_name: str = raw_server_name or "markdown-vault-mcp"
    logger.debug("load_config: server_name=%s", server_name)

    raw_instructions = (_env("INSTRUCTIONS") or "").strip()
    instructions: str | None = raw_instructions or None
    logger.debug("load_config: instructions=%s", "set" if instructions else "not set")

    # --- Embedding providers ---
    raw_embedding_provider = (_env("EMBEDDING_PROVIDER") or "").strip()
    embedding_provider: str | None = raw_embedding_provider or None
    logger.debug("load_config: embedding_provider=%s", embedding_provider or "not set")

    ollama_host: str = (
        os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    ).rstrip("/")
    logger.debug("load_config: ollama_host=%s", ollama_host)

    raw_ollama_model = (_env("OLLAMA_MODEL") or "").strip()
    ollama_model: str = raw_ollama_model or "nomic-embed-text"
    logger.debug("load_config: ollama_model=%s", ollama_model)

    raw_ollama_cpu_only = _env("OLLAMA_CPU_ONLY")
    ollama_cpu_only: bool = (
        _parse_bool(raw_ollama_cpu_only) if raw_ollama_cpu_only is not None else False
    )
    logger.debug("load_config: ollama_cpu_only=%s", ollama_cpu_only)

    raw_openai_api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    openai_api_key: str | None = raw_openai_api_key or None
    logger.debug(
        "load_config: openai_api_key=%s", "set" if openai_api_key else "not set"
    )

    raw_openai_base_url = (
        _env("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or ""
    ).strip()
    openai_base_url: str = (raw_openai_base_url or "https://api.openai.com/v1").rstrip(
        "/"
    )
    logger.debug("load_config: openai_base_url=%s", openai_base_url)

    raw_openai_embedding_model = (
        _env("OPENAI_EMBEDDING_MODEL") or os.environ.get("OPENAI_EMBEDDING_MODEL") or ""
    ).strip()
    openai_embedding_model: str = raw_openai_embedding_model or "text-embedding-3-small"
    logger.debug("load_config: openai_embedding_model=%s", openai_embedding_model)

    raw_fastembed_model = (_env("FASTEMBED_MODEL") or "").strip()
    fastembed_model: str = raw_fastembed_model or "BAAI/bge-small-en-v1.5"
    logger.debug("load_config: fastembed_model=%s", fastembed_model)

    raw_fastembed_cache_dir = (_env("FASTEMBED_CACHE_DIR") or "").strip()
    fastembed_cache_dir: str | None = raw_fastembed_cache_dir or None
    logger.debug(
        "load_config: fastembed_cache_dir=%s", fastembed_cache_dir or "not set"
    )

    # --- Search ranking and snippet truncation ---
    raw_chunks_per_file = (_env("CHUNKS_PER_FILE") or "").strip()
    if raw_chunks_per_file:
        try:
            chunks_per_file = int(raw_chunks_per_file)
        except ValueError as exc:
            raise ValueError(
                f"MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE must be a positive integer, "
                f"got {raw_chunks_per_file!r}"
            ) from exc
    else:
        chunks_per_file = 2
    if chunks_per_file < 1:
        raise ValueError(
            f"chunks_per_file must be >= 1, got {chunks_per_file}; set "
            "MARKDOWN_VAULT_MCP_CHUNKS_PER_FILE to a positive integer."
        )
    logger.debug("load_config: chunks_per_file=%s", chunks_per_file)

    raw_snippet_words = (_env("SNIPPET_WORDS") or "").strip()
    if raw_snippet_words:
        try:
            snippet_words = int(raw_snippet_words)
        except ValueError as exc:
            raise ValueError(
                f"MARKDOWN_VAULT_MCP_SNIPPET_WORDS must be a non-negative integer, "
                f"got {raw_snippet_words!r}"
            ) from exc
    else:
        snippet_words = 200
    if snippet_words < 0:
        raise ValueError(f"snippet_words must be >= 0, got {snippet_words}")
    logger.debug("load_config: snippet_words=%s", snippet_words)

    raw_alpha = (_env("LENGTH_DOWNWEIGHT_ALPHA") or "").strip()
    if raw_alpha:
        try:
            length_downweight_alpha = float(raw_alpha)
        except ValueError as exc:
            raise ValueError(
                f"MARKDOWN_VAULT_MCP_LENGTH_DOWNWEIGHT_ALPHA must be a non-negative "
                f"float, got {raw_alpha!r}"
            ) from exc
    else:
        length_downweight_alpha = 0.25
    if length_downweight_alpha < 0:
        raise ValueError(
            f"length_downweight_alpha must be >= 0, got {length_downweight_alpha}"
        )
    logger.debug("load_config: length_downweight_alpha=%s", length_downweight_alpha)

    raw_max_chunk_words = (_env("MAX_CHUNK_WORDS") or "").strip()
    if raw_max_chunk_words:
        try:
            max_chunk_words = int(raw_max_chunk_words)
        except ValueError as exc:
            raise ValueError(
                f"MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS must be a positive integer, "
                f"got {raw_max_chunk_words!r}"
            ) from exc
    else:
        max_chunk_words = 400
    if max_chunk_words < 1:
        raise ValueError(f"max_chunk_words must be >= 1, got {max_chunk_words}")
    logger.debug("load_config: max_chunk_words=%s", max_chunk_words)

    transfer_ttl_default_s = _parse_int_env("TRANSFER_TTL_DEFAULT_S", 3600)
    transfer_ttl_max_s = _parse_int_env("TRANSFER_TTL_MAX_S", 86400)
    transfer_max_upload_bytes = _parse_int_env("TRANSFER_MAX_UPLOAD_BYTES", 104857600)
    transfer = TransferConfig(
        ttl_default_s=transfer_ttl_default_s,
        ttl_max_s=transfer_ttl_max_s,
        max_upload_bytes=transfer_max_upload_bytes,
    )

    return VaultConfig(
        # CONFIG-FROM-ENV-START — domain fields populated from env; kept across copier update
        source_dir=source_dir,
        read_only=read_only,
        server_name=server_name,
        instructions=instructions,
        git=GitConfig(
            token=git_token,
            repo_url=git_repo_url,
            username=git_username,
            push_delay_s=git_push_delay_s,
            commit_name=git_commit_name,
            commit_email=git_commit_email,
            commit_name_claim=git_commit_name_claim,
            commit_email_claim=git_commit_email_claim,
            lfs=git_lfs,
            pull_interval_s=git_pull_interval_s,
        ),
        indexing=IndexingConfig(
            index_path=index_path,
            state_path=state_path,
            embeddings_path=embeddings_path,
            indexed_frontmatter_fields=indexed_frontmatter_fields,
            required_frontmatter=required_frontmatter,
            exclude_patterns=exclude_patterns,
        ),
        embeddings=EmbeddingsConfig(
            provider=embedding_provider,
            ollama_host=ollama_host,
            ollama_model=ollama_model,
            ollama_cpu_only=ollama_cpu_only,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            openai_embedding_model=openai_embedding_model,
            fastembed_model=fastembed_model,
            fastembed_cache_dir=fastembed_cache_dir,
        ),
        search=SearchConfig(
            chunks_per_file=chunks_per_file,
            snippet_words=snippet_words,
            length_downweight_alpha=length_downweight_alpha,
            max_chunk_words=max_chunk_words,
        ),
        sync=SyncConfig(
            file_watcher_enabled=file_watcher_enabled,
            file_watcher_debounce_s=file_watcher_debounce_s,
            github_webhook_secret=github_webhook_secret,
        ),
        content=ContentConfig(
            attachment_extensions=attachment_extensions,
            max_attachment_size_mb=max_attachment_size_mb,
            max_note_read_bytes=max_note_read_bytes,
            templates_folder=templates_folder,
            prompts_folder=prompts_folder,
        ),
        transfer=transfer,
        # CONFIG-FROM-ENV-END
        server=ServerConfig.from_env(_ENV_PREFIX),
    )
