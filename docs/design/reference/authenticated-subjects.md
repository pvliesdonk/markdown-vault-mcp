---
type: Reference
title: Authenticated subjects and human attribution
description: The caller identifiers exposed by FastMCP and pvl-core, and the limits of OAuth claims as evidence of a human
subject_version: "FastMCP 4.0.3 (7129236c); fastmcp-pvl-core 7.1.0 (f2b264439); RFC 9068 and RFC 9700"
valid_for: "FastMCP 4.x and fastmcp-pvl-core 7.x; re-research on either next major"
generated:
  by: process:researching-references
  at: 2026-09-14T05:33:32Z
verified:
  - by: process:researching-references-refute
    at: 2026-09-14T05:33:32Z
stale_after: 2027-03-14
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
---

# Authenticated subjects and human attribution

## Scope

- Covers the identity inputs consumed by `_identity.py`, especially #1463.
- Does not cover credential validation or issuer-specific human/service policy.
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

## Where this project departs from the subject

The [write-identity design](../design.md) determines OKF
attribution. Client-ID-only callers are service principals and use the tool
actor, including single, custom and mapped bearer credentials. Elicitation
can confirm a human review without identifying the person; that review uses
`human:local`. `trust-auth` requires a human principal.

For compatibility the server still interprets a usable token `sub` as a
human subject. This is an application assumption, not a consequence of OAuth:
service access tokens with `sub` remain a known limitation tracked in #1480.
Name/email claims remain independent inputs for Git attribution.

## Not covered

Issuer-specific distinction between end-user and service access tokens
(#1480); no live issuer was exercised in this research pass.
