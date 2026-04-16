"""Configuration loading from environment variables for markdown-vault-mcp.

Reads env vars and returns a :class:`CollectionConfig` suitable for
constructing a :class:`~markdown_vault_mcp.collection.Collection`.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from fastmcp.server.event_store import EventStore

logger = logging.getLogger(__name__)

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"

# ---------------------------------------------------------------------------
# Event store
# ---------------------------------------------------------------------------

_DEFAULT_EVENT_STORE_DIR = "/data/state/events"


def build_event_store(url: str | None = None) -> EventStore:
    """Build an ``EventStore`` for SSE polling/resumability.

    Parses the *url* scheme to select a storage backend:

    - ``None`` or empty → ``FileTreeStore`` at :data:`_DEFAULT_EVENT_STORE_DIR`
    - ``file:///path`` → ``FileTreeStore`` at the given path
    - ``memory://`` → in-memory (lost on restart, for development)

    Args:
        url: Event store URL from ``MARKDOWN_VAULT_MCP_EVENT_STORE_URL``.

    Returns:
        A configured :class:`~fastmcp.server.event_store.EventStore`.
    """
    from fastmcp.server.event_store import EventStore as _EventStore

    if not url:
        url = f"file://{_DEFAULT_EVENT_STORE_DIR}"

    parsed = urlparse(url)

    if parsed.scheme == "memory":
        logger.info("Event store: in-memory (sessions lost on restart)")
        return _EventStore(max_events_per_stream=100, ttl=3600)

    if parsed.scheme == "file":
        directory = parsed.path
        if not directory:
            directory = _DEFAULT_EVENT_STORE_DIR
        Path(directory).mkdir(parents=True, exist_ok=True)
        logger.info("Event store: file-backed at %s", directory)

        try:
            from key_value.aio.stores.filetree import FileTreeStore
        except ImportError:
            raise ImportError(
                "FileTreeStore requires fastmcp>=3.0 with key-value support. "
                "Install with: pip install 'markdown-vault-mcp[mcp]'"
            ) from None

        storage = FileTreeStore(data_directory=directory)
        return _EventStore(storage=storage, max_events_per_stream=100, ttl=3600)

    raise ValueError(
        f"Unsupported EVENT_STORE_URL scheme {parsed.scheme!r}. "
        "Use 'file:///path' or 'memory://'."
    )


def _resolve_auth_mode(config: CollectionConfig) -> str | None:
    """Determine which OIDC auth mode to use.

    Uses ``config.auth_mode`` for an explicit override.
    When not set, auto-detects based on which config fields are present:

    - All four OIDC fields (base_url, oidc_config_url, oidc_client_id, oidc_client_secret) → ``"oidc-proxy"``
    - Only base_url + oidc_config_url → ``"remote"``
    - Otherwise → ``None`` (no OIDC)

    Returns:
        ``"remote"``, ``"oidc-proxy"``, or ``None``.
    """
    if config.auth_mode in ("remote", "oidc-proxy"):
        logger.info("OIDC auth mode: %s (explicit via AUTH_MODE)", config.auth_mode)
        return config.auth_mode
    if config.auth_mode:
        logger.warning(
            "Unknown AUTH_MODE %r — ignoring, falling back to auto-detection",
            config.auth_mode,
        )

    if all(
        [
            config.base_url,
            config.oidc_config_url,
            config.oidc_client_id,
            config.oidc_client_secret,
        ]
    ):
        logger.info(
            "OIDC auth mode: oidc-proxy (auto-detected — all four OIDC vars set)"
        )
        return "oidc-proxy"

    if config.base_url and config.oidc_config_url:
        logger.info(
            "OIDC auth mode: remote (auto-detected — BASE_URL + OIDC_CONFIG_URL set)"
        )
        return "remote"

    return None


def _build_remote_auth(config: CollectionConfig) -> Any:
    """Build a RemoteAuthProvider from OIDC discovery.

    Fetches the OIDC discovery document at startup to extract ``jwks_uri``
    and ``issuer``, then constructs a ``JWTVerifier`` for local token
    validation via JWKS.  No client credentials are needed — tokens are
    validated locally.

    Returns:
        A configured ``RemoteAuthProvider``, or ``None`` when env vars are
        missing or the discovery fetch fails.
    """
    if not config.base_url or not config.oidc_config_url:
        logger.debug("Remote auth: disabled — missing BASE_URL or OIDC_CONFIG_URL")
        return None

    try:
        import httpx
    except ImportError:
        logger.error(
            "Remote auth: 'httpx' is not installed. "
            "Install it with: pip install 'markdown-vault-mcp[all]' "
            "or pip install httpx"
        )
        return None

    try:
        resp = httpx.get(config.oidc_config_url, timeout=10)
        resp.raise_for_status()
        discovery = resp.json()
    except Exception:
        logger.exception(
            "Remote auth: failed to fetch OIDC discovery from %s",
            config.oidc_config_url,
        )
        return None

    jwks_uri = discovery.get("jwks_uri")
    issuer = discovery.get("issuer")
    if not jwks_uri or not issuer:
        logger.error(
            "Remote auth: OIDC discovery missing jwks_uri or issuer (got jwks_uri=%s, issuer=%s)",
            jwks_uri,
            issuer,
        )
        return None

    logger.debug(
        "Remote auth config:\n"
        "  config_url      = %s\n"
        "  jwks_uri        = %s\n"
        "  issuer          = %s\n"
        "  base_url        = %s\n"
        "  audience        = %s\n"
        "  required_scopes = %s",
        config.oidc_config_url,
        jwks_uri,
        issuer,
        config.base_url,
        config.oidc_audience or "(not set)",
        config.oidc_required_scopes or "(not set)",
    )

    from fastmcp.server.auth import JWTVerifier, RemoteAuthProvider

    verifier = JWTVerifier(
        jwks_uri=jwks_uri,
        issuer=issuer,
        audience=config.oidc_audience,
        required_scopes=config.oidc_required_scopes,
    )
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[issuer],
        base_url=config.base_url,
    )


def _build_bearer_auth(config: CollectionConfig) -> Any:
    """Build a StaticTokenVerifier from configured bearer token.

    When ``config.bearer_token`` is set (non-empty), returns a
    :class:`~fastmcp.server.auth.StaticTokenVerifier` that
    validates ``Authorization: Bearer <token>`` headers against the
    configured static token.

    Returns:
        A configured ``StaticTokenVerifier``, or ``None`` when the token
        is absent or empty.
    """
    if not config.bearer_token:
        logger.debug("Bearer auth: BEARER_TOKEN not set — skipping")
        return None
    logger.debug("Bearer auth: BEARER_TOKEN is set (value redacted)")
    from fastmcp.server.auth import StaticTokenVerifier

    return StaticTokenVerifier(
        tokens={
            config.bearer_token: {"client_id": "bearer", "scopes": ["read", "write"]}
        }
    )


def _build_oidc_auth(config: CollectionConfig) -> Any:
    """Build an OIDCProxy auth provider from config, or return None.

    All four of ``base_url``, ``oidc_config_url``, ``oidc_client_id``, and
    ``oidc_client_secret`` must be set to enable authentication.

    Returns:
        A configured :class:`~fastmcp.server.auth.oidc_proxy.OIDCProxy` instance,
        or ``None`` when authentication is disabled.
    """
    if not all(
        [
            config.base_url,
            config.oidc_config_url,
            config.oidc_client_id,
            config.oidc_client_secret,
        ]
    ):
        missing = [
            name
            for name, val in [
                ("BASE_URL", config.base_url),
                ("OIDC_CONFIG_URL", config.oidc_config_url),
                ("OIDC_CLIENT_ID", config.oidc_client_id),
                ("OIDC_CLIENT_SECRET", config.oidc_client_secret),
            ]
            if not val
        ]
        logger.debug("OIDC auth: disabled — missing env vars: %s", ", ".join(missing))
        return None

    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    required_scopes = config.oidc_required_scopes or ["openid"]
    verify_id_token = not config.oidc_verify_access_token

    logger.debug(
        "OIDC auth config:\n"
        "  config_url          = %s\n"
        "  client_id           = %s\n"
        "  client_secret       = <redacted>\n"
        "  base_url            = %s\n"
        "  audience            = %s\n"
        "  required_scopes     = %s\n"
        "  jwt_signing_key     = %s\n"
        "  verify_id_token     = %s\n"
        "  verify_access_token = %s",
        config.oidc_config_url,
        config.oidc_client_id,
        config.base_url,
        config.oidc_audience or "(not set)",
        required_scopes,
        "(set)" if config.oidc_jwt_signing_key else "(not set)",
        verify_id_token,
        config.oidc_verify_access_token,
    )

    if verify_id_token and "openid" not in required_scopes:
        logger.warning(
            "OIDC: verify_id_token=True requires the 'openid' scope but it is "
            "not in MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES — the id_token may "
            "be absent from the token response; add 'openid' to the scope list "
            "or set MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN=true"
        )

    if config.oidc_jwt_signing_key is None and sys.platform.startswith("linux"):
        logger.warning(
            "OIDC: MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY is not set — "
            "the JWT signing key is ephemeral on Linux; all clients must "
            "re-authenticate after every server restart"
        )

    if verify_id_token:
        logger.info(
            "OIDC: verifying upstream id_token (works with opaque access tokens)"
        )
    else:
        logger.info(
            "OIDC: verifying upstream access_token as JWT "
            "(MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN=true)"
        )

    assert config.oidc_config_url is not None
    assert config.oidc_client_id is not None
    assert config.base_url is not None

    return OIDCProxy(
        config_url=config.oidc_config_url,
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret or "",
        base_url=config.base_url,
        audience=config.oidc_audience,
        required_scopes=required_scopes,
        jwt_signing_key=config.oidc_jwt_signing_key,
        verify_id_token=verify_id_token,
        require_authorization_consent=False,
    )


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def _env(name: str, default: str | None = None) -> str | None:
    """Return the value of ``{_ENV_PREFIX}_{name}`` from the environment.

    Args:
        name: Suffix after the prefix (e.g. ``"SOURCE_DIR"``).
        default: Fallback when the variable is unset.

    Returns:
        The environment variable value, or *default*.
    """
    return os.environ.get(f"{_ENV_PREFIX}_{name}", default)


def _parse_bool(value: str) -> bool:
    """Parse a boolean from an environment variable string.

    Treats ``"true"``, ``"1"``, and ``"yes"`` (case-insensitive) as ``True``.
    Everything else is ``False``.

    Args:
        value: Raw environment variable string.

    Returns:
        ``True`` for truthy strings, ``False`` otherwise.
    """
    return value.strip().lower() in ("true", "1", "yes")


def _parse_list(value: str) -> list[str]:
    """Parse a comma-separated environment variable into a list of strings.

    Splits on commas, strips whitespace from each element, and filters out
    empty strings.

    Args:
        value: Raw environment variable string (e.g. ``"a, b, c"``).

    Returns:
        List of non-empty stripped strings.  Returns ``[]`` when *value* is
        blank.
    """
    return [item.strip() for item in value.split(",") if item.strip()]


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
        git_lfs: When ``True`` (default), run ``git lfs pull`` during git
            strategy initialisation so LFS pointers are resolved before reads.
        git_pull_interval_s: Interval in seconds for periodic git fetch +
            fast-forward-only updates (default ``600``). Set to ``0`` to disable.
        auth_mode: Explicit authentication mode (``"remote"``, ``"oidc-proxy"``,
            or ``None`` for auto-detect).
        base_url: Base URL for OIDC redirects and token verification.
        oidc_config_url: OIDC discovery endpoint URL.
        oidc_client_id: OIDC client ID.
        oidc_client_secret: OIDC client secret.
        oidc_audience: Expected audience claim in tokens.
        oidc_required_scopes: List of scopes required for access.
        oidc_jwt_signing_key: Static key for signing OIDC session JWTs.
        oidc_verify_access_token: If ``True``, verify the access token instead
            of the ID token.
        bearer_token: Static token for simple bearer authentication.
        embedding_provider: Explicit provider (``"openai"``, ``"ollama"``,
            ``"fastembed"``).
        ollama_host: Base URL of the Ollama server.
        ollama_model: Ollama model name.
        ollama_cpu_only: Force CPU-only inference for Ollama.
        openai_api_key: OpenAI API key.
        fastembed_model: FastEmbed model identifier.
        fastembed_cache_dir: Local directory to cache FastEmbed models.

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

    # Server settings
    server_name: str = "markdown-vault-mcp"
    instructions: str | None = None

    # Auth settings
    auth_mode: str | None = None
    base_url: str | None = None
    oidc_config_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_audience: str | None = None
    oidc_required_scopes: list[str] | None = None
    oidc_jwt_signing_key: str | None = None
    oidc_verify_access_token: bool = False
    bearer_token: str | None = None

    # Embedding settings
    embedding_provider: str | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "nomic-embed-text"
    ollama_cpu_only: bool = False
    openai_api_key: str | None = None
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    fastembed_cache_dir: str | None = None

    def to_collection_kwargs(self) -> dict[str, Any]:
        """Return keyword arguments suitable for ``Collection(**kwargs)``.

        Creates a
        :class:`~markdown_vault_mcp.git.GitWriteStrategy` and includes
        it as the ``on_write`` parameter.

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

        if self.embeddings_path:
            try:
                from markdown_vault_mcp.providers import get_embedding_provider

                kwargs["embedding_provider"] = get_embedding_provider(self)
            except Exception:
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


