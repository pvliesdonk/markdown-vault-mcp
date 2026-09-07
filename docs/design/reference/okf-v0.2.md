---
type: Reference
title: Open Knowledge Format v0.2
description: What the OKF v0.2 spec requires of a bundle and its consumers (frontmatter families, trust tiers, staleness, reserved files, links, conformance), as published in July 2026 and as amended in place on 2026-08-21
subject_version: "0.2 (spec commit 62432a09, 2026-08-21)"
valid_for: "OKF 0.2 as amended 2026-08-21; re-research on any later commit to okf/SPEC.md or a 0.3 draft"
generated:
  by: process:researching-references
  at: 2026-09-06T21:59:57+02:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-06T22:11:28+02:00
stale_after: 2027-03-06
status: stable
sources:
  - id: spec
    title: Open Knowledge Format (OKF), Version 0.2, okf/SPEC.md at commit 62432a09 (2026-08-21)
    resource: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/62432a0954/okf/SPEC.md
    accessed: 2026-09-06
  - id: spec-july
    title: Open Knowledge Format (OKF), Version 0.2, okf/SPEC.md at commit 3fcbb9f8 (2026-07-24, the text v0.2 was published with)
    resource: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/3fcbb9f828/okf/SPEC.md
    accessed: 2026-09-06
  - id: pr-323
    title: knowledge-catalog PR #323, "okf: make every timestamp an ISO 8601 datetime with an explicit offset" (merged 2026-08-21)
    resource: https://github.com/GoogleCloudPlatform/knowledge-catalog/pull/323
    accessed: 2026-09-06
  - id: issue-242
    title: knowledge-catalog issue #242, "stale_after comparison has no timezone anchor, so staleness depends on where a bundle is read" (open)
    resource: https://github.com/GoogleCloudPlatform/knowledge-catalog/issues/242
    accessed: 2026-09-06
---

# Open Knowledge Format v0.2

What the OKF specification (`okf/SPEC.md` in
`GoogleCloudPlatform/knowledge-catalog`) requires of a bundle and of a
consumer, scoped to the fields and files this server reads, writes, audits
and ranks on. Written after #1357, which corrected two read-layer departures
on the word of a digest of the spec rather than the spec itself; this page
reads the spec, and finds that the spec has moved since that digest was
made. The document still calls itself "Version 0.2", but its text was
amended in place on 2026-08-21 [source: pr-323]; where the July and August
texts differ, both are recorded. The spec calls its field one that "is
evolving quickly" and keeps a "Considered and deferred" list for the next
revision [source: spec] (§1, §12), it was amended without a version bump,
and the repository carries no tags, so this page's `stale_after` is six
months, not twelve.

## Scope

- Covers: bundle structure and reserved filenames (§3), concept
  frontmatter (§4.1), the provenance/trust/lifecycle families (§5), the
  actor convention (§7), links and paths (§6), index and log files (§8, §9),
  conformance and versioning (§11–§13).
- Does not cover: Attested Computations (§10) and per-source credibility
  signals (`usage_count`, `usage_window`, §5.1) — nothing here reads them;
  the v0.3 proposal.
- Depended on by: `src/markdown_vault_mcp/okf.py` (detection, annotations,
  filters, ranking weights, the audit, index/log builders, write stamps),
  `_okf_write.py` and `_okf_convention.py` (write-side stamping and log
  maintenance), `okf_bundle.py` (export), `_server_tools/reader.py` and
  `writer.py` (the `okf_*` tools); `docs/design/okf.md` decides what this
  project does with these rules.

## Claims

### Bundle structure and reserved files (§3, §8, §9)

- A bundle is a directory tree of markdown files; structure is the
  producer's choice. `index.md` and `log.md` "have defined meaning at any
  level of the hierarchy and MUST NOT be used for concept documents"; every
  other `.md` file is a concept. [source: spec] (§3.1)
  [pins: tests/test_okf.py::TestOkfAudit::test_mixed_vault_report]
- There is no tag file format; a consumer wanting a tag view synthesises it
  from frontmatter. [source: spec] (§3.1)
- An `index.md` "MAY appear in any directory"; index files "contain no
  frontmatter, with one exception: a bundle-root `index.md` MAY carry an
  `okf_version` key". The body is sections of `* [Title](url) - description`
  entries; producers MAY generate one, consumers MAY synthesise one when
  none is present. [source: spec] (§8, §12)
  [pins: tests/test_okf.py::TestOkfDetector::test_declared_auto_active, tests/test_okf.py::TestBuildIndexMarkdown::test_entries_with_and_without_description]
