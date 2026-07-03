# Embedding Providers

The `providers` module defines an abstract base class for embedding providers and three concrete implementations for OpenAI, Ollama, and FastEmbed.

## Quick Start

```
from markdown_vault_mcp.providers import get_embedding_provider

# Auto-detect based on environment variables
provider = get_embedding_provider()

# Embed a batch of texts
vectors = provider.embed(["hello world", "example text"])
print(f"Dimension: {provider.dimension}")
```

## Provider Selection

The `get_embedding_provider()` function auto-detects the best available provider:

1. **OpenAI** (if `OPENAI_API_KEY` is set)
1. **Ollama** (if `OLLAMA_HOST` is reachable)
1. **FastEmbed** (if the package is installed)

Override with `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=openai|ollama|fastembed`. For OpenAI-compatible APIs, set `OPENAI_BASE_URL` and `OPENAI_EMBEDDING_MODEL`, or the prefixed equivalents `MARKDOWN_VAULT_MCP_OPENAI_BASE_URL` and `MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL`.

## API Reference

## `EmbeddingProvider`

Bases: `ABC`

Abstract base class for embedding providers.

### `dimension`

Embedding dimension size.

Returns:

| Type  | Description                                 |
| ----- | ------------------------------------------- |
| `int` | Integer dimension of each embedding vector. |

### `provider_name`

Stable provider identifier for index compatibility metadata.

### `model_name`

Stable model identifier for index compatibility metadata.

### `context_length`

Maximum input length the model accepts, in tokens.

Returns None when the limit cannot be determined; callers fall back to a conservative default. Used to derive a conservative chunker char cap that keeps chunks comfortably under the model's token limit (a token-dense batch that still exceeds it is skipped at embed time).

### `embed(texts)`

Embed a batch of texts.

Parameters:

| Name    | Type        | Description               | Default    |
| ------- | ----------- | ------------------------- | ---------- |
| `texts` | `list[str]` | List of strings to embed. | *required* |

Returns:

| Type                | Description                                    |
| ------------------- | ---------------------------------------------- |
| `list[list[float]]` | List of embedding vectors, one per input text. |

## `OllamaProvider(host, model, *, cpu_only=False)`

Bases: `EmbeddingProvider`

Embedding provider backed by the Ollama REST API.

Parameters:

| Name       | Type   | Description                                                                           | Default    |
| ---------- | ------ | ------------------------------------------------------------------------------------- | ---------- |
| `host`     | `str`  | Base URL of the Ollama server.                                                        | *required* |
| `model`    | `str`  | Model name to use for embeddings.                                                     | *required* |
| `cpu_only` | `bool` | When True, request CPU-only inference (sets num_gpu=0 in the Ollama options payload). | `False`    |

Initialise OllamaProvider with explicit parameters.

Parameters:

| Name       | Type   | Description                            | Default    |
| ---------- | ------ | -------------------------------------- | ---------- |
| `host`     | `str`  | Base URL of the Ollama server.         | *required* |
| `model`    | `str`  | Model name to use for embeddings.      | *required* |
| `cpu_only` | `bool` | When True, request CPU-only inference. | `False`    |

Raises:

| Type          | Description                |
| ------------- | -------------------------- |
| `ImportError` | If httpx is not installed. |

### `dimension`

Embedding dimension size.

Embeds a test string on first access to determine the dimension.

Returns:

| Type  | Description                                 |
| ----- | ------------------------------------------- |
| `int` | Integer dimension of each embedding vector. |

### `context_length`

Query /api/show once for the model's context length; cache it.

Returns None if the query fails or the field is absent. The result (including `None` on failure) is cached permanently for the provider instance — a transiently-unreachable Ollama at startup is not retried, so the conservative fallback cap persists until the server restarts.

### `embed(texts)`

Embed a batch of texts via the Ollama REST API.

Parameters:

| Name    | Type        | Description               | Default    |
| ------- | ----------- | ------------------------- | ---------- |
| `texts` | `list[str]` | List of strings to embed. | *required* |

Returns:

| Type                | Description                                    |
| ------------------- | ---------------------------------------------- |
| `list[list[float]]` | List of embedding vectors, one per input text. |

Raises:

| Type           | Description                                  |
| -------------- | -------------------------------------------- |
| `RuntimeError` | If the Ollama API returns an error response. |

## `OpenAIProvider(api_key, *, base_url=_BASE_URL, model=_MODEL)`

Bases: `EmbeddingProvider`

Embedding provider backed by the OpenAI-compatible Embeddings API.

Parameters:

