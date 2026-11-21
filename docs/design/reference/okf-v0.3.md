---
type: Reference
title: Open Knowledge Format v0.3
description: What the OKF v0.3 spec requires of a bundle, its producers and its consumers (timestamps, trust currency, actors, reserved files, links, conformance, versioning), how it differs from 0.2, and where this server departs from either text
subject_version: "0.3 (open-knowledge-format/open-knowledge-format SPEC.md at commit 527e81aff5, 2026-11-18)"
valid_for: "OKF 0.3 as released 2026-11-18; re-research on any later commit to SPEC.md or a 0.4 draft"
generated:
  by: process:researching-references
  at: 2026-11-21T11:04:16+01:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-21T11:32:00+01:00
stale_after: 2027-05-21T00:00:00Z
status: stable
sources:
  - id: spec
    title: Open Knowledge Format (OKF), Version 0.3, SPEC.md at commit 527e81aff5 (2026-11-18)
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81aff5/SPEC.md
    accessed: 2026-11-21
  - id: spec-draft
    title: OKF Version 0.3 (draft), SPEC.md at commit 9506f310ed (2026-10-23, "Update to OKF v0.3")
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/9506f310ed/SPEC.md
    accessed: 2026-11-21
  - id: spec-v02
    title: OKF Version 0.2 as amended, SPEC.md at commit 0b87c52c6e (2026-08-21)
    resource: https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/0b87c52c6e/SPEC.md
    accessed: 2026-11-21
  - id: old-readme
    title: GoogleCloudPlatform/open-knowledge-format README, deprecation notice (commit 8a8ff87bfd, 2026-11-18)
    resource: https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/8a8ff87bfd/README.md
    accessed: 2026-11-21
---

# Open Knowledge Format v0.3

What `SPEC.md` in `open-knowledge-format/open-knowledge-format` requires,
scoped to the fields and files this server reads, writes, audits and ranks
on. The companion [v0.2 page](okf-v0.2.md) keeps the 0.2 texts (July and
the 2026-08-21 amendment); this page records 0.3, what changed, and every
place the server departs from either. Written for #2403 under epic #2401,
where the account holder ruled that a departure from the standard is a bug.

## Scope

- Covers: bundle structure and concept discovery (§3), frontmatter syntax
  and keys (§4), timestamps, provenance, trust and lifecycle (§5), links
  and paths (§6), actors (§7), index and log files (§8, §9), conformance
  (§11), versioning and the change list (§12, §13).
- Does not cover: Attested Computations (§10), the `usage_count` /
  `usage_window` credibility signals (§5.1), and Appendix A. Nothing in
  the server reads or writes them.
- Depended on by: `src/markdown_vault_mcp/okf.py`, `_okf_write.py`,
  `_okf_convention.py`, `okf_bundle.py`, `managers/okf_migrate.py`, the
  link resolver in `scanner.py`, and `utils/links.py` (written
  destinations). `docs/design/okf.md` decides what the project does.

## Where the text lives

- The GoogleCloudPlatform repository is deprecated: "OKF development has
  moved to open-knowledge-format/open-knowledge-format, which has the
  latest spec (v0.3)". [source: old-readme]
- The successor's `SPEC.md` history shares the 0.2 commits (0b87c52c6e is
  in both), then 9506f310ed "Update to OKF v0.3" (2026-10-23) and two
  edits on 2026-11-18. The repository has no tags or releases.
  [observed: `gh api repos/open-knowledge-format/open-knowledge-format/commits?path=SPEC.md`; `.../tags` and `.../releases` empty, 2026-11-21]
- The 2026-11-18 edits change only the header from "Version 0.3 (draft)"
  to "Version 0.3" and remove the sentence giving `MUST`, `SHOULD` and
  `MAY` "the meaning that RFC 2119 gives them". The keywords still appear
  in capitals throughout. [observed: `diff` of SPEC.md at 9506f310ed and 527e81aff5]
  [source: spec-draft]
