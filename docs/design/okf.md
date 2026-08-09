# OKF (Open Knowledge Format) Support — Design

**Status:** phases 1 (#960 — detection, config surface, read annotations,
stats/config reporting, indexed-field extension), 2 (#961 — filter
dimensions, graph typing), 3 (#962 — `okf_validate` conformance audit), and all
of 4 (#963 — `okf_convert_links` / `okf_generate_index` /
`okf_seed_log`, plus bundle export via an `okf-bundle` `create_download_link`
ref) implemented; their design has
graduated into `design.md` ("OKF Read Semantics"). Later phases remain
proposals — tracking issue
[#959](https://github.com/pvliesdonk/markdown-vault-mcp/issues/959).
**Spec targeted:** OKF v0.2
([`GoogleCloudPlatform/knowledge-catalog` → `okf/SPEC.md`](https://github.com/GoogleCloudPlatform/knowledge-catalog),
Apache 2.0; announced 2026-06-12 on the
[Google Cloud blog](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)).

This document is the authoritative design for OKF support. As phases land,
the relevant subsections graduate into `design.md` alongside the features
they describe (phases 1-3 and the migration transforms of phase 4 have);
sections below covering unimplemented phases remain proposals.

---

## 1. Why OKF, and why this server

OKF formalizes the "LLM-wiki" pattern into a vendor-neutral interchange
format: a **bundle** is a directory of markdown files with YAML frontmatter
serving as curated context for AI agents. One concept per file; file path is
concept identity; conformance is deliberately minimal (parseable frontmatter
with a non-empty `type`); consumers are required to be permissive.

The overlap with this server is structural, not coincidental:

| OKF requirement | markdown-vault-mcp today |
|---|---|
| Markdown + YAML frontmatter, frontmatter-aware indexing | core feature |
| Permissive consumer (tolerate missing fields, unknown keys/types) | frontmatter optional by default |
| Broken links tolerated, surfaceable | `get_broken_links` |
| Standard markdown links incl. vault-root-relative | parsed alongside wikilinks (`scanner.py`) |
| Git-based content versioning | `GitQueryManager`, git strategies |
| Directory listing / progressive disclosure | `get_toc`, `list_folders` |

What is missing is *semantics*: the server indexes OKF frontmatter as opaque
metadata but attaches no meaning to `type`, `status`, `stale_after`, `sources`,
`generated`/`verified`, or the reserved files `index.md`/`log.md`.

### OKF v0.2 in brief (normative source: SPEC.md)

- **Conformance:** every non-reserved `.md` has parseable YAML frontmatter
  with a non-empty `type` (free vocabulary). Nothing else is required.
  Consumers MUST tolerate missing optional fields, unknown `type` values,
  unknown keys, broken links, and missing `index.md`.
- **Recommended fields:** `title`, `description`, `resource` (URI),
  `tags` (list).
- **Provenance:** `sources` list (`resource` required per entry; optional
  `id`/`title`/`author`/`usage_count`/`last_modified`/`usage_window`);
  per-claim attribution via markdown footnotes `[^source-id]`.
- **Trust:** `generated: {by, at}` and `verified: [{by, at}, ...]`.
  Actor convention `human:<id>` / `process:<id>` / `<tool>/<version>`.
  Derived tiers: **unverified → machine-confirmed → human-reviewed**.
- **Lifecycle:** `status: draft|stable|deprecated` (default `stable`),
  `stale_after: YYYY-MM-DD`.
- **Bundle marker:** `okf_version: "0.2"` allowed only in the bundle-root
  `index.md` frontmatter.
- **Reserved files:** `index.md` (progressive-disclosure directory listing)
  and `log.md` (change history, newest-first `## YYYY-MM-DD` headings).
- **Links:** standard markdown links, untyped; bundle-root-absolute
  (`/path/x.md`) recommended, relative allowed. Wikilinks are not part of
  the spec — but link style is *convention*, not conformance.
- **Extension keys:** arbitrary extra frontmatter keys are allowed and MUST
  be preserved on round-trip.
- **Out of scope for this server:** Attested Computations (sanctioned
  SQL/Python with executor/attester machinery) — data-warehouse territory.

### Spec-stability caveat

OKF is pre-1.0 and has one breaking change behind it already
(v0.1→v0.2: `timestamp` → `generated.at`, body `# Citations` → `sources`).
Consequence for this design: **the OKF field mapping is data, not code** — a
single module-level table (field name → indexing/annotation behavior) so a
v0.3 rename is a table edit plus tests, not a refactor.

---

## 2. Trust and activation model

The central design decision. Two configuration channels exist and they answer
different questions:

- **Vault-side declaration** (`okf_version` in root `index.md`) answers
  *"what is this data?"* It travels with the bundle, so the same vault behaves
  identically here, in `kcmd`, and in other OKF tooling. It is the sole key
  for **read semantics and advisory conventions**.
- **Operator env config** answers *"what may this server do to it?"* It is
  the sole key for **enforced/automated write behavior** and for vetoing
  detection entirely.

Rationale (trust boundary): the vault is writable by parties that are not the
operator — the agent itself via the `write` tool, and remote contributors in
git-synced vaults. Data that could enable byte-changing or write-refusing
server behavior would be a self-modifying-config loop (the agent could enable
its own write conventions by writing the marker). Therefore vault content may
only ever buy *advice and annotations*, never *authority*. This mirrors the
existing Folder Conventions design (`_conventions.md`): a vault-side channel
that shapes behavior advisorily while the server enforces nothing.

Explicitly rejected alternatives:

- **Always-on (or opt-out) semantics keyed on frontmatter shape** — `type`,
  `status`, `tags` are ubiquitous folk-vocabulary keys; inferring OKF from
  their presence reinterprets user metadata (e.g. `status: draft` meaning
  "unfinished blog post" acquiring lifecycle semantics), taxes every
  non-OKF vault with instruction/annotation noise, and couples default
  behavior to a pre-1.0 spec. Detection keys on the explicit `okf_version`
  marker only — a deliberate declaration with ~zero false positives.
- **Guide-only (no server support)** — unlike PARA/Zettelkasten
  (methodologies; nothing machine-checkable), OKF is a format with a
  conformance contract. Guides cannot validate, annotate trust tiers, or
  export a bundle.

### Config surface

Two settings, standard `CONFIG-*` sentinel placement in `config.py`, wizard
metadata via field `metadata={"help": ..., "tags": (...)}`:

| Env var | Values | Default | Meaning |
|---|---|---|---|
| `MARKDOWN_VAULT_MCP_OKF_MODE` | `auto` / `off` / `on` | `auto` | `auto`: read semantics + advisory conventions when the vault declares `okf_version` in root `index.md`. `off`: never apply OKF semantics (collision escape hatch). `on`: force read semantics even without the marker (bundle the operator cannot edit). |
| `MARKDOWN_VAULT_MCP_OKF_WRITE` | bool | `false` | Enforced write layer (§6). Requires effective read mode on (declared under `auto`, or mode `on`); `true` with mode `off` is a config validation error. |

### Detection

`okf_version` is read from the root `index.md` frontmatter by a small pure
disk-I/O probe (same pattern as `ConventionsResolver`: no index coupling, so
detection works before the index is built — relevant in managed-git mode where
the clone happens inside the server lifespan). The detection result (mode,
declared version, effective read/write state) is exposed via `stats` and
`config://vault`. `get_server_info` cannot carry it because that tool is
template-owned; `stats` is the authoritative reporting surface.

Unknown `okf_version` values (e.g. a future `"0.3"`) log a `WARNING` and are
treated as detected — permissive-consumer behavior extends to the marker
itself.

---

## 3. Layer 1 — read semantics (annotate)

*Active when detection is on. Additive only; no behavior is removed or
reordered.* The primary consumer of OKF metadata is the **agent**, not the
server's ranking math — layer 1 surfaces signal and lets the agent judge.

**Computed per-note properties** (derived at annotation time from the stored
frontmatter blob; not persisted):

- `staleness`: `stale` iff `stale_after` < today (date-only comparison,
  server-local date); absent field → not stale.
- `trust_tier`: `human-reviewed` if any `verified[].by` has the `human:`
  prefix; else `machine-confirmed` if `verified` is non-empty (all
  verifiers non-human); else `unverified`. Exact tier algorithm lives in
  the field-mapping table (§1 spec-stability caveat) with table-driven
  tests.
- `okf.status`: the raw `status` value, defaulted to `stable` when absent
  (per spec); unknown values pass through unmapped (no semantics attached).

**Delivery channels** (mirroring the Folder Conventions channel list):

1. `search` results, `read`, and `get_context` payloads gain an `okf` key
   carrying `{type, status, stale, trust_tier}` — omitted entirely when
   detection is off, and individual members omitted when underivable.
   `sources` are surfaced on `read` (full) and counted on search hits
   (`sources_count`), not inlined into result lists.
2. `stats` gains an `okf` section: declared version, effective mode,
   `type` histogram, status/trust-tier breakdowns, stale count.
3. Default server instructions gain an OKF paragraph (composed in
   `_instructions.py`, wired through the existing DOMAIN-WIRING seam):
   what the tiers mean, that `log.md`/`index.md` conventions apply, and —
   advisory write guidance — "maintain `log.md`, keep `index.md` current,
   prefer bundle-root-absolute links." Same caveat as conventions: an
   operator-set `MARKDOWN_VAULT_MCP_INSTRUCTIONS` replaces this entirely.
   Because instructions are composed before a managed-git clone runs,
   the paragraph is emitted when *mode permits* detection, phrased
   conditionally, rather than gated on the marker file existing.
4. Reserved files `index.md`/`log.md` remain indexed (they are searchable
   navigation) but are tagged for the ranking layer (§5) and excluded from
   conformance checks (§4) per spec.

**Indexing integration:** OKF scalar fields ride the existing
`document_tags` machinery — when detection is on, the effective
`indexed_frontmatter_fields` set is extended with the OKF keys
(`type`, `status`, `stale_after`). Per the Frontmatter Filtering design,
changing the effective indexed-field set alters chunking provenance and
triggers a one-time cold rebuild of `document_tags` on the startup where
detection first flips — an accepted, logged cost. Complex fields
(`sources`, `verified`, `generated`) stay in the raw JSON blob and are
consumed at annotation time only.

The advisory write layer needs **zero new server machinery** beyond the
instructions paragraph: a bundle can already ship a `_conventions.md` saying
"maintain log.md" today. Layer 1 makes that the documented default rather
than a per-vault authoring task.

---

## 4. Layer 2 — query dimensions, and `okf_validate`

*Status:* both halves implemented (#961, #962); `design.md`'s "OKF Read
Semantics" section carries the as-built details, which supersede the
sketches below (notably: the OKF dimensions are a uniform manager-level
post-filter with pool widening, not a SQL `NOT EXISTS` branch; the audit
is disk-based rather than index-based, and the tool is hidden under
`OKF_MODE=off` rather than gated on detection).

### Filters

`search` and `list_documents` accept OKF filter dimensions, implemented on the
existing `filters` → `document_tags` subquery path (AND semantics unchanged):

- `type=<value>` — direct tag lookup.
- `status=<value>` — tag lookup with the documented caveat that absent
  `status` means `stable` (the filter for `stable` must match
  absent-or-stable; this is the one filter that cannot be a pure tag lookup
  and needs a NOT-EXISTS branch).
- `stale=true|false` — `stale_after` is indexed as a tag value;
  staleness is a date comparison against it at query time.
- `trust_tier=<tier>` — derived from blob fields, so **post-filter** applied
  after candidate retrieval (documented as such; acceptable because trust
  filtering is a triage operation, not a hot search path).

A stale/deprecated listing (filterable `list_documents`) is the data source
for a future `triage-stale` prompt (docs phase).

Graph surfaces (`GraphFacet`, SPA GraphView) may color/segment nodes by
`type` — additive payload field on the existing wire serializer.

### `okf_validate` (audit tool)

Read-only tool reporting bundle conformance **as a degree, not a verdict** —
during migration it is a progress meter:

- Per-rule counts + capped example lists: notes missing parseable
  frontmatter; notes missing non-empty `type`; unknown `status` values;
  `okf_version` outside bundle root; reserved-file convention violations
  (structural only — `log.md` heading shape, root `index.md` presence:
  advisory findings, since the spec tolerates their absence).
- Informational (non-conformance) counts: wikilink usage (matters at export
  only), notes lacking recommended fields.
- Summary ratio ("N of M notes conformant") + the same exclude patterns as
  the index (convention files, configured excludes) so known-nonconforming
  zones can be whitelisted via existing config.
- Registered per the Tool Registration Checklist (title, `readOnlyHint`,
  icon, docstring, docs row, enforcement-test coverage).

Validation is **never** wired into the write path in this layer; a
write-time conformance gate is an explicit §6 opt-in.

---

## 5. Layer 3 — ranking (conservative, last)

Mild, curated-pipeline (`managers/_ranking.py`) adjustments, active only under
detection:

- `status: deprecated` → downweight (strength: same order as existing
  curated downweights; exact factor decided against the ranking test corpus).
- `stale` → milder downweight.
- Reserved files `index.md`/`log.md` → downweight (navigation, not content).

Explicitly rejected: boosting `human-reviewed` notes. Trust-based reshuffling
silently changes result composition; the tier is surfaced in annotations and
the agent applies that judgment visibly. Ranking changes ship in their own
phase because their risk profile (silent behavior change on existing vaults
that declare OKF) differs from the additive layers.

**Status:** shipped in phase 6 (#965). The factors (`okf.py`:
`OKF_DEPRECATED_WEIGHT = 0.5`, `OKF_STALE_WEIGHT = 0.75`,
`OKF_RESERVED_WEIGHT = 0.5`) compose multiplicatively via
`okf_downweight_factor`, so `deprecated < stale < normal` holds and a note that
is both deprecated and stale sinks below either alone. The pure re-ranker
`apply_okf_downweight` (alongside `apply_folder_boost`) runs as the conservative
last score mutation — immediately after the folder boost, before grouping — in
all three channels (keyword, semantic, hybrid), gated on
`self._okf.state().active`; the per-hit OKF factor comes from the same
`get_note` → frontmatter → `derive_annotation` path the annotation and filter
layers use, memoised per path. It mirrors `apply_folder_boost` in scaling only
positive scores, so a negative cosine is never promoted. On any vault where
detection is off, `_rank_okf` returns its rows untouched, so ranking is
byte-identical.

---

## 6. Layer 4 — enforced write layer (`OKF_WRITE=true`)

Everything here changes bytes or write outcomes; all of it is operator-gated
and none of it is implied by vault declaration.

- **Provenance stamping:** writes through `write`/`edit` set/update
  `generated: {by, at}`. Actor string: authenticated identity when available
  (`human:<subject>` via the existing access-token dependency), else
  `markdown-vault-mcp/<version>` as a tool actor. Existing `generated`
  values are overwritten (it describes the current bytes); `sources` are
  never touched.
- **Verification invalidation:** a content-changing `write`/`edit` to a note
  carrying `verified` clears the `verified` list — verification attests to
  bytes that no longer exist. Frontmatter-only edits that do not touch the
  body also invalidate (the spec ties verification to the concept, not the
  body alone); rename does not. This is the highest-value enforcement:
  it is exactly the invariant an advisory-only setup eventually misses.
- **`okf_verify` tool:** appends `{by: human:<subject>, at}` to `verified`,
  promoting the note's tier. Requires an authenticated identity; refuses
  (ToolError) when auth mode is `none`, so verification remains an
  attributable act. `destructiveHint=False`, `idempotentHint=False`.
- **Convention maintenance:** on successful writes, append a `log.md` entry
  (newest-first, `## YYYY-MM-DD` section, `**Update**:`-style bullet) and
  refresh the affected folder's `index.md` listing. Both are guaranteed
  versions of what the advisory layer asks the agent to do. Generated
  `index.md` content derives from the same data as `get_toc`.
  **Status:** shipped in phase 5b (see §9). Maintenance runs only for
  `write` / `edit` on an OKF-active vault, skips a write whose target is
  itself a reserved file (`index.md` / `log.md`) so it never recurses, and is
  skipped for suppressed writes (`okf_verify`, the one-shot migrations) so an
  attestation or mechanical rewrite does not churn the reserved files. The
  affected folder is the one directly containing the written note; the
  `index.md` refresh reuses the migration `generate_index`, draining the
  single-writer index first so a just-created note is listed. A brand-new
  subfolder's pointer in its parent `index.md` lands on the next write into
  the parent (per-write scope, not a full-tree walk).
- **Optional conformance gate:** rejected for this design. `required_frontmatter=["type"]`
  already exists for operators who want hard exclusion; a softer write-time
  warning can ride the existing write-result `conventions`/advisory channel
  without a new mechanism.

Concurrency note: log-append and index-refresh are secondary writes riding an
existing primary write; they flow through the same single-writer index path
and git-commit callback as any other write, and failures degrade to a logged
`WARNING` (the primary write is never rolled back for a convention-file
failure). The `log.md` read-modify-write is held under the shared re-entrant
write lock so concurrent writes into one folder cannot lost-update the log;
the `index.md` refresh is a full idempotent regeneration, safe without extra
locking.

Cost note (accepted trade-offs, not defects): the index refresh drains the
single-writer (a *global* wait, embeddings included, bounded at 10s) before it
regenerates so a just-created note is listed — this is the price of reusing
the FTS-backed `generate_index` rather than a disk scan, and it adds latency to
every enforced write on a busy vault. And because the secondary writes are
ordinary `DocumentManager` writes, a git-backed vault commits each separately,
so one logical note write can produce up to three commits. Both are documented
in the guide; a scoped (FTS-only) drain and commit coalescing are possible
future refinements.

---

## 7. Migration and export

### Migration = ratchet, not flag day

Per-file conformance makes incremental adoption natural. Recommended order —
**declare early**, then converge:

1. **Audit** — `okf_validate` produces the gap worklist and the progress
   ratio.
2. **Declare** — write `okf_version` into root `index.md`; read semantics
   and advisory conventions switch on, so *new* notes are authored
   conformantly from this point.
3. **Enrich (agent-driven)** — a migration prompt (PARA-triage style) walks
   the worklist: LLM proposes `type` per note from content, backfills
   `title`/`description`, normalizes tags; human approves in batches.
4. **Mechanical transforms (server tools, explicit invocation)** — the parts
   an LLM should not do note-by-note, where the server already owns the
   machinery:
   - wikilink → bundle-root-absolute markdown-link conversion (the same
     link-rewriting engine behind `rename`/`move_folder`);
   - `index.md` generation from `get_toc` data;
   - `log.md` seeding from git history via `GitQueryManager`, so a
     git-backed vault starts with real change history.

The in-place migration transforms are tools the operator/agent invokes
deliberately; they are not gated on `OKF_WRITE` (they are one-shot migrations,
not ongoing enforcement) but are write tools and respect read-only mode. Each is
registered per the Tool Registration Checklist, with `destructiveHint` /
`idempotentHint` reflecting that they mutate the vault.

### Export

Export ships **not as a bespoke tool but as an overloaded download ref** on
pvl-core's `create_download_link` (the transfer subsystem adopted in #979). The
domain `VaultTransferSink` recognises a bundle ref — `okf-bundle` for the whole
vault, `okf-bundle:<folder>` for a subtree — validates the scope at link
creation (gated out when `OKF_MODE=off`), and generates the archive at fetch
time via `okf_bundle.build_okf_bundle`. This is path 2 of pvl-core's transfer
model (a domain use of the primitives) expressed without a new tool: a
generated-bytes download the generic `create_download_link` serves unchanged.

The bundle is a conformant *copy*: wikilinks rewritten to root-absolute links
(reusing `convert_wikilinks_to_markdown` and the resolved outlink graph),
convention files (`_conventions.md`) and the template folder excluded, the
reserved `index.md` / `log.md` kept, non-conformant notes included as-is
(permissive consumers tolerate them), and a fixed zip-entry timestamp so an
unchanged vault re-exports to identical bytes. Notes are read raw from disk
(uncapped), so a large note exports in full. Export never mutates the vault.
Residual conformance gaps are reported separately by `okf_validate`, not embedded
in the archive.

---

## 8. Methodology interop (PARA / Zettelkasten)

OKF composes with the methodology guides — it is a metadata/interchange layer;
they are organization methodologies. The PARA guide already prescribes
`type`/`status` frontmatter, which is OKF's shape; Zettelkasten structure
notes map onto `index.md` progressive disclosure and literature-note citations
onto `sources`. Three documented frictions, resolved at guide level:

| Friction | Resolution |
|---|---|
| `status` vocabulary: PARA `active/archived` (workflow) vs OKF `draft/stable/deprecated` (lifecycle) | Different meanings — do **not** map (archived ≠ deprecated). PARA workflow state moves to its own extension key (OKF preserves unknown keys); `status` is reserved for OKF lifecycle. |
| PARA `0-Inbox/` notes are deliberately untyped until triage | Captures get placeholder `type: Capture` (conformant immediately; triage overwrites). Fallback: audit-whitelist the inbox via exclude patterns. |
| Zettelkasten/Obsidian wikilink dialect vs OKF standard-link convention | Not a conformance issue (conformance is frontmatter-only). Internal graph parses both dialects; conversion happens at export. Guides state: write either style, export converts. |

Deliverables (docs phase): `docs/guides/okf.md`; "Using X with OKF" interop
sections in `para.md` and `zettelkasten.md`; `examples/okf/` (templates +
prompts: author-concept, verify-note, triage-stale, migrate-vault);
`examples/para/` and `examples/zettelkasten/` templates gain the OKF fields
so new vaults are conformant from note one.

---

## 9. Phasing

| Phase | Scope | Depends on |
|---|---|---|
| 1 | Config surface, detection, read annotations, stats section, instructions paragraph, indexed-field extension | — |
| 2 | Query filters (`type`/`status`/`stale`/`trust_tier`), graph `type` field | 1 |
| 3 | `okf_validate` audit tool | 1 (detection; usable pre-declaration via `OKF_MODE=on`) |
| 4 | Migration tools (link conversion, `index.md` generation, `log.md` seeding) + `okf_export` | 1, 3 |
| 5a | Enforced write layer (`OKF_WRITE`): stamping, verification invalidation, `okf_verify` | 1 |
| 5b | Enforced-write convention maintenance: `log.md` append + affected-folder `index.md` refresh on successful writes | 5a |
| 6 | Ranking downweights | 1 (own phase: different risk profile) |
| Docs | Guide, interop sections, examples/prompt packs | trails each phase; guide lands with 4 |

Every phase carries its own documentation impact (tools reference,
configuration page, README, wizard metadata via config-field tags) per the
Documentation Discipline; the Docs phase covers the guide-level work no code
phase owns.

## 10. Open questions

- Trust-tier edge cases: `verified` entries by `process:` actors only —
  v0.2 tier language suggests machine-confirmed; confirm against SPEC.md
  examples before freezing the table.
- `stale_after` timezone semantics (spec gives date only) — proposal:
  server-local date, documented.
- Whether `okf_export` should optionally include a generated bundle-root
  `index.md` when absent (spec allows synthesized indexes) — lean yes.
- Community governance ("W3C Holon CG" / "DataBook" profile) is
  single-sourced; ignore until corroborated.
