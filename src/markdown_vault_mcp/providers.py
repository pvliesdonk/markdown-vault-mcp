"""Embedding providers for markdown-vault-mcp.

Provides an :class:`EmbeddingProvider` ABC and three concrete implementations:

- :class:`OllamaProvider` — HTTP client to Ollama REST API.
- :class:`OpenAIProvider` — HTTP client to OpenAI Embeddings API.
- :class:`FastEmbedProvider` — local fastembed/ONNX runtime embeddings.

Use :func:`get_embedding_provider` to auto-detect and return the best
available provider based on environment variables.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from markdown_vault_mcp.config import CollectionConfig

logger = logging.getLogger(__name__)

# Maximum texts per ONNX inference call inside FastEmbed.
# BAAI/bge-small-en-v1.5 (512-token context, the default model) has a
# manageable attention footprint even at batch_size=32.  If you switch to a
# long-context model such as nomic-embed-text-v1.5 (8192-token context) you
# may need to reduce this value significantly — see issue #306.
_FASTEMBED_ONNX_BATCH_SIZE = 32


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding dimension size.

        Returns:
            Integer dimension of each embedding vector.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier for index compatibility metadata."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stable model identifier for index compatibility metadata."""
        ...


class OllamaProvider(EmbeddingProvider):
    """Embedding provider backed by the Ollama REST API."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        cpu_only: bool = False,
    ) -> None:
        """Initialise OllamaProvider.

        Args:
            host: Base URL of the Ollama server.
            model: Model name to use.
            cpu_only: Force CPU-only inference.

        Raises:
            ImportError: If ``httpx`` is not installed.
        """
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "OllamaProvider requires 'httpx'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings-api]'"
            ) from exc

        self._httpx = httpx
        self._host = host.rstrip("/")
        self._model = model
        self._cpu_only = cpu_only
        self._dimension: int | None = None

        logger.debug(
            "OllamaProvider initialised: host=%s model=%s cpu_only=%s",
            self._host,
            self._model,
            self._cpu_only,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via the Ollama REST API.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.

        Raises:
            RuntimeError: If the Ollama API returns an error response.
        """
        payload: dict[str, object] = {"model": self._model, "input": texts}
        if self._cpu_only:
            payload["options"] = {"num_gpu": 0}

        url = f"{self._host}/api/embed"
        logger.debug("POST %s model=%s texts=%d", url, self._model, len(texts))

        with self._httpx.Client() as client:
            response = client.post(url, json=payload, timeout=30.0)

        if response.status_code != 200:
            raise RuntimeError(
                f"Ollama API error {response.status_code}: {response.text}"
            )

        data = response.json()
        embeddings: list[list[float]] = data["embeddings"]

        # Cache dimension from first successful call.
        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])

        return embeddings

    @property
    def dimension(self) -> int:
        """Embedding dimension size.

        Embeds a test string on first access to determine the dimension.

        Returns:
            Integer dimension of each embedding vector.
        """
        if self._dimension is None:
            self.embed(["dimension probe"])
        if self._dimension is None:
            raise RuntimeError(
                "OllamaProvider.embed() returned no embeddings; "
                "cannot determine dimension."
            )
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model