- A six-month `stale_after`: no tags, and 0.2 was amended in place after
  release. 0.3 now forbids that: "After the release of a version, a change
  to its normative text requires a new version." [source: spec] (§12)

## Claims

### Bundle structure and concept discovery (§3)

- `index.md` and `log.md` are reserved "at all levels of the hierarchy".
  "Consumers MUST NOT treat them as concepts, whatever frontmatter they
  contain." [source: spec] (§3.1)
- A concept is a non-reserved `.md` file "that has frontmatter with a
  non-empty `type`". A `.md` file with no frontmatter or no `type` "is not
  a concept. Consumers skip it. It does not affect conformance. Validators
  SHOULD warn about it." [source: spec] (§3.2) *0.2 differed:* every
  non-reserved `.md` was a concept and needed a `type` to conform.
  [source: spec-v02] (§3.1, §11)
  [pins: tests/test_okf.py::TestNonConceptAudit::test_typeless_files_are_warned_about_not_counted]
- Files and directories starting with `.` "are not part of the bundle". A
  consumer "does not follow symbolic links that go out of the bundle root".
  [source: spec] (§3.2)

### Frontmatter syntax and keys (§4)

- "The frontmatter MUST parse as a YAML 1.2 mapping with the core schema."
  Producers MUST NOT write duplicate keys; consumers MAY reject them.
  Producers SHOULD quote values that look like numbers or booleans.
  [source: spec] (§4)
- "A producer that modifies an existing file SHOULD NOT re-serialize
  values that it did not change." [source: spec] (§4)
- `type` is "the only key that is always required". "Consumers MUST accept
  unknown types." [source: spec] (§4.1)
  [pins: tests/test_okf.py::TestOkfAudit::test_mixed_vault_report]
- Producers MAY add keys at any level, including inside `sources` entries,
  `generated` and `verified` entries. "Consumers MUST NOT reject documents
  that contain unknown keys. A producer that modifies an existing file MUST
  keep the keys that it does not know." Future versions will not define
  `x-` keys. [source: spec] (§4.1)
  [pins: tests/test_okf_write.py::TestApplyOkfWriteStamp::test_preserves_sources_and_other_fields]
- The body is CommonMark with GFM tables and `[^label]` footnotes.
  [source: spec] (§4.2)

### Timestamps (§5)

- The timestamp keys are `generated.at`, `verified[].at`, `stale_after`,
  `sources[].last_modified`, `usage_window.from` and `.to`. Each value is
  an RFC 3339 `date-time` ("That instant") or `full-date`, which means "The
  start of that day in UTC". "No other form is valid." [source: spec] (§5)
- "Consumers MUST accept a quoted string and a YAML-native timestamp. A
  YAML-native date value means the start of that day in UTC." Producers
  SHOULD use the date-time form for `generated.at` and `verified[].at`.
  [source: spec] (§5)
  [pins: tests/test_okf.py::test_derive_stale_bare_date_is_the_utc_day_start, tests/test_okf_evidence.py::test_date_only_at_is_recorded]
- The change list says why: the v0.2 text gave a date-only value no time
  zone, the amendment then required an offset, and "v0.3 accepts date-only
  values again, with a fixed meaning, so v0.2 bundles that contain them
  stay valid. A date-time without an offset stays invalid."
  [source: spec] (§13.2)
- What the RFC 3339 `date-time` and `full-date` rules accept, the open
  erratum on a space separator, and how PyYAML and `fromisoformat` differ
  from them are recorded in [RFC 3339 timestamps](rfc3339-timestamps.md).

### Trust: `generated`, `verified`, tiers (§5.2, §5.3)

- `generated.by` is REQUIRED within `generated`. `generated.at` is "The
  time of the last meaningful change to the content. A trivial edit
  (format, whitespace, link repair) does not change `generated.at`."
  [source: spec] (§5.2)
