"""Configuration loading from environment variables for markdown-vault-mcp.

Reads env vars and returns a :class:`CollectionConfig` suitable for
constructing a :class:`~markdown_vault_mcp.collection.Collection`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fastmcp_pvl_core import ServerConfig
from fastmcp_pvl_core import build_bearer_auth as _core_build_bearer_auth
from fastmcp_pvl_core import build_oidc_proxy_auth as _core_build_oidc_proxy_auth
from fastmcp_pvl_core import build_remote_auth as _core_build_remote_auth
from fastmcp_pvl_core import env as _core_env

# Direct re-exports: parse_bool/parse_list match MV's old call shape (take a
# str, return bool / list[str]) and call sites still gate with
# ``if raw_xxx is not None`` so no shim wrapper is needed.
from fastmcp_pvl_core import parse_bool as _parse_bool
from fastmcp_pvl_core import parse_list as _parse_list
from fastmcp_pvl_core import parse_scopes as _core_parse_scopes
from fastmcp_pvl_core import resolve_auth_mode as _core_resolve_auth_mode

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


def _parse_scopes(raw: str | None) -> list[str] | None:
    """Parse a comma- or space-separated OIDC scopes string.

    Routes through :func:`fastmcp_pvl_core.parse_scopes` but preserves MV's
    historical "blank → ``None``" semantics so existing auth-builder
    fallbacks (``required_scopes if raw is not None else ["openid"]``)
    keep working.  Core returns ``[]`` for blank input; MV needs ``None``.
    """
    result = _core_parse_scopes(raw)
    return result or None


@dataclass
class CollectionConfig:
    """Configuration for a :class:`~markdown_vault_mcp.collection.Collection`.

    Attributes:
        source_dir: Root directory of the markdown collection.
        read_only: When ``True`` (default), write operations raise
            :exc:`~markdown_vault_mcp.exceptions.ReadOnlyError`.
        index_path: Path to the persistent SQLite index file.  ``None``
            (default) uses an in-memory database.
        embeddings_path: Base path for vector index sidecar files.  ``None``
            (default) means semantic search is disabled.
        state_path: Path to the hash-state JSON file used by
            :class:`~markdown_vault_mcp.tracker.ChangeTracker`.  ``None``
            defaults to ``{source_dir}/.markdown_vault_mcp/state.json``.
        indexed_frontmatter_fields: Frontmatter keys whose values are
            promoted to the ``document_tags`` table for structured filtering.
            ``None`` means no fields are indexed.
        required_frontmatter: If set, documents missing any listed field are
            excluded from the index entirely.  ``None`` means all documents
            are indexed regardless of frontmatter.
        exclude_patterns: Glob patterns matched against relative document
            paths to exclude from scanning (e.g. ``[".obsidian/**"]``).
            ``None`` means no files are excluded.
        git_token: Personal access token (PAT) for HTTPS git push/pull
            authentication.  When set together with *git_repo_url*, the
            collection is managed in write-through git mode.
        git_repo_url: Remote git repository URL.  Required when *git_token*
            is set; the collection will clone or validate the repo on startup.
        git_username: Username used with token auth (default
            ``"x-access-token"``, GitHub-compatible).
        git_push_delay_s: Seconds of write-idle time before flushing local
            commits to the remote (default ``30.0``).  ``0`` means push only
            on shutdown.
        git_commit_name: Git committer name for auto-commits (default
            ``"markdown-vault-mcp"``).
        git_commit_email: Git committer e-mail for auto-commits (default
            ``"noreply@markdown-vault-mcp"``).
        git_lfs: When ``True`` (default), run ``git lfs pull`` during git
            strategy initialisation so LFS pointers are resolved before reads.
        git_pull_interval_s: Interval in seconds for periodic git fetch +
            fast-forward-only updates (default ``600``). Set to ``0`` to disable.
        server_name: Display name for the MCP server (default
            ``"markdown-vault-mcp"``).
        instructions: Optional server-level instructions surfaced to clients.
        attachment_extensions: Allowlist of file extensions (without the
            leading dot, e.g. ``["pdf", "png"]``) that may be stored as
            attachments.  ``["*"]`` accepts every extension.  ``None`` uses
            the built-in default list from
            :class:`~markdown_vault_mcp.collection.Collection`.
        max_attachment_size_mb: Maximum attachment file size in megabytes
            (default ``10.0``).  ``0`` means unlimited.
        templates_folder: Vault-relative folder that holds note templates
            (default ``"_templates"``).
        prompts_folder: Vault-relative folder from which user-defined MCP
            prompts are loaded at startup.  ``None`` disables user prompts.
        event_store_url: URL for the FastMCP persistent event store used by
            the HTTP transport (e.g. ``"file:///data/state/events"``).
            ``None`` defaults to ``/data/state/events``.
        auth_mode: Explicit OIDC mode override: ``"oidc-proxy"`` or
            ``"remote"``.  ``None`` (default) means auto-detect from which
            OIDC env vars are present.  Bearer and multi-auth are determined
            automatically by the presence of ``bearer_token`` and OIDC fields.
        base_url: Public base URL of the server, required for OIDC remote mode.
        oidc_config_url: OIDC discovery endpoint URL.
        oidc_client_id: OIDC client identifier.
        oidc_client_secret: OIDC client secret (logged as set/not set).
        oidc_audience: Expected ``aud`` claim in OIDC tokens.
        oidc_required_scopes: Comma-separated OIDC scopes to require.
        oidc_jwt_signing_key: Key for signing session JWTs (logged as
            set/not set).
        oidc_verify_access_token: When ``True``, verify the OIDC access
            token instead of the id_token (default ``False``).
        bearer_token: Static bearer token for simple auth (logged as
            set/not set).
        embedding_provider: Name of the embedding provider to use (e.g.
            ``"ollama"``, ``"openai"``, ``"fastembed"``).  ``None`` disables
            semantic search.
        ollama_host: Base URL for the Ollama API (default
            ``"http://localhost:11434"``).
        ollama_model: Ollama model name for embeddings (default
            ``"nomic-embed-text"``).
        ollama_cpu_only: When ``True``, request CPU-only inference from
            Ollama (default ``False``).
        openai_api_key: OpenAI API key for embeddings (logged as set/not
            set).
        fastembed_model: FastEmbed model name (default
            ``"BAAI/bge-small-en-v1.5"``).
        fastembed_cache_dir: Directory for FastEmbed model cache.  ``None``
            uses the library default.
        server: Shared server-level configuration (transport, host/port,
            auth, base URL, event store URL, MCP App domain) populated
            from ``MARKDOWN_VAULT_MCP_*`` env vars by
            :meth:`fastmcp_pvl_core.ServerConfig.from_env`.  Domain-specific
            duplicates above remain on :class:`CollectionConfig` until
            MV-PR2/3 migrate consumers to read from ``self.server.*``.

    Example::

        config = load_config()
        collection = Collection(**config.to_collection_kwargs())
    """

    source_dir: Path
    read_only: bool = True
    index_path: Path | None = None
    embeddings_path: Path | None = None
    state_path: Path | None = None
    indexed_frontmatter_fields: list[str] | None = None
    required_frontmatter: list[str] | None = None
    exclude_patterns: list[str] | None = None
    git_token: str | None = None
    git_repo_url: str | None = None
    git_username: str = "x-access-token"
    git_push_delay_s: float = 30.0
    git_commit_name: str = "markdown-vault-mcp"
    git_commit_email: str = "noreply@markdown-vault-mcp"
    git_lfs: bool = True
    git_pull_interval_s: int = 600
    attachment_extensions: list[str] | None = None
    max_attachment_size_mb: float = 10.0
    templates_folder: str = "_templates"
    prompts_folder: str | None = None
    event_store_url: str | None = None

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

    # Universal server fields delegated to fastmcp_pvl_core.ServerConfig.
    # Domain-specific fields above remain on CollectionConfig; subsequent
    # MV-PR2/3 will migrate consumers (auth builders, middleware) to read
    # from ``self.server.*`` instead of the local duplicates.
    server: ServerConfig = field(default_factory=ServerConfig)

    def __post_init__(self) -> None:
        """Normalize fields that must not be empty strings."""
        if not self.ollama_host:
            self.ollama_host = "http://localhost:11434"
        self.ollama_host = self.ollama_host.rstrip("/")

    def to_collection_kwargs(self) -> dict[str, Any]:
        """Return keyword arguments suitable for ``Collection(**kwargs)``.

        Resolves the embedding provider (when ``embeddings_path`` is set)
        and creates a :class:`~markdown_vault_mcp.git.GitWriteStrategy`.

        Returns:
            Dict of keyword arguments accepted by
            :class:`~markdown_vault_mcp.collection.Collection.__init__`.

        Example::

            config = load_config()
            collection = Collection(**config.to_collection_kwargs())
        """
        kwargs: dict[str, Any] = {
            "source_dir": self.source_dir,
            "read_only": self.read_only,
            "index_path": self.index_path,
            "embeddings_path": self.embeddings_path,
            "state_path": self.state_path,
            "indexed_frontmatter_fields": self.indexed_frontmatter_fields,
            "required_frontmatter": self.required_frontmatter,
            "exclude_patterns": self.exclude_patterns,
            "attachment_extensions": self.attachment_extensions,
            "max_attachment_size_mb": self.max_attachment_size_mb,
            "git_pull_interval_s": 0,
        }

        # Resolve embedding provider if embeddings_path is configured.
        # ValueError propagates — it means the user set an invalid provider
        # name, which is a config mistake that should not be silenced.
        if self.embeddings_path is not None:
            try:
                from markdown_vault_mcp.providers import get_embedding_provider

                kwargs["embedding_provider"] = get_embedding_provider(self)
            except (ImportError, RuntimeError):
                logger.warning(
                    "Could not load embedding provider; semantic search disabled",
                    exc_info=True,
                )

        from markdown_vault_mcp.git import GitWriteStrategy

        if self.git_repo_url is not None:
            git_strategy = GitWriteStrategy(
                token=self.git_token,
                username=self.git_username,
                repo_url=self.git_repo_url,
                managed=True,
                enable_pull=True,
                enable_push=True,
                push_delay_s=self.git_push_delay_s,
                commit_name=self.git_commit_name,
                commit_email=self.git_commit_email,
                git_lfs=self.git_lfs,
                repo_path=self.source_dir,
            )
            kwargs["git_pull_interval_s"] = self.git_pull_interval_s
            kwargs["git_strategy"] = git_strategy
            kwargs["on_write"] = git_strategy
            return kwargs

        # Backward compatibility mode: token without explicit repo URL keeps
        # pull+push semantics, using the existing local checkout's origin.
        if self.git_token is not None:
            git_strategy = GitWriteStrategy(
                token=self.git_token,
                username=self.git_username,
                managed=False,
                enable_pull=True,
                enable_push=True,
                push_delay_s=self.git_push_delay_s,
                commit_name=self.git_commit_name,
                commit_email=self.git_commit_email,
                git_lfs=self.git_lfs,
                repo_path=self.source_dir,
            )
            kwargs["git_pull_interval_s"] = self.git_pull_interval_s
            kwargs["git_strategy"] = git_strategy
            kwargs["on_write"] = git_strategy
            return kwargs

        # Unmanaged / commit-only mode: commit locally if repo exists, never pull/push.
        git_strategy = GitWriteStrategy(
            token=None,
            username=self.git_username,
            managed=False,
            enable_pull=False,
            enable_push=False,
            push_delay_s=self.git_push_delay_s,
            commit_name=self.git_commit_name,
            commit_email=self.git_commit_email,
            git_lfs=self.git_lfs,
            repo_path=self.source_dir,
        )
        kwargs["git_strategy"] = git_strategy
        kwargs["on_write"] = git_strategy
        return kwargs


def load_config() -> CollectionConfig:
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
    - ``MARKDOWN_VAULT_MCP_GIT_LFS``: run ``git lfs pull`` during git strategy
      init to resolve LFS pointers; default ``true``.
    - ``MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S``: seconds between periodic
      git fetch + ff-only updates (default ``600``). Set to ``0`` to disable.

    **Attachments and templates:**

    - ``MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS``: comma-separated list of
      allowed attachment extensions (without dot, e.g. ``pdf,png,jpg``); use
      ``*`` to allow all non-.md files; default: common document and image types.
    - ``MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB``: maximum attachment size in
      megabytes for read and write; ``0`` disables the limit; default ``10.0``.
    - ``MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER``: relative folder path where
      template markdown files are stored; default ``_templates``.
    - ``MARKDOWN_VAULT_MCP_PROMPTS_FOLDER``: relative folder path where
      user-defined prompt markdown files are stored; default ``None`` (disabled).
    - ``MARKDOWN_VAULT_MCP_EVENT_STORE_URL``: event store backend for HTTP session
      persistence; ``file:///path`` (default ``/data/state/events``) or
      ``memory://`` (in-memory, lost on restart).

    **Server identity:**

    - ``MARKDOWN_VAULT_MCP_SERVER_NAME``: display name for the MCP server;
      default ``"markdown-vault-mcp"``.
    - ``MARKDOWN_VAULT_MCP_INSTRUCTIONS``: server-level instructions surfaced
      to clients; default ``None``.

    **Authentication:**

    - ``MARKDOWN_VAULT_MCP_AUTH_MODE``: explicit OIDC mode override
      (``"oidc-proxy"`` or ``"remote"``); default auto-detect.  Bearer and
      multi-auth are determined by the presence of ``BEARER_TOKEN`` and OIDC
      fields.
    - ``MARKDOWN_VAULT_MCP_BASE_URL``: public base URL of the server.
    - ``MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL``: OIDC discovery endpoint URL.
    - ``MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID``: OIDC client identifier.
    - ``MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET``: OIDC client secret.
    - ``MARKDOWN_VAULT_MCP_OIDC_AUDIENCE``: expected ``aud`` claim in tokens.
    - ``MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES``: comma-separated required
      OIDC scopes.
    - ``MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY``: key for signing session
      JWTs.
    - ``MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN``: verify access token
      instead of id_token; default ``false``.
    - ``MARKDOWN_VAULT_MCP_BEARER_TOKEN``: static bearer token for simple auth.

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
    - ``MARKDOWN_VAULT_MCP_FASTEMBED_MODEL``: FastEmbed model name; default
      ``"BAAI/bge-small-en-v1.5"``.
    - ``MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR``: FastEmbed model cache
      directory; default ``None``.

    Returns:
        A fully populated :class:`CollectionConfig` instance.

    Raises:
        ValueError: If ``MARKDOWN_VAULT_MCP_SOURCE_DIR`` is not set.

    Example::

        import os
        os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"] = "/home/user/vault"
        config = load_config()
        collection = Collection(**config.to_collection_kwargs())
    """
    raw_source_dir = (_env("SOURCE_DIR") or "").strip()
    if not raw_source_dir:
        raise ValueError(
            "MARKDOWN_VAULT_MCP_SOURCE_DIR is required but not set. "
            "Set it to the path of your markdown collection."
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

    raw_attachment_extensions = (_env("ATTACHMENT_EXTENSIONS") or "").strip()
    attachment_extensions: list[str] | None
    if not raw_attachment_extensions:
        attachment_extensions = None  # use default list in Collection
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
                "load_config: invalid MAX_ATTACHMENT_SIZE_MB=%r, using default 10.0",
                raw_max_attachment_size,
            )
            max_attachment_size_mb = 10.0
        else:
            if max_attachment_size_mb < 0:
                logger.warning(
                    "load_config: MAX_ATTACHMENT_SIZE_MB=%r is negative, using default 10.0",
                    max_attachment_size_mb,
                )
                max_attachment_size_mb = 10.0
    else:
        max_attachment_size_mb = 10.0
    logger.debug("load_config: max_attachment_size_mb=%s", max_attachment_size_mb)

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

    raw_event_store_url = (_env("EVENT_STORE_URL") or "").strip()
    event_store_url: str | None = raw_event_store_url or None
    logger.debug(
        "load_config: event_store_url=%s", event_store_url or "not set (file default)"
    )

    # --- Server identity ---
    raw_server_name = (_env("SERVER_NAME") or "").strip()
    server_name: str = raw_server_name or "markdown-vault-mcp"
    logger.debug("load_config: server_name=%s", server_name)

    raw_instructions = (_env("INSTRUCTIONS") or "").strip()
    instructions: str | None = raw_instructions or None
    logger.debug("load_config: instructions=%s", "set" if instructions else "not set")

    # --- Auth ---
    raw_auth_mode = (_env("AUTH_MODE") or "").strip()
    auth_mode: str | None = raw_auth_mode or None
    logger.debug("load_config: auth_mode=%s", auth_mode or "auto-detect")

    raw_base_url = (_env("BASE_URL") or "").strip()
    base_url: str | None = raw_base_url or None
    logger.debug("load_config: base_url=%s", base_url or "not set")

    raw_oidc_config_url = (_env("OIDC_CONFIG_URL") or "").strip()
    oidc_config_url: str | None = raw_oidc_config_url or None
    logger.debug("load_config: oidc_config_url=%s", oidc_config_url or "not set")

    raw_oidc_client_id = (_env("OIDC_CLIENT_ID") or "").strip()
    oidc_client_id: str | None = raw_oidc_client_id or None
    logger.debug("load_config: oidc_client_id=%s", oidc_client_id or "not set")

    raw_oidc_client_secret = (_env("OIDC_CLIENT_SECRET") or "").strip()
    oidc_client_secret: str | None = raw_oidc_client_secret or None
    logger.debug(
        "load_config: oidc_client_secret=%s",
        "set" if oidc_client_secret else "not set",
    )

    raw_oidc_audience = (_env("OIDC_AUDIENCE") or "").strip()
    oidc_audience: str | None = raw_oidc_audience or None
    logger.debug("load_config: oidc_audience=%s", oidc_audience or "not set")

    raw_oidc_required_scopes = (_env("OIDC_REQUIRED_SCOPES") or "").strip()
    oidc_required_scopes: str | None = raw_oidc_required_scopes or None
    logger.debug(
        "load_config: oidc_required_scopes=%s", oidc_required_scopes or "not set"
    )

    raw_oidc_jwt_signing_key = (_env("OIDC_JWT_SIGNING_KEY") or "").strip()
    oidc_jwt_signing_key: str | None = raw_oidc_jwt_signing_key or None
    logger.debug(
        "load_config: oidc_jwt_signing_key=%s",
        "set" if oidc_jwt_signing_key else "not set",
    )

    raw_oidc_verify_access_token = _env("OIDC_VERIFY_ACCESS_TOKEN")
    oidc_verify_access_token: bool = (
        _parse_bool(raw_oidc_verify_access_token)
        if raw_oidc_verify_access_token is not None
        else False
    )
    logger.debug("load_config: oidc_verify_access_token=%s", oidc_verify_access_token)

    raw_bearer_token = (_env("BEARER_TOKEN") or "").strip()
    bearer_token: str | None = raw_bearer_token or None
    logger.debug("load_config: bearer_token=%s", "set" if bearer_token else "not set")

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

    raw_fastembed_model = (_env("FASTEMBED_MODEL") or "").strip()
    fastembed_model: str = raw_fastembed_model or "BAAI/bge-small-en-v1.5"
    logger.debug("load_config: fastembed_model=%s", fastembed_model)

    raw_fastembed_cache_dir = (_env("FASTEMBED_CACHE_DIR") or "").strip()
    fastembed_cache_dir: str | None = raw_fastembed_cache_dir or None
    logger.debug(
        "load_config: fastembed_cache_dir=%s", fastembed_cache_dir or "not set"
    )

    return CollectionConfig(
        source_dir=source_dir,
        read_only=read_only,
        index_path=index_path,
        embeddings_path=embeddings_path,
        state_path=state_path,
        indexed_frontmatter_fields=indexed_frontmatter_fields,
        required_frontmatter=required_frontmatter,
        exclude_patterns=exclude_patterns,
        git_token=git_token,
        git_repo_url=git_repo_url,
        git_username=git_username,
        git_push_delay_s=git_push_delay_s,
        git_commit_name=git_commit_name,
        git_commit_email=git_commit_email,
        git_lfs=git_lfs,
        git_pull_interval_s=git_pull_interval_s,
        attachment_extensions=attachment_extensions,
        max_attachment_size_mb=max_attachment_size_mb,
        templates_folder=templates_folder,
        prompts_folder=prompts_folder,
        event_store_url=event_store_url,
        server_name=server_name,
        instructions=instructions,
        auth_mode=auth_mode,
        base_url=base_url,
        oidc_config_url=oidc_config_url,
        oidc_client_id=oidc_client_id,
        oidc_client_secret=oidc_client_secret,
        oidc_audience=oidc_audience,
        oidc_required_scopes=oidc_required_scopes,
        oidc_jwt_signing_key=oidc_jwt_signing_key,
        oidc_verify_access_token=oidc_verify_access_token,
        bearer_token=bearer_token,
        embedding_provider=embedding_provider,
        ollama_host=ollama_host,
        ollama_model=ollama_model,
        ollama_cpu_only=ollama_cpu_only,
        openai_api_key=openai_api_key,
        fastembed_model=fastembed_model,
        fastembed_cache_dir=fastembed_cache_dir,
        server=ServerConfig.from_env(_ENV_PREFIX),
    )


