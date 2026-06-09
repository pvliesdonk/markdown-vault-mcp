"""Embedding providers for markdown-vault-mcp.

Provides an :class:`EmbeddingProvider` ABC and three concrete implementations:

- :class:`OllamaProvider` — HTTP client to Ollama REST API.
- :class:`OpenAIProvider` — HTTP client to OpenAI Embeddings API.
- :class:`FastEmbedProvider` — local fastembed/ONNX runtime embeddings.

Use :func:`get_embedding_provider` to auto-detect and return the best
available provider based on a :class:`VaultConfig` instance.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from markdown_vault_mcp.config import VaultConfig

logger = logging.getLogger(__name__)

# Maximum texts per ONNX inference call inside FastEmbed.
# BAAI/bge-small-en-v1.5 (512-token context, the default model) has a
# manageable attention footprint even at batch_size=32.  If you switch to a
# long-context model such as nomic-embed-text-v1.5 (8192-token context) you
# may need to reduce this value significantly — see issue #306.
_FASTEMBED_ONNX_BATCH_SIZE = 32

# FastEmbed's model registry exposes only `dim`, not context length, so we
# carry a small table for the models we default to / commonly use. Unknown
# models return None and fall back to a conservative chunk cap.
_FASTEMBED_CONTEXT_LENGTHS: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 512,
    "BAAI/bge-base-en-v1.5": 512,
    "nomic-ai/nomic-embed-text-v1.5": 8192,
    "jinaai/jina-embeddings-v2-base-en": 8192,
}

# OpenAI's embeddings API does not report a model's context length, so we
# carry a table for the supported models. Unknown models return None and
# fall back to a conservative chunk cap.
_OPENAI_CONTEXT_LENGTHS: dict[str, int] = {
    "text-embedding-3-small": 8191,
    "text-embedding-3-large": 8191,
    "text-embedding-ada-002": 8191,
}


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

    @property
    @abstractmethod
    def context_length(self) -> int | None:
        """Maximum input length the model accepts, in tokens.

        Returns None when the limit cannot be determined; callers fall back
        to a conservative default. Used to derive a conservative chunker char
        cap that keeps chunks comfortably under the model's token limit (a
        token-dense batch that still exceeds it is skipped at embed time).
        """
        ...


class OllamaProvider(EmbeddingProvider):
    """Embedding provider backed by the Ollama REST API.

    Args:
        host: Base URL of the Ollama server.
        model: Model name to use for embeddings.
        cpu_only: When ``True``, request CPU-only inference (sets
            ``num_gpu=0`` in the Ollama options payload).
    """

    def __init__(self, host: str, model: str, *, cpu_only: bool = False) -> None:
        """Initialise OllamaProvider with explicit parameters.

        Args:
            host: Base URL of the Ollama server.
            model: Model name to use for embeddings.
            cpu_only: When ``True``, request CPU-only inference.

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
        self._context_length: int | None = None
        self._context_queried = False

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

    @property
    def context_length(self) -> int | None:
        """Query /api/show once for the model's context length; cache it.

        Returns None if the query fails or the field is absent. The result
        (including ``None`` on failure) is cached permanently for the provider
        instance — a transiently-unreachable Ollama at startup is not retried,
        so the conservative fallback cap persists until the server restarts.
        """
        if self._context_queried:
            return self._context_length
        self._context_queried = True
        try:
            with self._httpx.Client() as client:
                resp = client.post(
                    f"{self._host}/api/show",
                    json={"model": self._model},
                    timeout=10.0,
                )
            if resp.status_code != 200:
                logger.warning(
                    "ollama_context_length_unavailable model=%s status=%s",
                    self._model,
                    resp.status_code,
                )
                return None
            data = resp.json()
            info = data.get("model_info", {}) if isinstance(data, dict) else None
            if not isinstance(info, dict):
                # A well-behaved Ollama returns an object whose model_info is an
                # object; a proxy/error page (or a malformed model_info) could
                # be a list or string, on which .get()/.items() would raise an
                # uncaught AttributeError and crash startup.
                logger.warning("ollama_context_length_absent model=%s", self._model)
                return None
            for key, value in info.items():
                if key.endswith(".context_length") and isinstance(value, int):
                    self._context_length = value
                    return value
            logger.warning("ollama_context_length_absent model=%s", self._model)
            return None
        except (self._httpx.HTTPError, ValueError) as exc:
            # httpx network/timeout errors (ConnectError, TimeoutException, ...)
            # subclass httpx.HTTPError, NOT OSError; a malformed body raises
            # JSONDecodeError (a ValueError). Catching both keeps an unreachable
            # Ollama at startup from crashing config construction (config.py
            # reads this eagerly to derive the chunk char cap).
            logger.warning(
                "ollama_context_length_query_failed model=%s err=%s",
                self._model,
                exc,
            )
            return None


