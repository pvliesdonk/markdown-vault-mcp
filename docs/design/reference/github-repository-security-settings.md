---
type: Reference
title: GitHub repository security settings
description: Private vulnerability reporting, Dependabot alerts, push protection and the security policy file, as bootstrap.yml and SECURITY.md rely on them.
subject_version: "GitHub.com REST API 2022-11-28, as of 2026-09"
valid_for: "GitHub.com as of 2026-09"
generated:
  by: process:researching-references
  at: 2026-09-18
stale_after: 2027-03-18
status: draft
sources:
  - id: repos-rest
    title: REST API endpoints for repositories
    resource: https://docs.github.com/en/rest/repos/repos
    accessed: 2026-09-18
  - id: pvr-config
    title: Configuring private vulnerability reporting for a repository
    resource: https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository
    accessed: 2026-09-18
  - id: security-policy
    title: Adding a security policy to your repository
    resource: https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository
    accessed: 2026-09-18
  - id: health-files
    title: Creating a default community health file
    resource: https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file
    accessed: 2026-09-18
  - id: secret-scanning
    title: About secret scanning
    resource: https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning
    accessed: 2026-09-18
  - id: push-protection
    title: About push protection
    resource: https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection
    accessed: 2026-09-18
  - id: mvm-1510
    title: "markdown-vault-mcp#1510: request to enable private vulnerability reporting"
    resource: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1510
    accessed: 2026-09-18
---

# GitHub repository security settings

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

## Scope

This page supports the security job in `.github/workflows/bootstrap.yml` and
the root `SECURITY.md`. It covers how GitHub.com exposes private vulnerability
reporting, Dependabot alerts and secret-scanning push protection through the
REST API, and where GitHub looks for a security policy. It does not cover
code scanning (the `codeql.yml` workflow has its own contract), GitHub
Enterprise Server, or organisation-level security configurations.

## Claims

### Private vulnerability reporting

- `GET /repos/{owner}/{repo}/private-vulnerability-reporting` returns
  `{"enabled": <bool>}` with status 200; `PUT` on the same path enables the
  feature and `DELETE` disables it, both returning 204. The docs state the
  authenticated user must have admin access; 422 is the documented error
  status. [source: repos-rest]
- An anonymous `GET` on a public repository answers 200 with
  `{"enabled": false}` where the feature is off, so the read needs no
  privilege on a public repository.
  [observed: unauthenticated `curl` of the endpoint on
  `pvliesdonk/fastmcp-server-template`, 2026-09-18, answered 200 with
  `{"enabled": false}`]
- GitHub documents the feature for public repositories: owners and
  administrators of public repositories can enable it, from
  **Settings → Advanced Security → Private vulnerability reporting**.
  [source: pvr-config]
- When a report arrives, GitHub notifies repository administrators and
  security managers according to their watch and notification settings.
  [source: pvr-config]
- With the feature off, a researcher's only path is a public issue asking for
  a private channel, which is exactly what a researcher did on a project
  generated from this template. [source: mvm-1510]
- Whether the `PUT` succeeds on a private repository, and with which error if
  not, was not exercised. `bootstrap.yml` therefore reads the repository's
  `private` flag first and skips the call on a private repository.
  [unverified]

### Dependabot (vulnerability) alerts

- `GET /repos/{owner}/{repo}/vulnerability-alerts` answers 204 when alerts
  are enabled and 404 when they are not; `PUT` enables them with 204, and
  the authenticated user must have admin access. [source: repos-rest]
- The `GET` is not anonymous: without admin credentials it answers 403 on a
  public repository.
  [observed: unauthenticated `curl` of the endpoint on
  `pvliesdonk/fastmcp-server-template`, 2026-09-18, answered 403]

### Secret scanning and push protection

- Secret scanning runs automatically, free, on public repositories; private
  and internal repositories need GitHub Secret Protection on GitHub Team or
  GitHub Enterprise Cloud. [source: secret-scanning]
- Repository-level push protection is disabled by default and needs Secret
  Protection enabled; a repository administrator, organisation owner,
  security manager or enterprise owner can enable it. Push protection *for
  users*, which blocks a user's own pushes of secrets to public
  repositories, is enabled by default and is a separate account setting.
  [source: push-protection]
- `PATCH /repos/{owner}/{repo}` takes a `security_and_analysis` object whose
  members (`advanced_security`, `secret_scanning`,
  `secret_scanning_push_protection`, `secret_scanning_ai_detection`,
  `secret_scanning_non_provider_patterns`, and others) each carry a `status`
  of `enabled` or `disabled`; the caller must have admin permission on the
  repository or be an owner or security manager of the organisation.
  [source: repos-rest]
- Whether that `PATCH` accepts `secret_scanning_push_protection` on a public
  repository owned by a personal account without a Secret Protection
  licence was not exercised. The bootstrap step treats a refusal as a
  warning, not a failure. [unverified]

### The security policy file

- GitHub recognises `SECURITY.md` as a community health file in the
  `.github` folder, the repository root, or the `docs` folder, in that order
  of precedence. A default file in the account's public `.github`
  repository applies to any repository of that account that lacks its own.
  [source: health-files]
- The policy appears under the repository's Security tab, and GitHub asks
  it to state supported versions and how to report a vulnerability.
  [source: security-policy]

## Where this project departs from the subject

GitHub offers every setting above as a click in the UI. The template applies
them from `bootstrap.yml` instead, with `RELEASE_TOKEN`, so the state is
re-applied on every bootstrap run rather than set once by whoever created the
repository. Private vulnerability reporting is gated on the repository being
public, because the documentation names public repositories only; Dependabot
alerts are enabled on every visibility; push protection is attempted on
public repositories and reported, not enforced, when GitHub refuses it.

## Not covered

- Organisation-level security configurations, which can override or lock
  the repository-level settings this page describes. A repository inside an
  organisation with such a configuration may see the bootstrap calls
  rejected or silently overridden. [unverified]
- Live enabling of any setting. This research pass made no writes; the first
  bootstrap run on a generated project is the verification, and a refute
  pass should promote this page from `draft` once one has succeeded.
  [unverified]
