# Authentication

This guide covers how to protect your markdown-vault-mcp server with authentication. Choose the mode that fits your deployment.

!!! warning "Transport requirement"
    Authentication only works with HTTP transport (`--transport http` or `sse`). It has no effect with `--transport stdio`.

## Auth modes

The server supports five authentication modes:

| Mode | When to use | Configuration |
|------|-------------|---------------|
| **Multi-auth** | Mixed clients — e.g. Claude web (OIDC) + Claude Code (bearer token) on the same server | Set both `MARKDOWN_VAULT_MCP_BEARER_TOKEN` and OIDC variables |
| **Bearer token** | Simple deployments behind a VPN, Docker compose stacks, development | Set `MARKDOWN_VAULT_MCP_BEARER_TOKEN` only |
| **OIDC (remote)** | Production — recommended over oidc-proxy. Local JWKS validation, no token re-validation | Set `MARKDOWN_VAULT_MCP_BASE_URL` + `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` only |
| **OIDC (oidc-proxy)** | Production with user identity, SSO, multi-user access; backward-compatible mode | Set all four OIDC variables (`BASE_URL`, `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`) |
| **No auth** | Local stdio usage, trusted networks | Default (nothing to configure) |

When both bearer token and OIDC are configured, the server accepts **either** credential — a valid bearer token or a valid OIDC session. This is useful when different clients require different authentication flows against the same vault instance.

---

## Bearer token

The simplest way to protect your server. A single static token shared between server and clients.

### Setup

1. Generate a random token:

    ```bash
    openssl rand -hex 32
    ```

2. Set the environment variable:

    ```bash
    MARKDOWN_VAULT_MCP_BEARER_TOKEN=your-generated-token
    ```

3. Start the server with HTTP transport:

    ```bash
    markdown-vault-mcp serve --transport http --port 8000
    ```

### Client usage

Clients must include the token in every request:

```
Authorization: Bearer your-generated-token
```

### When to use bearer token

- Deployments behind a VPN or firewall
- Docker compose stacks where services communicate internally
- Development and testing environments
- Any scenario where full OIDC is overkill

See also: [`examples/bearer-auth.env`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/examples/bearer-auth.env) for a ready-to-use example.

### Mapped bearer tokens (multi-subject)

The bearer-token mode above shares one subject across every authenticated caller — by default the library's `bearer-anon`, override with `MARKDOWN_VAULT_MCP_BEARER_DEFAULT_SUBJECT`.  For audit logs and authorization that distinguish callers, switch to mapped-token mode by pointing `MARKDOWN_VAULT_MCP_BEARER_TOKENS_FILE` at a TOML file:

```toml
# tokens.toml
[tokens]
"ghp_alice_xxxxxxxx" = "user:alice@example.com"
"sk_ci_yyyyyyyy"     = "service:ci-bot"
```

Each token resolves to a distinct subject string for downstream attribution.  Subject strings are opaque — the `<kind>:<id>` convention (`user:`, `service:`, `token:`) is documentation only.  When `BEARER_TOKENS_FILE` is set it overrides `BEARER_TOKEN` (a `WARNING` is logged if both are present).  A missing or malformed file aborts startup with `ConfigurationError` rather than silently denying every request.

