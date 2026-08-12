# Embedding Providers

This guide covers configuring each supported embedding provider for semantic search. You only need one provider; choose based on your requirements:

| Provider | Runs locally | Requires GPU | Internet required | Install size | RAM during embedding |
|----------|-------------|-------------|-------------------|-------------|---------------------|
| [Ollama](#ollama) | Yes | No (CPU works fine) | No | ~2 GB (model) | ~2-4 GB (separate process) |
| [FastEmbed](#fastembed) | Yes | No | First run only (model download) | Small runtime + model | ~1-2 GB (in-process) |
| [OpenAI](#openai) | No (API call) | N/A | Yes | Minimal | Negligible |

All three providers produce embeddings that enable the `semantic` and `hybrid` search modes in the `search` tool.

## Ollama

[Ollama](https://ollama.com) runs embedding models locally. It's the recommended option for local, private embeddings: easy to set up and works well on CPU.

### Install Ollama

=== "macOS"

    ```bash
    brew install ollama
    ```

=== "Linux"

    ```bash
    curl -fsSL https://ollama.com/install.sh | sh
    ```

=== "Docker"

    If your vault server runs in Docker and Ollama runs on the host, no Ollama install inside the container is needed — just point to the host.

### Pull the embedding model

```bash
ollama pull nomic-embed-text
```

Verify it's available:

```bash
ollama list
```

You should see `nomic-embed-text` in the list.

### Configure

```bash
MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
MARKDOWN_VAULT_MCP_OLLAMA_MODEL=nomic-embed-text
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/store/embeddings
```

**CPU-only mode:** if you have a GPU but want to force CPU-only (such as to reserve the GPU for inference):

```bash
MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY=true
```

**Docker-to-host networking:** if Ollama runs on the host and the vault server runs in Docker:

=== "Docker Desktop (macOS/Windows)"

    ```bash
    OLLAMA_HOST=http://host.docker.internal:11434
    ```

=== "Linux (without Docker Desktop)"

    Add to your `compose.yml`:

    ```yaml
    services:
      markdown-vault-mcp:
        extra_hosts:
          - "host.docker.internal:host-gateway"
    ```

    Then use:

    ```bash
    OLLAMA_HOST=http://host.docker.internal:11434
    ```

### Verify

```bash
# Test Ollama is reachable
curl http://localhost:11434/api/tags

# Test embedding generation
curl http://localhost:11434/api/embeddings -d '{
  "model": "nomic-embed-text",
  "prompt": "test embedding"
}'
```

You should get a JSON response with an `embedding` array. After starting the vault server, use hybrid search:

> Search for "project planning" using hybrid mode

If embeddings are working, hybrid and semantic search modes will return results ranked by conceptual similarity.

---

## FastEmbed

[FastEmbed](https://github.com/qdrant/fastembed) runs ONNX embedding models directly in Python, with no separate server needed.

### Install

```bash
pip install markdown-vault-mcp[embeddings]
```

Or with uv:

```bash
uv pip install markdown-vault-mcp[embeddings]
```

The `[all]` extra includes FastEmbed as well.

### Configure

```bash
MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=fastembed
MARKDOWN_VAULT_MCP_FASTEMBED_MODEL=BAAI/bge-small-en-v1.5
MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR=/path/to/store/fastembed-cache
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/store/embeddings
```

No host URL or API key needed. The model downloads automatically on first use and is reused from cache after that.

!!! note "First startup downloads the model"
    Set `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` to a persistent location. In Docker, the default compose layout stores this under `/data/state/fastembed` on the `state-data` named volume to avoid re-downloading on container recreation.

!!! info "Memory usage: in-process vs out-of-process"
    FastEmbed runs the ONNX model **inside the Python process**, so the container itself bears the full inference memory cost. The default model (`BAAI/bge-small-en-v1.5`, 512-token context) keeps this manageable. If you switch to a long-context model such as `nomic-ai/nomic-embed-text-v1.5` (8192-token context), you should reduce `_FASTEMBED_ONNX_BATCH_SIZE` in `providers.py` by a large amount; see issue [#306](https://github.com/pvliesdonk/markdown-vault-mcp/issues/306).

    By contrast, Ollama runs inference in a **separate server process**; the Python container only sends HTTP requests and receives float vectors, so its own memory footprint stays low. If memory is tight (such as on a small VPS), Ollama may be a better fit since its memory is isolated from the MCP server.

### Verify

Start the server and test with a search:

> Search for "meeting notes" using semantic mode

If FastEmbed is working, you'll get results ranked by semantic similarity even if the exact phrase doesn't appear in the documents.

---

## OpenAI

Uses the [OpenAI Embeddings API](https://platform.openai.com/docs/guides/embeddings) (`text-embedding-3-small` by default). Requires an API key and internet access. Lowest local resource usage, but sends document content to OpenAI.

### Get an API key

1. Go to [OpenAI API Keys](https://platform.openai.com/api-keys)
2. Create a new secret key
3. Copy it

### Configure

```bash
MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-your-api-key-here
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/store/embeddings
```

For OpenAI-compatible providers, override the base URL and model:

```bash
MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=your-provider-api-key
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
OPENAI_EMBEDDING_MODEL=BAAI/bge-m3
MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH=/path/to/store/embeddings
```

!!! warning "Privacy"
    Document content (titles, headings, body text) is sent to the configured OpenAI-compatible provider for embedding. Do not use this provider if your vault contains sensitive data you don't want to share with that provider. Use Ollama or FastEmbed for fully local, private embeddings.

!!! tip "Cost"
    OpenAI embeddings are inexpensive. `text-embedding-3-small` costs $0.02 per million tokens. A vault of 1,000 notes (~500K tokens) costs about $0.01 to embed. Reindexing only processes changed documents.

### Verify

```bash
# Test your API key (replace $OPENAI_API_KEY with your key, or export it first)
curl https://api.openai.com/v1/embeddings \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": "test", "model": "text-embedding-3-small"}'
```

For OpenAI-compatible providers:

```bash
curl "$OPENAI_BASE_URL/embeddings" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"input\": \"test\", \"model\": \"$OPENAI_EMBEDDING_MODEL\"}"
```

You should get a JSON response with an embedding array. After starting the server, test hybrid search:

> Search for "project ideas" using hybrid mode

---

## Auto-detection

If you don't set `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER`, the server tries providers in this order:

1. **OpenAI:** if `OPENAI_API_KEY` is set
2. **Ollama:** if `OLLAMA_HOST` is reachable
3. **FastEmbed:** if the package is installed

Set `MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER` explicitly to avoid surprises when your environment changes (setting `OPENAI_API_KEY` for another tool will cause the server to switch from Ollama to OpenAI).

## Common to all providers

Regardless of which provider you choose:

- **`MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` is required** to enable semantic search. Without it, only keyword search is available.
- Embeddings are built automatically on first startup when a provider is configured. Subsequent starts load the persisted index from disk and only process changed files.
- Use `mode="hybrid"` in search for best results; it combines keyword (BM25) and semantic (cosine similarity) scores using Reciprocal Rank Fusion.

!!! note "Large vaults"
    The initial embedding build uses two levels of batching to keep memory bounded:

    1. **Vault level** — 4 chunks per provider call by default, configurable via `MARKDOWN_VAULT_MCP_EMBEDDING_BATCH_SIZE`
    2. **ONNX level** (FastEmbed only) — 32 chunks per inference call (`_FASTEMBED_ONNX_BATCH_SIZE` in `providers.py`)

    The ONNX batch size is tuned for the default `BAAI/bge-small-en-v1.5` model (512-token context). ONNX self-attention scales as O(batch × seq_len²) in RAM; long-context models require a much smaller batch size to avoid OOM — see issue [#306](https://github.com/pvliesdonk/markdown-vault-mcp/issues/306).

    For very large vaults (thousands of notes), the first startup may take several minutes. If the process is interrupted mid-build, it will rebuild from scratch on the next startup — partial indices are never persisted.

### Chunk sizing and the embedding context

The chunker (shared by keyword and semantic search) bounds every chunk by a word cap (`MARKDOWN_VAULT_MCP_MAX_CHUNK_WORDS`, default `400`) and a character cap (`MARKDOWN_VAULT_MCP_MAX_CHUNK_CHARS`). The character cap exists because a chunk that fits the word cap can still exceed the embedding model's **token** limit; token-dense content (tables, code, CJK) packs far more tokens per word. Without it, such a chunk would abort the build (Ollama returns HTTP 400) or be silently truncated (FastEmbed), producing degraded embeddings.

When you don't set `MAX_CHUNK_CHARS` explicitly, the default is `min(1500, round(context_length × 2.8))`: retrieval quality peaks at ~256 to 512 tokens per chunk regardless of the model's context, and the 1500-char ceiling keeps the fastembed/ONNX path clear of the out-of-memory regime seen with oversize chunks (issue [#306](https://github.com/pvliesdonk/markdown-vault-mcp/issues/306)). The result:

- `bge-small-en-v1.5` (512-token context) → ~1,434 chars (below the ceiling, used as is)
- a shorter-context model (below ~536 tokens) uses its own smaller value, `round(context_length × 2.8)`
- a longer-context model (2048, 8192, …) is capped at the `1500`-char ceiling
- unknown context (no provider, or Ollama unreachable at startup) → the `1500`-char ceiling

Set a positive value to force an exact cap. Set `-1` to opt into unbounded context-scaling (`round(context_length × 2.8)` with no ceiling, or `1500` when the model context is unknown). This reproduces the pre-bounded context-scaling and **can OOM the host** on the fastembed/ONNX path with a long-context model, so use it only with Ollama (out-of-process) or a remote provider.

`MARKDOWN_VAULT_MCP_CHUNK_OVERLAP_WORDS` (default `40`, `0` disables) adds a few words from the end of the previous fragment to the start of each fragment when a section is too large for the caps and gets split on paragraph, line, or word boundaries. This improves retrieval recall at those arbitrary split points. An overlapped fragment can exceed the word or character cap by up to the overlap word count. Overlap applies to new and re-indexed notes, so run `reindex` to apply it across an existing vault; it does not force a rebuild on its own.

!!! note "Changing the embedding model triggers a one-time cold rebuild"
    Because the char cap is derived from the model's context, the chunk boundaries themselves depend on the embedding model. Changing the embedding model (or setting/changing `MAX_CHUNK_CHARS`) re-chunks the **FTS index**, not just the embeddings, so on the next startup the server automatically rejects the warm-restart short-circuit and does a background cold rebuild (keyword search returns first, semantic search once embeddings finish). No manual `reindex` is needed. The same one-time rebuild happens when an embedding-enabled vault is upgraded from a release before this behavior existed.

!!! warning "Long-context models are opt-in"
    The defaults (`BAAI/bge-small-en-v1.5` for FastEmbed, `nomic-embed-text` for Ollama) are memory-light. Long-context models (`nomic-ai/nomic-embed-text-v1.5` at 8192 tokens for FastEmbed, or `bge-m3:latest` for Ollama, using the `name:tag` form Ollama requires) give larger chunks but cost more memory: FastEmbed runs ONNX in-process and its self-attention is `O(batch × seq_len²)` in RAM (see the memory note under [FastEmbed](#fastembed) and issue [#306](https://github.com/pvliesdonk/markdown-vault-mcp/issues/306)); Ollama needs the model to fit GPU VRAM at the larger context. Prefer the defaults unless you have the headroom.
