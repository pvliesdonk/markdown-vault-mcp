"""Embedding-provider configuration for semantic search."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingsConfig:
    """Embedding provider selection + per-provider settings."""

    provider: str | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "nomic-embed-text"
    ollama_cpu_only: bool = False
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embedding_model: str = "text-embedding-3-small"
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    fastembed_cache_dir: str | None = None

    def __post_init__(self) -> None:
        """Normalize ollama_host: non-empty, no trailing slash."""
        host = (self.ollama_host or "http://localhost:11434").rstrip("/")
        object.__setattr__(self, "ollama_host", host)

    @classmethod
    def from_env(cls, prefix: str) -> EmbeddingsConfig:
        """Construct EmbeddingsConfig by reading ``{prefix}_*`` env vars.

        Reads ``OLLAMA_HOST`` and ``OPENAI_API_KEY`` from the bare (unprefixed)
        environment, matching the ecosystem conventions.  ``OPENAI_BASE_URL``
        and ``OPENAI_EMBEDDING_MODEL`` use prefixed-wins-bare-fallback semantics.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.

        Returns:
            Populated EmbeddingsConfig with defaults for unset vars.
        """
        import os

        from fastmcp_pvl_core import parse_bool

        from markdown_vault_mcp.config_sections._helpers import env

        # __post_init__ normalizes (strip trailing slash / empty→default).
        ollama_host = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
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