class OpenAIProvider(EmbeddingProvider):
    """Embedding provider backed by the OpenAI-compatible Embeddings API.

    Args:
        api_key: OpenAI API key for authentication.
        base_url: Base URL for an OpenAI-compatible API.
        model: Embedding model name.
    """

    _MODEL = "text-embedding-3-small"
    _BASE_URL = "https://api.openai.com/v1"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _BASE_URL,
        model: str = _MODEL,
    ) -> None:
        """Initialise OpenAIProvider with an explicit API key.

        Args:
            api_key: OpenAI API key for authentication.
            base_url: Base URL for an OpenAI-compatible API.
            model: Embedding model name.

        Raises:
            ImportError: If ``httpx`` is not installed.
            RuntimeError: If ``api_key`` is empty.
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
            raise RuntimeError("OpenAIProvider requires a non-empty api_key.")
        self._api_key = api_key
        self._base_url = (base_url or self._BASE_URL).rstrip("/")
        self._endpoint = f"{self._base_url}/embeddings"
        self._model = model or self._MODEL
        self._dimension: int | None = None

        logger.debug(
            "OpenAIProvider initialised: base_url=%s model=%s",
            self._base_url,
            self._model,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via the OpenAI Embeddings API.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors in input order.

        Raises:
            RuntimeError: If the OpenAI API returns an error response.
        """
        payload = {"input": texts, "model": self._model}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        logger.debug(
            "POST %s model=%s texts=%d", self._endpoint, self._model, len(texts)
        )

        with self._httpx.Client() as client:
            response = client.post(
                self._endpoint, json=payload, headers=headers, timeout=30.0
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenAI API error {response.status_code}: {response.text}"
            )

        data = response.json()
        # Sort by index to guarantee input order is preserved.
        items: list[dict[str, Any]] = sorted(data["data"], key=lambda d: d["index"])
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
        return self._model

    @property
    def context_length(self) -> int | None:
        """Return the model's context length from the known-model table.

        Returns None for models absent from the table; callers fall back to a
        conservative chunk cap.
        """
        return _OPENAI_CONTEXT_LENGTHS.get(self._model)


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
        if self._cache_dir:
            self._model = TextEmbedding(
                model_name=self._model_name, cache_dir=self._cache_dir
            )
        else:
            self._model = TextEmbedding(model_name=self._model_name)
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

    @property
    def context_length(self) -> int | None:
        """Return the model's context length from the known-model table.

        Returns None for models absent from the table; callers fall back to a
        conservative chunk cap.
        """
        return _FASTEMBED_CONTEXT_LENGTHS.get(self._model_name)


def get_embedding_provider(config: VaultConfig) -> EmbeddingProvider:
    """Auto-detect and return an embedding provider from config.

    Checks ``config.embeddings.provider`` for an explicit selection. When
    that field is ``None``, probes for available providers in this order:

    1. If ``config.embeddings.openai_api_key`` is set → :class:`OpenAIProvider`.
    2. If Ollama is reachable at ``config.embeddings.ollama_host`` →
       :class:`OllamaProvider`.
    3. If ``fastembed`` can be imported →
       :class:`FastEmbedProvider`.
    4. Raises :class:`RuntimeError` with installation instructions.

    Args:
        config: Vault configuration containing embedding settings.

    Returns:
        An initialised :class:`EmbeddingProvider` instance.

    Raises:
        RuntimeError: If no provider is available and
            ``config.embeddings.provider`` is not set, or if the explicitly
            requested provider cannot be initialised.
        ValueError: If ``config.embeddings.provider`` is set to an
            unrecognised value.
    """
    explicit = (config.embeddings.provider or "").strip().lower()

    if explicit == "openai":
        logger.info("Using OpenAIProvider (embedding_provider=openai)")
        return OpenAIProvider(
            api_key=config.embeddings.openai_api_key or "",
            base_url=config.embeddings.openai_base_url,
            model=config.embeddings.openai_embedding_model,
        )

    if explicit == "ollama":
        logger.info("Using OllamaProvider (embedding_provider=ollama)")
        return OllamaProvider(
            host=config.embeddings.ollama_host,
            model=config.embeddings.ollama_model,
            cpu_only=config.embeddings.ollama_cpu_only,
        )

    if explicit == "fastembed":
        logger.info(
            "Using FastEmbedProvider (embedding_provider=%s)",
            explicit,
        )
        return FastEmbedProvider(
            model_name=config.embeddings.fastembed_model,
            cache_dir=config.embeddings.fastembed_cache_dir,
        )

    if explicit:
        raise ValueError(
            f"Unrecognised embedding_provider value: {explicit!r}. "
            "Valid values: 'openai', 'ollama', 'fastembed'."
        )

    # Auto-detect: OpenAI API key present?
    if config.embeddings.openai_api_key:
        logger.info("Auto-detected OpenAIProvider (openai_api_key is set)")
        return OpenAIProvider(
            api_key=config.embeddings.openai_api_key,
            base_url=config.embeddings.openai_base_url,
            model=config.embeddings.openai_embedding_model,
        )

    # Auto-detect: Ollama reachable? (EmbeddingsConfig.__post_init__ already
    # guarantees ollama_host is non-empty with no trailing slash.)
    host = config.embeddings.ollama_host
    try:
        import httpx

        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{host}/api/tags")
        if response.status_code == 200:
            logger.info("Auto-detected OllamaProvider (Ollama reachable at %s)", host)
            return OllamaProvider(
                host=host,
                model=config.embeddings.ollama_model,
                cpu_only=config.embeddings.ollama_cpu_only,
            )
    except Exception:
        logger.debug("Ollama not reachable at %s, skipping", host)

    # Auto-detect: fastembed importable?
    try:
        import fastembed  # noqa: F401

        logger.info("Auto-detected FastEmbedProvider")
        return FastEmbedProvider(
            model_name=config.embeddings.fastembed_model,
            cache_dir=config.embeddings.fastembed_cache_dir,
        )
    except ImportError:
        logger.debug("fastembed not available, skipping")

    raise RuntimeError(
        "No embedding provider is available. Install one of:\n"
        "  pip install 'markdown-vault-mcp[embeddings-api]'  # httpx for Ollama or OpenAI\n"
        "  pip install 'markdown-vault-mcp[embeddings]'       # fastembed (local)\n"
        "Or set OPENAI_API_KEY for the OpenAI provider, "
        "or start an Ollama server for the Ollama provider."
    )