# ---------------------------------------------------------------------------
# Auth builder functions — thin wrappers that delegate to fastmcp-pvl-core.
#
# Each wrapper accepts the legacy :class:`CollectionConfig` (which still
# carries duplicated auth/OIDC fields), constructs a transient
# :class:`ServerConfig` view via :func:`_server_from_collection`, and
# delegates to the core implementation.  The duplicates on
# :class:`CollectionConfig` will be removed in a later PR once consumers
# (currently :mod:`markdown_vault_mcp.mcp_server`) read directly from
# ``config.server`` instead.
# ---------------------------------------------------------------------------


def _server_from_collection(config: CollectionConfig) -> ServerConfig:
    """Build a :class:`ServerConfig` view from CollectionConfig duplicates.

    The duplicate auth fields on :class:`CollectionConfig` are still the
    source of truth for the auth wrappers in this module — until consumers
    migrate to :attr:`CollectionConfig.server`, we synthesize a transient
    :class:`ServerConfig` from the same duplicates so ``fastmcp_pvl_core``
    can do the actual work.
    """
    return ServerConfig(
        base_url=config.base_url,
        bearer_token=config.bearer_token,
        oidc_config_url=config.oidc_config_url,
        oidc_client_id=config.oidc_client_id,
        oidc_client_secret=config.oidc_client_secret,
        oidc_audience=config.oidc_audience,
        oidc_required_scopes=tuple(_parse_scopes(config.oidc_required_scopes) or ()),
        oidc_jwt_signing_key=config.oidc_jwt_signing_key,
        oidc_verify_access_token=config.oidc_verify_access_token,
        auth_mode=config.auth_mode,
    )