For the per-tool authorization that consumes these subjects, see [Authorization (opt-in)](https://github.com/pvliesdonk/markdown-vault-mcp/blob/main/README.md#authorization-opt-in) in the project README.

---

## OIDC

Full OAuth 2.1 authentication using an external identity provider. Supports user login flows, SSO, and multi-user access control.

### OIDC mode selection

The server supports two OIDC modes:

| Mode | How it works | When to use |
|------|-------------|-------------|
| **remote** (recommended) | Validates tokens locally via JWKS. No client credentials needed. Token refresh happens between client and IdP directly. | Most deployments. Avoids the OIDCProxy double-validation session timeout. |
| **oidc-proxy** | Acts as an OAuth proxy — exchanges codes for tokens, stores sessions, re-validates upstream tokens on every request. | When your provider requires Dynamic Client Registration (DCR) emulation or you need the proxy's session management. |

Set `MARKDOWN_VAULT_MCP_AUTH_MODE` to force a mode, or let the server auto-detect:

- All four OIDC vars set (`BASE_URL`, `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`) → **oidc-proxy** (backward compatible)
- Only `BASE_URL` + `OIDC_CONFIG_URL` set → **remote**

!!! tip "Migrating from oidc-proxy to remote"
    Remove `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` from your env. The server will auto-detect remote mode. Or set `AUTH_MODE=remote` explicitly.

### How it works (remote mode) { #remote-mode }

The server validates tokens locally using JWKS — no upstream token calls after startup:

```
Client → IdP (authenticate + get JWT)
Client → markdown-vault-mcp (present JWT → validate via JWKS)
```

### How it works (oidc-proxy mode)

The server uses FastMCP's built-in `OIDCProxy` — no external auth sidecar needed:

```
Client → markdown-vault-mcp (OIDCProxy) → OIDC Provider
```

1. Client connects to the server
2. Server redirects to the OIDC provider for login
3. Provider authenticates the user and returns a code
4. Server exchanges the code for tokens
5. Subsequent requests include the JWT

### Required variables

| Variable | Description |
|----------|-------------|
| `MARKDOWN_VAULT_MCP_BASE_URL` | Public base URL (e.g. `https://mcp.example.com`). Also required for MCP Apps domain computation. |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | OIDC discovery endpoint |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | Client ID registered with your provider (oidc-proxy mode only) |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | Client secret (oidc-proxy mode only) |

### Optional variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARKDOWN_VAULT_MCP_AUTH_MODE` | auto-detected | Force OIDC mode: `remote` or `oidc-proxy` |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | ephemeral | JWT signing key — **required on Linux/Docker** (oidc-proxy mode only) |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | — | Expected JWT audience claim; leave unset if your provider does not set one |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | `openid` (oidc-proxy) | Comma-separated required scopes. The `openid` default applies in oidc-proxy mode; remote mode requires no scopes unless this is set |
| `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN` | `false` | Set `true` to verify the access token as a JWT instead of the id token; useful for audience-claim validation on JWT access tokens (oidc-proxy mode only) |

!!! danger "JWT signing key on Linux/Docker (oidc-proxy mode)"
    Without `OIDC_JWT_SIGNING_KEY`, FastMCP generates an ephemeral key that invalidates all tokens on restart. Always set a stable key in production:

    ```bash
    openssl rand -hex 32
    ```

!!! tip "Long-running sessions"
    Current MCP clients do not reliably refresh tokens — see [Known Limitations](#known-limitations-mcp-oauth-token-refresh). Configure **all** token lifetimes (access, id, refresh) on your identity provider to cover a full workday (8h+). For simpler deployments, bearer token auth is unaffected by these limitations.

### Provider guides

For step-by-step setup with specific providers:

- [Authelia](oidc-providers.md#authelia)
- [Keycloak](oidc-providers.md#keycloak)
- [Google](oidc-providers.md#google)
- [GitHub (via Keycloak broker)](oidc-providers.md#github)

For the full OIDC reference (env vars, Docker Compose, subpath deployments, architecture):

- [OIDC Authentication reference](../deployment/oidc.md)

---

## Troubleshooting

### "invalid client" error

The `client_id` and/or `redirect_uris` in your OIDC provider config don't match the values in your `.env` file. Verify both sides match exactly.

### Tokens invalidated after restart

You're missing `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY`. Without it, FastMCP generates an ephemeral key on each startup. Generate and set a stable key:

```bash
openssl rand -hex 32
```

### Auth has no effect

Authentication only works with HTTP transport. If you're using `--transport stdio`, auth is silently ignored. Switch to `--transport http`.

### Bearer token not working

- Verify the env var is set and non-empty (whitespace-only values are ignored)
- Check that clients send `Authorization: Bearer <token>` (not `Basic` or other schemes)
- If OIDC is also configured, multi-auth is active — both bearer and OIDC are accepted simultaneously

### OIDC redirect fails

- Verify `BASE_URL` matches your public URL exactly (including any subpath prefix)
- For subpath deployments, see the [subpath deployment guide](../deployment/oidc.md#subpath-deployments) — `BASE_URL` must include the prefix, `HTTP_PATH` must not
- Check that `redirect_uris` in your provider config includes your callback URL (e.g., `https://mcp.example.com/auth/callback`)

### Session drops after token expiry

**Symptom:** the MCP client works for a period (often ~1 hour), then starts returning 401 errors or stops responding. Restarting the client fixes it temporarily.

**Root cause:** this is almost always a token lifetime issue, not a server bug. Check three things:

1. **id_token lifetime** (most common): When using `verify_id_token` mode (the default for Authelia), the server re-validates the upstream `id_token` on every request. If your provider's `id_token` lifetime is shorter than the `access_token` lifetime, the session dies at the `id_token` expiry — even though the access token is still valid. Authelia defaults `id_token` to 1 hour. **Fix: set `id_token` lifetime to match `access_token`** in your provider config.

2. **access_token lifetime**: If both `id_token` and `access_token` are set correctly but sessions still drop, check that the provider's `expires_in` response matches your configured lifetime.

3. **No refresh token**: See [Known Limitations](#known-limitations-mcp-oauth-token-refresh) below — current MCP clients cannot refresh tokens, so sessions are limited to the token lifetime.

**Workaround:** configure **all** token lifetimes on your identity provider to cover a full workday:

```yaml
# Authelia example
lifespans:
  custom:
    mcp_long_lived:
      access_token: '8h'
      id_token: '8h'        # must match access_token for verify_id_token mode
      refresh_token: '30d'
```

See the [Authelia provider guide](oidc-providers.md#authelia) for the full configuration.

### Opaque access tokens (Authelia)

Authelia issues opaque (non-JWT) access tokens. This is handled automatically — the server verifies the `id_token` instead. No extra configuration needed. See the [Authelia guide](oidc-providers.md#authelia) for details.

---

## Known Limitations: MCP OAuth token refresh

!!! warning "Ecosystem-wide issue"
    The limitations below affect **all** OAuth-protected MCP servers, not just markdown-vault-mcp. They are caused by issues in the MCP client implementations (Claude Code, Claude.ai, Claude Desktop) and the MCP Python SDK. Check the linked tracking issues for current status.

### The problem

MCP clients cannot maintain sessions beyond the token lifetime because token refresh does not work. When tokens expire, the session drops and requires manual re-authentication. This affects every provider — Authelia, Keycloak, Google, Slack, Notion, Atlassian, and others.

### Why refresh doesn't work

Three independent issues prevent token refresh:

| Layer | Issue | Impact |
|-------|-------|--------|
| **Claude Code** | Stores refresh tokens but never uses them ([claude-code#21333](https://github.com/anthropics/claude-code/issues/21333)) | Refresh tokens are obtained and saved but never sent back to refresh expired access tokens |
| **Claude Code** | Never requests `offline_access` scope ([claude-code#7744](https://github.com/anthropics/claude-code/issues/7744)) | Most OIDC providers won't issue a refresh token without this scope |
| **MCP Python SDK** | Token refresh deadlocks inside SSE streams ([python-sdk#1326](https://github.com/modelcontextprotocol/python-sdk/issues/1326)) | Even with a valid refresh token, the SDK hangs when attempting refresh during an active stream |

The server-side refresh architecture (FastMCP's `OAuthProxy.exchange_refresh_token()`) is correctly implemented and would work — but it requires the client to initiate the refresh, which none of the current clients do reliably.

### What works today

**Remote auth mode** (`AUTH_MODE=remote` or auto-detected) avoids the double-validation problem entirely. The server validates tokens locally via JWKS — it never stores or re-validates upstream tokens. This is the recommended mode for new deployments.

**Bearer token auth** is unaffected by all of the above. If your deployment allows it (e.g., Claude Code with env vars, or API clients), bearer tokens are the simplest and most reliable option.

**Long token lifetimes** are the only viable workaround for OIDC in oidc-proxy mode. Set all three lifetimes (access, id, refresh) to cover your typical session duration:

- `access_token: '8h'` — covers a workday
- `id_token: '8h'` — **must match access_token** when using `verify_id_token` mode (critical for Authelia)
- `refresh_token: '30d'` — ready for when clients support refresh
- Include `offline_access` in provider-side scopes — no effect today, but will enable refresh when clients are fixed

### Tracking

These upstream issues are actively tracked:

- [anthropics/claude-code#21333](https://github.com/anthropics/claude-code/issues/21333) — refresh tokens stored but never used
- [anthropics/claude-code#7744](https://github.com/anthropics/claude-code/issues/7744) — `offline_access` scope never requested
- [modelcontextprotocol/python-sdk#1326](https://github.com/modelcontextprotocol/python-sdk/issues/1326) — SSE refresh deadlock

When these are resolved, OIDC sessions should persist indefinitely via automatic token refresh with no changes needed to markdown-vault-mcp.

<!-- DOMAIN-AUTH-EXTRA-START -->
<!-- DOMAIN-AUTH-EXTRA-END -->
