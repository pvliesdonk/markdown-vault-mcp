# OIDC Authentication

Optional token-based authentication for HTTP deployments. OIDC activates automatically based on which environment variables are set. For an overview of all authentication modes (bearer token, OIDC, no auth), see the [Authentication guide](../guides/authentication.md).

!!! warning "Transport requirement"
    OIDC requires `--transport http` (or `sse`). It has no effect with `--transport stdio`.

## Auth Modes

| Mode | Required Variables | Description |
|------|-------------------|-------------|
| **remote** (recommended) | `BASE_URL`, `OIDC_CONFIG_URL` | Local JWKS validation. No client credentials needed. |
| **oidc-proxy** | `BASE_URL`, `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | Full OAuth proxy with session management. |

Set `MARKDOWN_VAULT_MCP_AUTH_MODE` to force a mode, or let the server auto-detect based on which variables are set.

## Remote Mode Variables

| Variable | Description |
|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | Public base URL of the server |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | OIDC discovery endpoint |

Optional: `OIDC_AUDIENCE`, `OIDC_REQUIRED_SCOPES` (same as OIDCProxy mode).

No `CLIENT_ID` or `CLIENT_SECRET` needed — tokens are validated locally via JWKS.

## OIDCProxy Required Variables

| Variable | Description |
|----------|-------------|
<<<<<<< before updating
| `MARKDOWN_VAULT_MCP_BASE_URL` | Public base URL of the server (e.g. `https://mcp.example.com`; include prefix when mounted under subpath, e.g. `https://mcp.example.com/vault`) |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | OIDC discovery endpoint (e.g. `https://auth.example.com/.well-known/openid-configuration`) |
=======
| `MARKDOWN_VAULT_MCP_BASE_URL` | Public base URL of the server (such as `https://mcp.example.com`; include prefix when mounted under subpath, such as `https://mcp.example.com/myservice`) |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | OIDC discovery endpoint (such as `https://auth.example.com/.well-known/openid-configuration`) |
>>>>>>> after updating
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | OIDC client ID registered with your provider |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | OIDC client secret |

## Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | ephemeral | JWT signing key. **Required on Linux/Docker**: the default is ephemeral and invalidates tokens on restart |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | n/a | Expected JWT audience claim; leave unset if your provider does not set one |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | `openid` | Comma-separated required scopes |
| `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN` | `false` | Set `true` to verify the upstream access token as JWT instead of the id token. Only needed when your provider issues JWT access tokens and you require audience-claim validation on that token |

## JWT Signing Key

The FastMCP default signing key is ephemeral (regenerated on startup), which forces clients to re-authenticate after every restart. Set a stable random secret to avoid this:

```bash
# Generate once, store in your .env file
openssl rand -hex 32
```

!!! danger "Linux / Docker"
    On Linux (including Docker), the ephemeral key is especially problematic because it does not persist across process restarts. Always set `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` in production.

## Setup with Authelia

!!! note
    Authelia does not support Dynamic Client Registration (RFC 7591). Clients must be registered manually in `configuration.yml`.

!!! warning "Opaque vs JWT access tokens"
    Authelia issues opaque (non-JWT) access tokens by default. **Remote mode requires JWT access tokens** — add `access_token_signed_response_alg: 'RS256'` to the Authelia client config. OIDCProxy mode works with opaque tokens (it verifies the `id_token` instead).

### Remote mode (recommended)

#### 1. Register the client in Authelia

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: markdown-vault-mcp
        client_secret: '$pbkdf2-sha512$...'   # authelia crypto hash generate
        redirect_uris:
          - https://mcp.example.com/callback
        grant_types: [authorization_code]
        response_types: [code]
        pkce_challenge_method: S256
        scopes: [openid, profile, email]
        access_token_signed_response_alg: 'RS256'
        token_endpoint_auth_method: 'client_secret_post'
```

#### 2. Set environment variables

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
```

Note: `CLIENT_ID` and `CLIENT_SECRET` are configured in Authelia only — they are not needed as MCP server env vars in remote mode.

#### 3. Start with HTTP transport

```bash
markdown-vault-mcp serve --transport http --port 8000
```

### OIDCProxy mode (fallback)

!!! note "Opaque access tokens"
    Authelia issues opaque (non-JWT) access tokens. This is handled automatically: the server verifies the `id_token` (always a standard JWT) instead. No extra configuration is needed.