class OpenAIProvider(EmbeddingProvider):
    """Embedding provider backed by the OpenAI Embeddings API.

    Uses the ``text-embedding-3-small`` model.
    """

    _MODEL = "text-embedding-3-small"
    _ENDPOINT = "https://api.openai.com/v1/embeddings"

    def __init__(self, api_key: str | None = None) -> None:
        """Initialise OpenAIProvider.

        Args:
            api_key: OpenAI API key.

        Raises:
            ImportError: If ``httpx`` is not installed.
            RuntimeError: If ``api_key`` is not provided.
        """
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "OpenAIProvider requires 'httpx'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings-api]'"
            ) from exc

        self._httpx = httpx
        if not api_key:
            raise RuntimeError(
                "OpenAIProvider requires the OPENAI_API_KEY environment variable (passed as api_key)."
            )
        self._api_key = api_key
        self._dimension: int | None = None

        logger.debug("OpenAIProvider initialised: model=%s", self._MODEL)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via the OpenAI Embeddings API.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors in input order.

        Raises:
            RuntimeError: If the OpenAI API returns an error response.
        """
        payload = {"input": texts, "model": self._MODEL}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        logger.debug(
            "POST %s model=%s texts=%d", self._ENDPOINT, self._MODEL, len(texts)
        )

        with self._httpx.Client() as client:
            response = client.post(
                self._ENDPOINT, json=payload, headers=headers, timeout=30.0
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenAI API error {response.status_code}: {response.text}"
            )

        data = response.json()
        # Sort by index to guarantee input order is preserved.
        items: list[dict] = sorted(data["data"], key=lambda d: d["index"])
        embeddings: list[list[float]] = [item["embedding"] for item in items]

        # Cache dimension from first successful call.
        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])

        return embeddings

    @property
    def dimension(self) -> int:
        """Embedding dimension size.

        Embeds a test string on first access to determine the dimension.

        Returns:
            Integer dimension of each embedding vector.
        """
        if self._dimension is None:
            self.embed(["dimension probe"])
        if self._dimension is None:
            raise RuntimeError(
                "OpenAIProvider.embed() returned no embeddings; "
                "cannot determine dimension."
            )
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._MODEL


class FastEmbedProvider(EmbeddingProvider):
    """Embedding provider backed by the local fastembed library.

    The ``fastembed`` package is imported lazily at instantiation
    time so that it does not need to be installed unless this provider is used.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        cache_dir: str | None = None,
    ) -> None:
        """Initialise FastEmbed model.

        Args:
            model_name: FastEmbed model identifier.
            cache_dir: Optional model cache directory.

        Raises:
            ImportError: If ``fastembed`` is not installed.
        """
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise ImportError(
                "FastEmbedProvider requires 'fastembed'. "
                "Install it with: pip install 'markdown-vault-mcp[embeddings]'"
            ) from exc

        self._model_name = model_name
        self._cache_dir = cache_dir
        kwargs: dict[str, object] = {"model_name": self._model_name}
        if self._cache_dir:
            kwargs["cache_dir"] = self._cache_dir
        self._model = TextEmbedding(**kwargs)
        self._dimension: int | None = None
        logger.debug(
            "FastEmbedProvider initialised: model=%s cache_dir=%s",
            self._model_name,
            self._cache_dir,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using the local fastembed model.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        vectors = [
            vector.tolist()
            for vector in self._model.embed(
                texts, batch_size=_FASTEMBED_ONNX_BATCH_SIZE
            )
        ]
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
        return vectors

    @property
    def dimension(self) -> int:
        """Embedding dimension size from the loaded model.

        Returns:
            Integer dimension of each embedding vector.
        """
        if self._dimension is None:
            self.embed(["dimension probe"])
        if self._dimension is None:
            raise RuntimeError(
                "FastEmbedProvider.embed() returned no embeddings; "
                "cannot determine dimension."
            )
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "fastembed"

    @property
    def model_name(self) -> str:
        return self._model_name


def get_embedding_provider(config: CollectionConfig | None = None) -> EmbeddingProvider:
    """Auto-detect and return an embedding provider.

    Args:
        config: Optional :class:`~markdown_vault_mcp.config.CollectionConfig`.
            When provided, settings from the config take precedence over
            auto-detection.

    Returns:
        An initialised :class:`EmbeddingProvider` instance.

    Raises:
        RuntimeError: If no provider is available and no explicit provider is
            set in *config*, or if the requested provider cannot be initialised.
        ValueError: If an unrecognised provider is requested.
    """
    if config:
        explicit = config.embedding_provider
        if explicit == "openai":
            logger.info("Using OpenAIProvider (config)")
            return OpenAIProvider(api_key=config.openai_api_key)
        if explicit == "ollama":
            logger.info("Using OllamaProvider (config)")
            return OllamaProvider(
                host=config.ollama_host,
                model=config.ollama_model,
                cpu_only=config.ollama_cpu_only,
            )
        if explicit == "fastembed":
            logger.info("Using FastEmbedProvider (config)")
            return FastEmbedProvider(
                model_name=config.fastembed_model, cache_dir=config.fastembed_cache_dir
            )
        if explicit:
            raise ValueError(
                f"Unrecognised embedding_provider: {explicit!r}. "
                "Valid values: 'openai', 'ollama', 'fastembed'."
            )

    # Fallback to auto-detection (uses os.environ internally if config is missing)
    # but since we want to move away from os.environ, we should ideally always pass config.
    # For now, we'll maintain backward compatibility by reading env vars if config is None.
    # Actually, let's just use os.environ as a final fallback if config is None.

    provider_name = os.environ.get("EMBEDDING_PROVIDER", "").strip().lower()
    if provider_name == "openai":
        return OpenAIProvider(api_key=os.environ.get("OPENAI_API_KEY"))
    if provider_name == "ollama":
        return OllamaProvider(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model=os.environ.get("MARKDOWN_VAULT_MCP_OLLAMA_MODEL", "nomic-embed-text"),
            cpu_only=os.environ.get(
                "MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY", "false"
            ).lower()
            in ("true", "1", "yes"),
        )
    if provider_name == "fastembed":
        return FastEmbedProvider(
            model_name=os.environ.get(
                "MARKDOWN_VAULT_MCP_FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5"
            ),
            cache_dir=os.environ.get("MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR"),
        )

    # Auto-detect: OpenAI API key present?
    api_key = config.openai_api_key if config else os.environ.get("OPENAI_API_KEY")
    if api_key:
        logger.info("Auto-detected OpenAIProvider")
        return OpenAIProvider(api_key=api_key)

    # Auto-detect: Ollama reachable?
    host = (
        config.ollama_host
        if config
        else os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    try:
        import httpx

        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{host.rstrip('/')}/api/tags")
        if response.status_code == 200:
            logger.info("Auto-detected OllamaProvider (Ollama reachable at %s)", host)
            if config:
                return OllamaProvider(
                    host=config.ollama_host,
                    model=config.ollama_model,
                    cpu_only=config.ollama_cpu_only,
                )
            return OllamaProvider(host=host)
    except Exception:
        logger.debug("Ollama not reachable at %s, skipping", host)

    # Auto-detect: fastembed importable?
    try:
        import fastembed  # noqa: F401

        logger.info("Auto-detected FastEmbedProvider")
        if config:
            return FastEmbedProvider(
                model_name=config.fastembed_model, cache_dir=config.fastembed_cache_dir
            )
        return FastEmbedProvider()
    except ImportError:
        logger.debug("fastembed not available, skipping")

    raise RuntimeError(
        "No embedding provider is available. Install one of:\n"
        "  pip install 'markdown-vault-mcp[embeddings-api]'  # httpx for Ollama or OpenAI\n"
        "  pip install 'markdown-vault-mcp[embeddings]'       # fastembed (local)\n"
    )
