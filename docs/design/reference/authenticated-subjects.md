---
type: Reference
title: Authenticated subjects and human attribution
description: The caller identifiers exposed by FastMCP and pvl-core, the limits of OAuth claims as evidence of a human, and how common issuers mark an application's token
subject_version: "FastMCP 4.0.3 (7129236c); fastmcp-pvl-core 7.1.0 (f2b264439); RFC 9068 and RFC 9700; issuer documentation and source read 2026-10-31 (Keycloak a79e6657, authentik 88786996, Authelia 2eb6cb8c, Zitadel 0c0ee1da)"
valid_for: "FastMCP 4.x and fastmcp-pvl-core 7.x; re-research on either next major. The issuer section is a moving target: re-research by stale_after"
generated:
  by: process:researching-references
  at: 2026-10-31T12:00:00Z
verified:
  - by: process:researching-references-refute
    at: 2026-09-14T05:33:32Z
  - by: process:researching-references-refute
    at: 2026-10-31T13:00:00Z
stale_after: 2027-04-30
status: stable
sources:
  - id: core-subject
    title: pvl-core subject extraction at f2b264439
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/blob/f2b2644395b55368ca339b07e75d3b68d2c66883/src/fastmcp_pvl_core/_subject.py
    accessed: 2026-09-14
  - id: core-bearer
    title: pvl-core bearer verifier construction at f2b264439
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/blob/f2b2644395b55368ca339b07e75d3b68d2c66883/src/fastmcp_pvl_core/_auth.py
    accessed: 2026-09-14
  - id: fastmcp-jwt
    title: FastMCP static token verification at 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/fastmcp_slim/fastmcp/server/auth/providers/jwt.py
    accessed: 2026-09-14
  - id: jwt-profile
    title: RFC 9068 — JWT Profile for OAuth 2.0 Access Tokens
    resource: https://www.rfc-editor.org/rfc/rfc9068.html
    accessed: 2026-09-14
  - id: oauth-security
    title: RFC 9700 — Best Current Practice for OAuth 2.0 Security
    resource: https://www.rfc-editor.org/rfc/rfc9700.html
    accessed: 2026-09-14
  - id: jwt-profile-2
    title: RFC 9068 — JWT Profile for OAuth 2.0 Access Tokens (re-read for §2.2, §5)
    resource: https://www.rfc-editor.org/rfc/rfc9068.txt
    accessed: 2026-10-31
  - id: cognito-access-token
    title: Amazon Cognito — Understanding the access token
    resource: https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-access-token.html
    accessed: 2026-10-31
  - id: authelia-session
    title: Authelia OIDC session subject at 2eb6cb8c
    resource: https://github.com/authelia/authelia/blob/2eb6cb8c/internal/oidc/session.go
    accessed: 2026-10-31
  - id: authelia-const
    title: Authelia OIDC claim-name constants at 2eb6cb8c
    resource: https://github.com/authelia/authelia/blob/2eb6cb8c/internal/oidc/const.go
    accessed: 2026-10-31
  - id: authelia-clients
    title: Authelia — OpenID Connect clients configuration
    resource: https://www.authelia.com/configuration/identity-providers/openid-connect/clients/
    accessed: 2026-10-31
  - id: okta-oauth
    title: Okta OAuth 2.0 and OpenID Connect overview (token claims)
    resource: https://developer.okta.com/docs/api/openapi/okta-oauth/guides/overview/
    accessed: 2026-10-31
  - id: auth0-profiles
    title: Auth0 — Access token profiles
    resource: https://auth0.com/docs/secure/tokens/access-tokens/access-token-profiles
    accessed: 2026-10-31
  - id: entra-optional
    title: Microsoft Entra ID — Optional claims reference
    resource: https://learn.microsoft.com/en-us/entra/identity-platform/optional-claims-reference
    accessed: 2026-10-31
  - id: entra-claims-validation
    title: Microsoft Entra ID — Secure applications and APIs by validating claims
    resource: https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation
    accessed: 2026-10-31
  - id: keycloak-grant
    title: Keycloak client credentials grant at a79e6657
    resource: https://github.com/keycloak/keycloak/blob/a79e6657/services/src/main/java/org/keycloak/protocol/oidc/grants/ClientCredentialsGrantType.java
    accessed: 2026-10-31
  - id: keycloak-constants
    title: Keycloak service-account constants at a79e6657
    resource: https://github.com/keycloak/keycloak/blob/a79e6657/common/src/main/java/org/keycloak/common/constants/ServiceAccountConstants.java
    accessed: 2026-10-31
  - id: keycloak-scope
    title: Keycloak service_account client scope (addServiceAccountClientScope) at a79e6657
    resource: https://github.com/keycloak/keycloak/blob/a79e6657/services/src/main/java/org/keycloak/protocol/oidc/OIDCLoginProtocolFactory.java
    accessed: 2026-10-31
  - id: keycloak-sub
    title: Keycloak subject mapper at a79e6657
    resource: https://github.com/keycloak/keycloak/blob/a79e6657/services/src/main/java/org/keycloak/protocol/oidc/mappers/SubMapper.java
    accessed: 2026-10-31
  - id: authentik-cc
    title: authentik client credentials grant at 88786996
    resource: https://github.com/goauthentik/authentik/blob/88786996/authentik/providers/oauth2/token/client_credentials.py
    accessed: 2026-10-31
  - id: authentik-id-token
    title: authentik token claims (sub, azp) at 88786996
    resource: https://github.com/goauthentik/authentik/blob/88786996/authentik/providers/oauth2/id_token.py
    accessed: 2026-10-31
  - id: zitadel-cc
    title: Zitadel client credentials client at 0c0ee1da
    resource: https://github.com/zitadel/zitadel/blob/0c0ee1da/internal/api/oidc/client_credentials.go
    accessed: 2026-10-31
