# Embedding Providers

The `providers` module defines an abstract base class for embedding providers and four concrete implementations for OpenAI, Voyage AI, Ollama, and FastEmbed.

## Quick Start

```python
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
2. **Ollama** (if `OLLAMA_HOST` is reachable)
3. **FastEmbed** (if the package is installed)

Override with `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=openai|voyage|ollama|fastembed`.
For OpenAI-compatible APIs, set `OPENAI_BASE_URL` and
`OPENAI_EMBEDDING_MODEL`, or the prefixed equivalents
`MARKDOWN_VAULT_MCP_OPENAI_BASE_URL` and
`MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL`.

`voyage` is a preset over the same OpenAI-compatible transport, pinned to
`https://api.voyageai.com/v1` and configured with `VOYAGE_API_KEY` and
`MARKDOWN_VAULT_MCP_VOYAGE_MODEL`. It is never auto-detected: select it
explicitly.

## API Reference

<!-- vale off -->
::: markdown_vault_mcp.providers.EmbeddingProvider

::: markdown_vault_mcp.providers.OllamaProvider

::: markdown_vault_mcp.providers.OpenAIProvider

::: markdown_vault_mcp.providers.VoyageProvider

::: markdown_vault_mcp.providers.FastEmbedProvider

::: markdown_vault_mcp.providers.get_embedding_provider
<!-- vale on -->