def resolve_auth_mode(config: CollectionConfig) -> str | None:
    """Return the OIDC auth flavor for *config*, or ``None`` for no OIDC.

    Existing callers (``mcp_server.create_server``) compose multi-auth
    themselves by combining the OIDC flavor with the bearer verifier, so
    this wrapper hides core's ``"multi"`` outcome by re-resolving with
    the bearer token suppressed — the caller still sees just the OIDC
    flavor (``"remote"`` / ``"oidc-proxy"``) and ``None`` for bearer-only
    or no-auth configurations.

    Args:
        config: Populated configuration object.

    Returns:
        ``"remote"``, ``"oidc-proxy"``, or ``None``.
    """
    server = _server_from_collection(config)
    mode = _core_resolve_auth_mode(server)
    if mode == "multi":
        mode = _core_resolve_auth_mode(replace(server, bearer_token=None))
    return None if mode in ("none", "bearer") else mode


def build_remote_auth(config: CollectionConfig) -> Any:
    """Build a :class:`RemoteAuthProvider` from OIDC discovery.

    Delegates to :func:`fastmcp_pvl_core.build_remote_auth`.  Returns
    ``None`` when ``base_url`` / ``oidc_config_url`` are missing, when
    ``httpx`` is not installed, when discovery fails, or when the
    discovery document is missing required keys.
    """
    return _core_build_remote_auth(_server_from_collection(config))


def build_bearer_auth(config: CollectionConfig) -> Any:
    """Build a :class:`StaticTokenVerifier` from ``config.bearer_token``.

    Delegates to :func:`fastmcp_pvl_core.build_bearer_auth`.  Returns
    ``None`` when the bearer token is absent or blank.
    """
    return _core_build_bearer_auth(_server_from_collection(config))


def build_oidc_auth(config: CollectionConfig) -> Any:
    """Build an :class:`OIDCProxy` provider, or return ``None``.

    Delegates to :func:`fastmcp_pvl_core.build_oidc_proxy_auth`.  Returns
    ``None`` when any of the four required fields (``base_url``,
    ``oidc_config_url``, ``oidc_client_id``, ``oidc_client_secret``) is
    missing.
    """
    return _core_build_oidc_proxy_auth(_server_from_collection(config))
