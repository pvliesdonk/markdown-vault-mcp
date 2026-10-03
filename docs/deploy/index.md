---
description: "Run Markdown Vault MCP for real: Docker or a system package, authentication, OIDC and its providers, a reverse proxy."
kind: how-to
---

# Deploy

These pages are for an operator running the server for others over HTTP, as a container or a system service, with every caller authenticated. Each page covers one task. Start with [Docker](docker.md) for a container or [systemd](systemd.md) for the `.deb`/`.rpm` package; then [Authentication](authentication.md), then the [reverse proxy](reverse-proxy.md) that exposes it.

- [Docker](docker.md): the image, Compose, ports, volumes and health checks.
- [systemd and the Linux packages](systemd.md): the package's contents and the environment file; the unit's confinement; upgrades.
- [Authentication](authentication.md): bearer tokens or OIDC, and when to use which.
- [OIDC](oidc.md): which of the two OIDC modes to run, and how to configure it.
- [OIDC providers](oidc-providers.md): Authelia, Keycloak, Google, and GitHub through a broker.
- [Reverse proxy](reverse-proxy.md): TLS, its own hostname or a path prefix, and the routing OAuth discovery needs.
- [MCP Apps](mcp-apps.md): which clients render the server's interface, and the one setting it may need.
- [Transfer links](transfer-links.md): one-time download and upload URLs, for the person holding one and for the operator.

Before exposing the server, read the [security model](../security-model.md): an authenticated caller has every tool the instance exposes, and under OIDC the identity provider alone decides who gets in.

<!-- DOMAIN-DEPLOY-INTRO-START -->
<!-- What deploying this server adds to the generic picture (data it needs mounted, services it calls); kept across copier update. -->
<!-- DOMAIN-DEPLOY-INTRO-END -->
