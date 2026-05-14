# MCP File Exchange

`fastmcp-pvl-core` 2.1.0+ ships two helpers for spec-compliant out-of-band
file transfer between co-deployed MCP servers (and HTTP fallback for
remote clients):

- `register_file_exchange(...)` — download direction. Mints a
  spec-compliant `create_download_link(origin_id, ttl_seconds)` tool
  and a one-time `GET /artifacts/{token}` route.
- `register_file_exchange_upload(...)` — upload direction. Mints a
  spec-compliant `create_upload_link(target_id, ttl_seconds)` tool and
  a one-time `POST /markdown-vault-mcp/uploads/{token}` route.

Both helpers are wired in `src/markdown_vault_mcp/server.py`.

## Status in markdown-vault-mcp

!!! info "Upload wired, download deferred"
    **Upload direction is wired** as of
    [#443](https://github.com/pvliesdonk/markdown-vault-mcp/pull/443) —
    `register_file_exchange_upload(...)` is active in
    `src/markdown_vault_mcp/server.py`. The
    [`create_upload_link`](../tools/index.md#create_upload_link) tool
    mints a one-time `POST /markdown-vault-mcp/uploads/{token}` URL when the server runs
    on HTTP or SSE transport with `MARKDOWN_VAULT_MCP_BASE_URL` set;
    bytes are committed via `Collection.write` (for `.md` filenames) or
    `Collection.write_attachment` (for binaries).

    **Download direction remains deferred** to
    [#431](https://github.com/pvliesdonk/markdown-vault-mcp/issues/431).
    pvl-core's spec-compliant `create_download_link(origin_id,
    ttl_seconds)` tool name collides with MV's existing
    `create_download_link(path, ttl_seconds)` (registered via
    `ArtifactStore`). Wiring both would silently shadow one or the
    other depending on registration order, so
    `register_file_exchange(...)` is intentionally not called in
    `server.py` until #431 resolves the collision.

## Today

### Inbound (uploads from agent → vault)

The preferred path is the wired upload tool:

- **`create_upload_link(target_id, ttl_seconds)`** — mints a one-time
  `POST /markdown-vault-mcp/uploads/{token}` URL. The agent (or a local helper) POSTs
  the raw bytes; the server commits them to the vault via
  `Collection.write` / `Collection.write_attachment`. Bytes flow over
  HTTP, not through the MCP context — recommended for any file >100 KB.
  See the [Claude Desktop workflow](claude-desktop.md#uploading-a-local-file-to-the-vault)
  for a curl walkthrough and the
  [tool reference](../tools/index.md#create_upload_link) for the full
  parameter / error contract. Requires HTTP/SSE transport and
  `MARKDOWN_VAULT_MCP_BASE_URL`; gated by `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

For deployments that cannot run HTTP/SSE — for example pure stdio
Claude Desktop setups — two fallbacks remain:

- **`fetch(url, path)`** — server-side download from a publicly
  reachable HTTP/HTTPS URL into the vault.
- **`write(path, content_base64)`** — round-trip binary content
  through the LLM context. Bounded by
  `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` (default 1 MiB); not
  recommended for files larger than ~100 KB because of base64 inflation.

### Outbound (downloads from vault → agent / peer server)

- **`create_download_link(path, ttl_seconds)`** — mints a one-time
  `GET /artifacts/{token}` URL backed by `ArtifactStore`. This is the
  tool that conflicts with the pvl-core spec-compliant version's name;
  see #431.

## Configuration

The pvl-core helpers' env-var contract is documented in
[Configuration > MCP File Exchange](../configuration.md#mcp-file-exchange).
Upload-direction variables (`MARKDOWN_VAULT_MCP_UPLOAD_*`) take effect
today; download-direction variables
(`MARKDOWN_VAULT_MCP_FILE_EXCHANGE_*`) remain inert until #431 wires
`register_file_exchange(...)`.

## When #431 lands

The migration in #431 will:

1. Drop MV's bespoke `create_download_link` tool registration.
2. Drop the `ArtifactStore` HTTP route registration.
3. Add the `register_file_exchange(...)` call in
   `src/markdown_vault_mcp/server.py`.
4. Update this guide to retire the "deferred" half of the status
   admonition and document the wired download patterns.

See [#431](https://github.com/pvliesdonk/markdown-vault-mcp/issues/431)
for status.
