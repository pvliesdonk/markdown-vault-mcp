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
    embed_context: bool = False

    def __post_init__(self) -> None:
        """Normalize ollama_host and openai_base_url: non-empty, no trailing slash."""
        host = (self.ollama_host or "http://localhost:11434").rstrip("/")
        object.__setattr__(self, "ollama_host", host)
        base = (self.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        object.__setattr__(self, "openai_base_url", base)