- Each `verified` event needs both `by` and `at`; "Consumers ignore an
  event that does not have both." A bare `{by, at}` mapping MUST be read
  as a one-item list. [source: spec] (§5.2)
  [pins: tests/test_okf.py::test_derive_trust_tier]
- "A verification event is **current** when its `at` is equal to or later
  than `generated.at`, or when `generated` is absent. Consumers MUST
  calculate the trust tier only from current events. Earlier events stay
  in the file as history." [source: spec] (§5.3) *New in 0.3*, listed as a
  breaking change: "Some concepts go from human-reviewed to a lower tier."
  [source: spec] (§13.1)
- Tiers: no current event ⇒ unverified; current events from non-`human:`
  actors only ⇒ machine-confirmed; a current `human:<id>` event ⇒
  human-reviewed. [source: spec] (§5.3)

### Lifecycle: `status`, `superseded_by`, `stale_after` (§5.4, §5.5)

- `status` absent ⇒ `stable`. "A value that is not in this list is not
  `stable`. Consumers that filter to current knowledge exclude the
  concept. Consumers SHOULD show the raw value." *New in 0.3.*
  [source: spec] (§5.4)
  [pins: tests/test_okf.py::TestDeriveAnnotation::test_unknown_status_passes_through]
  [pins: tests/test_okf.py::TestDeriveAnnotation::test_status_outside_vocabulary_is_raw_and_not_stable]
- `superseded_by` is one path to the replacement, meaningful only with
  `status: deprecated`; "Consumers that filter results to current
  knowledge SHOULD follow it". *New in 0.3.* [source: spec] (§5.4)
- A concept is stale when `now >= stale_after`. "An empty or null
  `stale_after` is the same as an absent key." "A consumer MUST NOT treat
  a `stale_after` value that it cannot parse as fresh. It SHOULD show the
  value as malformed." [source: spec] (§5.5)

### Provenance: `sources` (§5.1)

- `resource` is REQUIRED in each entry; it is a followable artifact or a
  scope descriptor. `id` is optional and SHOULD be present when the body
  cites the source. [source: spec] (§5.1)
  [pins: tests/test_citations.py::test_literal_join_and_stable_ids]
- `sources[].author` is an actor. [source: spec] (§5.1, §7)

### Links and paths (§6)

- A value with a URI scheme is an absolute URL; a leading `/` resolves
  from the bundle root; anything else resolves from the file's directory.
  "Consumers MUST resolve both forms." The spec "no longer recommends one
  form" ("v0.2 recommended `/` for links"). [source: spec] (§6, §13.2)
- "A value that resolves to a location outside the bundle root is
  external. Consumers MUST NOT read it from the local file system."
  [source: spec] (§6)
- Targets are RFC 3986 URI references: "A value that contains a space or
  another reserved character MUST be percent-encoded". Paths are
  case-sensitive. [source: spec] (§6)
- Path fields are `resource`, `sources[].resource`, `computation`,
  `executor.resource`, `attester.resource` and `superseded_by`. A path
  field that resolves to a concept is a graph edge. [source: spec] (§6.2, §6.4)
- Only `[text](target)` and reference-style links are links; "`[[wikilinks]]`,
  are not links in OKF". Consumers MUST accept broken links. [source: spec] (§6.1)

### Actors (§7)

- An actor is `<producer>/<version>` or `<scheme>:<id>`, where "The `id`
  is an opaque string". OKF defines `human:`, `team:` and `process:`.
  "Producers MAY use other schemes. Consumers MUST accept other schemes and
  treat them as non-human." A `team:` verifier gives machine-confirmed.
  [source: spec] (§7) *0.2 named only `human:` and `process:` and the
  `<producer>/<version>` form.* [source: spec-v02] (§7)

### Index and log files (§8, §9)

- A subdirectory `index.md` "has no frontmatter". A bundle-root `index.md`
  MAY carry `okf_version`, `title`, `description`, `license` and extension
  keys. *0.2 allowed only `okf_version`.* [source: spec] (§8, §8.1)
  [pins: tests/test_okf.py::TestIndexFrontmatterAudit::test_root_index_may_carry_bundle_metadata, tests/test_okf.py::TestIndexFrontmatterAudit::test_folder_index_with_a_block]