---

# Authenticated subjects and human attribution

## Scope

- Covers the identity inputs consumed by `_identity.py`, especially #1463.
- Covers how common issuers shape a client-credentials (application) access
  token, for #1480.
- Does not cover credential validation.
- Elicitation transport semantics are in [FastMCP 4](fastmcp-4.md).

## Claims

- `get_subject()` prefers a non-empty string `claims.sub`, then a non-empty
  token `client_id`. Without a token it returns `local` in auth mode `none`,
  otherwise `None`; it does not classify people and services. [source: core-subject]
  [pins: tests/test_identity.py::TestServicePrincipals::test_real_bearer_verifier_is_not_human]
- `get_claims()` returns the token's dictionary, `{}` for a token without
  usable claims, and `None` without a token. [source: core-subject]
  [pins: tests/test_identity.py::TestServicePrincipals::test_real_bearer_verifier_is_not_human]
- The single bearer builder puts `bearer_default_subject` (default
  `bearer-anon`) in `client_id`; the mapped builder puts each configured
  subject there. Both supply scopes and no `sub`. Subject spelling does not
  establish human identity. [source: core-bearer]
  [pins: tests/test_identity.py::TestServicePrincipals::test_real_bearer_verifier_is_not_human, tests/test_okf_write.py::TestServiceTokenOkf::test_write_uses_tool_provenance]
- FastMCP's static verifier returns the configured entry as `claims`, so
  these bearer tokens have `client_id` and scopes in their claims, rather
  than an empty dictionary. [source: fastmcp-jwt]
  [pins: tests/test_identity.py::TestServicePrincipals::test_real_bearer_verifier_is_not_human]
  [observed: installed sources match the pinned upstream files byte-for-byte;
  invoking build_bearer_auth, verify_token and resolve_mcp_principal in the
  SDK auth context reproduced human:bearer-anon on cde3374b]
- RFC 9068 §2.2 allows access-token `sub` to identify either a resource owner
  or a client application; `client_id` is present in both cases. Neither
  claim's presence proves a human. [source: jwt-profile]
- RFC 9700 §4.15 describes confusion between client and resource-owner
  subjects; §4.15.1 requires an issuer mechanism to distinguish them when
  the described namespace-confusion risk cannot otherwise be avoided.
  It defines no universal human/service claim. [source: oauth-security]

## How issuers mark an application's token

No standard claim marks an access token as issued to an application. The
RFC 9068 `client_id` claim is REQUIRED in every token, so its presence says
nothing; §2.2 says only that a client-credentials `sub` SHOULD identify the
client, and §5 calls using the `client_id` itself an election the issuer
makes. [source: jwt-profile-2] The issuers differ:

- **Amazon Cognito:** in a machine-to-machine token, "the `sub` and
  `client_id` claims have the same value". [source: cognito-access-token]
  [pins: tests/test_identity.py::TestIssuerServiceMarkers::test_application_token_is_a_service]
- **Authelia:** the subject falls back to the client ID for the client
  credentials flow, and the token carries a `client_id` claim with the same
  value (the claim is added in every flow). [source: authelia-session]
  [source: authelia-const] Access tokens are encoded as RFC 9068 JWTs only
  when a client sets `access_token_signed_response_alg` to something other
  than its default `none`, or sets `access_token_signed_response_key_id`.
  [source: authelia-clients]
