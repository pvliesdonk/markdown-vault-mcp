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
What the spec actually says, with sources, dates and the in-place amendment
of 2026-08-21 (every timestamp an ISO 8601 datetime with an offset;
`stale_after` an instant), is the reference page
[`reference/okf-v0.2.md`](reference/okf-v0.2.md); the departures this
document decides are listed there.

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
  `stale_after`: a `YYYY-MM-DD` date (the July 2026 text) or an ISO 8601
  instant with an offset (the text since 2026-08-21).
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

Three settings today and two proposed (§6.0), standard `CONFIG-*` sentinel
placement in `config.py`, wizard metadata via field
`metadata={"help": ..., "tags": (...)}`:

| Env var | Values | Default | Meaning |
|---|---|---|---|
| `MARKDOWN_VAULT_MCP_OKF_MODE` | `auto` / `off` / `on` | `auto` | `auto`: read semantics + advisory conventions when the vault declares `okf_version` in root `index.md`. `off`: never apply OKF semantics (collision escape hatch). `on`: force read semantics even without the marker (bundle the operator cannot edit). |
| `MARKDOWN_VAULT_MCP_OKF_WRITE` | bool | `false` | Stamp provenance on the server's own writes and expose `okf_verify` (§6). Requires effective read mode on (declared under `auto`, or mode `on`); `true` with mode `off` is a config validation error. In the shipped code it also switches on reserved-file maintenance, which §6.0 gives its own switch. |
| `MARKDOWN_VAULT_MCP_OKF_VERIFY` | `elicit` / `off` / `trust-auth` | `elicit` | How `okf_verify` attributes a human review (§6). Only meaningful with `OKF_WRITE` on; a non-default value with it off is a config validation error. |
| `MARKDOWN_VAULT_MCP_OKF_MAINTAIN` (proposed, #1412) | bool | `false` | The server is the maintainer of `index.md` / `log.md` (§6.0). Same read-mode requirement as `OKF_WRITE`. |
| `MARKDOWN_VAULT_MCP_OKF_RECONCILE` (proposed, #1412) | bool | `false` | The server may repair provenance on notes it did not write (§6.0). Same read-mode requirement. |

The names of the two proposed settings are candidates; the `config-contract`
skill fixes them at implementation.

### Detection

`okf_version` is read from the root `index.md` frontmatter by a small pure
disk-I/O probe (same pattern as `ConventionsResolver`: no index coupling, so
detection works before the index is built — relevant in managed-git mode where
the clone happens inside the server lifespan). The detection result (mode,
declared version, active state) is exposed via `stats` and
`config://vault`; the effective write-side state (`OKF_WRITE` and, once
they exist, the §6.0 switches) is not reported by either today, which the
`okf` section of both should fix (#1432). `get_server_info` cannot carry it because that tool is
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

- `staleness`: both texts of v0.2 honoured (#1357, #1373). A bare date is
  stale from that server-local day on (the named date is the first stale
  day); an instant with an offset is stale when `now >= stale_after`,
  compared as instants; a datetime *without* an offset is allowed by neither
  text and is ignored (treated as absent), as the 2026-08-21 amendment
  asks; absent field → not stale. Accepting the bare date is a deliberate
  lenience against the amended text's "ignore" — Obsidian authors write
  dates, and the upstream issue behind the amendment is still open.
- `trust_tier`: `human-reviewed` if any `verified[].by` has the `human:`
  prefix; else `machine-confirmed` if `verified` is non-empty (all
  verifiers non-human); else `unverified`. A bare `verified` mapping is
  read as a one-element list (spec shorthand; `okf.verified_entries` is the
  one reader, #1357). Exact tier algorithm lives in
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
   `type` histogram, status/trust-tier breakdowns, stale count. The
   histograms cover the note population — reserved files are excluded
   from them and counted apart as `reserved_count` (#1251), so the
   section reconciles with the `okf_validate` audit rather than
   contradicting it on a conformant bundle.
3. Default server instructions gain an OKF paragraph (composed in
   `_instructions.py`, wired through the existing DOMAIN-WIRING seam):
   what the tiers mean, that `log.md`/`index.md` conventions apply, and —
   advisory write guidance — "maintain `log.md`, keep `index.md` current,
   prefer bundle-root-absolute links." Same caveat as conventions: an
   legacy `MARKDOWN_VAULT_MCP_INSTRUCTIONS` replaces this entirely, while
   `MARKDOWN_VAULT_MCP_INSTRUCTIONS_EXTRA` appends and keeps it.
   Because instructions are composed before a managed-git clone runs,
   the paragraph is emitted when *mode permits* detection, phrased
   conditionally, rather than gated on the marker file existing.
4. Reserved files `index.md`/`log.md` remain indexed (they are searchable
   navigation) but are tagged for the ranking layer (§5) and excluded from
   conformance checks (§4) per spec. They are not notes, so the `stats`
   histograms (channel 2) exclude them as well.

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

## 6. Layer 4 — the write-side switches

Everything here changes bytes or write outcomes; all of it is operator-gated
by one of the switches in §6.0, none of it is implied by vault declaration,
and each behaviour below names the switch that owns it. Until §6.0 lands,
`OKF_WRITE` is the only switch and owns all of it; the section was headed
"enforced write layer (`OKF_WRITE=true`)" for that reason.

### 6.0 Ownership: three independent switches (proposed, #1412)

**Status:** design, 2026-09-09; not implemented. Supersedes the single
`OKF_WRITE` ladder (`off|stamp|maintain|own`) that #1412 was filed for and
that both earlier ownership drafts (PR #1426, PR #1429) carried. This
section says what to build; how to get there from the shipped code is
the epic's plan (#1425), and the constraints an implementer needs are on
the issues this section names, next to the code they constrain.

The server can write into an OKF bundle in three ways, and they differ in
what else they can collide with:

| Write | Touches | Can collide with |
|---|---|---|
| Stamp the notes it writes itself (`generated`, cleared `verified`) | bytes the server is writing anyway | nothing beyond the write |
| Maintain `index.md` / `log.md` | two files per folder that another party may also maintain | a second maintainer, on every folder |
| Repair provenance on notes it did not write | any note in the vault | the note's author |

An operator answers three yes/no questions, one per row, and the answers
are independent. The ladder assumed they were not (each rung a superset of
the last) and so forbade combinations that exist: a vault whose provenance
is stamped by a git hook for every writer, human included, that still wants
the server to keep `index.md` current (maintain without stamp); a vault
where another tool owns the listing but the server should stamp what it
writes (stamp without maintain, the case #1393 asks for). Three switches
cover all eight combinations; the four rungs of the ladder are four of
them.

| Question | Switch | Yes | No |
|---|---|---|---|
| Does the server stamp what it writes? | `OKF_WRITE` (exists) | `generated` set and `verified` cleared on own `write`/`edit`/`append`; `okf_verify` exposed under `OKF_VERIFY` | the note is written as given; `okf_verify` hidden |
| Is the server the maintainer of the reserved files? | `OKF_MAINTAIN` (proposed) | regenerates `index.md` after any indexed change to a non-reserved note (#1392; a reserved-file change never triggers its own regeneration, #1414), curates `log.md` per the log design (separate), and refuses client writes to those paths (#1419) | no automatic maintenance |
| May the server repair notes it did not write? | `OKF_RECONCILE` (proposed) | removes demonstrably stale `generated` / `verified` on ingested external changes, per the provenance design (separate, #1420) | no automatic repair |

The switches govern what the server does **on its own**: stamping as a
side effect of its write, maintenance and repair as reactions to changes.
An explicit, client-invoked operation on a non-reserved note is not
theirs to permit or forbid, whatever the switches say: an agent's
`write`/`edit`/`append` of such a note is governed by read-only mode
alone, and the `okf_*` migration tools additionally by `OKF_MODE` (their
`okf` tag; §7). Two explicit operations are the switches' own: a client
write to a reserved file is refused while the server maintains it
(#1419), with the migration tools that generate those files admitted as
the server's own generators; and `okf_verify` exists only under
`OKF_WRITE`, as the table says. A "no" above means the server
initiates nothing, not that an operation is unreachable. Each
switch defaults to off, and none implies another. What the server then
actually does additionally requires an active bundle and a writable
vault, so all three are inert on `auto` with no declaration and on a
read-only vault, and `OKF_MODE=off` conflicts with a switch only when it
is true, as with `OKF_WRITE=true` today. Read-side behaviour is not an
ownership question and never depends on the switches. The switches are per
instance; how one instance treats another's commits is the ingest
design's question (#1414).

**Rejected.** The ladder, above. A single set-valued setting
(`OKF_WRITE=stamp,maintain,reconcile`): the same eight combinations with
more parsing and a worse wizard entry. `OKF_MAINTAIN` defaulting to
`OKF_WRITE`'s value: it is what the shipped code does, but it makes one
answer imply another, which is the ladder's mistake in a smaller form.
Stamping on by default whenever a bundle is active: it is what a
conformant producer does, but §2's trust model says a vault-side
declaration buys advice and annotations, never authority to change
bytes, and a default-on stamp would make the declaration do exactly that.

**Where the implementation constraints live.** The configured-versus-
runnable projections and the two reporting surfaces: #1432. The
instruction snippet's clause: #1431. The read-only warning: #1434. The
reserved-file guard's admission of the server's own generators: #1419.
A reserved-file change never triggering its own regeneration: #1414.
Tool tags following switches: #1412.

- **Provenance stamping** (`OKF_WRITE`): writes through `write`/`edit` set/update
  `generated: {by, at}`, `at` the UTC instant of the write in the spec's
  example form (`2026-06-30T14:00:00Z`, a string; #1372). Actor string:
  authenticated identity when available
  (`human:<subject>` via the existing access-token dependency), else
  `markdown-vault-mcp/<version>` as a tool actor. Existing `generated`
  values are overwritten (it describes the current bytes); `sources` are
  never touched.
- **Verification invalidation** (`OKF_WRITE`): a content-changing `write`/`edit` to a note
  carrying `verified` clears the `verified` list — verification attests to
  bytes that no longer exist. Frontmatter-only edits that do not touch the
  body also invalidate (the spec ties verification to the concept, not the
  body alone); rename does not. This is the highest-value enforcement:
  it is exactly the invariant an advisory-only setup eventually misses.
- **`okf_verify` tool** (`OKF_WRITE`, attribution per `OKF_VERIFY`): appends `{by: human:<subject>, at}` (`at` a UTC instant, #1372) to `verified`,
  promoting the note's tier. `destructiveHint=False`, `idempotentHint=False`.

  The authenticated subject is *whose token* is in play, not evidence that a
  human reviewed the note. In an agentic session the model wields the human's
  token, so attributing to the auth subject alone lets the model self-promote a
  note to `human-reviewed` — which would make the tier meaningless. `okf_verify`
  is the only vector for this: `derive_trust_tier` reads `verified` exclusively,
  and the write-time `generated` stamp is provenance that does not affect the
  tier. So the attestation path is operator-configurable via
  `MARKDOWN_VAULT_MCP_OKF_VERIFY` (meaningful only when `OKF_WRITE` is on):

  - **`elicit`** (default): the tool issues an MCP **elicitation** asking the
    human to confirm they personally reviewed the note, and writes the
    `verified` entry only on an affirmative reply. **Fails closed** — if the
    client cannot elicit, or the human declines, it raises `ToolError` and
    writes nothing. A model cannot answer an elicitation, so the attestation
    leaves its control by construction; a headless agent (no human) can never
    produce a `human-reviewed` entry. The subject stamped is the authenticated
    subject when present, else the `local` sentinel — the elicitation, not the
    token, is the human-presence proof.
  - **`off`**: hides the tool entirely (via the `okf-enforce` tag disable), so
    `verified` is set only by external tooling (CLI / git-hook / CI) beyond the
    model's reach.
  - **`trust-auth`**: today's behaviour — attribute to the authenticated
    subject with no elicitation; refuses (`ToolError`) when auth mode is `none`.
    An explicit opt-in, only safe when the sole `okf_verify` caller is a
    genuinely human-driven UI, not an agent.

  Trust caveat: even `elicit` makes `human-reviewed` mean *"a human deliberately
  confirmed the review,"* not *"provably correct"* — a human can rubber-stamp.
  It guarantees a deliberate human act gates the tier, not diligence; downstream
  OKF consumers should calibrate to that.
- **Convention maintenance** (gated by `OKF_MAINTAIN` once §6.0 lands;
  today by `OKF_WRITE`): on successful writes, append a `log.md` entry
  (newest-first, `## YYYY-MM-DD` section, `**Update**:`-style bullet) and
  refresh the affected folder's `index.md` listing. The section heading is
  the server's *local* calendar day, as §9 has it and as a reader of the
  log expects, while the same write's `generated.at` is a UTC instant
  (#1372); around local midnight the two can name different days, which is
  the two rules stating what they each mean, not a defect. Both are guaranteed
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
- **Reconciliation of notes the server did not write** (`OKF_RECONCILE`,
  proposed): designed separately with the provenance evidence it depends
  on (#1420); named here so the switch has an owner in this section.
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

### Generated reserved files and the index gate

The generated `index.md` / `log.md` are subject to the same
`required_frontmatter` gate as any other note, so a body-only reserved file is
a deterministic `missing_frontmatter` skip on a vault that configures one — the
server generating a file its own indexer then rejects (#1174, #1175). Two
second-order effects follow: the bundle's change history disappears from
`search` and `list_documents` while staying readable from disk, and a folder
whose only indexed file would have been its generated `index.md` loses its
pointer in the parent listing, because `generate_index` synthesises sub-folder
pointers from index entries.

`ReservedFrontmatterPolicy` (`okf.py`) is the single owner of what those files
carry. Both generators and the `OKF_WRITE` maintainer write through it:

- **Conditional, not unconditional.** With no required fields configured a
  reserved file needs no frontmatter to be indexed, and the policy returns
  `None` so the generators keep emitting the body-only files existing bundles
  already have on disk. Closing the defect costs an unaffected vault no churn
  — and, on a git vault, no commits.
- **Existing keys win.** A hand-authored title and the root `index.md`'s
  `okf_version` declaration survive regeneration; only genuinely absent
  required fields are added.
- **`title_field` gets the derived title** (the same H1 the body builders
  emit); any other required field is seeded as `null`, since the gate tests
  presence rather than value and the server has nothing truthful to put there.
- **`okf_version` is never synthesized.** The audit flags it on any file but
  the bundle root as `misplaced`, so seeding it into a folder's index would
  trade one defect for another.

The maintainer (`OKF_MAINTAIN`; today `OKF_WRITE`) needs this for a second
reason: `_append_log` is a
read-modify-write, so the log's frontmatter has to be carried across the
rewrite explicitly. Since the maintainer runs after *every* content write, the
alternative is not a one-time gap but frontmatter stripped again after each
save — including frontmatter an operator seeded by hand.

The split is the maintainer's own work, and getting it backwards is #1391.
`DocumentManager.read` returns the *whole file* in `NoteContent.content`,
block included, while `DocumentManager.write` puts a `frontmatter=` mapping
above the body it is handed. Feeding the read text straight back therefore
serialises a second block above the one still in it, once per write; a live
folder accumulated eighteen. `scanner.strip_frontmatter_block` takes the
block off first, delegating detection to `python-frontmatter` so it agrees
with `parse_note` about where the block ends, and returning the remainder
unnormalised. Only the block that *opens* the text comes off: an identical
block further down is body, because separating "stacked by the defect" from
"quoted on purpose" needs a rule about body content that this layer does not
have. Repairing files the defective releases already stacked is #1403.

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
| 5c | Ownership switches (§6.0): `OKF_MAINTAIN`, `OKF_RECONCILE`, effective-state reporting (#1432), instruction gating (#1431), read-only warning (#1434), reserved-file write refusal (#1419) | 5b |
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
- ~~`stale_after` timezone semantics~~ — settled by the spec's 2026-08-21
  amendment and #1373: the value carries its own offset; a bare date is the
  server-local day.
- Whether `okf_export` should optionally include a generated bundle-root
  `index.md` when absent (spec allows synthesized indexes) — lean yes.
- Community governance ("W3C Holon CG" / "DataBook" profile) is
  single-sourced; ignore until corroborated.
