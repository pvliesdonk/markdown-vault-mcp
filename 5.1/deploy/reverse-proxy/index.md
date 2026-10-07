# Reverse proxy

The server speaks plain HTTP on one port. A reverse proxy in front of it handles TLS and gives it a public hostname; it also decides which networks reach it. The [security model](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/security-model/index.md) counts all three as the operator's part. This page covers what the server needs from the proxy, the common case of its own hostname, and a deployment under a path prefix.

## What the server needs from the proxy

- Requests forwarded to the port the server listens on: `8000` in the container, `MARKDOWN_VAULT_MCP_PORT` for a package install.
- `MARKDOWN_VAULT_MCP_BASE_URL` set to the public URL, prefix included. The server builds the URLs it advertises from this variable, its OAuth metadata and its callback among them, so it is required once OIDC is on and useful before. `MARKDOWN_VAULT_MCP_HOST` is the interface the server binds to, not the name the proxy routes.
- `/health` and `/health/ready` reachable for whatever probes the deployment; they sit outside the MCP mount and outside authentication ([Health](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/docker/#health)).

## Its own hostname

Proxy configuration is deployment-specific, so `compose.yml` ships none. Add it in a second file rather than by editing `compose.yml`, which is template-owned and re-rendered: save this as `compose.override.yml`, which Compose loads automatically alongside `compose.yml`. It is written for Traefik; another proxy needs the same two facts, the hostname and the container port.

```
services:
  markdown-vault-mcp:
    # `!reset` drops the published port: the proxy reaches the container over
    # the shared network, so nothing needs to be on the host. Plain merging
    # appends to sequences, so without this the port stays published.
    ports: !reset []
    networks:
      - traefik
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.markdown-vault-mcp.rule=Host(`mcp.example.com`)"
      - "traefik.http.routers.markdown-vault-mcp.tls.certresolver=letsencrypt"
      - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"

networks:
  traefik:
    external: true
```

`!reset` needs Compose 2.24.4 or newer. On an older engine, drop that line and remove the port mapping from `compose.yml` directly, accepting that the edit conflicts on the next template update.

Check the result before starting anything, since a merge that silently kept the port mapping looks identical until the port clashes:

```
docker compose config
```

Substitute your own hostname for `mcp.example.com` and set `MARKDOWN_VAULT_MCP_BASE_URL` to the same public URL in `.env`. The network must already exist and be the one the proxy watches. The OIDC settings that go with a public hostname are on the [OIDC](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/oidc/index.md) page; `compose.yml` itself needs no change for them.

For a package install, the proxy forwards to `127.0.0.1:MARKDOWN_VAULT_MCP_PORT` on the same host instead, and nothing else differs.

## A path prefix

When the server runs behind a reverse-proxy subpath (`https://mcp.example.com/myservice/mcp`) rather than its own hostname, `BASE_URL` and `HTTP_PATH` serve different roles:

| Variable    | Purpose                                                    | Example                             |
| ----------- | ---------------------------------------------------------- | ----------------------------------- |
| `BASE_URL`  | Public URL of the server, **including the subpath prefix** | `https://mcp.example.com/myservice` |
| `HTTP_PATH` | Internal MCP endpoint mount point (**no subpath prefix**)  | `/mcp`                              |

The reverse proxy strips the subpath prefix before forwarding to the application. FastMCP concatenates `BASE_URL + HTTP_PATH` to build the public resource URL, so including the prefix in both produces broken URLs with duplicated path segments.

Do not duplicate the subpath

Setting `BASE_URL=https://mcp.example.com/myservice` together with `HTTP_PATH=/myservice/mcp` produces a duplicated resource URL: `https://mcp.example.com/myservice/myservice/mcp`. The subpath belongs in `BASE_URL` only.

### Configuration

Environment variables:

```
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com/myservice
MARKDOWN_VAULT_MCP_HTTP_PATH=/mcp
```

Register this callback URI in your OIDC provider:

```
https://mcp.example.com/myservice/auth/callback
```

### What the server serves, and where

The MCP endpoint and the OAuth discovery documents sit on opposite sides of the prefix, which is what makes subpath routing more than one strip rule. With `BASE_URL=https://mcp.example.com/myservice` and `HTTP_PATH=/mcp`:

| Public URL                                                                  | Path the container listens on        | Served when                                         |
| --------------------------------------------------------------------------- | ------------------------------------ | --------------------------------------------------- |
| `/myservice/mcp`                                                            | `/mcp`                               | always                                              |
| `/myservice/authorize`, `/token`, `/register`, `/auth/callback`, `/consent` | the same path without the prefix     | the server proxies OAuth (client ID and secret set) |
| `/.well-known/oauth-protected-resource/myservice/mcp`                       | the identical path, prefix included  | OIDC is enabled, in every mode                      |
| `/.well-known/oauth-authorization-server`                                   | the identical path, at the host root | the server proxies OAuth (client ID and secret set) |

Two properties of that table drive every routing rule below:

1. The MCP endpoint and the OAuth endpoints listen **without** the prefix, so the proxy has to strip it.
1. The protected-resource document's path **contains** the prefix, because RFC 9728 §3.1 appends the resource path after the well-known segment. The server registers that full path verbatim, so stripping the prefix from this request returns 404.

A server holding no client ID and secret verifies tokens the provider issued directly. It serves protected-resource metadata only and answers `/.well-known/oauth-authorization-server` with 404. That is the correct answer, because the provider is the authorization server, so do not route that path to a server configured this way.

### Reverse proxy routing

The reverse proxy needs two routers pointing at the same service, because a prefix rule cannot express both halves:

1. **Operational routes** match the prefix and strip it: `/myservice/mcp`, in proxy mode the OAuth endpoints, and `/myservice/transfer/` when the server mints [transfer links](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/deploy/transfer-links/index.md).
1. **Discovery routes** match their own well-known paths and pass through untouched. Their URLs do not begin with `/myservice`, so a `PathPrefix(/myservice)` rule never sees them.

```
labels:
  # Operational routes: strip the /myservice prefix before forwarding
  - "traefik.http.routers.mcp-app.rule=Host(`mcp.example.com`) && PathPrefix(`/myservice`)"
  - "traefik.http.middlewares.strip-myservice.stripprefix.prefixes=/myservice"
  - "traefik.http.routers.mcp-app.middlewares=strip-myservice"
  - "traefik.http.services.mcp-app.loadbalancer.server.port=8000"
  # Discovery routes: same service, no strip middleware
  - "traefik.http.routers.mcp-wellknown.rule=Host(`mcp.example.com`) && (PathPrefix(`/.well-known/oauth-protected-resource/myservice/mcp`) || PathPrefix(`/.well-known/oauth-authorization-server`))"
  - "traefik.http.routers.mcp-wellknown.service=mcp-app"
```

Drop the `oauth-authorization-server` clause when the server holds no client ID and secret. That path returns 404 in such a configuration, and claiming it on a shared hostname takes the document away from whichever service does answer.

Two routers rather than one is deliberate. A single router carrying the strip rule would apply it to the discovery request as well. Nothing here relies on how a given proxy treats a strip prefix that fails to match: separate routers state the untouched route outright, in any proxy that has the concept.

Any other proxy needs these two routes in its own syntax: a prefix-stripping route for the operational paths, and an untouched route for the two well-known paths.

### Sharing a hostname

The failure that follows from point 2 above is worth spelling out, because its symptom points somewhere else entirely.

The discovery URL sits outside the prefix, so on a hostname shared with other services, prefix-based routing cannot claim it. Without a router that matches it explicitly, the request falls through to whatever else holds the host, such as an SSO portal or another MCP server mounted at the root. That service answers with **its** metadata, the client builds an authorization URL from another service's endpoints, and the visible symptom is an authorization URL 404ing at a path nobody configured. It reads as a client bug or an auth bug; it is a routing rule one path too narrow.

The `mcp-wellknown` router above is the fix. In Traefik it also wins by default: routers sort by rule length, so a rule naming the full well-known path outranks a bare `Host(...)` catch-all. Where the competing service sets an explicit `priority`, set a higher one here, because Traefik ignores its rule-length default for any router that carries one.

One document still collides: `oauth-authorization-server`

In proxy mode the server serves authorization-server metadata at `/.well-known/oauth-authorization-server`, at the **host root**, whatever prefix `BASE_URL` carries. FastMCP does contain an RFC 8414 path-aware override, `OAuthProvider.get_well_known_routes()`, which would serve it at `/.well-known/oauth-authorization-server/myservice`. Nothing reaches it: the HTTP app mounts `get_routes()` instead, leaving the path-aware form unreachable. (Verified against FastMCP 4.0.9; the project's `tests/test_oidc_discovery_routes.py` fails if a FastMCP upgrade changes this.)

Only this one document collides. Protected-resource metadata is path-namespaced, so several servers can share a hostname without contending for it.

Where another OAuth service already owns the root path, either give this server its own hostname, or run it without client ID and secret so it never serves the document. An authorization-server metadata request then belongs to the provider, where the `authorization_servers` entry in this server's protected-resource metadata sends the client anyway.

### Verifying the routing

Probe the container first, to separate what the server serves from what the proxy does with it. The image ships no HTTP client, so borrow one. Passing `--network container:` puts the throwaway container inside the server's network namespace, which makes `localhost:8000` the server itself:

```
NS="container:$(docker compose ps -q markdown-vault-mcp)"
docker run --rm --network "$NS" curlimages/curl -s -o /dev/null \
  -w '%{http_code}\n' localhost:8000/mcp
docker run --rm --network "$NS" curlimages/curl -s \
  localhost:8000/.well-known/oauth-protected-resource/myservice/mcp
```

Expect `401` for the first, meaning the endpoint is there and authentication is on, and the metadata JSON for the second. A `404` instead of the `401` means `HTTP_PATH` carries the prefix it should not. Then repeat from outside, through the proxy:

```
curl -s -o /dev/null -w '%{http_code}\n' https://mcp.example.com/myservice/mcp
curl -s https://mcp.example.com/.well-known/oauth-protected-resource/myservice/mcp
```

Both must reach this server, and the `resource` field in the JSON must read `https://mcp.example.com/myservice/mcp`. A `resource` naming a different service is the shared-hostname problem above, not an incorrect `BASE_URL`.

This server adds two operational routes, each mounted only when its secret is set: `/github-webhook` and `/gitlab-webhook`, for push-triggered pulls ([Webhooks](https://pvliesdonk.github.io/markdown-vault-mcp/5.1/use/git-integration/#push-triggered-pull-webhooks)). Route them the way this page routes `/transfer/`; under a path prefix, match the prefix and strip it.
