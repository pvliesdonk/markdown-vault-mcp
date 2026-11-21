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
**Spec targeted:** OKF v0.3, the one version the server reads every bundle
as, whatever its `okf_version` declares (#2422). OKF v0.2
([`GoogleCloudPlatform/knowledge-catalog` → `okf/SPEC.md`](https://github.com/GoogleCloudPlatform/knowledge-catalog),
Apache 2.0) was announced 2026-06-12 on the
[Google Cloud blog](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing).
What the v0.2 text says, with sources, dates and the in-place amendment
of 2026-08-21 (every timestamp an ISO 8601 datetime with an offset;
`stale_after` an instant), is the reference page
[`reference/okf-v0.2.md`](reference/okf-v0.2.md), now deprecated. The
current spec, OKF v0.3 (2026-11-18), and every place the server departs from
it are in [`reference/okf-v0.3.md`](reference/okf-v0.3.md); until OKF 1.0 the
server follows one OKF version only.

**Authority update (11 September 2026):** the adopted [north star](okf-north-star.md)
governs the intended OKF end state; the [roadmap](okf-roadmap.md) sequences the
transition. Conflicting policies below, including the exact three switches,
write stamping, verification deletion (since removed, #2405) and reserved-file admission exceptions,
are historical decisions rather than requirements for new implementation.
Descriptions of shipped behavior remain evidence, not a claim of north-star
completion.

This document records the earlier design for OKF support. As phases land,
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
  Actor convention `<scheme>:<id>` (`human:`, `team:`, `process:` defined,
  others accepted as non-human) / `<tool>/<version>`.
  Derived tiers: **unverified → machine-confirmed → human-reviewed**.
- **Lifecycle:** `status: draft|stable|deprecated` (default `stable`),
  `stale_after`: a timestamp. Every timestamp key (`generated.at`,
  `verified[].at`, `stale_after`) is an RFC 3339 date-time with an offset
  (that instant) or a date only, `YYYY-MM-DD`, "The start of that day in
  UTC"; a date-time without an offset is invalid (OKF 0.3 §5, §13.2). A
  string date-time follows the RFC 3339 grammar exactly: seconds required,
  a `.` fraction, `T` (or `t`) between date and time, `Z` (or `z`) or
  `±hh:mm` (#2428); a YAML-native datetime is a value and is read as one.
- **YAML version:** every frontmatter block is read by the YAML 1.2.2 core
  schema (OKF 0.3 §4, #2415): `yes`, `on`, `1_000`, `1:30` and every
  untagged date or timestamp are strings, and `0o17` is 15. So a
  timestamp reaches the rules above as the text written, and only an
  explicit `!!timestamp` arrives as a native value. Writes quote any string
  a YAML 1.1 or 1.2 reader would take for something else
  ([YAML 1.2 core schema](reference/yaml-1-2-core-schema.md)).
- **Bundle marker:** `okf_version: "0.2"` allowed only in the bundle-root
  `index.md` frontmatter. Under 0.3 a bundle declares `"0.3"`; see Detection.
- **Reserved files:** `index.md` (progressive-disclosure directory listing)
  and `log.md` (change history, newest-first `## YYYY-MM-DD` headings).
- **Links:** standard markdown links, untyped; bundle-root-absolute
  (`/path/x.md`) recommended, relative allowed. Wikilinks are not part of
  the spec — but link style is *convention*, not conformance. "Standard
  markdown link" is the binding half: a destination the server writes is an
  RFC 3986 URI reference, as OKF 0.3 §6 requires — a space, `%`, `#`, `?`,
  `[`, `]`, the characters outside the URI set and non-ASCII are
  percent-encoded, sub-delims stay literal (`/50%25%20done/a%23b%20(c).md`,
  #1494, #2417).
- **Extension keys:** arbitrary extra frontmatter keys are allowed and MUST
  be preserved on round-trip.
- **Out-of-root targets:** a link target that climbs above the bundle root
  is external (§6 of 0.3) and broken (account holder, 2026-11-21): never
  read from disk, never an outlink, backlink or graph edge, never rewritten
  by a rename, but listed by `get_broken_links` and counted in
  `broken_link_count` (#2416; design.md § Link Extraction, path resolution).
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
the clone happens inside the server lifespan). `config://vault` reports the
mode, declared version and active state, as well as `okf_write_configured`
and `okf_write_effective`. Active `stats.okf` reports the configured and
effective write layer as `write_configured` and `write_effective`; it is
absent when detection is inactive. Effective means `OKF_WRITE` is enabled,
OKF is active, and the vault is not read-only. Independent services remain
the subject of #1412.

The declared value turns detection on and selects no rules: every bundle is
read by OKF 0.3's rules, with none of §12.1's optional fallbacks (#2422). The
known versions are `0.3` and the `0.1` and `0.2` that the 0.3 text names and
says how to read (§12.1, §13); they log nothing. Any other value (e.g. a
future `"0.4"`) logs `okf_unknown_version` at `WARNING`, once per value per
process, and is treated as detected: OKF §12 asks a consumer to try
best-effort consumption and not refuse the bundle.

---

## 3. Layer 1 — read semantics (annotate)

*Active when detection is on. Additive only; no behavior is removed or
reordered.* The primary consumer of OKF metadata is the **agent**, not the
server's ranking math — layer 1 surfaces signal and lets the agent judge.

**Computed per-note properties** (derived at annotation time from the stored
frontmatter blob; not persisted):

- `staleness`: stale when `now >= stale_after`, compared as instants (OKF
  0.3 §5.5). A date only, quoted or YAML-native, is the start of that day
  in UTC, so `2026-12-31` is stale from `2026-12-31T00:00:00Z` whatever the
  server's zone (#2404; it was the server-local day before); a date-time
  with an offset is that instant; a date-time *without* an offset is not a
  valid timestamp and is unparseable (next bullet); absent field → not
  stale.
- Unparseable `stale_after` → `stale: true` (#2410). OKF 0.3 §5.5: "A
  consumer MUST NOT treat a `stale_after` value that it cannot parse as
  fresh. It SHOULD show the value as malformed." The spec defines stale only
  as `now >= stale_after` and forbids only "fresh", so three readings fit:
  `stale: true`; a third `stale` value; or leaving `stale: false` and
  excluding the note from `stale=false`. A third value or a filter-only
  exclusion still has to act stale in the ranking downweight and the stats
  `stale_count`, and `false` there is exactly the fresh treatment the MUST
  NOT forbids. The boolean has one not-fresh value, so the server reports
  `stale: true` where the definition is silent, consistently in the
  annotation, the `stale` filter (`stale=true` matches, `stale=false` does
  not), the ranking downweight and `stale_count`. The SHOULD is met by
  `freshness.state: malformed`, which keeps malformed apart from expired.
  Null, an empty string and an empty list are an absent key (§5.5, §11.2),
  not unparseable, so they stay `stale: false`.
- `trust_tier`: a compatibility label for recorded claims, not server
  certification. Only a mapping with an actor and an `at` timestamp (a
  date-time with an offset, or a date, #2400) counts, and of those only the current
  events count (#2409). OKF 0.3 §5.3: "A verification event is **current** when
  its `at` is equal to or later than `generated.at`, or when `generated` is
  absent. Consumers MUST calculate the trust tier only from current
  events." Instants compare as instants, across offsets. An actor is `<scheme>:<id>` or `<producer>/<version>`
  (OKF 0.3 §7, #2407): the id is opaque, and "Consumers MUST accept other
  schemes and treat them as non-human". §7 defines no grammar for a scheme,
  id, producer or version, so the server invents none: any string
  containing a `:` or `/` is an actor, whitespace, empty parts (`human:`,
  `:x`, `agent/`) and further separators included. A string with neither,
  such as `codex` or `Jane Doe`, fits no form and is malformed; an empty
  `by` is absent (§11.2). Only the `human:` scheme, matched exactly at the
  start, is human, so `human:` with an empty id is human-reviewed (the spec
  is silent on an empty id), while `Human:x`, `team:x` and every other
  scheme or `<producer>/<version>` actor are machine-confirmed. A current
  human record yields `human-reviewed`; otherwise a current record yields
  `machine-confirmed`; otherwise `unverified`. An earlier record stays in
  the file and in `recorded` as history ("verified before the last
  change") and promotes nothing. A bare mapping is still one record. Malformed
  siblings do not erase usable records. This deliberately changes the
  public helper and filter behavior from v6.1.4 (#1444).

  **Decision (#2409; spec silent, confirmed by the account holder on
  21 November 2026):** §5.3 does not say whether an event is current when
  `generated` is present but has no usable `at` (absent, unparseable, or a
  non-empty `generated` that is not a mapping). A null, an empty string or
  an empty list counts as an absent `generated` (§11.2 rule 1), so every
  event is current there. The server reads the definition
  literally: neither of its two conditions holds (there is no `generated.at`
  to reach, and `generated` is not absent), so no event is current and the
  tier is `unverified`. Treating such a note as if `generated` were absent
  would count reviews whose relation to the last content change cannot be
  established, which is the outcome §5.3 exists to prevent. `review.currency`
  reports this case as `no_generation_time`. The cost of this reading:
  on such a note an `okf_verify` entry is not current either, so the tool
  records the review without promoting the tier. Every comparison reads both
  timestamps through one parser (`_parse_timestamp`), so the accepted
  timestamp forms (#2404) apply to currency unchanged.
- `freshness`: `state` is `missing`, `malformed`, `unexpired`, or `expired`;
  `basis` is `no_expiry_supplied`, `unsupported_expiry`, `offset_instant`
  (a date-time with an offset), or `utc_day_start` (a date only, read as
  00:00 UTC). The compatibility `stale: false` includes missing
  expiry and therefore does not mean fresh or true; unparseable expiry is
  `stale: true` (#2410).
- `review`: `state` is `missing`, `malformed`, or `recorded`, with
  `recorded_entries`, `malformed_entries`, and `basis: supplied_metadata_only`.
  The recorded entries are split by §5.3 currency (#2409): `current_entries`
  feed `trust_tier`; `earlier_entries` have an `at` before `generated.at`;
  `currency` is `no_generation` (`generated` absent, null or empty, so every
  recorded entry is current), `generated_at` (compared against it) or `no_generation_time`
  (`generated` present without a usable `at`; both counts are 0, per the
  recorded interpretation above). Mixed lists retain both counts; `recorded` never means applicability to
  the current revision has been established. No source fetching or reviewer
  authentication occurs.
- Absent and single values (#2411, #2412) follow OKF 0.3 §11.2: "A null
  value, an empty string, and an empty list are the same as an absent key",
  so a null or empty `stale_after`, `generated`, `generated.at`, `verified`
  or `sources` (or `sources[].resource`) reads as absent, and `review` and
  `attribution` report it `missing` (there is no separate `empty` state).
  For the list fields `verified` and `sources`, "consumers MUST accept one
  item as a list with one item": a bare mapping, or any other single value,
  is one entry, judged like an entry of a list. An entry without its
  REQUIRED key (`resource`; `by` and `at`) is ignored, and its
  `malformed_entries` count is the warning rule 3 asks for. The server
  reads no OKF-specific `tags`; the generic `tags` indexing is unchanged.
- `review_applicability`: `{state, basis}` stating whether a supplied review
  applies to the current content. No review is bound to a content snapshot,
  so a `verified` value with entries, malformed ones included, reports
  `state: unknown` with `basis: no_snapshot_binding`. `review` reports
  entry validity; timestamps and extensions never establish coverage. A
  `verified` with no entries (absent, null, `""` or `[]`) reports
  `state: none` with `basis: no_review_supplied`. This assessment is
  separate from freshness.

Expiry strings accept extended calendar dates or calendar datetimes with
`T`, `t`, or a space, hours/minutes, optional seconds/fraction, and `Z` or a
numeric `±HH:MM` offset. Week/basic dates, offset-less times, out-of-range
clock components and offsets are unsupported. This bounded interpretation
is not full ISO 8601 validation. Parsed Python dates/datetimes are also
accepted; datetime awareness requires a non-None `utcoffset()`.

- `okf.status`: the raw `status` value, defaulted to `stable` when absent
  (per spec); unknown values pass through unmapped (no semantics attached).
  OKF 0.3 §5.4: "A value that is not in this list is not `stable`" and
  consumers "SHOULD show the raw value" (#2420). Absent, null, `""` and
  `[]` read as `stable` (§11.2 rule 1). The vocabulary is the exact tokens
  `draft`, `stable`, `deprecated`: no trimming or case folding, so a quoted
  `" stable "` or `Stable` is outside it. A non-string value is shown as
  its JSON text (`3` → `"3"`, `true` → `"true"`, a list as a JSON array, a
  YAML date in ISO form), so `okf.status` is always a string, a `status`
  filter matches that text, `status=stable` never matches it, and the
  audit counts it under `unknown_status`.
- `attribution` and `generation` (#2386, replacing #2069's true-only
  `malformed_sources`/`malformed_generated` flags before release): the
  provenance fields reported in the evidence objects' vocabulary, shared by
  full-read and compact annotations. `attribution` reports `sources` in
  `review`'s shape: `state` `missing`, `malformed` or `recorded`,
  `recorded_entries` (entries that are mappings whose `resource` has a
  value, which §5.1 makes "REQUIRED in an entry"), `malformed_entries`
  (any other entry; a single non-list value is one entry) and
  `basis: supplied_metadata_only`; a mixed list is `recorded` with both
  counts. `generation` is `{state, basis}` with `state` `missing`,
  `malformed` or `recorded` and `basis: supplied_metadata_only`, judged as
  the [format reference](reference/okf-v0.2.md) states §5.2: `by` is
  required and is an actor in the sense above (any `<scheme>:<id>` or
  `<producer>/<version>`, #2407); `at` is optional, and when present it is an OKF §5
  timestamp: a date-time with an explicit offset or a date only (#2400). A
  `generated` with `by` and no `at` is therefore `recorded`, a date-only
  `at` is `recorded`, and an offset-less date-time `at` is `malformed`. A `verified`
  entry needs both `by` and `at`, because §5.2 gives every verification
  event both. The rules match the spec exactly, by the account holder's
  decision of 21 November 2026. The server's own stamps
  (`human:<subject>`, `markdown-vault-mcp/<version>`, UTC `Z` instants)
  read back as `recorded`. Source lists pass through intact and retain
  their full count even when mixed with non-mappings; non-list sources
  gain no invented list/count, and empty lists remain omitted. No raw
  `generated` projection is added. These computed checks change no stored
  rows, trust, filters, ranking, admission, or write policy.

**Delivery channels** (mirroring the Folder Conventions channel list):

1. `search` results, `read`, and `get_context` payloads gain an `okf` key
   carrying `{type, status, stale, trust_tier, freshness, review, review_applicability, attribution, generation}`
   — omitted entirely when detection is off, and individual members omitted
   when underivable.
   Whole reads also carry `recorded` with supplied `stale_after` and
   `verified` values as parsed, including null/malformed values and extensions;
   missing keys stay absent. Compact results omit these potentially large
   records. Values use the existing metadata serialization, not original YAML
   spelling. `sources` are surfaced on `read` (full) and counted on search hits
   (`sources_count`), not inlined into result lists.
2. `stats` gains an `okf` section: declared version, effective mode,
   `type` histogram, status/trust-tier breakdowns, stale count. The
   histograms cover the note population — reserved files are excluded
   from them and counted apart as `reserved_count` (#1251), so the
   section reconciles with the `okf_validate` audit rather than
   contradicting it on a conformant bundle.
3. Default server instructions gain an OKF paragraph (composed in
   `_instructions.py`, wired through `_server_wiring`, which the DOMAIN-WIRING seam calls):
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
  staleness is a date comparison against it at query time, and an
  unparseable value counts as stale (#2410).
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

- Conformance findings follow OKF 0.3 §11.1. Rule 1: a frontmatter block
  in any `.md` file, `index.md` and `log.md` included, that does not parse
  as a YAML mapping (`unparseable_frontmatter`; an empty or comment-only
  block reads as no keys and passes). Rule 2: a reserved file that
  disagrees with §8 or §9 — any block on a folder `index.md`
  (`index_frontmatter`, presence read from the block, #1396), any block on
  a `log.md` (`log_frontmatter`), a `log.md` `##` heading that is not
  `YYYY-MM-DD` (`log_heading_shape`). The bundle-root `index.md` may carry
  any keys (§8.1: `okf_version`, `title`, `description`, `license` and
  extension keys), so only rule 1 applies to it (#2413, #2414).
  `okf_version` on a concept is an extension key (§4.1: "Consumers MUST
  NOT reject documents that contain unknown keys") and no finding; on a
  folder `index.md` it is part of that file's `index_frontmatter` (#2427).
- Concepts (§3.2): a non-reserved `.md` file whose frontmatter has a
  non-empty `type`. `conformant_notes` counts them; `total_notes` adds the
  non-reserved files whose block fails rule 1, since they may be concepts.
  A file with no frontmatter or no `type` is not a concept, does not affect
  conformance, and is reported only as the `missing_type` warning §3.2 asks
  validators for; every concept and evidence rule skips it (#2406).
- Advisory: `missing_type`, unknown `status` values on concepts, and a
  missing root `index.md`, which the spec tolerates.
- Informational (non-conformance) counts: wikilink usage in concepts
  (matters at export only), concepts lacking recommended fields.
- `malformed_sources` and `malformed_generated` are evidence findings
  (#2386): they reuse the annotation's `attribution` (any malformed entry)
  and `generation` (`state: malformed`) rules, counting each assessed
  non-reserved note once per field with sorted, capped examples. Like the
  other evidence findings they are withheld for a file counted under
  `evidence_unavailable` (unreadable, unparseable, or over the read cap),
  rather than judged from truncated content. They leave `conformant_notes`
  unchanged, and reading and auditing never rewrite malformed fields.
- Evidence findings (#1444): `missing_freshness` (informational),
  `malformed_freshness` and `malformed_review` (format observations, using
  the annotation's `freshness` and `review` rules), and
  `evidence_unavailable` for discovered concepts whose read or parse failed
  or whose content exceeds the read cap (detected by reading one character
  past it). They leave `conformant_notes` unchanged and cover discovered,
  non-excluded concepts, not complete traversal or editorial policy.
- Every report field added after `index_frontmatter` is defaulted and
  appended, provenance fields first and then evidence fields, so positional
  library construction keeps working.
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
| Stamp the notes it writes itself (`generated`; `verified` kept) | bytes the server is writing anyway | nothing beyond the write |
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
| Does the server stamp what it writes? | `OKF_WRITE` (exists) | `generated` set on own `write`/`edit`/`append`, `verified` kept as history; `okf_verify` exposed under `OKF_VERIFY` | the note is written as given; `okf_verify` hidden |
| Is the server the maintainer of the reserved files? | `OKF_MAINTAIN` (proposed) | regenerates `index.md` after any indexed change to a non-reserved note (#1392; a reserved-file change never triggers its own regeneration, #1414), curates `log.md` per the log design (separate), and refuses client writes to those paths (#1419) | no automatic maintenance |
| May the server repair notes it did not write? | `OKF_RECONCILE` (proposed) | removes demonstrably stale `generated` / `verified` on ingested external changes, per the provenance design (separate, #1420) | no automatic repair |

The switches govern what the server does **on its own**: stamping as a
side effect of its write, maintenance and repair as reactions to changes.
An explicit, client-invoked operation on a non-reserved note is not
theirs to permit or forbid, whatever the switches say: an agent's
`write`/`edit`/`append` of such a note answers to the vault's general
write policies (read-only mode, write protection, conditional writes,
and whatever else `design.md` gives the write path) and to no OKF
switch; the `okf_*` migration tools additionally answer to `OKF_MODE`
(their `okf` tag; §7). Two explicit operations are the switches' own: a client
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
instruction snippet's clause: #1431. Read-only with `OKF_WRITE` enabled is
accepted and logs `okf_write_inert` at INFO on startup: #1434. The
reserved-file guard's admission of the server's own generators: #1419.
A reserved-file change never triggering its own regeneration: #1414.
Tool tags following switches: #1412.

- **Provenance stamping** (`OKF_WRITE`): writes through `write`/`edit` set/update
  `generated: {by, at}`, `at` the UTC instant of the write in the spec's
  example form (`2026-06-30T14:00:00Z`, a string; #1372). Actor string:
  the token's usable `sub` when the principal is human (`human:<subject>`), else
  `markdown-vault-mcp/<version>` as a tool actor. Existing `generated`
  values are overwritten (it describes the current bytes); `sources` are
  never touched. Client-ID-only identities, including static and mapped
  bearer credentials, use the tool actor (#1463). So does an OAuth token the
  issuer marks as an application's (`sub` equal to the client ID, `idtyp: app`
  or `gty: client-credentials`; #1480), and so does a token whose `sub` the
  operator lists in `SERVICE_SUBJECTS`; an unmarked, unlisted service-account
  token still stamps `human:<subject>`. See
  [authenticated subjects](reference/authenticated-subjects.md).

  Content writes and verification preserve surviving frontmatter fields in
  order, including nested mappings and mappings inside lists. Replacing
  `generated` or `verified` retains its slot, and an absent field appends.
  The stamp and `okf_verify` rewrite only those keys' lines; every other
  line of the block, comments and quoting included, and the body keep their
  bytes (OKF 0.3 §4: "A producer that modifies an existing file SHOULD NOT
  re-serialize values that it did not change", #2419). The splice is kept
  only when the result reads back as the intended frontmatter with the same
  body; otherwise, and for a CRLF block, a key written twice or a
  flow-style top-level mapping, the block is serialized whole, keeping
  order and values but not comments (`_frontmatter_splice.py`). The
  serializer boundary is
  recorded in [Python frontmatter serialization](reference/python-frontmatter-pyyaml.md).

  A stamp is written *into* the note's frontmatter, so content whose block
  no parser can read leaves nothing to stamp and the write is **refused**
  (#1454). Writing it unstamped was the alternative and is worse: the layer's
  whole claim is that every write it passes carries current provenance, and a
  silent exception to that is the kind an operator finds months later. The
  refusal is raised where the path is known (`_finalize_content_write`) as the
  `ValueError` this layer uses for every other rejected write, so a caller is
  told which note and why rather than being handed the parser's own exception.
  With `OKF_WRITE` off there is no stamp to fail: the write lands, and the
  indexer records the file as a `parse_error` skip (#1408).
- **Verification kept as history** (`OKF_WRITE`): a content write leaves
  `verified` as it was. OKF 0.3 §5.3: "Earlier events stay in the file as
  history." The write moves `generated.at`, and a reader counts only the
  events at or after it toward the tier, so a review recorded before the
  write stops counting without being erased (#2405). Clearing on write was
  the earlier decision; it removed evidence the spec keeps, and it is
  unnecessary once the tier reads only current events. Recording what a
  kept review applies to is separate work (#1443).

  The kept value is the **stored** note's, never the written text's: the
  write path reads the file it replaces under the same write lock (`edit`
  and `append` already hold that text), and the stamp sets `verified` to
  the stored value exactly, or removes the key when the stored note has
  none, does not parse, is not UTF-8 or does not exist yet. A content write
  therefore cannot add, alter or drop a review, and `okf_verify`, behind
  its `OKF_VERIFY` gate, stays the only path that records one. Taking the
  text's value instead would let any caller of `write` forge a
  `human-reviewed` event. Before #2405 the layer removed `verified` on every
  stamped write, so client-supplied events never landed then either.
- **`okf_verify` tool** (`OKF_WRITE`, attribution per `OKF_VERIFY`): appends `{by: human:<subject>, at}` (`at` a UTC instant, #1372) to `verified`,
  promoting the note's tier. `destructiveHint=False`, `idempotentHint=False`.

  The authenticated subject is *whose token* is in play, not evidence that a
  human reviewed the note. In an agentic session the model wields the human's
  token, so attributing to the auth subject alone lets the model self-promote a
  note to `human-reviewed` — which would make the tier meaningless. `okf_verify`
  is the only vector for this: `derive_trust_tier` promotes only from
  `verified`, and the write-time `generated` stamp can only demote: it moves
  `generated.at`, so a review older than the write is no longer current
  (§5.3, #2409). So the attestation path is operator-configurable via
  `MARKDOWN_VAULT_MCP_OKF_VERIFY` (meaningful only when `OKF_WRITE` is on):

  - **`elicit`** (default): the tool issues an MCP **elicitation** asking the
    human to confirm they personally reviewed the note, and writes the
    `verified` entry only on an affirmative reply. **Fails closed** — if the
    client cannot elicit, or the human declines, it raises `ToolError` and
    writes nothing. A model cannot answer an elicitation, so the attestation
    leaves its control by construction; a headless agent (no human) can never
    produce a `human-reviewed` entry. The subject stamped is the token's `sub`
    when the principal is human, else `local` (including service credentials
    and application-marked tokens) — the elicitation, not the
    token, is the human-presence proof.
  - **`off`**: hides the tool entirely (via the `okf-enforce` tag disable), so
    `verified` is set only by external tooling (CLI / git-hook / CI) beyond the
    model's reach.
  - **`trust-auth`**: attribute to the token's `sub` with no elicitation;
    refuses (`ToolError`) without auth, with a client-ID-only identity,
    including static bearer credentials (#1463), or with a token the issuer
    marks as an application's or whose `sub` is in `SERVICE_SUBJECTS` (#1480).
    An explicit opt-in, only safe when the sole `okf_verify` caller is a
    genuinely human-driven UI, not an agent.

  Trust caveat: `elicit` asks a human to confirm this tool's review entry, but
  the `human-reviewed` tier also classifies supplied metadata. It does not
  authenticate the reviewer or establish applicability to current content.
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
  attestation or mechanical rewrite does not churn the reserved files. A
  transform suppresses each write it issues from inside `OkfMigrationManager`,
  so the exemption is the transform's own and does not depend on which entry
  point called it (#1401); it had been entered in the MCP tool handlers alone,
  which left a library caller's bundle stamped by its own migration. The
  affected folder is the one directly containing the written note; the
  `index.md` refresh reuses the migration `generate_index`, draining the
  single-writer index first so a just-created note is listed. When that
  folder has no `index.md`, the listing regenerated is the nearest indexed
  ancestor's (`ConventionMaintainer.indexed_anchor`): `generate_index`
  creates an `index.md` in every subfolder it points at that lacks one, down
  to the deepest level, so a generated listing never points at a missing
  file and every level is navigable (#1647). A subfolder that already has
  an `index.md` is pointed at and not regenerated. A `delete`,
  `rename` or `move_folder` refreshes the listings it made stale, through
  `ConventionMaintainer.refresh_indexes` with the same guards (#1609): the
  deleted note's folder; a renamed note's old folder; after a folder move,
  every vault-owned folder of the moved subtree and the old parent. The
  subtree walk omits a root inside a git submodule and prunes submodule
  directories before descent, using `DocumentManager.in_submodule` (#1950).
  Ordinary containing folders and non-hidden descendants retain their
  sorted walk order; no submodule listing refresh is attempted. Of those, only
  folders that already carry an `index.md` are refreshed. The destination
  is reached as a write reaches it: through its indexed anchor (for a move,
  the anchor of the new parent), whose regeneration lists it and indexes
  every missing level in between. No `log.md` entry is written for these operations, which is
  left to the log design. A rename to or from a reserved name triggers
  nothing, both because a reserved-file change never triggers itself (#1414)
  and because regenerating would overwrite the note just renamed onto it.
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

Cost note: `generate_index` now waits for a successful queued FTS refresh
before deriving its listing (#1464), just like explicit link conversion.
The refresh queues behind an in-flight initial build before the generator checks
readiness, covering convention maintenance during cold startup as well.
The wait requires a successful build outcome, including marker and readiness
publication, with one 60-second budget for the build and refresh. Failed or
cancelled attempts also prevent regeneration; failures leave the existing listing in
place and convention maintenance logs a warning. The primary note write
is not rolled back. Follow-up embedding work need not finish, although
jobs already ahead of the refresh can delay it. The old separate 10-second
best-effort drain is no longer wired by Vault; the optional maintainer
preflight hook remains available to existing integrations.

Commit fan-out is no longer one of these costs. It was, while each secondary
write committed separately; since #1264 the dispatcher buffers every write a
tool call makes and commits them together, and the maintainer's writes are
issued synchronously inside the note's own `write` / `edit`, so they share its
commit. [verified 2026-09-12: one `write` into a folder on a git-backed bundle
produced one commit, `write: 3 files`, naming the note, `log.md` and
`index.md` (#1423).]

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
- **`okf_version` is never synthesized.** Only the bundle-root `index.md`
  declares the version (OKF 0.3 §8.1), and a folder index carries no
  frontmatter at all (§8).

The audit does not exempt these files. A key the policy seeds into an
folder `index.md` is an `index_frontmatter` finding like any hand-written
one, and one seeded into a `log.md` a `log_frontmatter` finding (#1396,
#2413); the root `index.md` may carry the keys (§8.1). The finding
describes the file against §8 and §9, not who wrote it, and
changes neither what the policy seeds nor what the gate admits. Whether the
gate should reach generated reserved files at all is open (#1438).

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
have. Repairing files the defective releases already stacked is #1403,
through the deliberate operation below.

### Deliberate legacy log repair (#1403)

The repair is an operator command, `markdown-vault-mcp admin repair-log`, over
library methods on `Vault.writer`. It is not an MCP tool: a one-off historical
repair does not earn permanent tool surface, and the review it needs is a
person reading the exact bytes, which a CLI run provides directly.

`okf_preview_log_repair(path)` reads one bounded raw UTF-8 snapshot of a
vault-relative `log.md`. It recognizes an opening YAML mapping delimited by
`---` and followed by one blank line, then an immediate run of byte-identical
copies of that whole unit. LF and CRLF units and a leading UTF-8 BOM are
supported. A differing or interleaved adjacent block, malformed mapping, or
unsupported shape returns `no_candidate`; no bytes change. The resolved
target must also be named `log.md`.

Equality establishes only a structural candidate, not generated origin. An
authored example can have the same bytes. Preview returns `review_required`
with the exact removal, its half-open byte span, the snapshot etag, the
proposed output etag and the block count. The proposal is a pure function of
the path and the snapshot bytes, so the snapshot etag binds the whole removal:
`okf_apply_log_repair(path, etag=...)` recomputes the proposal under the write
lock and refuses with `ConcurrentModificationError` unless the current etag is
the reviewed one, and with `InvalidRequestError` when no candidate remains. The
command prints the removal and the apply command carrying that etag; running
the apply is the operator's authorization. The server never infers it from
equality, frontmatter or a filename.

`DocumentManager` owns application under the shared write lock, with existing
writability, path, nested-repository and read-limit gates. It recomputes the
proposal and checks the etag before creating the adjacent
`log.md.frontmatter-repair.bak`. Creation is exclusive: every existing entry,
including an equal copy, directory, or dangling symlink, is a collision.
The copy is restricted to mode `0600` before content is written and its bytes
are verified against the complete original. Its write notification completes
before replacement is attempted. The target etag is checked again after
retention, then the approved span is spliced out through the existing atomic
byte writer. The opening unit, BOM, all other bytes, and supplied provenance
claims stay identical. Only the target is marked dirty for indexing.

The recovery artifact remains after success or failure. Failures after a
retention attempt identify it and whether target replacement was attempted;
the artifact may be complete, partial, or absent. A notification failure after
replacement reports possible mutation, never rollback. No automatic retry,
reuse, cleanup, or reversal occurs.

Preview builds a vault with an in-memory index and Git settings ignored, so it
runs beside a server without touching its index, state or checkout. Apply
builds an owning vault, as `admin index` does: a server holding the on-disk
index's writer lock makes it refuse before anything is written. Nothing
excludes a server with an in-memory index, external editors or sync tools; the
write lock serializes only this process's writes. This is not a transaction or
a power-loss durability guarantee. In a Git-backed vault both the backup and
the log are committed through the ordinary write callback.

Repair bypasses provenance enrichment and convention maintenance; ordinary
writes still never clean the body. No parser or index-semantics behavior
changes: the repair changes file bytes, and the hash-based indexer picks them
up. `log.md` can retain frontmatter under the recorded role contract
([OKF v0.2 §9](reference/okf-v0.2.md)); the index-only restriction is not
applied to logs.

### Export

Export ships **not as a bespoke tool but as an overloaded download ref** on
`create_download_link` (the transfer subsystem adopted in #979). The
domain `VaultTransferSink` recognises a bundle ref — `okf-bundle` for the whole
vault, `okf-bundle:<folder>` for a subtree — validates the scope at link
creation (gated out when `OKF_MODE=off`), and generates the archive at fetch
time via `okf_bundle.build_okf_bundle`. This is a domain use of the transfer
primitives expressed without a new tool: a
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
| 5a | Enforced write layer (`OKF_WRITE`): stamping, verification clearing (removed by #2405), `okf_verify` | 1 |
| 5b | Enforced-write convention maintenance: `log.md` append + affected-folder `index.md` refresh on successful writes; `index.md` refresh after `delete` / `rename` / `move_folder` (#1609); generated listings create missing subfolder indexes (#1647) | 5a |
| 5c | Ownership switches (§6.0): `OKF_MAINTAIN`, `OKF_RECONCILE`, effective-state reporting (#1432), instruction gating (#1431), read-only startup diagnostic (#1434), reserved-file write refusal (#1419) | 5b |
| 6 | Ranking downweights | 1 (own phase: different risk profile) |
| Docs | Guide, interop sections, examples/prompt packs | trails each phase; guide lands with 4 |

Every phase carries its own documentation impact (tools reference,
configuration page, README, wizard metadata via config-field tags) per the
Documentation Discipline; the Docs phase covers the guide-level work no code
phase owns.

## 10. Open questions

- Reads report a supplied review's applicability as unknown (#1413). How observed changes
  are represented and how reviews bind to snapshots remain follow-ups (#1414,
  #1445).
- ~~`stale_after` timezone semantics~~ — settled by OKF 0.3 §5 (#2404): a
  date-time carries its own offset; a date only is the start of that day in
  UTC.
- Whether `okf_export` should optionally include a generated bundle-root
  `index.md` when absent (spec allows synthesized indexes) — lean yes.
- Community governance ("W3C Holon CG" / "DataBook" profile) is
  single-sourced; ignore until corroborated.
