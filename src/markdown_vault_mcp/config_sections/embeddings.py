"""Embedding-provider configuration for semantic search."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError


@dataclass(frozen=True)
class EmbeddingsConfig:
    """Embedding provider selection, per-provider settings, and shared embedding execution knobs (context enrichment, request timeout, batch size)."""

    provider: str | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "nomic-embed-text"
    ollama_cpu_only: bool = False
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embedding_model: str = "text-embedding-3-small"
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    fastembed_cache_dir: str | None = None
    embed_context: bool = False
    embed_timeout_s: float = 30.0
    embedding_batch_size: int = 4

    def __post_init__(self) -> None:
        """Normalize hosts/URLs and validate embedding knob bounds."""
        host = (self.ollama_host or "http://localhost:11434").rstrip("/")
        object.__setattr__(self, "ollama_host", host)
        base = (self.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        object.__setattr__(self, "openai_base_url", base)
        if self.embed_timeout_s <= 0:
            raise ConfigurationError(
                f"embed_timeout_s must be > 0, got {self.embed_timeout_s!r}"
            )
        if self.embedding_batch_size < 1:
            raise ConfigurationError(
                f"embedding_batch_size must be >= 1, got {self.embedding_batch_size!r}"
            )
