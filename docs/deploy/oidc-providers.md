---
description: "Register Markdown Vault MCP with Authelia, Keycloak or Google, or sign in through GitHub via a broker, and see which OIDC mode each allows."
kind: how-to
---

# OIDC providers

Each provider below has one section with four parts:

- the [mode](oidc.md#which-mode) it allows;
- the provider's side: the client to register, and the discovery URL;
- the server's side: the variables;
- a check.

The modes and the variables themselves are on the [OIDC](oidc.md) page. Under OIDC the provider alone decides who gets in; what an accepted caller then reaches is the [security model](../security-model.md).

| Provider | Dynamic Client Registration | Access tokens | Mode as the provider ships |
|---|---|---|---|
| Authelia | none | opaque; signed JSON Web Tokens per client with `access_token_signed_response_alg` | `oidc-proxy`; `remote` with a hand-registered client and signed access tokens |
| Keycloak | yes (26.6.0 or newer for MCP clients) | signed JSON Web Tokens | `remote`; `oidc-proxy` works too |
| Google | none | opaque | `oidc-proxy` |
| GitHub | not an OpenID Connect provider | none | a broker in front, Keycloak below |

Every setup shares two variables: `MARKDOWN_VAULT_MCP_BASE_URL`, the server's public URL (prefix included when it runs under one), and `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL`, the provider's discovery document. In `oidc-proxy` mode the client registered at the provider has one redirect URI, `BASE_URL` followed by `/auth/callback`, and the server also takes `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID`, `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` and a stable `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` (`openssl rand -hex 32`). `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` takes a space- or comma-separated list and defaults to `openid`.

## Authelia

Authelia has no Dynamic Client Registration, so the client is registered by hand in `configuration.yml`, and the usual mode is `oidc-proxy`: the server is that client. Authelia's access tokens are opaque unless the client sets `access_token_signed_response_alg`; the proxy verifies the ID token by default, so nothing changes for it.

### 1. Register the client

```yaml
identity_providers:
  oidc:
    lifespans:
      custom:
        mcp_long_lived:
          access_token: '8h'
          id_token: '8h'
          refresh_token: '30d'
    clients:
      - client_id: markdown-vault-mcp
        client_name: Markdown Vault MCP
        client_secret: '$pbkdf2-sha512$...'   # step 2
        authorization_policy: two_factor      # your policy; one_factor also works
        lifespan: mcp_long_lived
        redirect_uris:
          - https://mcp.example.com/auth/callback
        grant_types: [authorization_code, refresh_token]
        response_types: [code]
        scopes: [openid, profile, email, offline_access]
        pkce_challenge_method: S256
```

The custom lifespan is there because of the token-refresh limitation on the [Authentication](authentication.md#known-limitations-mcp-oauth-token-refresh) page: a session through the proxy lasts as long as the upstream tokens, Authelia's defaults are one hour, and the proxy verifies the ID token, so its lifetime is the one that counts. `refresh_token` and `offline_access` let a client that refreshes keep going; in `oidc-proxy` mode it is the server that asks Authelia for `offline_access`, since it widens the scopes it advertises to clients with that scope and forwards them upstream. The server authenticates at the token endpoint with `client_secret_basic`, Authelia's default for a confidential client, so no `token_endpoint_auth_method` is needed.

### 2. Hash the secret

```bash
openssl rand -base64 32              # the secret; keep it for the server
authelia crypto hash generate pbkdf2 # paste it; the hash goes in configuration.yml
```

### 3. Configure the server

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=the-plain-secret-from-step-2
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=the-64-hex-characters-from-openssl
```

### Remote mode with Authelia

Four changes:

- `access_token_signed_response_alg: RS256` on the client, so Authelia issues its access tokens as signed JSON Web Tokens;
- a client for your MCP clients to use, whose redirect URIs are theirs rather than the server's;
- each MCP client configured with that client ID;
- `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` and `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` unset on the server.

Authelia puts `name` and `email` into an access token only through a `claims_policies` entry with an `access_token` section that the client references as its `claims_policy`.

## Keycloak

Keycloak accepts client registration over its API and issues signed access tokens, so `remote` works as it ships: the server validates tokens against the realm's keys and registers nothing. The realm's discovery document is at:

```text
https://auth.example.com/realms/<realm>/.well-known/openid-configuration
```

### Remote mode

1. Create a realm, or pick one, and switch to it.
2. Allow MCP clients to register: Keycloak 26.6.0 or newer (earlier versions ignored a registering client's `token_endpoint_auth_method` when it was `client_secret_post`, which MCP clients ask for; keycloak/keycloak#45309), with the realm's **Client Registration Policies** allowing anonymous registration from your clients' hosts (the Trusted Hosts and Max Clients policies), or an initial access token for a client you register in advance.
3. Give the realm an audience mapper that puts a value naming this server into its tokens, and set `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` to it. Without it the server accepts any unexpired token the realm signed, whichever client it was issued to.

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/realms/<realm>/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_AUDIENCE=https://mcp.example.com
```

### Proxy mode

1. **Clients** › **Create client**, with:
    - client ID `markdown-vault-mcp`;
    - **Client authentication** on, which makes it a confidential client;
    - **Valid redirect URIs** `https://mcp.example.com/auth/callback`.
2. Copy the secret from the client's **Credentials** tab.

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/realms/<realm>/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=the-secret-from-the-credentials-tab
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=the-64-hex-characters-from-openssl
```

## Google

Google has no Dynamic Client Registration and its access tokens are opaque, so the mode is `oidc-proxy`. Its discovery document is `https://accounts.google.com/.well-known/openid-configuration`.

1. In the [Google Cloud Console](https://console.cloud.google.com/apis/credentials), pick or create a project. A new project asks for the OAuth consent screen first: **Internal** for a Google Workspace organisation, **External** otherwise.
2. **APIs & Services** › **Credentials** › **Create credentials** › **OAuth client ID**, type **Web application**, with `https://mcp.example.com/auth/callback` as an authorized redirect URI.
3. Note the client ID and secret.

```bash
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://accounts.google.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=123456789-abcdef.apps.googleusercontent.com
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=the-secret-from-the-console
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=the-64-hex-characters-from-openssl
MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES=openid email
```

The server works with `openid` alone; it identifies a caller by the token's `sub` claim. `email` is added here because Google's ID token carries the address only when that scope is requested, and an address is what a reader of the logs recognises.

## GitHub

GitHub signs users in with OAuth 2.0, not OpenID Connect: it publishes no discovery document and offers no client registration, so it cannot be the provider the server reads `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` from. Put a broker in front and let the broker sign users in with GitHub. With Keycloak:

1. In the realm, **Identity Providers** › **Add provider** › **GitHub**, and copy the **Redirect URI** it shows.
2. On GitHub, create an OAuth App (**Settings** › **Developer settings** › **OAuth Apps**) with that redirect URI as its **Authorization callback URL**; note its client ID and secret.
3. Paste both into the Keycloak identity provider and save.

The server is configured against Keycloak exactly as in the [Keycloak](#keycloak) section; GitHub appears as a sign-in choice on Keycloak's login page.

## Check a setup

1. Open the discovery URL in a browser: it is a JSON document with `authorization_endpoint`, `token_endpoint` and `jwks_uri`. The server fetches it at startup and refuses to start when it cannot.
2. `curl -s -o /dev/null -w '%{http_code}\n' https://mcp.example.com/mcp` answers `401`: the endpoint is there and authentication is on.
3. `curl -s https://mcp.example.com/.well-known/oauth-protected-resource/mcp` returns the server's protected-resource metadata; its `authorization_servers` names the server itself in `oidc-proxy` mode and the provider in `remote` mode.
4. Connect a client. With Claude Code that is `claude mcp add --transport http markdown-vault-mcp https://mcp.example.com/mcp`, then `/mcp` › **Authenticate**. The provider's sign-in page opens in the browser, and the first tool call after it goes through. [A remote server](../get-started/http-client.md) covers the other clients.

A failed sign-in names its cause: `invalid_client` is a client ID or secret that does not match the provider's; `redirect_uri_mismatch` is a callback URI that differs from `BASE_URL` + `/auth/callback` in scheme, host, port or path, the prefix included.

<!-- DOMAIN-OIDC-PROVIDERS-EXTRA-START -->
### Claims this server reads

This server reads claims for one thing: the author of a git commit. With `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME_CLAIM` and `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL_CLAIM` set (to `name` and `email`, say), a commit made for a signed-in user names that user as its author. The committer stays the server's own identity. In `oidc-proxy` mode the claims come from the provider's ID token, and in `remote` mode from the access token, so the provider must put them in the token that mode reads. A missing claim falls back to the server's identity, with one warning in the log. The scopes that carry these claims (`profile`, `email`) go in `MARKDOWN_VAULT_MCP_OIDC_ADVERTISED_SCOPES`.

For Open Knowledge Format provenance, a signed-in user counts as a person; [OKF](../guides/okf.md) explains what that changes.
<!-- DOMAIN-OIDC-PROVIDERS-EXTRA-END -->