- "A producer that regenerates `index.md` MUST keep the existing
  frontmatter of the bundle-root `index.md`." [source: spec] (§8.1)
  [pins: tests/test_okf.py::TestReservedFrontmatterPolicy::test_existing_frontmatter_is_preserved_unconfigured]
- `log.md` date headings MUST be `YYYY-MM-DD`; "`log.md` has no
  frontmatter." *The no-frontmatter sentence is new in 0.3.*
  [source: spec] (§9)
  [pins: tests/test_okf.py::TestBuildLogMarkdown::test_groups_by_date_newest_first, tests/test_okf.py::TestLogFileAudit::test_log_frontmatter_is_a_log_finding, tests/test_okf.py::TestLogFileAudit::test_non_date_heading_is_reported]

### Conformance (§11)

- A bundle conforms when (1) "Each frontmatter block in a `.md` file under
  the bundle root (§3.2) parses as a YAML mapping" and (2) "Each reserved
  file (`index.md`, `log.md`) agrees with §8 and §9 when it is present".
  [source: spec] (§11.1) *0.2's rules required a parseable frontmatter block in
  every non-reserved `.md` file and a non-empty `type` in it; 0.3 "does not
  require frontmatter in every `.md` file".* [source: spec-v02] (§11)
  [source: spec] (§13.2)
  [pins: tests/test_okf.py::TestLogFileAudit::test_unparseable_log_block_is_both_findings, tests/test_okf.py::TestIndexFrontmatterAudit::test_root_index_block_not_a_mapping_is_unparseable, tests/test_okf.py::TestNonConceptAudit::test_note_block_that_is_not_a_mapping_is_unparseable]
- §11.2 consumer rules, for absent, empty or unexpected values: "A null
  value, an empty string, and an empty list are the same as an absent
  key"; for a list field (`tags`, `sources`, `verified`, `parameters`)
  "consumers MUST accept one item as a list with one item"; a scalar `tags`
  is one tag, never split at commas; an entry missing its REQUIRED key is
  ignored with a SHOULD warn. [source: spec] (§11.2)

### Versioning (§12, §13)

- Before 1.0 a minor version MAY break; §13 MUST list each breaking
  change, "with a consumer fallback where one exists". A consumer that does not know the declared version SHOULD try
  best-effort consumption. [source: spec] (§12)
- §12.1 names two fallbacks, both MAY, for a bundle declaring 0.2 or
  earlier, or no version, and names no others: resolve a relative path field from the
  root when it fails from its file, and the v0.1 `timestamp` / `# Citations`
  fallbacks. [source: spec] (§12.1)
- §13.1 lists two breaking changes: relative path fields resolve from the
  file's directory, and only current verification events count.
  [source: spec] (§13.1)

## The one-rule-set question

#2403 asks whether a bundle declaring 0.2 must still be served by 0.2's
rules. What the text says, per option, without choosing:

- **One rule set (0.3 for every bundle).** §13.2 says date-only values
  "stay valid", and §13 frames both breaks as changes to how a consumer
  reads v0.2 values: "Both alter how a consumer reads a v0.2 value." The
  trust-tier break applies 0.3's reading to 0.2 bundles on purpose:
  "None necessary. The tier can only go down, which is the safe
  direction." [source: spec] (§13, §13.1, §13.2)
- **Two rule sets (0.2 rules for a 0.2 declaration).** The text keys
  behaviour to the declaration in one place: §6.2's "Compatibility with
  v0.2" lets a consumer resolve a relative path field from the root when
  "the bundle declares `okf_version` 0.2 or earlier or declares no
  version", and §12 counts "a change to how a consumer reads an existing
  value" as breaking. §12.1 names its two fallbacks and is silent on any
  other 0.2 reading. [source: spec] (§6.2, §12, §12.1) Against this, "0.2" names two texts the declaration cannot tell apart
  ([v0.2 page](okf-v0.2.md), "Conformance and versioning"), so a 0.2 rule
  set is itself a choice between July and August.

