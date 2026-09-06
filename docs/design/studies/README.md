# Architectural Studies

> **Status:** long-term investigations. No implementation proposed or committed to.

Two studies map the project's potential — from a single MCP server to a layered
vault platform. They are orthogonal and compose: one asks *where* the vault runs,
the other asks *how its internals are shaped*.

## The two studies

1. **[Vault service separation](vault-service-separation.md)** — *process*
   separation: run the vault as a separate read-write service with an HTTP/JSON
   API, the MCP server as a thin client, and the integrated mode preserved
   alongside.

2. **[Decoupling and layering](decoupling-and-layering.md)** — *internal*
   decoupling: nine seams (versioned filestore, identity, search/index, adapter
   layer, access policy, vault registry, dialect hooks, artifact store, document
   parser) that collapse to a single content boundary — indexable (format +
   dialect) vs. artifact.

## The combined potential

Together they describe one target: a **vault platform** — a synchronous core with
pluggable versioning, identity, and search, driven by interchangeable adapters
(MCP, CLI, service), running in-process or as a service, over one or many vaults,
for one or many users, over any indexable format.

| Axis | Process (study 1) | Internal (study 2) |
|---|---|---|
| Deployment | separate service; MCP as a thin client | — |
| Versioning | git moves into the service | `VersionedStore` (Git/Noop/Remote) |
| Identity | propagation across the boundary | `Principal`/`IdentitySource`/`AccessPolicy` |
| Search | the service owns the index | `KeywordIndex`/`GraphStore`/`VectorStore` |
| Content | — | dialect hooks + `ArtifactStore` + `DocumentParser` |
| Scale | one service, many consumers | `VaultRegistry` + multi-user |

## Framing

These are investigations, not plans. The question they answer is *whether* these
directions are worth investigating in theory — not *how* to get there. Sequencing,
mechanics, and migration are deliberately out of scope. No short-term
implementation is intended.
