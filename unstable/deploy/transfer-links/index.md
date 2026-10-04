# Transfer links

This server has transfer links when `create_download_link` and `create_upload_link` appear in the [tools reference](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/tools/index.md). A transfer link is a one-time URL for one file. The bytes travel over plain HTTP between the server and whoever holds the link; the conversation carries only the URL and its expiry, so a large file never passes through the model. The link route sits outside authentication, which makes the URL itself the credential: the [security model](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/security-model/index.md) says what the tools behind it reach.

## For the person holding a link

A tool call returns `url` and `expires_in_s`. A download link answers one `GET`; the response carries the file as an attachment, so a browser saves it rather than showing it:

```
curl -o report.pdf "https://mcp.example.com/transfer/<token>"
```

An upload link takes the file's bytes as the request body of one `POST` (or `PUT`), raw, not as a form:

```
curl -X POST --data-binary @diagram.png "https://mcp.example.com/transfer/<token>"
```

What the answers mean:

| Status | Meaning                                                                                                                                                           |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200`  | The transfer happened. For a short grace window the same link still answers, so a download that stalled part-way can be fetched again; after it the link is gone. |
| `404`  | No such link: unknown, expired, already used, or minted for the other direction. The server does not say which.                                                   |
| `409`  | Another request is using this link at this moment; retry shortly.                                                                                                 |
| `413`  | The upload is larger than the server accepts; the link stays usable for a smaller one.                                                                            |

Where the file goes on an upload is fixed when the link is minted; whoever uploads cannot change it.

## For the operator

- **The route.** `/transfer/{token}` is registered beside the MCP endpoint and outside the authentication middleware. No bearer token or sign-in is asked for; the token in the URL is the authorization. It is 256 bits of randomness. A used or expired link answers like one that never existed.
- **The public URL.** Links are built from `MARKDOWN_VAULT_MCP_BASE_URL`, which the server requires before it registers the route at all; under a path prefix the link carries the prefix, so the [reverse proxy](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deploy/reverse-proxy/#a-path-prefix) must route `/transfer/` to the server the same way it routes the MCP path. Stdio has no route: the tools are absent there, not failing.
- **The store.** Tokens live in the server's key-value store (`MARKDOWN_VAULT_MCP_KV_STORE_URL`), so replicas that share the store share the links.
- **The variables.** The five settings below are read from the environment under this server's prefix; the [configuration reference](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/reference/configuration/index.md) carries the authoritative list for this server, and the defaults here are fastmcp-pvl-core 10.1's.

| Variable                                       | Default     | Meaning                                                                             |
| ---------------------------------------------- | ----------- | ----------------------------------------------------------------------------------- |
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_DEFAULT_S`    | `3600`      | Link lifetime when the caller gives none.                                           |
| `MARKDOWN_VAULT_MCP_TRANSFER_TTL_MAX_S`        | `86400`     | Ceiling; a longer requested lifetime is capped to it.                               |
| `MARKDOWN_VAULT_MCP_TRANSFER_GRACE_TTL_S`      | `60`        | How long a used link stays valid for a retry of a stalled transfer.                 |
| `MARKDOWN_VAULT_MCP_TRANSFER_LEASE_S`          | `60`        | How long a transfer may hold the link before a crashed handler's claim is released. |
| `MARKDOWN_VAULT_MCP_TRANSFER_MAX_UPLOAD_BYTES` | `104857600` | The per-upload size cap (100 MiB).                                                  |

A link pasted into a chat or written to a log is a live credential until it is used or expires, since nothing else guards the route. A shorter default lifetime narrows that window; the model can still ask for less with the tool's `ttl_s` argument, never for more than the ceiling.

### What a link names on this server

A download link's `ref` is a vault path: a note (`.md`) or an attachment with an allowed extension. A note downloads as its raw Markdown, frontmatter included. With Open Knowledge Format on, `okf-bundle` serves the whole bundle as a zip, and `okf-bundle:<folder>` serves one folder of it.

### Upload destinations

An upload link names a vault path for a note or an allowed attachment. While `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING` is on (the default), a link can be minted only for a path that doesn't exist yet. With it off, the upload replaces the file. An uploaded note goes through the normal write path: the index picks it up, and with git on it is committed. A successful upload answers `{"path": ..., "bytes": ...}`. `create_upload_link` is a write tool, so read-only mode hides it.

### Statuses this server adds

| Status | Meaning                                                                                                                                      |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `409`  | Also: the destination exists by now, created after the link was minted or by an earlier upload through the same link. Retrying doesn't help. |
| `410`  | The file, or the bundle folder, was removed after the link was minted.                                                                       |
| `503`  | The vault is shutting down. Retry; the link is kept.                                                                                         |