### 1. Register the client in Authelia

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: markdown-vault-mcp
        client_secret: '$pbkdf2-sha512$...'   # authelia crypto hash generate
        redirect_uris:
          - https://mcp.example.com/auth/callback
        grant_types: [authorization_code]
        response_types: [code]
        pkce_challenge_method: S256
        scopes: [openid, profile, email]
```

### 2. Set environment variables

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=$(openssl rand -hex 32)
```

For subpath deployments (e.g., public URL `https://mcp.example.com/vault/mcp`), see [Subpath Deployments](#subpath-deployments) below.

See also `examples/obsidian-oidc.env`.

### 3. Start with HTTP transport

```bash
markdown-vault-mcp serve --transport http --port 8000
```

## Architecture

### Remote mode

The server validates tokens locally using JWKS — no upstream token calls after startup:

```
Client → IdP (authenticate + get JWT)
Client → markdown-vault-mcp (present JWT → validate via JWKS)
```

1. Client authenticates directly with the OIDC provider
2. Client presents the JWT access token to the MCP server
3. Server validates the token locally using the provider's JWKS keys
4. No upstream calls — token refresh is between client and IdP

### OIDCProxy mode

The server uses FastMCP's built-in `OIDCProxy` auth provider (not the external `mcp-auth-proxy` sidecar). The authentication flow:

```
Client → markdown-vault-mcp (with OIDCProxy) → OIDC Provider (Authelia/Keycloak)
```

1. Client connects to the MCP server
2. Server redirects to the OIDC provider for authentication
3. Provider authenticates the user and returns a code
4. Server exchanges the code for tokens
5. Subsequent requests include the JWT token

## Docker Compose with OIDC

```yaml
services:
  markdown-vault-mcp:
    image: ghcr.io/pvliesdonk/markdown-vault-mcp:latest
    env_file: .env
    volumes:
      - ${MARKDOWN_VAULT_MCP_SOURCE_DIR:?Set MARKDOWN_VAULT_MCP_SOURCE_DIR}:/data/vault
      - state-data:/data/state
    environment:
      MARKDOWN_VAULT_MCP_SOURCE_DIR: /data/vault
      MARKDOWN_VAULT_MCP_INDEX_PATH: /data/state/index.db
      MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH: /data/state/embeddings/embeddings
      MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR: /data/state/fastembed
      FASTMCP_HOME: /data/state/fastmcp
    restart: unless-stopped
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.markdown-vault-mcp.rule=Host(`mcp.example.com`)"
      - "traefik.http.routers.markdown-vault-mcp.tls.certresolver=letsencrypt"
      - "traefik.http.services.markdown-vault-mcp.loadbalancer.server.port=8000"
    networks:
      - traefik

volumes:
  state-data:

networks:
  traefik:
    external: true
```

With the corresponding `.env`:

```bash
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-stable-hex-key
```

<<<<<<< before updating
For a prefixed deployment (e.g., `https://mcp.example.com/vault/mcp`), see [Subpath Deployments](#subpath-deployments) below.
=======
For a prefixed deployment (such as `https://mcp.example.com/myservice/mcp`), see [Subpath Deployments](#subpath-deployments) below.
>>>>>>> after updating

## Subpath Deployments

When OIDC is enabled behind a reverse-proxy subpath, `BASE_URL` and `HTTP_PATH` serve different roles:

| Variable | Purpose | Example |
|----------|---------|---------|
<<<<<<< before updating
| `BASE_URL` | Public URL of the server, **including the subpath prefix** | `https://mcp.example.com/vault` |
| `HTTP_PATH` | Internal MCP endpoint mount point — **no subpath prefix** | `/mcp` |
=======
| `BASE_URL` | Public URL of the server, **including the subpath prefix** | `https://mcp.example.com/myservice` |
| `HTTP_PATH` | Internal MCP endpoint mount point (**no subpath prefix**) | `/mcp` |
>>>>>>> after updating

The reverse proxy strips the subpath prefix before forwarding to the application. FastMCP concatenates `BASE_URL + HTTP_PATH` to build the public resource URL, so including the prefix in both produces broken URLs with duplicated path segments.

!!! danger "Do not duplicate the subpath"
<<<<<<< before updating
    Setting `BASE_URL=https://mcp.example.com/vault` **and** `HTTP_PATH=/vault/mcp` produces a duplicated resource URL: `https://mcp.example.com/vault/vault/mcp`. The subpath belongs in `BASE_URL` only.
=======
    Setting `BASE_URL=https://mcp.example.com/myservice` together with `HTTP_PATH=/myservice/mcp` produces a duplicated resource URL: `https://mcp.example.com/myservice/myservice/mcp`. The subpath belongs in `BASE_URL` only.
>>>>>>> after updating

### Configuration

Environment variables:

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com/vault
MARKDOWN_VAULT_MCP_HTTP_PATH=/mcp
```

Register this callback URI in your OIDC provider:

```text
https://mcp.example.com/vault/auth/callback
```

### Reverse proxy routing

The reverse proxy must:

1. **Strip the prefix** (`/vault`) from operational routes before forwarding to the app
2. **Forward OAuth discovery routes** to this service (without stripping prefixes):
<<<<<<< before updating
    - `/.well-known/oauth-authorization-server` — authorization server metadata
    - `/.well-known/oauth-protected-resource/vault/mcp` — protected resource metadata
=======
    - `/.well-known/oauth-authorization-server`: authorization server metadata
    - `/.well-known/oauth-protected-resource/myservice/mcp`: protected resource metadata
>>>>>>> after updating

Example Traefik configuration:

```yaml
labels:
  # Operational routes: strip /vault prefix before forwarding
  - "traefik.http.routers.vault-app.rule=Host(`mcp.example.com`) && PathPrefix(`/vault`)"
  - "traefik.http.middlewares.strip-vault.stripprefix.prefixes=/vault"
  - "traefik.http.routers.vault-app.middlewares=strip-vault"
  - "traefik.http.services.vault-app.loadbalancer.server.port=8000"
  # OAuth discovery routes: forward without stripping
  - "traefik.http.routers.vault-wellknown.rule=Host(`mcp.example.com`) && (PathPrefix(`/.well-known/oauth-authorization-server`) || PathPrefix(`/.well-known/oauth-protected-resource/vault/mcp`))"
  - "traefik.http.routers.vault-wellknown.service=vault-app"
```

!!! note
    This configuration requires that no other OAuth service claims `/.well-known/oauth-authorization-server` on this hostname. See [Shared-hostname limitation](#shared-hostname-limitation) below.

### Shared-hostname limitation

!!! warning "Shared-hostname subpath with native OIDC is not supported"
    When multiple OAuth-capable services share a hostname (e.g., `mcp-auth-proxy` at the root and `markdown-vault-mcp` at `/vault`), native OIDC on a subpath does not work.

    **Why:** FastMCP serves the OAuth authorization-server metadata at `/.well-known/oauth-authorization-server` (host root), regardless of the subpath in `BASE_URL`. The FastMCP codebase contains an RFC 8414 path-aware override (`OIDCProxy.get_well_known_routes()`) that would serve it at `/.well-known/oauth-authorization-server/vault`. However, this method is not wired into the route mounting flow and is effectively dead code.

    The protected-resource metadata (`/.well-known/oauth-protected-resource/vault/mcp`) is correctly path-namespaced and does not collide. Only the authorization-server discovery route is the problem.

    This works when `markdown-vault-mcp` is the **only** OAuth service on the hostname — the host-root `/.well-known/oauth-authorization-server` does not collide with anything. It breaks when another service already owns that route.

**Recommendations for shared-hostname scenarios:**

<<<<<<< before updating
- **Dedicated hostname** (preferred): give `markdown-vault-mcp` its own hostname (e.g., `vault.example.com`) so discovery routes do not collide.
=======
- **Dedicated hostname** (preferred): give the MCP server its own hostname (such as `myservice.example.com`) so discovery routes do not collide.
>>>>>>> after updating
- **External auth gateway**: use `mcp-auth-proxy` as a sidecar instead of native OIDC. The MCP server runs unauthenticated behind the proxy, and the proxy handles OAuth discovery at its own routes.


<!-- DOMAIN-OIDC-EXTRA-START -->
<!-- Project-specific notes for OIDC deployment; kept across copier update. -->

## Project-specific notes

<!-- Add domain-specific caveats here (e.g. "Keycloak requires X claim",
     "Authelia token-cache quirk for /admin paths", "this server's audience
     claim must include 'mcp'"). Use sub-headings to organize if needed. -->

<!-- DOMAIN-OIDC-EXTRA-END -->
