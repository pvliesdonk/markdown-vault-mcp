# Sharing files with other MCP servers (File Exchange v0.3)

Use this guide when you want one MCP server to hand a 5 MB image (or PDF, or any other binary blob) to another MCP server **without** routing the bytes through your LLM as base64. The MCP File Exchange v0.3 protocol — spec lives in [`fastmcp-pvl-core`](https://github.com/pvliesdonk/fastmcp-pvl-core) — solves this with a shared filesystem volume and a `file_ref` envelope on tool results.

## What it solves

By default, an MCP tool returning a 5 MB PNG must encode it as ~7 MB of base64, dump it through the LLM context, and let the next tool decode it back. That round-trip is slow, expensive, and pointless when both tools live on the same host. File Exchange lets the producer write the bytes to a shared volume and return a tiny `file_ref` envelope; the consumer reads the bytes directly from disk.

```text
┌──────────────┐    file_ref envelope    ┌──────────────┐
│ image-mcp    │ ──────────────────────→ │     LLM      │
│ (producer)   │                          │              │
└──────┬───────┘                          └──────┬───────┘
       │ writes bytes                            │ passes file_ref
       ▼                                         ▼
   /data/exchange/image-mcp/<id>.png   ┌──────────────────────┐
       ▲                               │ markdown-vault-mcp   │
       └─── reads same bytes ─────────►│ (consumer)           │
                                       └──────────────────────┘
```

## Prerequisites

- Two or more MCP servers built on `fastmcp-pvl-core>=1.1.0` (this server, plus a peer like `image-generation-mcp`).
- A shared writable directory accessible to every server (a Docker named volume, NFS mount, or just a host bind mount on a single machine).
- `markdown-vault-mcp>=1.27.x` with PR #427 merged.

## Step 1 — Mount a shared volume

The deployer owns the volume; each container needs it mounted at the same path so the URIs round-trip cleanly.

```yaml title="compose.yml (excerpt)"
services:
  markdown-vault-mcp:
    image: ghcr.io/pvliesdonk/markdown-vault-mcp:latest
    volumes:
      - mcp-exchange:/data/exchange
    environment:
      MCP_EXCHANGE_DIR: /data/exchange
      MCP_EXCHANGE_NAMESPACE: markdown-vault-mcp

  image-mcp:
    image: ghcr.io/pvliesdonk/image-generation-mcp:latest
    volumes:
      - mcp-exchange:/data/exchange      # same volume, same path
    environment:
      MCP_EXCHANGE_DIR: /data/exchange
      MCP_EXCHANGE_NAMESPACE: image-mcp  # different namespace per server

volumes:
  mcp-exchange:
```

The first server to start writes a random UUID to `/data/exchange/.exchange-id`; every subsequent server reads it and refuses to read URIs from a different group. Pin `MCP_EXCHANGE_ID=<uuid>` if you need to compose volumes that were initialised separately.

## Step 2 — Verify the capability

After both containers are up, query the MCP `initialize` response. The capability should look like this:

```json
{
  "experimental": {
    "file_exchange": {
      "version": "0.3",
      "namespace": "markdown-vault-mcp",
      "exchange_id": "f0e1d2c3-...",
      "produces": ["application/octet-stream", "image/png", ...],
      "consumes": ["text/markdown", "image/png", "*/*", ...],
      "transfer_methods": {
        "exchange": {},
        "http": { "tool": "create_download_link" }
      }
    }
  }
}
```

`exchange` appearing in `transfer_methods` confirms `MCP_EXCHANGE_DIR` is wired correctly.

## Step 3 — Producer side: read augments with file_ref

When the LLM calls `read("assets/diagram.png")` against this server, the response now carries both the legacy `content_base64` field and a `file_ref` envelope:

```json
{
  "path": "assets/diagram.png",
  "mime_type": "image/png",
  "size_bytes": 245760,
  "content_base64": "iVBORw0KGgo...",
  "file_ref": {
    "origin_server": "markdown-vault-mcp",
    "origin_id": "assets/diagram.png",
    "mime_type": "image/png",
    "size_bytes": 245760,
    "transfer": {
      "exchange": { "uri": "exchange://f0e1.../markdown-vault-mcp/<hash>.png" },
      "http": { "tool": "create_download_link" }
    }
  }
}
```

The bytes are also written to `/data/exchange/markdown-vault-mcp/<hash>.png`. The `<hash>` is a deterministic SHA-256 prefix of the vault path, so re-reads of the same attachment overwrite the same file (no proliferation).

## Step 4 — Consumer side: fetch resolves exchange://

The LLM passes the `file_ref` (or just its `transfer.exchange.uri`) to a peer's `fetch` tool. On this server, that looks like either of:

```jsonc
// 1. Pass the URI directly.
fetch({ "url": "exchange://f0e1.../image-mcp/abc.png", "path": "assets/incoming.png" })

// 2. Pass the file_ref block (preferred when available — automatic exchange/http selection).
fetch({ "file_ref": <file_ref from step 3>, "path": "assets/incoming.png" })
```

The bytes never touch the LLM. `fetch` reads them from disk, validates the exchange-group matches, and writes them as a vault attachment.

## Step 5 — Sweep + lifecycle

Producers own their namespace's lifecycle. This server runs an idempotent sweep every 5 minutes and one final pass at shutdown:

- Files older than 1 hour (default TTL) are evicted.
- Optional `storage_ceiling_bytes` lets you cap the namespace's total size; oldest files go first.

Other servers' namespaces under the same `$MCP_EXCHANGE_DIR` are read-only — only the producer ever deletes its own files.

## Common pitfalls

- **`exchange group mismatch` error in `fetch`** — the URI was minted by a server with a different `.exchange-id` than yours. Either share a single volume across both groups (recommended) or set `MCP_EXCHANGE_ID` on one side to match the other.
- **`MCP_EXCHANGE_DIR is not configured`** — the env var is unset on the consumer. Set it (or stop sending exchange URIs to that server).
- **No `file_ref` in `read` results** — `MCP_EXCHANGE_DIR` is unset on this server, or the path you read is a `.md` note (notes are returned as text, not exchange artefacts).
- **UID mismatch** — the entrypoint takes care of `/data/state` ownership but does **not** touch `/data/exchange` (the deployer owns it). Either ensure all containers run with the same UID/GID, or `chown` the host directory once.

## Reference

- Spec text: `fastmcp-pvl-core/docs/specs/file-exchange.md` (v0.3).
- Capability declaration helper: `fastmcp_pvl_core.register_file_exchange_capability`.
- Runtime: `fastmcp_pvl_core.FileExchange` (env-driven via `MCP_EXCHANGE_*`).