- A `log.md` MAY appear at any level; it is "a flat list of date-grouped
  entries, newest first"; "Date headings MUST use ISO 8601 `YYYY-MM-DD`
  form"; the leading bold word of an entry is a convention, not a
  requirement. [source: spec] (§9)
  [pins: tests/test_okf.py::TestBuildLogMarkdown::test_groups_by_date_newest_first, tests/test_okf.py::TestAppendOkfLogEntry::test_inserts_new_day_section_on_top]
- The August amendment left the log's date headings alone: "`## 2026-05-22`
  groups a day's entries and is not a field value." [source: pr-323]

### Concept frontmatter (§4.1)

- `type` is "the only always-required key; a concept carrying just `type`
  is fully conformant". Values are not registered; "consumers MUST tolerate
  unknown types gracefully". [source: spec] (§4.1)
  [pins: tests/test_okf.py::TestOkfAudit::test_mixed_vault_report]
- `title`, `description`, `resource` and `tags` are recommended; a consumer
  "MAY derive a title from the filename" when `title` is absent.
  [source: spec] (§4.1)
- Producers "MAY include any additional keys"; consumers "SHOULD preserve
  unknown keys when round-tripping and MUST NOT reject documents with
  unrecognized fields". [source: spec] (§4.1)
  [pins: tests/test_okf_write.py::TestApplyOkfWriteStamp::test_preserves_sources_and_other_fields, tests/test_okf.py::TestOkfAudit::test_mixed_vault_report]

### Trust: `generated`, `verified`, tiers (§5.2, §5.3, §7)

- `generated: { by, at }` records how the current content was produced;
  `generated.by` is required within `generated` and is an actor;
  `generated.at` is "an ISO 8601 datetime marking the content's last
  meaningful change". [source: spec] (§5.2) [source: spec-july] (§5.2)
- `verified` is "a list of verification events, each with `by` (an actor)
  and `at` (an ISO 8601 datetime)"; it "is independent of `generated.at`:
  content can change without re-confirmation, and facts can be re-confirmed
  without regeneration". [source: spec] (§5.2)
- "A single verifier MAY be written as one `{ by, at }` mapping without
  the list dash. Consumers MUST treat a bare mapping as a one-element
  list" — restated as a consumer MUST in §11. [source: spec] (§5.2, §11)
  [pins: tests/test_okf.py::test_derive_trust_tier, tests/test_okf_write.py::TestAppendOkfVerification::test_a_bare_mapping_is_kept_as_the_first_entry, tests/test_okf_write.py::TestOkfVerifyTrustAuth::test_counts_a_bare_mapping_as_one]
- Tiers, lowest to highest: no `verified` key ⇒ unverified; "`verified` by
  non-`human:` actors only ⇒ machine-confirmed"; "`verified` by a
  `human:<id>` actor ⇒ human-reviewed". "Trust tiers are advisory signals,
  not access control." [source: spec] (§5.3)
  [pins: tests/test_okf.py::test_derive_trust_tier]
- Actors: `<producer>/<version>` for agents and tools, `human:<id>` for a
  person, `process:<id>` for an automated process; "producers MUST use
  [`human:`] for hand-authored or human-confirmed content" because
  consumers key trust off that prefix. [source: spec] (§7)
  [pins: tests/test_okf_write.py::TestActorResolution::test_tool_actor_format, tests/test_okf_write.py::TestApplyOkfWriteStamp::test_human_actor_is_recorded_verbatim]
- The spec says nothing about a tier for a `verified` entry that carries no
  `by`, or an empty list; "non-`human:` actors only" reads naturally as
  machine-confirmed for `[{}]`, which is what the server does.
  [unverified] A spec-maintainer answer or a reference-agent fixture would
  settle it.

### Lifecycle: `status` and `stale_after` (§5.4, §5.5)

- `status` is `draft | stable | deprecated`; "Absent `status` ⇒ `stable`";
  `deprecated` is "kept for links and history; no longer current".
  [source: spec] (§5.4)
  [pins: tests/test_okf.py::TestDeriveAnnotation::test_empty_metadata_defaults, tests/test_okf.py::TestDeriveAnnotation::test_unknown_status_passes_through]
- **July text:** `stale_after` is "an absolute date (`YYYY-MM-DD`). A
  concept is stale when `today >= stale_after`". [source: spec-july] (§5.5)
  [pins: tests/test_okf.py::test_derive_stale]
