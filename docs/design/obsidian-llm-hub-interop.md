# Interop with Obsidian LLM Hub — Investigation and Plan

Status: investigation / proposal. No code changes yet.

Subject: [`takeshy/obsidian-llm-hub`](https://github.com/takeshy/obsidian-llm-hub)
(MIT, desktop-only Obsidian plugin, Obsidian ≥ 0.15). Findings below are from
reading the plugin source at `main` and from driving this server with the exact
request shape the plugin's MCP client emits.

Goal: **several independent interfaces over one body of knowledge**, each with
its own index and its own runtime, kept coherent by git — not one shared
process, one shared index, or one shared embedding store.

---

## 1. What LLM Hub actually is

An in-Obsidian LLM workbench. The parts that matter to us:

| Subsystem | Summary |
|---|---|
| Chat | Many providers (Gemini / OpenAI / Anthropic / OpenRouter / Grok; Ollama / LM Studio / vLLM; Claude, Codex, Antigravity CLIs). Streaming, history, web search. |
| Vault tools | Function-calling over the vault: `read_note`, `create_note`, `propose_edit`, `propose_delete`, `search_notes`, list folders/files, rename, bulk ops. Edits are confirmation-gated. |
| RAG | A **local** vector index over vault files. Chunk + embed + cosine similarity, injected into the system prompt before the LLM call. Works with every provider. |
| Workflows | Visual node editor; workflows are markdown files with embedded YAML; hotkeys and file-event triggers. |
| Agent Skills | `SKILL.md` bundles with `references/` and executable scripts. |
| **MCP client** | Streamable HTTP **and** stdio. Per-tool enable/disable. Supports **MCP Apps** (sandboxed iframe UI). |
| **OKF** | Reads Open Knowledge Format bundles as curated chat context, with progressive disclosure. |
| Discord | Bridges the vault LLM to a Discord bot. |

Two of these — the MCP client and OKF — mean the plugin is not only a *peer*
writing into the same vault. It is also a *consumer* of everything this server
already exposes.

### The plugin's own search is weak

`src/vault/search.ts` is substring matching over names and file content
(`searchByName` / `searchByContent`), plus the local RAG index. There is no
FTS, no BM25, no link graph, no frontmatter filtering. Anything this server's
`search` / `get_similar` / graph tools do is strictly better — which is the
core of the integration argument in §3.2.

---

## 2. The plugin's vault footprint

Everything below lands **inside the vault**, i.e. inside the git-synced tree.

| Path | Contents | Source |
|---|---|---|
| `LLMHub/` | Workspace folder (default). Only *CSS-hidden* in Obsidian's file explorer — it is an ordinary directory on disk. | `src/types/index.ts:925`, `src/plugin.ts:907` |
| `LLMHub/gemini-workspace.json` | Workspace state (RAG settings, selections). | `src/core/workspaceStateManager.ts:32` |
| `LLMHub/rag/<setting>/index.json` | Chunk metadata + per-file checksums. | `src/core/localRagStorage.ts:45` |
| `LLMHub/rag/<setting>/vectors.bin` | Raw `Float32` embedding matrix. **Binary, rewritten on every sync.** | `src/core/localRagStorage.ts:49` |
| `LLMHub/workflow-history/` | Workflow execution logs (optionally encrypted). | `src/workflow/history.ts:33` |
| chat history `.md` | Timestamped markdown, optionally AES-encrypted with an RSA-wrapped key in frontmatter. | `saveChatHistory` setting |
| `workflows/` | Workflow definitions (markdown + YAML). | `src/types/index.ts:929` |
| `skills/` | Agent skills (`SKILL.md` + `references/` + scripts). | `src/types/index.ts:927` |

Credentials: `credentialStorage` defaults to `"plaintext"`, i.e. API keys in
`.obsidian/plugins/llm-hub/data.json`. Setting it to `"secret-storage"` moves
them into Obsidian's per-device secret store, out of the vault entirely
(`src/core/secretStorage.ts:1-9`). **On a git-synced vault this is not
optional.**

### Consequence for us, today

`MARKDOWN_VAULT_MCP_EXCLUDE` defaults to *nothing* (README:252). Point this
server at a vault that LLM Hub also uses and it will index chat transcripts,
workflow definitions, workflow logs, encrypted blobs and skill files as if they
were notes. That is finding **W1** below and it is the single cheapest fix.

What does *not* break: our auto-commit stages explicit pathspecs per write
(`git/strategy.py::_stage_and_commit`), so plugin churn is never swept into our
commits. And `vectors.bin` / `index.json` are not in
`DEFAULT_ATTACHMENT_EXTENSIONS`, so they are not served as attachments.

---

## 3. The four interop planes

These are independent. Adopt any subset.

### 3.1 Plane A — files (git-synced vault)

Both sides read and write plain markdown in one repo. This is the baseline the
`obsidian-everywhere` guide already describes; LLM Hub is simply another writer
on the Obsidian side. Requirements are hygiene, not features — see W1, W9, W10.

### 3.2 Plane B — protocol (LLM Hub connects to this server over MCP)

**This already works.** Verified end-to-end against a live in-process server
using the plugin's exact request shape:

| Step | Result |
|---|---|
| `server/discover` (plugin's "modern era" probe, protocol `2026-07-28`) | HTTP **400** + JSON-RPC `-32600`. The plugin's `shouldFallbackToLegacy` treats 400 as "fall back", so it silently drops to the legacy era. ✅ |
| `initialize` (protocol `2025-11-25`) | 200, `text/event-stream` body, `Mcp-Session-Id` header. The plugin parses the SSE body directly. ✅ |
| `tools/list` | 200, 39 tools. ✅ |
| `_meta.ui` on tools | `browse_vault` and `show_context` carry `{"resourceUri": "ui://markdown_vault_mcp/app.html"}` — exactly what `getMcpAppResourceUri` looks for. ✅ |
| `resources/read` of that URI | 200, `text/html;profile=mcp-app`, 1.2 MB self-contained SPA, `_meta.ui.csp.resourceDomains: []`. ✅ |
| `tools/call search` | 200, not an error. ✅ |

So **our MCP Apps vault browser renders inside Obsidian** with no work on our
side. That is the most valuable single fact in this document.

Constraints the plugin imposes, and what they mean for us:

- **Static headers only.** `McpServerConfig.headers` is the entire auth story —
  no OAuth, no device flow. Our bearer-token mode is the supported path;
  OIDC-only deployments cannot be used from LLM Hub. Document accordingly.
- **POST-only.** No GET SSE stream, so no server-initiated notifications. We do
  not rely on any.
- **Fallback is status-sensitive.** `shouldFallbackToLegacy` returns `false` for
  401, 403 and **any 5xx**. If a reverse proxy or middleware ever turns the
  unknown `server/discover` method into a 500, the plugin fails outright instead
  of falling back. Today we answer 400 — that must not regress (W2).
- **URL must be exactly `/mcp`.** `/mcp/` answers 307.
- **Tools are renamed** to `mcp_<server>_<tool>`, lowercased, non-alphanumerics
  collapsed to `_`. Any prose that tells a model to "call `search`" is wrong on
  this client.
- **Only `tools/list`, `tools/call`, `resources/read` are used.** Our MCP
  *prompts* are invisible to LLM Hub.
- **Schema conversion is lossy** — see W3.

### 3.3 Plane C — index (publish an LLM-Hub-readable RAG index)

The plugin's RAG has an **external index mode**: point a setting at one or more
absolute directories containing `index.json` + `vectors.bin` and it searches
them read-only, never syncing vault files into them, merging multiple indexes
that share a dimension (`localRagStore.ts:785-792`).

The format is trivially producible from our vector index:

```jsonc
// index.json
{
  "meta": [{ "filePath": "Journal/note.md", "chunkIndex": 0, "text": "…" }],
  "dimension": 768,
  "fileChecksums": { "Journal/note.md": "…" },
  "embeddingModel": "nomic-embed-text",
  "chunkSize": 500,
  "chunkOverlap": 100
}
// vectors.bin: raw little-endian Float32, row-major, in meta order
```

Two details make this unusually easy:

1. `normalizeExternalRagIndex` accepts **snake_case aliases** for every key
   (`file_path`, `embedding_model`, `chunk_size`, `chunk_overlap`,
   `file_checksums`, `content_type`, `page_label`) — the shape a Python producer
   writes naturally.
2. `cosineSimilarity` computes both norms explicitly, so vectors need not be
   pre-normalised.

One hard constraint: **the query is embedded by the plugin**, using
`index.embeddingModel` against the setting's own embedding endpoint. Scores are
only meaningful when both sides embed with the same model — practically, when
both point at the same OpenAI-compatible endpoint (Ollama / LM Studio / vLLM).
This is the "share the expensive work, keep the interfaces independent" move:
the server embeds once; Obsidian reads the result and never re-embeds.

### 3.4 Plane D — knowledge (OKF bundles)

Both sides implement OKF. The plugin is a **v0.1** consumer; we target **v0.2**.

How the plugin consumes a bundle (`src/core/okfLoader.ts`):

- A bundle is any directory that directly contains `index.md`; the bundle id is
  its path relative to the configured root; nested `index.md` directories are
  folded into the topmost bundle.
- The root may be a vault-relative folder **or an absolute desktop path outside
  the vault** — so a separate synced "knowledge" repo works.
- Only `index.md` is injected into the system prompt. Everything else is fetched
  on demand through the `read_okf_document` tool. `log.md` is always skipped.
- Frontmatter read: `title`, `type`, `description`, `tags`. Bodies capped at
  20 000 chars.
- Links must be ordinary markdown — bundle-root-absolute (`/features/chat.md`)
  or relative (`./chat.md`). **Wikilinks do not resolve.**

Compatibility verdict: **good, by construction.** OKF requires consumers to
tolerate unknown keys, so our v0.2 additions (`status`, `stale_after`,
`generated`, `verified`, `sources`) pass through harmlessly. Our
`okf-bundle` export already rewrites wikilinks to root-absolute markdown links
and keeps `index.md` / `log.md`. Two gaps remain, both small: the export is a
**zip**, and the plugin wants a **directory** (W6); and since only `index.md`
reaches the prompt, the quality of `okf_generate_index` output directly
determines how well the plugin's chat can navigate the bundle.

---

## 4. Work items

Ordered by value ÷ cost. Each is a candidate issue.

### W1 — Coexistence exclusion preset *(docs + config; cheap, highest value)*

Document, and surface in the config wizard, a recommended exclusion set for a
vault shared with LLM Hub:

```
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**,LLMHub/**,workflows/**,skills/**
```

Do **not** change the default silently — that is an operator-surface change for
existing deployments. Ship it as a documented preset plus a wizard group.

Pair it with the mirror-image advice for the plugin side: its RAG
`excludeRegex` should skip `^LLMHub/` and `\.conflict-mcp-`.

### W2 — Regression test for the LLM Hub client shape *(test; cheap, protects §3.2)*

A test that drives the HTTP app the way the plugin does and asserts:

- `server/discover` → 4xx or JSON-RPC `-32601`/`-32600`, **never** 5xx
  (a 5xx kills the plugin's legacy fallback);
- legacy `initialize` returns a session id and an SSE-framed body;
- `tools/list` surfaces `_meta.ui.resourceUri` for the app tools;
- `resources/read` returns `text/html;profile=mcp-app` in `contents[0]`.

This freezes today's verified-working behaviour against middleware drift.

### W3 — Schema shape for lossy converters *(code; medium)*

`convertPropertySchema` (`src/core/mcpTools.ts:51-103`) understands only `type`,
`description`, `enum`, `array.items`, `object.properties/required`. `anyOf`,
`oneOf`, `$ref`, `$defs` and `default` are **dropped**, and a property with no
top-level `type` silently becomes `type: "string"`.

Measured on our surface: **20 of 39 tools** carry `anyOf` optionals.

| Lost shape | Params | Risk |
|---|---|---|
| `str \| None` | `folder`, `section`, `pattern`, `old_text`, `if_match`, `focus`, `path`, `since`, `until`, `since_sha`, `view` | None — string is the right guess. |
| `int \| None` | `limit`, `chunks_per_file`, `snippet_words`, `max_level`, `line_start`, `line_end`, `max_notes` | Model emits a string; pydantic lax mode usually coerces. Verify. |
| `dict[str,str] \| None` | `filters`, `frontmatter` | Model emits a JSON string against a `type: "string"` schema with no structure. Depends on FastMCP's stringified-argument pre-parsing. Verify. |

Action: first *measure* (a matrix test calling each tool with the flattened
schema's most likely argument spelling), then flatten only what actually breaks.
Per the versioning policy this is an MCP-surface change, not a breaking one.

### W4 — Tool-surface profiles *(code; medium)*

39 tools, plus the plugin's own ~12 vault tools, is a large function-calling
payload for Gemini Flash or a local model. FastMCP supports `include_tags` /
`exclude_tags`; we expose neither. Add an env-driven profile so an operator can
serve a compact set to weak clients. Useful well beyond LLM Hub.

### W5 — Publish an LLM-Hub-compatible external RAG index *(code; large, high value)*

Emit `index.json` + `vectors.bin` from our vector index, per §3.3. Delivery
options, in preference order:

1. An `llm-hub-index` download ref on `create_download_link`, mirroring how
   `okf-bundle` is overloaded onto the same transfer primitive (`docs/design/okf.md` §7).
2. An optional on-disk output directory refreshed after each embeddings build,
   for operators who sync a directory rather than fetch a link.

Must document the shared-embedding-model constraint prominently — a mismatched
model yields silently meaningless scores, the worst failure mode available.

### W6 — OKF directory export *(code; small)*

Add a directory-tree form of the `okf-bundle` export (or document
"unzip into the folder LLM Hub's OKF root points at"). The plugin cannot read a
zip. A separate synced knowledge repo, exported by us and consumed read-only by
the plugin, is the cleanest topology.

### W7 — Inline `#tag` indexing *(code; medium; genuine Obsidian compat)*

Tags come only from frontmatter today (`SearchManager.list_tags(field="tags")`);
Obsidian's inline `#tag` in note bodies is not indexed. Two interfaces over one
vault should agree on what a tag is. Requires an `INDEX_SEMANTICS_VERSION` bump
in `fts_index.py` per the hash-based-indexing rule in CLAUDE.md.

Already correct and worth keeping: `[[wikilink]]` / `[[path|alias]]` parsing
(`scanner.py:793`) and `aliases:` frontmatter (`fts_index.py:133`).

### W8 — A `SKILL.md` that teaches LLM Hub when to use us *(docs/example; cheap)*

Ship an example agent skill for the plugin's `skills/` folder that tells its
chat to prefer `mcp_<server>_search` over the built-in substring `search_notes`,
to route graph questions to `get_backlinks` / `get_connection_path`, and to pick
one interface for writes. Without it the two tool families compete and the model
picks the weaker one roughly half the time.

### W9 — Guide: two independent interfaces on one vault *(docs)*

Extend `docs/guides/obsidian-everywhere.md` (or add `docs/guides/obsidian-llm-hub.md`)
with the topology, the four planes, the exclusion preset, the `.gitignore`
additions, the `credentialStorage: secret-storage` requirement, and conflict
handling. Suggested `.gitignore` additions on top of the existing guide:

```gitignore
LLMHub/rag/            # binary vectors.bin, rewritten on every sync
LLMHub/workflow-history/
.obsidian/plugins/*/data.json   # plugin credentials
```

### W10 — Conflict-sibling ergonomics *(docs, maybe config)*

On divergent history we rebase; on real conflicts upstream wins and the local
version is preserved as `<stem>.conflict-mcp-<timestamp>.md` with
`conflict_with` / `conflict_date` frontmatter (`git/conflict.py:336-411`). Those
siblings appear in Obsidian as ordinary notes and get pulled into LLM Hub's RAG.
Document the pattern; consider a configurable location or suffix.

### W11 — Declare the fonts our MCP App actually loads *(bug, found in passing)*

`static/app.html` loads Google Fonts via `<link rel="stylesheet">`, but
`_CDN_RESOURCE_DOMAINS` is `[]`, so the app resource declares
`_meta.ui.csp.resourceDomains: []`. A host that enforces the declared policy
strictly blocks the font CSS. LLM Hub happens to recover — it inlines the
stylesheet and infers the origins from the leftover `preconnect` links — but
that is luck, not contract. Either vendor the fonts into the SPA or declare
`fonts.googleapis.com` / `fonts.gstatic.com`. Independent of this integration.

---

## 5. Recommended reference topology

```text
        ┌──────────────────────────────┐
        │ Obsidian desktop             │
        │  ├ LLM Hub                   │
        │  │   ├ vault tools (local)   │
        │  │   ├ RAG: external index ──┼──┐  (Plane C, read-only)
        │  │   ├ MCP client ───────────┼──┼──┐  (Plane B, over HTTPS + bearer)
        │  │   └ OKF root ─────────────┼──┼──┼──┐  (Plane D)
        │  └ obsidian-git              │  │  │  │
        └──────────┬───────────────────┘  │  │  │
                   │ git push/pull        │  │  │
                   ▼                      │  │  │
            ┌─────────────┐               │  │  │
            │ vault repo  │               │  │  │
            └──────┬──────┘               │  │  │
                   │ git pull (600 s) or webhook
                   ▼                      │  │  │
        ┌──────────────────────────────┐  │  │  │
        │ markdown-vault-mcp           │◄─┘◄─┘◄─┘
        │  FTS5 + embeddings + graph   │
        │  own index, own embeddings   │
        └──────────────────────────────┘
```

Each interface keeps its own index. Git carries the *notes*; planes B–D carry
*derived artifacts* one way only (server → plugin), so there is exactly one
producer for each artifact and nothing to merge.

Settings that make it work:

| Side | Setting | Value |
|---|---|---|
| server | `MARKDOWN_VAULT_MCP_EXCLUDE` | the W1 preset |
| server | `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | lower than 600, or use the GitHub webhook |
| server | auth | bearer token (OIDC is unreachable from the plugin) |
| plugin | MCP server URL | `https://…/mcp` (no trailing slash) |
| plugin | MCP headers | `{"Authorization": "Bearer …"}` |
| plugin | `credentialStorage` | `secret-storage` |
| plugin | RAG exclude regex | `^LLMHub/`, `\.conflict-mcp-` |
| plugin | embedding model | identical to the server's, if Plane C is used |

---

## 6. Open questions

1. **Is Plane C worth the maintenance?** It couples us to an undocumented,
   single-consumer on-disk format. The counter-argument is that the format is
   three JSON keys and a `Float32` blob, the plugin already normalises
   snake_case for foreign producers, and it removes a whole redundant embedding
   pass from the user's laptop.
2. **Which side owns writes?** Both can write. The plugin gates edits behind
   user confirmation; we auto-commit. A "one writer per session" convention is
   probably better guidance than any mechanism.
3. **Should the exclusion preset ever become the default?** It would be an
   operator-surface break for existing deployments. Probably never — but it
   should be a wizard preset, not prose.
4. **Do we want to consume the plugin's artifacts too?** Its RAG index is a
   plausible read-side source for `get_similar` when our own embeddings are
   absent. Likely not worth it; noted for completeness.
