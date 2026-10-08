# Vault Service Separation — Architectural Study

> **Status:** study (investigation only). No implementation is proposed or
> committed to in the short term.
> **Date:** 2026-08-22
> **Related:** [`decoupling-and-layering.md`](decoupling-and-layering.md) — the
> orthogonal *internal-decoupling* study; [`README.md`](README.md) ties the two
> together.

## Purpose

`markdown-vault-mcp` is one package containing two things: a **vault** (writing,
indexing, and searching a directory of markdown) and an **MCP layer** around it.
The vault is more generically useful than the MCP surface — a CLI, a web
interface, or another service could all want to search and edit the same running
index. This study investigates disentangling the two so the vault can run as a
**separate read-write service with an API**, with the MCP server as a thin
client, while the current **integrated** mode (one plugin / one container)
continues to exist alongside.

The study is deliberately not a plan. It maps the current boundary, identifies
the hard problems, and recommends a direction. Nothing here is a commitment.

## 1. Baseline — the split that already exists

The disentanglement is not primarily a code refactor, because the code-level
split already exists. The design spec is explicit: `Vault` is the primary
interface and *"MCP is one consumer, not the only one"* (Use Cases #3).
Concretely:

- **The library is synchronous.** `Vault` is a thin façade over four facets —
  `reader`, `writer`, `graph`, `index` — plus lifecycle (`start`/`stop`/`close`).
  Managers are injected; no manager holds a back-reference to `Vault`.
- **The MCP layer wraps the library.** Tool handlers call the facets through
  `asyncio.to_thread(...)`. The MCP layer is a protocol adapter: it owns the tool
  surface, resources, prompts, auth, MCP Apps, and instructions.
- **One process owns the state.** The index (SQLite FTS5 + numpy sidecar +
  `state.json`) and the single-writer `IndexWriter` (all index mutations through
  one FIFO thread) live inside the `Vault` instance. The write-side orchestration
  — git sync (pull loop, deferred push, conflict resolution), the file watcher,
  the webhook, OKF write tools, transfer links, summarize — all assume this
  in-process ownership.

So the question is not "how do we untangle the code" but "how do we move a clean
in-process library boundary to a service boundary" — a process/deployment
separation, not a code separation.

### How clean is the library boundary, exactly?

A grep for `fastmcp` / `fastmcp_pvl_core` imports across `src/` shows the split
is already nearly complete:

- **The core library is clean.** `vault.py`, `managers/`, `facets/`,
  `indexing/`, `scanner.py`, `fts_index.py`, `vector_index.py`, `tracker.py`,
  `providers.py`, `types.py`, and `utils/` import nothing from FastMCP or
  pvl-core.
- **One deliberate exception.** `exceptions.py` re-exports pvl-core's
  `ConfigurationError` as the canonical config error (#638), so
  `markdown_vault_mcp.exceptions.ConfigurationError` *is* the pvl-core class.
- **Two auth-context couplings in the orchestration layer.** `git/strategy.py`
  reads the MCP access token (`fastmcp.server.dependencies.get_access_token`) to
  authenticate git operations, and `_okf_write.py` reads the current subject
  (`fastmcp_pvl_core.get_subject`) for OKF provenance stamping. Both are "who is
  acting" identity that flows from the MCP auth context into the write path.

This is the single most useful fact for the study: the vault library is already
~99% decoupled from MCP, and the residual couplings are (a) one re-export and
(b) two identity reads. The hard work is not untangling code; it is moving the
*identity* and *state-ownership* assumptions across a process boundary.

## 2. Target — the disentangled architecture

```
                    ┌──────────────────────────────┐
   MCP client ────▶ │  markdown-vault-mcp (thin)    │  auth, prompts, apps, instructions
                    │  = protocol adapter           │
                    └──────────────┬───────────────┘
                                   │ VaultClient (HTTP/JSON)
                    ┌──────────────▼───────────────┐
   CLI ───────────▶ │  vault service (separate      │  owns files + index + writer
   web ───────────▶ │  process / container)          │  + git + watcher + webhook
   other svc ─────▶ │  = Vault library + API        │  + OKF + summarize + transfer
                    └──────────────────────────────┘
```

- The **vault service** is the `Vault` library plus its orchestration, behind an
  API. It owns the files, the index, the single writer, git sync, the file
  watcher, the webhook, OKF, summarize, and transfer links.
- The **MCP server** becomes a thin client: it keeps auth, prompts, MCP Apps, and
  instructions, but every tool is a call to the vault service's API.
- **Other consumers** (CLI, web, other services) talk to the same API and share
  the same running index.
- The **integrated mode** is preserved: when no vault service is configured, the
  MCP server constructs the `Vault` in-process exactly as today.

## 3. The hard problems

### 3.1 The API surface and wire format

The facet surface is rich and typed: `search` returns `list[GroupedResult]`
(nested `SectionHit`s), `read` returns `NoteContent`, `get_context` returns a
`NoteContext` dossier, graph tools return `BacklinkInfo`/`OutlinkInfo`/
`BrokenLinkInfo`, index tools return `IndexStats`/`ReindexResult`, and so on.
Today these are Python dataclasses; the MCP layer already serializes them to JSON
for the MCP wire.

The vault service needs a **wire format** for the first time. The good news: the
JSON shapes already exist (the MCP tools are the de-facto schema). The work is to
formalize them — a typed schema (OpenAPI/JSON Schema) that the service serves and
the client validates — rather than invent a new one. The dataclasses become the
single source of truth, with the JSON schema generated from them (or
hand-maintained alongside, as the MCP tool schemas are today).

### 3.2 Transport

Three candidates:

| Transport | For | Against |
|---|---|---|
| **HTTP/JSON** | Universal; CLI/web/other-services are all HTTP-native; the MCP layer already speaks JSON; pvl-core already has HTTP infrastructure (transfer routes, auth) | No streaming/typed contract out of the box (mitigated by a JSON schema) |
| **gRPC** | Typed contracts, streaming, codegen | Heavier tooling; poor fit for CLI/web; the MCP layer is JSON-native, so it buys little |
| **MCP-as-service** | Reuses MCP tooling; the vault service's tools *are* the API | CLI/web clients need an MCP client (heavy); couples the vault to the MCP protocol — the exact coupling being removed; the "thin wrapper" degenerates to a proxy |

**Recommendation: HTTP/JSON with a typed schema.** The consumers are
CLI/web/other-services, and the MCP layer is already JSON. MCP-as-service is
tempting but wrong: it re-couples the vault to MCP and makes non-MCP consumers
heavier. gRPC buys nothing the consumers need.

### 3.3 Write-side orchestration across the boundary

This is the hard part the read-write choice commits to. Everything that assumes
in-process ownership of files + index moves into the service:

- **Git sync** (pull loop, deferred push, conflict resolution, history/diff) —
  the service owns the working tree, so it owns git.
- **File watcher** and **webhook** — these are *triggers* on the files; they live
  in the service.
- **OKF write tools** and **transfer links** — write paths; they live in the
  service.
- **Summarize** — vault-adjacent (reads note content, references paths); it lives
  in the service.

The **single-writer model survives**, and in one sense improves: the service is
now the *only* owner of the index, so the "one writer" invariant is enforced by
the process boundary rather than by convention. But the boundary introduces new
concerns:

- **Idempotency and retries.** A write that succeeds server-side but whose
  response is lost must be retryable without double-applying. The existing
  `if_match`/etag mechanism is the concurrency guard (it prevents *conflicting*
  writes), but it does not dedupe *duplicate* writes; a client-generated request
  ID is needed on top for true idempotency.
- **Long-running operations.** `reindex`/`build_embeddings`/`summarize` are
  already dual-mode (inline or promoted to a pollable job). Across the boundary
  they become service-side jobs with handles; the MCP server's `get_job_result`
  proxies them.
- **Concurrent writers.** Multiple clients (MCP + CLI + web) now write through
  one service. The service's single writer serializes them; `if_match` gives
  optimistic concurrency at the client level.

### 3.4 Identity propagation

The two auth-context couplings (§1) are the subtlest part of the boundary. Today
the write path reads the acting identity *in-process*: git needs the MCP access
token to authenticate the remote, and OKF write needs the current subject to
stamp provenance. Across the boundary, the client must pass the acting identity,
and the service must map it to git credentials and provenance.

This is a real design decision, not a mechanical one:

- **Git credentials.** The vault service needs its own git credential (a token
  or deploy key), independent of any MCP client's token. The MCP server's access
  token is the wrong credential for the service to use — it is scoped to the MCP
  session, not to the vault's remote.
- **OKF provenance.** The "who wrote this" stamp must survive the boundary. The
  client passes a subject (or the service derives one from its own auth), and the
  service stamps it. This is a small but load-bearing semantic: provenance is
  currently tied to the MCP auth subject.

The clean resolution is to give the vault service its own identity model — a
service credential for git, and a caller-supplied subject for provenance — rather
than trying to forward the MCP auth context.

### 3.5 MCP-layer signals across the boundary

Three MCP-layer mechanisms today reach into the index in-process. Each becomes an
API concern:

| Today (in-process) | Across the boundary |
|---|---|
| `needs_queryable` decorator blocks on `IndexFacet.wait_until_queryable(timeout)` | The service exposes a readiness/status endpoint; the client polls it with the same timeout semantics |
| `_meta.index_stale` (OR of wait-timeout, write-generation advance, non-idle writer) | The service exposes a monotonic write-generation counter (it already has one internally); the client maps it to `_meta.index_stale` |
| Dual-mode jobs (`reindex`/`build_embeddings`/`summarize`) via pvl-core jobs | The service owns the job store; the MCP server's `get_job_result` proxies the service's job endpoint |

The semantics get *fuzzier* across a network boundary (eventual consistency; a
stale read is now "the service's last-known state" rather than "the writer's
in-flight state"), but the shape is the same.

### 3.6 Auth and exposure

The vault service needs its own access control. Two positions:

- **Public service** — the vault service is exposed and does its own auth
  (reusing pvl-core's bearer/OIDC builders, or a simpler token). This makes the
  vault service a first-class public API.
- **Private service** — the vault service is on a private network; the MCP server
  is the only public entry point and forwards a service credential (shared secret
  or mTLS). The vault service's auth is "network isolation + a shared secret."

**Recommendation: private service.** The MCP server already does the real auth
(OIDC/bearer) and is the public surface. Making the vault service public
duplicates that and expands the attack surface. A private service with a shared
secret keeps the vault service simple; the MCP server (and any other trusted
consumer) holds the credential. This also matches the current deployment (single
container, private network).

### 3.7 Coexistence — one codebase, two backends

The key design move that keeps the integrated mode alive: **define the facet
interface as the seam, and implement it twice.**

- **In-process `Vault`** — the current implementation. The MCP layer calls the
  facets directly via `asyncio.to_thread`.
- **Remote `VaultClient`** — implements the same facet interface but calls the
  vault service's HTTP API.

The MCP layer is written against the facet interface and selects the backend at
startup: `VAULT_SERVICE_URL` unset → in-process; set → remote. This is the
"separated lives next to integrated" requirement, satisfied by one codebase.

Two sub-decisions:

- **Sync vs async client.** The facet interface is sync. The simplest
  `VaultClient` is a sync HTTP client, so the MCP layer keeps its
  `asyncio.to_thread` shape unchanged. An async-native MCP layer (async HTTP
  client, no `to_thread`) is cleaner for a network backend but a larger change;
  it can come later.
- **Where the interface lives.** If the vault splits into its own package (§4),
  the facet interface and the dataclasses live there, and both `Vault` and
  `VaultClient` implement it. The MCP package depends on the vault package and is
  agnostic to which backend it got.

## 4. The template and pvl-core

### 4.1 pvl-core — mostly unaffected

pvl-core serves the MCP side: `ServerConfig`, auth builders, middleware, logging,
transfer routes, jobs, CLI helpers. That side is unchanged — the MCP server still
uses all of it. The vault service is *not* an MCP server, so it doesn't need the
MCP-specific parts (OIDCProxy, MCP Apps, prompts). It might reuse pvl-core's
HTTP/auth primitives if it is exposed, but under the private-service
recommendation it needs little more than a shared-secret check. **Conclusion:
pvl-core is not a constraint on this design.**

### 4.2 The template — four options

The template (`fastmcp-server-template`) is built for "one MCP server package." A
second deployable (the vault service) and a second package (the vault library)
are outside its model. The four options:

| Option | What it means | Cost | Benefit |
|---|---|---|---|
| **Change the template** | The template gains a generic "additional service" capability | Large, speculative template change; the template's consumers are all MCP servers, so a generic "vault service" doesn't generalize cleanly | All consumers get the capability |
| **Fork the template** | The project forks and diverges | Loses `copier update`; manual reconciliation of CI/release/Dockerfile/packaging forever | Full control |
| **Absorb** | Stop using copier; own everything | Loses all template updates (CI/release/packaging improvements) | Maximum freedom |
| **Separate package (subrepo direction)** | The vault library + service become their own package/repo; the MCP server stays template-managed and depends on it | A package split (breaking change to the import surface; a second release stream) | The template stays as-is; the vault is a normal dependency; cleanest long-term |

**Recommendation: separate package.** The disentanglement's natural end-state is
a package split: `markdown-vault` (the library + service, no MCP dependency) and
`markdown-vault-mcp` (the thin MCP server, template-managed, depending on
`markdown-vault`). This is the "subrepo" direction, framed as "the vault becomes
a standalone package the MCP server consumes" rather than "the template is
applied to a subdirectory."

The reasons:

- **The template stays as-is.** No speculative generic multi-service change; no
  fork's maintenance cost; no absorb's loss of updates. The template keeps
  managing exactly what it is good at — the MCP server.
- **The vault's consumers get a clean dependency.** A CLI or web interface
  depends on `markdown-vault` (or its service), not on an MCP server package.
- **The MCP dependency is removed from the vault package.** The core library is
  already clean (§1); the split makes the *package* clean too, so the vault no
  longer ships MCP-adjacent concerns (config sentinels, server wiring) to
  consumers who only want the vault.

The costs are real and should be named:

- **Breaking change.** The public import surface (`markdown_vault_mcp.vault`,
  `.types`, `.config`, …) moves to `markdown_vault.*`. This is a major-version
  event for library consumers.
- **Config splits.** `ProjectConfig` today mixes vault settings (`source_dir`,
  `index_path`, embeddings) with MCP settings (auth, `base_url`, apps). The split
  moves vault config into the vault package and leaves MCP config in the MCP
  package.
- **Two release streams.** Two packages to version, release, and publish. The
  template's release machinery (knope, `stamp_manifests`) manages the MCP
  package; the vault package needs its own (simpler) release flow.
- **The sentinel structure.** The template's sentinels (config fields, domain
  wiring, apps) currently hold vault-specific code. Splitting moves that code out
  of the sentinels into the vault package, shrinking the MCP package's domain
  surface to "the protocol adapter."

## 5. Recommendation, summarized

1. **Split the package** into `markdown-vault` (library + service) and
   `markdown-vault-mcp` (thin MCP server). This is the enabling move; everything
   else follows from it.
2. **HTTP/JSON API** with a typed schema, mirroring the facet surface. Not
   MCP-as-service, not gRPC.
3. **Backend abstraction at the facet seam** — in-process `Vault` and remote
   `VaultClient` implement the same interface; the MCP layer selects at startup.
   This is what keeps the integrated mode alive.
4. **The vault service owns the write-side orchestration** (git, watcher,
   webhook, OKF, summarize, transfer); the single-writer model survives as a
   service-internal invariant.
5. **The vault service gets its own identity model** — a service credential for
   git and a caller-supplied subject for OKF provenance — rather than forwarding
   the MCP auth context.
6. **The MCP-layer signals become API concerns** (readiness endpoint, generation
   counter, job handles), mapped back to `needs_queryable` / `_meta.index_stale`
   / `get_job_result`.
7. **Private service** with a shared secret; the MCP server remains the public,
   authenticated entry point.
8. **pvl-core is unaffected**; the template stays as-is and manages only the MCP
   package.

## 6. Risks and open questions

**Risks**

- **The wire format becomes a public API** with a stability burden. The
  dataclasses and their JSON shapes must be versioned and evolved deliberately.
- **Two backends to maintain** (in-process + remote) — drift risk between `Vault`
  and `VaultClient`. Mitigated by a shared interface and a conformance test that
  runs the same suite against both.
- **The single-writer model under network concurrency** — idempotency, retries,
  and `if_match` across the wire need care; the existing etag mechanism is the
  foundation but was designed for in-process use.
- **Readiness/staleness semantics get fuzzier** across a network boundary
  (eventual consistency).
- **The package split is a breaking change** to the public import surface and the
  release model.

**Open questions**

- **One vault or many per service?** Recommend one vault per service instance
  (matches today); multi-vault is a separate concern.
- **Async-native MCP layer?** Recommend sync-client + `to_thread` first;
  async-native later.
- **Where does summarize's LLM call live?** In the service (it owns the content);
  the MCP server just proxies.
- **Does the vault service reuse pvl-core's HTTP/auth primitives, or stay
  dependency-free?** Under the private-service recommendation it needs little;
  decide when the service is actually built.