**Decision (account holder, 2026-11-21): one rule set.** Every bundle is
read by 0.3's rules whatever it declares, and §12.1's optional fallbacks are
not applied. In the account holder's words: "We will not apply fall back
then"; keeping 0.2 rules or a migration step would be "keeping separate
logic for an outdated unstable spec"; "the user does not get a vote in
staying at 0.2, because for all purposes 0.2 became irrelevant". A declared
`okf_version` still turns detection on, but its value selects no rules.

The wider policy, also stated that day: the server follows OKF's
versioning, and until a stable 1.0 it reads and writes one OKF version
only. Moving to a newer OKF version is a server upgrade and is expected to
be a breaking change. Both ownership positions apply: as a consumer the
server reads every bundle as the current version; as the operator-designated
maintainer of the reserved files it also writes the current version into
the root `index.md`.

## Where this server departs

Behaviour is [observed: `uv run python` fixtures over `derive_annotation`,
`matches_okf_filters`, `audit_bundle`, `parse_frontmatter`,
`_resolve_link_path` and `build_plain_destination` on main at 26ccd98b,
2026-11-21]. "Level" is the 0.3 keyword the behaviour breaks. A departure
has no test that honours the spec. Tests that pin the current behaviour
are named where they exist; a fix rewrites them.

| # | Rule | 0.2 July | 0.2 Aug | 0.3 | Server | Level | Issue |
|---|------|----------|---------|-----|--------|-------|-------|
| 1 | Trust tier from current events only (§5.3) | every event counts | every event counts | required | fixed: counts only current events; `generated` without a usable `at` makes none current (recorded interpretation in `okf.md`) | MUST | #2409 |
| 2 | Actor schemes (§7) | 2 schemes | 2 schemes | any `<scheme>:<id>`, id opaque | fixed: any string with a `:` or `/` is an actor; `team:x`, `svc:x` and an id with a tab are machine-confirmed, only `human:` gives human-reviewed | MUST | #2407 (fixed) |
| 3 | Unparseable `stale_after` (§5.5) | silent | silent (PR #323: the reference agent ignores offset-less values) | never fresh | fixed by #2410: `stale: true`, so `stale=false` excludes it and it is downweighted | MUST NOT | #2410 (fixed) |
| 4 | Date-only `stale_after` (§5) | date, no time zone | invalid | 00:00 UTC | fixed by #2404: read as 00:00 UTC (was the server-local day, stale an hour early at 00:30+01:00) | defined (no keyword) | #2404 (inverted) |
| 5 | Date-only `verified[].at`, `generated.at` (§5) | datetime (offset unstated) | invalid | valid, 00:00 UTC | fixed by #2404: recorded, read as 00:00 UTC (was malformed, the event ignored and the tier unverified) | valid form (no keyword) | #2400 (inverted) |
| 6 | Null/empty = absent (§11.2) | silent | silent | required | fixed: null, `""` and `[]` read as absent in the annotation and the audit (was: null `stale_after` / `generated` malformed; empty `verified` reported `empty`) | stated (no keyword) | #2411 (fixed) |
| 7 | One item as a list (§11.2) | `verified` only | `verified` only | every list field | fixed: one `sources` item is a one-item list (was: a `sources` mapping is malformed) | MUST | #2412 (fixed) |
| 8 | `type` not a conformance rule (§3.2, §11.1) | required | required | not required; SHOULD warn | fixed: `missing_type` is the §3.2 warning; `total_notes` counts concepts | stricter | #2406 (fixed) |
| 9 | Reserved files under rule 1 (§11.1) | n/a | n/a | every `.md` | fixed: an unparseable block in any reserved file is `unparseable_frontmatter` | lenient | #2413 (fixed) |
| 10 | Root `index.md` keys (§8.1) | `okf_version` | `okf_version` | + title, description, license, extensions | fixed: the root may carry any keys; only a folder index's block is `index_frontmatter` | stricter | #2414 (fixed) |
| 11 | `log.md` frontmatter (§9) | silent | silent | none | audit fixed (`log_frontmatter`, #2413); generators seed it under `required_frontmatter` | lenient / producer | #2413 (audit), #1438 (032) |
| 12 | Log heading shape (§9, §11.1 rule 2) | MUST + conformance | MUST + conformance | MUST + conformance | fixed: `log_heading_shape` is a conformance finding | lenient | #2413 (fixed) |
| 13 | YAML 1.2 core schema (§4) | silent | silent | MUST | fixed: read by the §10.3.2 core schema on PyYAML's C loader, so `yes`, `1_000`, `1:30` and dates stay strings and `0o17` is 15; writes quote for 1.1 and 1.2 readers (was PyYAML 1.1: `yes`/`on` → `True`, `1_000` → 1000, `0o17` → string) | MUST | #2415 (fixed) |
| 14 | Out-of-root targets are external (§6) | silent | silent | MUST NOT read locally | fixed: external and reported broken (account holder, 2026-11-21); stored as `../x.md`, never resolved or read. It was clamped, so `../../x.md` from `a/n.md` resolved to `x.md` | MUST NOT | #2416 (fixed) |
| 15 | Written targets are RFC 3986 URI references (§6) | silent | silent | MUST (space, reserved chars); RFC 3986 for `%` | fixed: `50%25%20done/a%23b%20(c).md` (was `50%%20done/a\#b%20(c).md`) | MUST | #2417 (fixed) |
| 16 | `verified` kept as history (§5.2, §5.3) | "independent" | "independent" | "stay in the file" | fixed: kept on the server's own `write`/`edit`; the write moves `generated.at` | stated (no keyword) | #2405 (fixed) |
| 17 | Trivial edit keeps `generated.at` (§5.2) | silent | silent | stated | every `write`/`edit` restamps | producer rule | none |
| 18 | No re-serialising unchanged values (§4) | silent | silent | SHOULD NOT | stamps and `okf_verify` re-dump the whole block, dropping comments | SHOULD NOT | none |
| 19 | Unknown `status` (§5.4) | silent | silent | not stable | fixed: any value outside the exact tokens, a non-string one included, is shown as its raw text (`3` → `"3"`) and is not `stable` | stated | #2420 |
| 20 | `superseded_by` (§5.4) | absent | absent | SHOULD follow | not read | SHOULD | none |
| 21 | Known versions (§12) | — | — | `0.3` | fixed: `KNOWN_OKF_VERSIONS` is `0.1`, `0.2`, `0.3`; the value selects no rules; any other value warns, then reads best-effort | none broken | #2422 (fixed) |
| 22 | `okf_version` on a concept (§4.1, §8.1) | silent | silent | an extension key; MUST NOT reject unknown keys | fixed: no finding (was `misplaced_okf_version`, a conformance finding); on a folder `index.md` it is `index_frontmatter` | MUST NOT | #2427 (fixed) |
| 23 | RFC 3339 `date-time` only (§5) | silent | silent | the `date-time` rule; "No other form is valid" | fixed: seconds, a `.` fraction and a `T` separator required, lower-case `t`/`z` and a `:60` leap second accepted (was: `14:00Z`, `,5` fractions and a space accepted; the checker took any `fromisoformat` form) | stated (no keyword) | #2428 (fixed) |

Notes on the table:

- **#1 and #16 are coupled.** Clearing `verified` on write hid #1 for the
  server's own writes. Stopping the clear (#2405) without the currency rule
  would leave a rewritten note human-reviewed, the outcome §5.3 exists to
  prevent. The currency rule is safe alone; the clear's removal is not, so
  #2405 lands after it.
- **#4 and #5 invert their issues.** #2404 asks the server to ignore a bare
  date (the August 0.2 text); 0.3 makes it valid at 00:00 UTC. #2400 asks
  for this bundle's bare dates to be treated as malformed; under 0.3 they
  are valid and the audit is what departs.
- **#8 dissolves #2406.** 0.3 conformance never asks a reserved file, or
  any file, for a `type`, so the exemption #2406 calls a departure is what
  §3.1 and §11.1 now say. The departure that remains is the audit treating
  a typeless file as non-conformant instead of a non-concept with a warning.
- **#2 widened #2407.** The fix is any scheme, any opaque id, non-`human:`
  as non-human. Tabs and spaces were only part of it. §7 defines no
  grammar for a scheme, producer or version, so the server reads any
  string with a `:` or `/` as an actor, empty parts included.
- **#16 rests on §5.3's reasoning, not a keyword.** No 0.3 sentence forbids
  a producer from clearing `verified`; the case is that §5.3 keeps earlier
  events as history and makes clearing unnecessary.
- **#1 needs a decision the spec does not make:** whether an event is
  current when `generated` is present but has no `at` (§5.3 covers only an
  absent `generated`). #2409 reads it as "no event is current" and records
  that as an interpretation in `okf.md`, pending the account holder's
  confirmation.
- **#17 is a producer judgement.** The server cannot tell a trivial edit
  from a meaningful one without a caller signal. Recorded as a departure
  because the rule is stated; whether a write flag is in scope is open.
- **#21 keeps 0.1 and 0.2 known.** §12 asks best-effort consumption, not
  refusal, of a version the consumer "does not know"; the server's
  `okf_unknown_version` warning is its own signal for that case. The 0.3
  text names "0.2 or earlier" and says how a 0.3 consumer reads those
  values (§12.1, §13), so a 0.2 declaration is known, read by 0.3's rules,
  and logs nothing. Only a value outside `0.1`–`0.3` logs the warning.
- **Not departures:** the ranking weights (§5 of `okf.md`; the spec is
  silent on ranking), wikilinks reported rather than rejected (the spec
  says they are not links, not that a bundle with them is non-conformant),
  root-relative links written by `okf_convert_links` (both forms valid),
  and `-` list markers in a generated index (the spec's `*` is an example).

## Not covered

- Rules named by the refute pass and not yet checked against the server:
  the opening line "MUST start with a line that is exactly `---`" (the
  server accepts a BOM first, #1407); "Producers SHOULD NOT use custom YAML
  tags"; the `x-<producer>-<name>` key SHOULD; "Producers MUST NOT use
  them for concepts" (§3.1) on writes of `index.md` / `log.md` carrying a
  `type`; "producers MUST use `human:` for content that a person wrote or
  confirmed"; "Producers SHOULD NOT make two paths that differ only by
  case"; §6.1's rule that a link refers to a concept only when its
  fragment-less target is a concept file; the §8.1 SHOULD to put a root
  `index.md` with a quoted `okf_version` in each bundle; §5.5's "Do not
  invent one" against `required_frontmatter` seeding; and §11.2's "SHOULD
  calculate staleness only from `stale_after`". [source: spec] (§3.1, §4,
  §4.1, §5.5, §6, §6.1, §7, §8.1, §11.2)
- A tension inside the spec: the YAML 1.2 core schema (§4) has no
  timestamp type, yet §5 says consumers "MUST accept a quoted string and a
  YAML-native timestamp". Rows 4, 5 and 13 assume both hold.
  [source: spec] (§4, §5)

- §10 Attested Computations and the `usage_*` signals: nothing reads them.
- The bundle root is assumed to be the vault root; §2 lets a bundle be a
  subdirectory identified by its root `index.md`. Not checked.
- Dot-path and out-of-root symlink handling (§3.2 rules 4 and 5) in
  `iter_markdown_files` was not exercised by fixture. [unverified: run the
  audit over a vault with a `.hidden/x.md` and an outward symlink]
- Index entry format details beyond the description (§8 SHOULD).