def load_config(strict: bool = True) -> CollectionConfig:
    """Load configuration from environment variables.

    Reads the following environment variables:

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

    The ``EMBEDDING_PROVIDER`` variable is intentionally **not** resolved here;
    call :func:`~markdown_vault_mcp.providers.get_embedding_provider`
    separately in the server layer.

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
    if not raw_source_dir and strict:
        raise ValueError(
            "MARKDOWN_VAULT_MCP_SOURCE_DIR is required but not set. "
            "Set it to the path of your markdown collection."
        )
    source_dir = Path(raw_source_dir or ".")
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

    # Auth settings
    auth_mode = (_env("AUTH_MODE") or "").strip().lower() or None
    base_url = (_env("BASE_URL") or "").strip() or None
    oidc_config_url = (_env("OIDC_CONFIG_URL") or "").strip() or None
    oidc_client_id = (_env("OIDC_CLIENT_ID") or "").strip() or None
    oidc_client_secret = (_env("OIDC_CLIENT_SECRET") or "").strip() or None
    oidc_audience = (_env("OIDC_AUDIENCE") or "").strip() or None
    oidc_required_scopes = _parse_list(_env("OIDC_REQUIRED_SCOPES") or "") or None
    oidc_jwt_signing_key = (_env("OIDC_JWT_SIGNING_KEY") or "").strip() or None
    oidc_verify_access_token = _parse_bool(_env("OIDC_VERIFY_ACCESS_TOKEN") or "false")
    bearer_token = (_env("BEARER_TOKEN") or "").strip() or None

    # Server settings
    server_name = _env("SERVER_NAME", "markdown-vault-mcp")
    instructions = _env("INSTRUCTIONS")

    # Embedding settings
    embedding_provider = (
        os.environ.get("EMBEDDING_PROVIDER", "").strip().lower() or None
    )
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    ollama_model = _env("OLLAMA_MODEL", "nomic-embed-text")
    ollama_cpu_only = _parse_bool(_env("OLLAMA_CPU_ONLY") or "false")
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    fastembed_model = _env("FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
    fastembed_cache_dir = _env("FASTEMBED_CACHE_DIR")

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
        server_name=server_name or "markdown-vault-mcp",
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
        ollama_model=ollama_model or "nomic-embed-text",
        ollama_cpu_only=ollama_cpu_only,
        openai_api_key=openai_api_key,
        fastembed_model=fastembed_model or "BAAI/bge-small-en-v1.5",
        fastembed_cache_dir=fastembed_cache_dir,
    )