- **August text:** "Every timestamp-valued key in OKF is an ISO 8601
  datetime with an explicit UTC offset, for example
  `2026-06-30T14:00:00Z`", stated once in the §5 preamble; `stale_after` is
  "an absolute instant. A concept is stale when `now >= stale_after`".
  [source: spec] (§5, §5.5)
  [pins: tests/test_okf.py::test_derive_stale]
- The amendment's reason: a bare date "names a different instant in every
  timezone, so the same bundle can be stale in one office and fresh in
  another" (filed upstream as issue #242, still open). "Consumers are
  expected to be strict rather than lenient about a value that arrives
  without an offset: a midnight-UTC fallback would quietly reintroduce the
  disagreement this change exists to remove. The reference agent ignores
  such a value, and the demo's storage layer rejects it outright."
  [source: pr-323] [source: issue-242]
- The amendment also records that PyYAML's YAML 1.1 loader coerces
  `2026-06-30T14:00:00Z` into a `datetime` and dumps it back as
  `2026-06-30 14:00:00+00:00`, corrupting `generated.at` on rewrite; the
  reference agent now keeps every frontmatter value as the string the
  author wrote. [source: pr-323] [observed: `yaml.safe_load` on
  `stale_after: 2026-09-23T00:00:00Z` yields a `datetime`, on the quoted
  form a `str`; python-frontmatter uses the same loader]

### Provenance: `sources` (§5.1)

- `sources` is a list of entries in which `resource` is "REQUIRED within an
  entry" and `id`, `title`, `author`, `usage_count` and `last_modified` are
  optional; the server passes the list through on reads and counts it on
  search hits. [source: spec] (§5.1)
  [pins: tests/test_okf.py::TestDeriveAnnotation::test_read_mode_includes_sources, tests/test_okf.py::TestDeriveAnnotation::test_search_mode_counts_sources]
- Per-claim attribution is a markdown footnote whose label is a
  `sources[].id`, "keyed rather than positional". Nothing here depends on
  it. [source: spec] (§5.1)

### Links and paths (§6)

- Concepts link with "standard markdown links" in two forms: absolute
  (bundle-relative, beginning with `/`, "the **recommended** form") and
  relative. Wikilinks are not among them. [source: spec] (§6.1)
  [pins: tests/test_okf.py::TestConvertWikilinks::test_basic_and_aliased_and_skipped, tests/test_okf.py::TestConvertWikilinks::test_markdown_links_untouched]
- "Consumers MUST tolerate broken links: a link whose target does not exist
  in the bundle is not malformed". [source: spec] (§6.1)

### Conformance and versioning (§11, §12, §13)

- A bundle is conformant when every non-reserved `.md` file has parseable
  frontmatter with a non-empty `type`, and every reserved file present
  follows §8/§9. Consumers MUST NOT reject a bundle for missing optional
  fields, unknown types, unknown keys, broken links or missing `index.md`.
  [source: spec] (§11)
  [pins: tests/test_okf.py::TestOkfAudit::test_mixed_vault_report]
- `okf_version: "0.2"` MAY be declared in the bundle-root `index.md`, "the
  only place frontmatter is permitted in an `index.md`"; a consumer that
  does not understand the declared version "SHOULD attempt best-effort
  consumption rather than refusing the bundle". [source: spec] (§12)
  [pins: tests/test_okf.py::TestOkfDetector::test_unknown_version_warns_once_and_detects, tests/test_okf.py::TestOkfDetector::test_unquoted_yaml_scalar_version]
- v0.2 supersedes v0.1 with two breaking renames — `timestamp` →
  `generated.at` (consumers MAY fall back to `timestamp`) and the body
  `# Citations` list → `sources` — plus the additive families this page
  documents: `sources`, `generated`/`verified`, `status`/`stale_after`, the
  `Attested Computation` type and the actor convention. Bundle structure,
  reserved filenames, the required `type`, the recommended keys,
  cross-linking, index and log files and the permissive conformance rule
  are "carried forward unchanged". [source: spec] (§13)
- The 2026-08-21 timestamp amendment changed the text of "Version 0.2"
  without a version bump: the header still reads 0.2, `okf_version: "0.2"`
  still declares it, and the repository carries no tags. [observed:
  `gh api repos/GoogleCloudPlatform/knowledge-catalog/tags` is empty; the
  commit list for `okf/SPEC.md` is 2026-06-12, 2026-07-24 ×2, 2026-08-21]
  So a bundle declaring 0.2 may follow either text, and a consumer cannot
  tell which from the declaration.

## Where this project departs from the subject

Behaviour lines are [observed: `okf.py`, `_okf_write.py` on the working
tree, 2026-09-07].

