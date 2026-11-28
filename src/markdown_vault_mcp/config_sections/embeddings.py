"""Embedding-provider configuration for semantic search."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from markdown_vault_mcp._env import parse_bool
from markdown_vault_mcp.embed_text import check_embed_input
from markdown_vault_mcp.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

# Deprecated variables already reported in this process: a CLI command can
# read the environment more than once (``_local_search`` then
# ``_build_vault``), and the operator needs the notice once.
_DEPRECATIONS_LOGGED: set[str] = set()


def read_embed_context(raw: str | None) -> bool | None:
    """Parse the deprecated ``EMBED_CONTEXT`` env value, logging its deprecation.

    Called from ``ProjectConfig.from_env`` only, never from a
    ``__post_init__``; the warning is logged once per process.

    Args:
        raw: The already-read ``EMBED_CONTEXT`` env value; ``None`` when unset
            or blank.

    Returns:
        ``None`` when unset, else the parsed flag.
    """
    if raw is None:
        return None
    value = parse_bool(raw)
    if "EMBED_CONTEXT" not in _DEPRECATIONS_LOGGED:
        _DEPRECATIONS_LOGGED.add("EMBED_CONTEXT")
        # At the effective level when it is above WARNING, as the
        # FASTMCP_LOG_LEVEL notice does, so a stricter LOG_LEVEL still shows it.
        logger.log(
            max(logging.WARNING, logger.getEffectiveLevel()),
            "embed_context_env_deprecated old=MARKDOWN_VAULT_MCP_EMBED_CONTEXT "
            "new=MARKDOWN_VAULT_MCP_EMBED_INPUT value=%s equivalent=%s",
            value,
            "context+fields" if value else "unset",
        )
    return value


@dataclass(frozen=True)
class EmbeddingsConfig:
    """Embedding provider selection, per-provider settings, and shared embedding execution knobs (embedding input, request timeout, batch size)."""

    provider: str | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "nomic-embed-text"
    ollama_cpu_only: bool = False
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embedding_model: str = "text-embedding-3-small"
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-4"
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    fastembed_cache_dir: str | None = None
    embed_context: bool = False
    embed_input: str | None = None
    embed_timeout_s: float = 30.0
    embedding_batch_size: int = 4

    def __post_init__(self) -> None:
        """Normalize hosts/URLs, validate knob bounds and the embedding input."""
        host = (self.ollama_host or "http://localhost:11434").rstrip("/")
        object.__setattr__(self, "ollama_host", host)
        base = (self.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        object.__setattr__(self, "openai_base_url", base)
        try:
            check_embed_input(self.embed_input, embed_context=self.embed_context)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
        if self.embed_timeout_s <= 0:
            raise ConfigurationError(
                f"embed_timeout_s must be > 0, got {self.embed_timeout_s!r}"
            )
        if self.embedding_batch_size < 1:
            raise ConfigurationError(
                f"embedding_batch_size must be >= 1, got {self.embedding_batch_size!r}"
            )
