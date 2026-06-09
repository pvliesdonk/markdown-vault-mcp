"""Configuration loading from environment variables for markdown-vault-mcp.

Reads env vars via :meth:`VaultConfig.from_env` and returns a
:class:`VaultConfig` suitable for constructing a
:class:`~markdown_vault_mcp.vault.Vault`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmcp_pvl_core import ServerConfig
from fastmcp_pvl_core import parse_bool as _parse_bool

from markdown_vault_mcp.config_sections import (
    ContentConfig,
    EmbeddingsConfig,
    GitConfig,
    IndexingConfig,
    SearchConfig,
    SyncConfig,
    TransferConfig,
)
from markdown_vault_mcp.config_sections._helpers import env as _env
from markdown_vault_mcp.git import GitWriteStrategy

logger = logging.getLogger(__name__)

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"

# Heuristic ratio converting an embedding model's token context length into a
# conservative character budget for the chunker. English prose averages ~4
# chars/token; 2.8 leaves headroom for token-dense (CJK, code, tables) content
# so a derived char cap stays safely under the model's real token limit.
_CHARS_PER_TOKEN = 2.8

# Fallback char cap when the model's context length is unknown and the operator
# set no explicit override.
_DEFAULT_MAX_CHUNK_CHARS = 6000


def derive_max_chunk_chars(*, context_length: int | None, override: int | None) -> int:
    """Resolve the chunker character cap.

    An explicit operator override wins; otherwise the cap is derived from the
    embedding model's token context length; otherwise a conservative fixed
    fallback is used.

    Args:
        context_length: The embedding model's maximum input length in tokens,
            or ``None`` when it cannot be determined.
        override: An explicit operator-supplied char cap, or ``None``.

    Returns:
        The character budget to pass to the chunker.
    """
    if override is not None:
        return override
    # ``context_length > 0`` guards against a degenerate 0 cap (no real model
    # reports a 0-token context, but a malformed/absent value must not yield a
    # chunker that splits everything to nothing).
    if context_length is not None and context_length > 0:
        return round(context_length * _CHARS_PER_TOKEN)
    return _DEFAULT_MAX_CHUNK_CHARS


@dataclass(frozen=True)
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

        config = VaultConfig.from_env()
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

            config = VaultConfig.from_env()
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
        provider = None
        if self.indexing.embeddings_path is not None:
            try:
                from markdown_vault_mcp import providers as _providers

                provider = _providers.get_embedding_provider(self)
                kwargs["embedding_provider"] = provider
            except (ImportError, RuntimeError):
                logger.warning(
                    "Could not load embedding provider; semantic search disabled",
                    exc_info=True,
                )

        # Derive the chunker char cap from the embedding model's token context
        # (a token-dense chunk that fits max_chunk_words can still exceed the
        # model context). An explicit override always wins; an unreachable or
        # unknown provider falls back to a conservative fixed cap.
        kwargs["max_chunk_chars"] = derive_max_chunk_chars(
            context_length=(provider.context_length if provider is not None else None),
            override=self.search.max_chunk_chars_override,
        )
        # The explicit override is also threaded straight through as the stable
        # warm-restart key (#649): the coordinator compares it (not the derived
        # cap) so a transient model-context read cannot trigger a rebuild.
        kwargs["max_chunk_chars_override"] = self.search.max_chunk_chars_override

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

    @classmethod
    def from_env(cls, prefix: str = _ENV_PREFIX) -> VaultConfig:
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

        Args:
            prefix: Env var prefix; defaults to ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            A fully populated :class:`VaultConfig` instance.

        Raises:
            ValueError: If ``MARKDOWN_VAULT_MCP_SOURCE_DIR`` is not set, or if a
                search ranking env var is invalid or out of range.

        Example::

            import os
            os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/home/user/vault"
            config = VaultConfig.from_env()
            vault = Vault(**config.to_vault_kwargs())
        """
        raw_source_dir = (_env(prefix, "SOURCE_DIR") or "").strip()
        if not raw_source_dir:
            raise ValueError(
                "MARKDOWN_VAULT_MCP_SOURCE_DIR is required but not set. "
                "Set it to the path of your markdown vault."
            )
        source_dir = Path(raw_source_dir)
        logger.debug("from_env: source_dir=%s", source_dir)

        raw_read_only = _env(prefix, "READ_ONLY")
        read_only = _parse_bool(raw_read_only) if raw_read_only is not None else True
        logger.debug("from_env: read_only=%s (raw=%r)", read_only, raw_read_only)

        raw_server_name = (_env(prefix, "SERVER_NAME") or "").strip()
        server_name = raw_server_name or "markdown-vault-mcp"
        logger.debug("from_env: server_name=%s", server_name)

        raw_instructions = (_env(prefix, "INSTRUCTIONS") or "").strip()
        instructions: str | None = raw_instructions or None
        logger.debug("from_env: instructions=%s", "set" if instructions else "not set")

        git = GitConfig.from_env(prefix)
        indexing = IndexingConfig.from_env(prefix)
        embeddings = EmbeddingsConfig.from_env(prefix)
        search = SearchConfig.from_env(prefix)
        sync = SyncConfig.from_env(prefix)
        content = ContentConfig.from_env(prefix, source_dir)
        transfer = TransferConfig.from_env(prefix)
        server = ServerConfig.from_env(prefix)

        if git.token and not git.repo_url:
            logger.warning(
                "from_env: MARKDOWN_VAULT_MCP_GIT_TOKEN is set without "
                "MARKDOWN_VAULT_MCP_GIT_REPO_URL. This legacy mode is deprecated; "
                "set GIT_REPO_URL to enable explicit managed mode."
            )

        return cls(
            # CONFIG-FROM-ENV-START — domain fields populated from env; kept across copier update
            source_dir=source_dir,
            read_only=read_only,
            server_name=server_name,
            instructions=instructions,
            git=git,
            indexing=indexing,
            embeddings=embeddings,
            search=search,
            sync=sync,
            content=content,
            transfer=transfer,
            # CONFIG-FROM-ENV-END
            server=server,
        )