- **Okta (custom authorization server):** `cid` is the client ID and `uid`
  is absent when no user is bound to the token. [source: okta-oauth] The
  default `sub` claim is the client ID when there is no user
  (`(appuser != null) ? appuser.userName : app.clientId`), and an admin can
  change it. [unverified: read in a recorded API response under
  `tests/integration/cassettes/test_auth_server_it/` in okta/okta-sdk-python,
  not in Okta's documentation; Okta documenting the custom authorization
  server's default `sub` expression would verify it]
  [pins: tests/test_identity.py::TestIssuerServiceMarkers::test_application_token_is_a_service]
- **Auth0:** a machine-to-machine `sub` identifies the application, typically
  `<client_id>@clients` (an agent-linked client's `sub` is its `agent_id`,
  without the suffix); in the Auth0 token profile, `gty` is present for
  `client-credentials`, `password` and `refresh_token`, while the RFC 9068
  profile does not use `gty`. [source: auth0-profiles]
  [pins: tests/test_identity.py::TestIssuerServiceMarkers::test_application_token_is_a_service]
- **Microsoft Entra ID:** `idtyp` is `app` in an app-only token, "the most
  accurate way for an API to determine if a token is an app token or an
  app+user token"; it is an optional claim the resource may have to
  configure. [source: entra-optional] For app-only permissions an API can
  instead check that `scp` is absent. [source: entra-claims-validation]
  [pins: tests/test_identity.py::TestIssuerServiceMarkers::test_application_token_is_a_service]
- **Keycloak:** a client-credentials token belongs to the client's
  service-account user, whose name has the prefix `service-account-`.
  [source: keycloak-grant] [source: keycloak-constants] `sub` is the session
  user's ID, so here the service account's, not the client ID.
  [source: keycloak-sub] The `service_account` client scope maps a
  `client_id` claim from a user-session note that the client-credentials
  grant sets; no other grant was seen setting it. [source: keycloak-scope]
  [source: keycloak-grant] RFC 9068 tokens from other issuers carry
  `client_id` in every token, so its presence is not a marker across issuers.
- **authentik:** client credentials authenticate as a user. With the
  provider's own secret, authentik creates a service account named
  `ak-<provider>-client_credentials`; with a username and password it uses
  that existing user. [source: authentik-cc] `sub` is that user's subject per
  the provider's `sub_mode`, and `azp` is the provider's client ID in every
  access token, so neither marks an application's token.
  [source: authentik-id-token]
- **Zitadel:** the token's user is the machine user's ID and its client ID is
  the machine user's username, so the two differ; the token type follows the
  machine user's access-token setting. [source: zitadel-cc] No marker claim
  was found. [unverified: absence in the code read, not a documented
  statement; reading the JWT access-token claim builder would verify it]

`sub == client_id` can also be wrong the other way: where client IDs and user
identifiers share a namespace, RFC 9700 §4.15.1 tells the issuer not to let a
client choose an ID that collides with a user's, and to give the resource
server another way to tell the tokens apart where it cannot avoid that.
[source: oauth-security] authentik is one place this matters: `azp` is the
client ID in every access token, so a `sub_mode` that yields usernames makes
`sub == azp` depend on account naming. [source: authentik-id-token]

## Where this project departs from the subject

The [write-identity design](../design.md) determines OKF
attribution. Client-ID-only callers are service principals and use the tool
actor, including single, custom and mapped bearer credentials. Elicitation
can confirm a human review without identifying the person; that review uses
`human:local`. `trust-auth` requires a human principal.

A token with a usable `sub` is a service principal when the issuer marks it
as an application's by one of the markers above that the check can read
without knowing the issuer: `sub` equal to `client_id`, `azp` or `cid`, `idtyp: app`, or
`gty: client-credentials` (#1480).
[pins: tests/test_identity.py::TestIssuerServiceMarkers::test_application_token_is_a_service, tests/test_identity.py::TestIssuerServiceMarkers::test_user_token_stays_human, tests/test_identity.py::TestIssuerServiceMarkers::test_application_token_cannot_attest_as_human]
Every other token with a usable `sub` is still read as a human subject. That
is an application assumption, not a consequence of OAuth: Keycloak,
authentik and Zitadel service accounts carry no such marker and are recorded
as human. Name/email claims remain independent inputs for Git attribution.

## Not covered

A token from Keycloak, authentik or Zitadel that this page names no marker
for; no live issuer was exercised in either research pass. Opaque access
tokens (Google's, and Authelia's and Zitadel's defaults) carry no claims to
inspect.