- **Both texts' `stale_after` forms are honoured, and the bare date is
  kept.** `derive_stale` compares an instant with an offset as an instant
  (`now >= stale_after`, the August rule) and a bare date against the
  server-local calendar day (the July rule, #1357). Against the August
  text this is lenient on purpose: the amendment says to ignore an
  offset-less value, but a bare date is what the July text prescribed and
  what an Obsidian author writes, and upstream #242 is still open. A
  datetime *without* an offset — allowed by neither text — is ignored.
  Decided in `docs/design/okf.md` § 3 (#1373).
  [pins: tests/test_okf.py::test_derive_stale, tests/test_okf.py::test_derive_stale_bare_date_uses_the_server_local_day]
- **Write stamps are dates.** `apply_okf_write_stamp` and
  `append_okf_verification` write `generated.at` and `verified[].at` as
  `YYYY-MM-DD` (`today.isoformat()`), where both texts of v0.2 say "an ISO
  8601 datetime" and the August text adds "with an explicit UTC offset".
  #1372, filed from this page.
- **`verified` is cleared on a content-changing write.** The spec keeps
  `verified` "independent of `generated.at`: content can change without
  re-confirmation"; the server's enforced write layer clears it so an
  attestation cannot outlive the bytes it attested. Deliberate; decided in
  `docs/design/okf.md` § 6.
  [pins: tests/test_okf_write.py::TestInvalidationMatrix::test_body_write_clears_verified_and_stamps, tests/test_okf_write.py::TestInvalidationMatrix::test_rename_preserves_verified]
- **Reserved files are exempt from the `type` rule in the audit and the
  stats** (#1251, #962). The spec's conformance rule 2 applies to "every
  frontmatter block", but reserved files carry none by §8, so exempting
  them is the spec's own consequence rather than a departure.
- **A generated reserved file carries frontmatter when the operator requires
  it.** §8 allows frontmatter in an `index.md` only for the bundle root's
  `okf_version`, and none in `log.md`. With `required_frontmatter`
  configured, the server's own `index.md` / `log.md` generators
  (`ReservedFrontmatterPolicy` in `okf.py`) seed the configured keys — the
  title field with the file's title, any other as `null` — because a
  generated file with no frontmatter would fail the same index gate the
  server enforces and vanish from `search` and `list_documents` (#1174,
  #1175). Existing keys, including the root `okf_version`, are preserved;
  `okf_version` is never synthesised into a folder index. With nothing
  required, the files are written body-only as §8 and §9 have them.
  Deliberate; decided in `docs/design/okf.md`, "Generated reserved files
  and the index gate".
  [pins: tests/test_okf.py::TestReservedFrontmatterPolicy::test_title_field_is_seeded_with_the_derived_title, tests/test_okf.py::TestReservedFrontmatterPolicy::test_other_required_fields_are_seeded_as_null, tests/test_okf.py::TestReservedFrontmatterPolicy::test_existing_frontmatter_is_preserved_unconfigured]
- **This bundle's own `stale_after` is a calendar date.** The
  template-owned `scripts/check_references.py` requires
  `stale_after` to be `YYYY-MM-DD` and reads `verified` only as a list, both
  the July text's shape; the pages' `generated.at` / `verified[].at` are
  offset datetimes as both texts require. Filed upstream as
  pvliesdonk/fastmcp-server-template#603; the plugin digest #1357 followed
  is pvliesdonk/claude-plugins#47.
- **Ranking downweights** deprecated (×0.5), stale (×0.75) and reserved
  (×0.5) notes when a bundle is detected. The spec keeps `status` and
  staleness as advisory annotations and says nothing about ranking; trust
  tiers are deliberately *not* boosted, matching "advisory signals, not
  access control". Decided in `docs/design/okf.md` § 5.
- **Wikilinks are reported, not rejected.** The audit lists notes that
  contain wikilinks as informational, and `okf_convert_links` rewrites them
  to root-relative markdown links at export; a bundle with wikilinks is
  still served. The spec names only markdown links (§6.1) but does not
  forbid other syntax.
- **`okf_version` accepted values** are `0.1` and `0.2`; any other declared
  version warns once and is still detected (§12's best-effort rule).

## Not covered

- Attested Computations (§10) and the `sources[]` credibility signals
  (`usage_count`, `usage_window`): nothing here reads or writes them.
- Whether `generated.at`, once written as a datetime, should carry the
  server's local offset or UTC; the spec only requires an explicit offset.
- The v0.1 fallbacks (`timestamp`, `# Citations`): the server neither reads
  nor migrates them, and no test covers a v0.1 bundle.