| Name       | Type  | Description                            | Default     |
| ---------- | ----- | -------------------------------------- | ----------- |
| `api_key`  | `str` | OpenAI API key for authentication.     | *required*  |
| `base_url` | `str` | Base URL for an OpenAI-compatible API. | `_BASE_URL` |
| `model`    | `str` | Embedding model name.                  | `_MODEL`    |

Initialise OpenAIProvider with an explicit API key.

Parameters:

| Name       | Type  | Description                            | Default     |
| ---------- | ----- | -------------------------------------- | ----------- |
| `api_key`  | `str` | OpenAI API key for authentication.     | *required*  |
| `base_url` | `str` | Base URL for an OpenAI-compatible API. | `_BASE_URL` |
| `model`    | `str` | Embedding model name.                  | `_MODEL`    |

Raises:

| Type           | Description                |
| -------------- | -------------------------- |
| `ImportError`  | If httpx is not installed. |
| `RuntimeError` | If api_key is empty.       |

### `dimension`

Embedding dimension size.

Embeds a test string on first access to determine the dimension.

Returns:

| Type  | Description                                 |
| ----- | ------------------------------------------- |
| `int` | Integer dimension of each embedding vector. |

### `context_length`

Return the model's context length from the known-model table.

Returns None for models absent from the table; callers fall back to a conservative chunk cap.

### `embed(texts)`

Embed a batch of texts via the OpenAI Embeddings API.

Parameters:

| Name    | Type        | Description               | Default    |
| ------- | ----------- | ------------------------- | ---------- |
| `texts` | `list[str]` | List of strings to embed. | *required* |

Returns:

| Type                | Description                               |
| ------------------- | ----------------------------------------- |
| `list[list[float]]` | List of embedding vectors in input order. |

Raises:

| Type           | Description                                  |
| -------------- | -------------------------------------------- |
| `RuntimeError` | If the OpenAI API returns an error response. |

## `FastEmbedProvider(model_name='BAAI/bge-small-en-v1.5', cache_dir=None)`

Bases: `EmbeddingProvider`

Embedding provider backed by the local fastembed library.

The `fastembed` package is imported lazily at instantiation time so that it does not need to be installed unless this provider is used.

Initialise FastEmbed model.

Parameters:

| Name         | Type  | Description                 | Default                         |
| ------------ | ----- | --------------------------- | ------------------------------- |
| `model_name` | `str` | FastEmbed model identifier. | `'BAAI/bge-small-en-v1.5'`      |
| `cache_dir`  | \`str | None\`                      | Optional model cache directory. |

Raises:

| Type          | Description                    |
| ------------- | ------------------------------ |
| `ImportError` | If fastembed is not installed. |

### `dimension`

Embedding dimension size from the loaded model.

Returns:

| Type  | Description                                 |
| ----- | ------------------------------------------- |
| `int` | Integer dimension of each embedding vector. |

### `context_length`

Return the model's context length from the known-model table.

Returns None for models absent from the table; callers fall back to a conservative chunk cap.

### `embed(texts)`

Embed a batch of texts using the local fastembed model.

Parameters:

| Name    | Type        | Description               | Default    |
| ------- | ----------- | ------------------------- | ---------- |
| `texts` | `list[str]` | List of strings to embed. | *required* |

Returns:

| Type                | Description                                    |
| ------------------- | ---------------------------------------------- |
| `list[list[float]]` | List of embedding vectors, one per input text. |

## `get_embedding_provider(config)`

Auto-detect and return an embedding provider from config.

Checks `config.embeddings.provider` for an explicit selection. When that field is `None`, probes for available providers in this order:

1. If `config.embeddings.openai_api_key` is set → :class:`OpenAIProvider`.
1. If Ollama is reachable at `config.embeddings.ollama_host` → :class:`OllamaProvider`.
1. If `fastembed` can be imported → :class:`FastEmbedProvider`.
1. Raises :class:`RuntimeError` with installation instructions.

Parameters:

| Name     | Type            | Description                                        | Default    |
| -------- | --------------- | -------------------------------------------------- | ---------- |
| `config` | `ProjectConfig` | Vault configuration containing embedding settings. | *required* |

Returns:

| Type                | Description                                       |
| ------------------- | ------------------------------------------------- |
| `EmbeddingProvider` | An initialised :class:EmbeddingProvider instance. |

Raises:

| Type                 | Description                                                                                                                           |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `RuntimeError`       | If no provider is available and config.embeddings.provider is not set, or if the explicitly requested provider cannot be initialised. |
| `ConfigurationError` | If config.embeddings.provider is set to an unrecognised value.                                                                        |
