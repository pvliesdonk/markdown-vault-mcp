# CLI Reference

The `markdown-vault-mcp` command drives the server and the index from the shell:

```
markdown-vault-mcp <command> [options]
```

## `serve`

Start the MCP server.

```
markdown-vault-mcp serve [--transport {stdio|sse|http}] [--host HOST] [--port PORT] [--http-path PATH]
```

| Flag                           | Default                                      | Description                                                                                                                                                               |
| ------------------------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--transport`                  | `stdio`                                      | MCP transport: `stdio` (stdin/stdout, default), `sse` (Server-Sent Events), `http` (streamable-HTTP). Use `http` for Docker with a reverse proxy or when OIDC is enabled. |
| `--host`                       | `127.0.0.1`                                  | Bind host for the `http` transport (ignored for `stdio` and `sse`); pass `0.0.0.0` to bind all interfaces inside Docker                                                   |
| `--port`                       | `8000`                                       | Port for the `http` transport (ignored for `stdio` and `sse`)                                                                                                             |
| `--http-path` (alias `--path`) | env `MARKDOWN_VAULT_MCP_HTTP_PATH` or `/mcp` | MCP HTTP path for `http` transport; useful for reverse-proxy subpath mounting (such as `/vault/mcp`). The legacy `--path` spelling is still accepted.                     |

## Reverse proxy subpath mounts

By default, HTTP transport serves MCP on `/mcp`. You can run it under a subpath:

```
markdown-vault-mcp serve --transport http --http-path /vault/mcp
```

Equivalent env-based config:

```
MARKDOWN_VAULT_MCP_HTTP_PATH=/vault/mcp
```

For reverse proxies, you can either:

- Keep app path at `/mcp` and use proxy rewrite/strip-prefix middleware.
- Set app path directly to the public path (`/vault/mcp`) and route without rewrite.

When OIDC is enabled under a subpath, the configuration is different: the subpath goes in `BASE_URL` only, and `HTTP_PATH` stays at `/mcp`. See [OIDC subpath deployments](https://pvliesdonk.github.io/markdown-vault-mcp/unstable/deployment/oidc/#subpath-deployments).

Then your redirect URI is:

```
https://mcp.example.com/vault/auth/callback
```

## `index`

Build the full-text search index.

```
markdown-vault-mcp index [--source-dir PATH] [--index-path PATH] [--force]
```

## `search`

Search the vault from the CLI.

```
markdown-vault-mcp search <query> [-n LIMIT] [-m {keyword|semantic|hybrid}] [--folder PATH] [--json]
```

## `reindex`

Incrementally reindex the vault (only processes changed files). When semantic search is configured, the vector index is converged to the updated chunk set: exactly the changed documents are re-embedded and orphaned vectors dropped, never the whole corpus.

```
markdown-vault-mcp reindex [--source-dir PATH] [--index-path PATH] [--force]
```

`--force` drops the index and re-parses every file, ignoring change detection. An upgrade that changes how notes are parsed rebuilds the index by itself on the next run, so this is a manual repair for an index you have reason to distrust, not routine maintenance.
