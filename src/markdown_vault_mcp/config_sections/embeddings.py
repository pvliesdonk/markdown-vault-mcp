"""Embedding-provider configuration for semantic search."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
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
        if not self.ollama_host:
            self.ollama_host = "http://localhost:11434"
        self.ollama_host = self.ollama_host.rstrip("/")
