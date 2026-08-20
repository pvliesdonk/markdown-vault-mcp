# CHANGELOG

<!-- version list -->

## 4.0.0-rc.5 (2026-08-20)

### Breaking Changes

- replace anthropic summarizer with generic OpenAI-compatible backend (#917)
- dual-mode background jobs via pvl-core 4.11.0 + template v3.3.0 (#1034)
- make reindex and build_embeddings dual-mode background jobs (#1037)
- adopt the branch-aware release model via copier update to template v4.0.0 (#1066)
- follow redirects, report the final URL, and classify the change (#1118)
- default READ_ONLY to false so the write surface ships enabled (#1119)

### Features

- add LLM-backed summarize tool gated on an API key (#869)
- folder conventions — per-folder authoring policy for LLM clients (#877)
- configurable title field, searchable frontmatter, and ranking weights (#867)
- map-reduce summarization for inputs beyond one model request (#924)
- per-call max_notes with in-result coverage hint for summarize (#926)
- rebuild on INDEXED_FIELDS change; SEARCHABLE_FIELDS defaults to it (#928)
- bound summarize latency with a timeout error and background jobs (#937) (#938)
- detect OKF bundles and annotate read surfaces (#968)
- OKF filter dimensions and graph node typing (#970)
- okf_validate conformance audit tool (#971)
- migration transforms (wikilink conversion, index/log generation) (#973)
- adopt fastmcp-pvl-core transfer subsystem; retire local store (#981)
- bundle export via an okf-bundle download ref (#982)
- enforced write layer — provenance stamping, verification invalidation, okf_verify (#964) (#984)
- enforced-write convention maintenance — log.md append + index.md refresh (#964) (#987)
- ranking downweights for deprecated/stale/reserved files (#965) (#988)
- configurable timeout (EMBED_TIMEOUT_S) + batch size (EMBEDDING_BATCH_SIZE) (#1000)
- harden okf_verify against model self-attestation (#990) (#1004)
- scope okf_seed_log's log.md content to the folder's subtree (#974) (#1005)
- add append tool for end-of-note writes without a prior read (#1025)
- ship client-side summarization recipe as summarize-subtree prompt (#1038)
- adopt template v3.4.0 — generated mcpb screen, curated fields, pre-release checks (#1045)
- adopt template v3.5.0 — plugin channel onto the template scaffold (#1046)
- vault-summarize skill and vault-mapper agent for client-side summarization (#1047)
- vault-setup skill and SessionStart doctor hook for in-session bootstrap (#1048)
- generated userConfig configuration screen — no more shell-profile setup (#1049)

### Bug Fixes

- hybrid search vector channel misses folder normalization (#882)
- recover keyword/hybrid search for hyphenated terms (#866) (#884)
- stop reasoning models exhausting the summarize token budget (#920)
- forbid assistant-style offers in summarize output (#923)
- bump transitive mcp 1.28.0 → 1.28.1 (CVE-2026-59950) (#934)
- make incremental reindex inline embedding resilient to provider timeouts (#930) (#932)
- guard inline/converge vector mutation against dimension mismatch (#935) (#936)
- resolve leading-slash markdown links against the vault root (#972)
- skip malformed-frontmatter files in build_embeddings (#994)
- retry failed deferred pushes on the periodic pull-loop tick (#997)
- honour process umask for newly created files (#958) (#998)
- single-agent reviewer; drop fan-out plugin + pin experiment
- drop Task from @claude — #1499 background-subagent orphan footgun
- allow-list Skill defensively (both claude workflows)
- finalize pin-combo reviewer (restore CI gate, drop show_full_output)
- close SSRF gaps by migrating to pvl-core's hardened fetch_url (#1029)
- adopt template v3.2.2 — template-owned bump_manifests, non-mutating CI syncs (#1032)
- drop track_progress from the notes drafting action (#1094)
- make the notes drafting agent able to write — and safe to run (#1095)
- rc releases get their full artifact set, and the 4.0 notes page passes its own evidence contract (#1096)
- clear the v4.0 milestone's bug reports (#1109)
- never send a blank string to the embedding provider (#1112)
- raise the pvl-core floor to 4.11.2 and drop the removed read_only kwarg (#1117)

## 4.0.0-rc.4 (2026-08-19)

### Breaking Changes

- replace anthropic summarizer with generic OpenAI-compatible backend (#917)
- dual-mode background jobs via pvl-core 4.11.0 + template v3.3.0 (#1034)
- make reindex and build_embeddings dual-mode background jobs (#1037)
- adopt the branch-aware release model via copier update to template v4.0.0 (#1066)

### Features

- add LLM-backed summarize tool gated on an API key (#869)
- folder conventions — per-folder authoring policy for LLM clients (#877)
- configurable title field, searchable frontmatter, and ranking weights (#867)
- map-reduce summarization for inputs beyond one model request (#924)
- per-call max_notes with in-result coverage hint for summarize (#926)
- rebuild on INDEXED_FIELDS change; SEARCHABLE_FIELDS defaults to it (#928)
- bound summarize latency with a timeout error and background jobs (#937) (#938)
- detect OKF bundles and annotate read surfaces (#968)
- OKF filter dimensions and graph node typing (#970)
- okf_validate conformance audit tool (#971)
- migration transforms (wikilink conversion, index/log generation) (#973)
- adopt fastmcp-pvl-core transfer subsystem; retire local store (#981)
- bundle export via an okf-bundle download ref (#982)
- enforced write layer — provenance stamping, verification invalidation, okf_verify (#964) (#984)
- enforced-write convention maintenance — log.md append + index.md refresh (#964) (#987)
- ranking downweights for deprecated/stale/reserved files (#965) (#988)
- configurable timeout (EMBED_TIMEOUT_S) + batch size (EMBEDDING_BATCH_SIZE) (#1000)
- harden okf_verify against model self-attestation (#990) (#1004)
- scope okf_seed_log's log.md content to the folder's subtree (#974) (#1005)
- add append tool for end-of-note writes without a prior read (#1025)
- ship client-side summarization recipe as summarize-subtree prompt (#1038)
- adopt template v3.4.0 — generated mcpb screen, curated fields, pre-release checks (#1045)
- adopt template v3.5.0 — plugin channel onto the template scaffold (#1046)
- vault-summarize skill and vault-mapper agent for client-side summarization (#1047)
- vault-setup skill and SessionStart doctor hook for in-session bootstrap (#1048)
- generated userConfig configuration screen — no more shell-profile setup (#1049)

### Bug Fixes

- hybrid search vector channel misses folder normalization (#882)
- recover keyword/hybrid search for hyphenated terms (#866) (#884)
- stop reasoning models exhausting the summarize token budget (#920)
- forbid assistant-style offers in summarize output (#923)
- bump transitive mcp 1.28.0 → 1.28.1 (CVE-2026-59950) (#934)
- make incremental reindex inline embedding resilient to provider timeouts (#930) (#932)
- guard inline/converge vector mutation against dimension mismatch (#935) (#936)
- resolve leading-slash markdown links against the vault root (#972)
- skip malformed-frontmatter files in build_embeddings (#994)
- retry failed deferred pushes on the periodic pull-loop tick (#997)
- honour process umask for newly created files (#958) (#998)
- single-agent reviewer; drop fan-out plugin + pin experiment
- drop Task from @claude — #1499 background-subagent orphan footgun
- allow-list Skill defensively (both claude workflows)
- finalize pin-combo reviewer (restore CI gate, drop show_full_output)
- close SSRF gaps by migrating to pvl-core's hardened fetch_url (#1029)
- adopt template v3.2.2 — template-owned bump_manifests, non-mutating CI syncs (#1032)
- drop track_progress from the notes drafting action (#1094)
- make the notes drafting agent able to write — and safe to run (#1095)
- rc releases get their full artifact set, and the 4.0 notes page passes its own evidence contract (#1096)

## 4.0.0-rc.3 (2026-08-19)

### Breaking Changes

- replace anthropic summarizer with generic OpenAI-compatible backend (#917)
- dual-mode background jobs via pvl-core 4.11.0 + template v3.3.0 (#1034)
- make reindex and build_embeddings dual-mode background jobs (#1037)
- adopt the branch-aware release model via copier update to template v4.0.0 (#1066)

### Features

- add LLM-backed summarize tool gated on an API key (#869)
- folder conventions — per-folder authoring policy for LLM clients (#877)
- configurable title field, searchable frontmatter, and ranking weights (#867)
- map-reduce summarization for inputs beyond one model request (#924)
- per-call max_notes with in-result coverage hint for summarize (#926)
- rebuild on INDEXED_FIELDS change; SEARCHABLE_FIELDS defaults to it (#928)
- bound summarize latency with a timeout error and background jobs (#937) (#938)
- detect OKF bundles and annotate read surfaces (#968)
- OKF filter dimensions and graph node typing (#970)
- okf_validate conformance audit tool (#971)
- migration transforms (wikilink conversion, index/log generation) (#973)
- adopt fastmcp-pvl-core transfer subsystem; retire local store (#981)
- bundle export via an okf-bundle download ref (#982)
- enforced write layer — provenance stamping, verification invalidation, okf_verify (#964) (#984)
- enforced-write convention maintenance — log.md append + index.md refresh (#964) (#987)
- ranking downweights for deprecated/stale/reserved files (#965) (#988)
- configurable timeout (EMBED_TIMEOUT_S) + batch size (EMBEDDING_BATCH_SIZE) (#1000)
- harden okf_verify against model self-attestation (#990) (#1004)
- scope okf_seed_log's log.md content to the folder's subtree (#974) (#1005)
- add append tool for end-of-note writes without a prior read (#1025)
- ship client-side summarization recipe as summarize-subtree prompt (#1038)
- adopt template v3.4.0 — generated mcpb screen, curated fields, pre-release checks (#1045)
- adopt template v3.5.0 — plugin channel onto the template scaffold (#1046)
- vault-summarize skill and vault-mapper agent for client-side summarization (#1047)
- vault-setup skill and SessionStart doctor hook for in-session bootstrap (#1048)
- generated userConfig configuration screen — no more shell-profile setup (#1049)

### Bug Fixes

- hybrid search vector channel misses folder normalization (#882)
- recover keyword/hybrid search for hyphenated terms (#866) (#884)
- stop reasoning models exhausting the summarize token budget (#920)
- forbid assistant-style offers in summarize output (#923)
- bump transitive mcp 1.28.0 → 1.28.1 (CVE-2026-59950) (#934)
- make incremental reindex inline embedding resilient to provider timeouts (#930) (#932)
- guard inline/converge vector mutation against dimension mismatch (#935) (#936)
- resolve leading-slash markdown links against the vault root (#972)
- skip malformed-frontmatter files in build_embeddings (#994)
- retry failed deferred pushes on the periodic pull-loop tick (#997)
- honour process umask for newly created files (#958) (#998)
- single-agent reviewer; drop fan-out plugin + pin experiment
- drop Task from @claude — #1499 background-subagent orphan footgun
- allow-list Skill defensively (both claude workflows)
- finalize pin-combo reviewer (restore CI gate, drop show_full_output)
- close SSRF gaps by migrating to pvl-core's hardened fetch_url (#1029)
- adopt template v3.2.2 — template-owned bump_manifests, non-mutating CI syncs (#1032)
- drop track_progress from the notes drafting action (#1094)
- make the notes drafting agent able to write — and safe to run (#1095)
- rc releases get their full artifact set, and the 4.0 notes page passes its own evidence contract (#1096)

## 4.0.0-rc.2 (2026-08-19)

### Breaking Changes

- replace anthropic summarizer with generic OpenAI-compatible backend (#917)
- dual-mode background jobs via pvl-core 4.11.0 + template v3.3.0 (#1034)
- make reindex and build_embeddings dual-mode background jobs (#1037)
- adopt the branch-aware release model via copier update to template v4.0.0 (#1066)

### Features

- add LLM-backed summarize tool gated on an API key (#869)
- folder conventions — per-folder authoring policy for LLM clients (#877)
- configurable title field, searchable frontmatter, and ranking weights (#867)
- map-reduce summarization for inputs beyond one model request (#924)
- per-call max_notes with in-result coverage hint for summarize (#926)
- rebuild on INDEXED_FIELDS change; SEARCHABLE_FIELDS defaults to it (#928)
- bound summarize latency with a timeout error and background jobs (#937) (#938)
- detect OKF bundles and annotate read surfaces (#968)
- OKF filter dimensions and graph node typing (#970)
- okf_validate conformance audit tool (#971)
- migration transforms (wikilink conversion, index/log generation) (#973)
- adopt fastmcp-pvl-core transfer subsystem; retire local store (#981)
- bundle export via an okf-bundle download ref (#982)
- enforced write layer — provenance stamping, verification invalidation, okf_verify (#964) (#984)
- enforced-write convention maintenance — log.md append + index.md refresh (#964) (#987)
- ranking downweights for deprecated/stale/reserved files (#965) (#988)
- configurable timeout (EMBED_TIMEOUT_S) + batch size (EMBEDDING_BATCH_SIZE) (#1000)
- harden okf_verify against model self-attestation (#990) (#1004)
- scope okf_seed_log's log.md content to the folder's subtree (#974) (#1005)
- add append tool for end-of-note writes without a prior read (#1025)
- ship client-side summarization recipe as summarize-subtree prompt (#1038)
- adopt template v3.4.0 — generated mcpb screen, curated fields, pre-release checks (#1045)
- adopt template v3.5.0 — plugin channel onto the template scaffold (#1046)
- vault-summarize skill and vault-mapper agent for client-side summarization (#1047)
- vault-setup skill and SessionStart doctor hook for in-session bootstrap (#1048)
- generated userConfig configuration screen — no more shell-profile setup (#1049)

### Bug Fixes

- hybrid search vector channel misses folder normalization (#882)
- recover keyword/hybrid search for hyphenated terms (#866) (#884)
- stop reasoning models exhausting the summarize token budget (#920)
- forbid assistant-style offers in summarize output (#923)
- bump transitive mcp 1.28.0 → 1.28.1 (CVE-2026-59950) (#934)
- make incremental reindex inline embedding resilient to provider timeouts (#930) (#932)
- guard inline/converge vector mutation against dimension mismatch (#935) (#936)
- resolve leading-slash markdown links against the vault root (#972)
- skip malformed-frontmatter files in build_embeddings (#994)
- retry failed deferred pushes on the periodic pull-loop tick (#997)
- honour process umask for newly created files (#958) (#998)
- single-agent reviewer; drop fan-out plugin + pin experiment
- drop Task from @claude — #1499 background-subagent orphan footgun
- allow-list Skill defensively (both claude workflows)
- finalize pin-combo reviewer (restore CI gate, drop show_full_output)
- close SSRF gaps by migrating to pvl-core's hardened fetch_url (#1029)
- adopt template v3.2.2 — template-owned bump_manifests, non-mutating CI syncs (#1032)
- drop track_progress from the notes drafting action (#1094)
- make the notes drafting agent able to write — and safe to run (#1095)

## 4.0.0-rc.1 (2026-08-16)

### Bug Fixes

- **fetch**: Close SSRF gaps by migrating to pvl-core's hardened fetch_url (#1029)
  ([#1029](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1029),
  [`3e2bf4b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3e2bf4bb6f48eb75c4b0eb2d956ce7a2671fcd36))

- **release**: Adopt template v3.2.2 — template-owned bump_manifests, non-mutating CI syncs (#1032)
  ([#1032](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1032),
  [`ec9f51d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ec9f51d331b01ec0d4798cebf12728c673a12238))

### Chores

- **copier**: Adopt the branch-aware release model via copier update to template v4.0.0 (#1066)
  ([#1066](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1066),
  [`9f11ca9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9f11ca96e42cbdbfbd3bd985494d5ce17027bc6a))

- **copier**: Update to template v4.1.0 and clear stale rc manifest pins (#1075)
  ([#1075](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1075),
  [`923ea21`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/923ea2114535613467c922262df23b20715dba0a))

### Documentation

- Backfill 3.0 and 1.x release notes, reconstruct CHANGELOG.md in machine-generated form (#1067)
  ([#1067](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1067),
  [`bf848cc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bf848cc2703e7685e094f44a162e63985fa56cfa))

- Bring the CLAUDE.md module map in sync with the source tree (#1062)
  ([#1062](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1062),
  [`91f6a9c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/91f6a9c2e0545a5d362282458f954e4b16aa5f90))

- Correct transfer-links after the pvl-core adoption; document EMBED_TIMEOUT_S (#1061)
  ([#1061](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1061),
  [`37a4b2f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/37a4b2faf244d4e435668eb78245036d432e9e2e))

- Establish docs/releases/ structure with the 3.1 notes page (#1065)
  ([#1065](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1065),
  [`b7b2a47`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b7b2a479df9f7562ca72b66d9cccbad19453ff17))

- Record release-model decision, cut criterion, and remaining map entries (#1080)
  ([#1080](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1080),
  [`2a71091`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2a71091d4c8862f7ac559f281f54ae993c1b19f0))

- Release-notes surface, CLAUDE.md module map, and deployment-guide cleanup (#1076)
  ([#1076](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1076),
  [`0e5df1f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0e5df1fb2fca1bf21de5aeb7427b42dc89d0083f))

- **design**: Describe the branch-aware release model in the spec (#1074)
  ([#1074](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1074),
  [`5297acc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5297acc462f9122e2590662fc8f81d99b238b53b))

### Features

- **index**: Make reindex and build_embeddings dual-mode background jobs (#1037)
  ([#1037](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1037),
  [`da23481`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/da234811b835d005f569c02d9847e73debd1ceae))

- **packaging**: Adopt template v3.4.0 — generated mcpb screen, curated fields, pre-release checks
  (#1045) ([#1045](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1045),
  [`c87e662`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c87e662d71f5d8c6bd2eca6ec16b394f896db8cd))

- **packaging**: Adopt template v3.5.0 — plugin channel onto the template scaffold (#1046)
  ([#1046](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1046),
  [`5197151`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/51971511e4fa59d0ec5d5b786e1c5406ac0fe82e))

- **plugin**: Generated userConfig configuration screen — no more shell-profile setup (#1049)
  ([#1049](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1049),
  [`e9232c4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e9232c4dba06dad07248cd8271c65ac3705182f2))

- **plugin**: Vault-setup skill and SessionStart doctor hook for in-session bootstrap (#1048)
  ([#1048](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1048),
  [`6339fba`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6339fbab3b13f95af2e3041d1d2cfb132fc2f85d))

- **plugin**: Vault-summarize skill and vault-mapper agent for client-side summarization (#1047)
  ([#1047](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1047),
  [`217e192`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/217e19256d92da328f12143250d4efc71c98b90c))

- **prompts**: Ship client-side summarization recipe as summarize-subtree prompt (#1038)
  ([#1038](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1038),
  [`0ee8a11`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0ee8a11d5feda3d100ff741d8c5b9ad7c43c85ae))

- **summarize**: Dual-mode background jobs via pvl-core 4.11.0 + template v3.3.0 (#1034)
  ([#1034](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1034),
  [`469b41c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/469b41c90d6da3075674a85431dd0ab98f3f3b1a))

### Testing

- Stop re-testing pvl-core's SSRF behaviour in the fetch-tool suite (#1031)
  ([#1031](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1031),
  [`9cb80e0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9cb80e01f6003bce909e6bd0fd1de2b5119a15ef))


## 3.2.0-rc.7 (2026-08-12)

### Bug Fixes

- Guard inline/converge vector mutation against dimension mismatch (#935) (#936)
  ([#936](https://github.com/pvliesdonk/markdown-vault-mcp/pull/936),
  [`0f4987a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0f4987a4f4838235c45de02014ed3d79b4b98dd9))

- Honour process umask for newly created files (#958) (#998)
  ([#998](https://github.com/pvliesdonk/markdown-vault-mcp/pull/998),
  [`c227d32`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c227d3245c7758a8bb0b91ff16cff0365496e54d))

- Make incremental reindex inline embedding resilient to provider timeouts (#930) (#932)
  ([#932](https://github.com/pvliesdonk/markdown-vault-mcp/pull/932),
  [`21d682d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/21d682d41830c900093f0571c157e105ba209811))

- **claude**: Drop Task from @claude — #1499 background-subagent orphan footgun
  ([`7137a22`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7137a22c388d7c2ef988c845633ef287165eff1c))

- **deps**: Bump transitive mcp 1.28.0 → 1.28.1 (CVE-2026-59950) (#934)
  ([#934](https://github.com/pvliesdonk/markdown-vault-mcp/pull/934),
  [`f058038`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f058038539db918a0c21bf6bcdf32686d46277c7))

- **embeddings**: Skip malformed-frontmatter files in build_embeddings (#994)
  ([#994](https://github.com/pvliesdonk/markdown-vault-mcp/pull/994),
  [`7204e84`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7204e848faa527ae76fd5ca3af03573b55b69c40))

- **git**: Retry failed deferred pushes on the periodic pull-loop tick (#997)
  ([#997](https://github.com/pvliesdonk/markdown-vault-mcp/pull/997),
  [`9856601`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9856601ad797beb7f2f6de52c45e2e7d96c5e20e))

- **links**: Resolve leading-slash markdown links against the vault root (#972)
  ([#972](https://github.com/pvliesdonk/markdown-vault-mcp/pull/972),
  [`45580e2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/45580e29611732860776d27548a383bd717b9797))

- **review**: Finalize pin-combo reviewer (restore CI gate, drop show_full_output)
  ([`dbf197a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dbf197acdffd57ba1c07569f60410add62658f3a))

- **review**: Single-agent reviewer; drop fan-out plugin + pin experiment
  ([`3350091`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/33500911e1ab1717a3966be7cc60c354fc31d374))

- **workflows**: Allow-list Skill defensively (both claude workflows)
  ([`1173fae`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1173fae3ca4a8b068cacccf47a06fe3e7aedb83a))

### Chores

- Update copier template to fastmcp-server-template v3.2.1 (#1013)
  ([#1013](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1013),
  [`8bb31ca`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8bb31caca81b04a74c37e6dc5522f02bdaf7aee9))

- Update copier template to v3.0.2 (generated config surface) (#953)
  ([#953](https://github.com/pvliesdonk/markdown-vault-mcp/pull/953),
  [`8c5f020`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8c5f020fb34ac84fe2ee39aecd43939feb298b9b))

- Update copier template to v3.1.1 (#976)
  ([#976](https://github.com/pvliesdonk/markdown-vault-mcp/pull/976),
  [`1af200a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1af200a8abe21c8f8db47dae38cf001c64800d4f))

- Update copier template to v3.1.3 (#986)
  ([#986](https://github.com/pvliesdonk/markdown-vault-mcp/pull/986),
  [`f505bff`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f505bff471b8816afdc86c68aa0ea6fbd26c177a))

- **deps**: Bump python-semantic-release/publish-action from 10.5.3 to 10.6.1 (#897)
  ([#897](https://github.com/pvliesdonk/markdown-vault-mcp/pull/897),
  [`c0c68f9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c0c68f9c74a483a6fe30b2fd90a4beaa131cdcb5))

- **deps**: Refresh uv.lock within existing constraints (#1026)
  ([#1026](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1026),
  [`59b3054`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/59b3054756496e448f441d7d770cbb6a455c04b0))

- **deps**: Update dependency fastmcp to v3.4.6 (#991)
  ([#991](https://github.com/pvliesdonk/markdown-vault-mcp/pull/991),
  [`6063df8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6063df8531f2ab134cb33568127f80accefc4b9f))

- **deps**: Update dependency mkdocs-material to v9.7.7 (#992)
  ([#992](https://github.com/pvliesdonk/markdown-vault-mcp/pull/992),
  [`c50a15e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c50a15e0f99a28a470ad739c4546166667512e14))

- **deps**: Update dependency mkdocstrings to v1.0.6 (#995)
  ([#995](https://github.com/pvliesdonk/markdown-vault-mcp/pull/995),
  [`0d751c8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0d751c8f340ba0ff0e1ef4701f1f1b1bdab00093))

- **deps**: Update dependency numpy to v2.5.2 (#1019)
  ([#1019](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1019),
  [`b465aa8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b465aa8ef8d503d503b1df1e14b83c09434d29f3))

- **deps**: Update dependency pre-commit to v4.6.1 (#996)
  ([#996](https://github.com/pvliesdonk/markdown-vault-mcp/pull/996),
  [`ab1dd09`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ab1dd09577bada421e1863b5870a38eb6a2a53d0))

- **deps**: Update dependency pre-commit to v4.6.2 (#1016)
  ([#1016](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1016),
  [`773f795`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/773f79574a17b5c2d9765b286da35843c5be19ca))

### Documentation

- De-enumerate incidental write-op shorthand in docstrings and spec (#1011)
  ([#1011](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1011),
  [`ea221fd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ea221fd27881dadf85830980d121c289f1a70d57))

- **design**: OKF (Open Knowledge Format) support design (#967)
  ([#967](https://github.com/pvliesdonk/markdown-vault-mcp/pull/967),
  [`15604a7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/15604a75ade34d6989b095dade10b8374692bde5))

- **okf**: Guide interop sections, examples/okf pack, methodology template OKF fields (#966) (#989)
  ([#989](https://github.com/pvliesdonk/markdown-vault-mcp/pull/989),
  [`d22f2bb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d22f2bbe7b7f09fefcaf38c187e2a7be97db6f40))

### Features

- Add append tool for end-of-note writes without a prior read (#1025)
  ([#1025](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1025),
  [`5f696a9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5f696a9fd89a2b0ba209804760fa0795ea63f5ee))

- Bound summarize latency with a timeout error and background jobs (#937) (#938)
  ([#938](https://github.com/pvliesdonk/markdown-vault-mcp/pull/938),
  [`b7442bd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b7442bd42808ec728acac73aeb52fba694c9b6ea))

- **embeddings**: Configurable timeout (EMBED_TIMEOUT_S) + batch size (EMBEDDING_BATCH_SIZE) (#1000)
  ([#1000](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1000),
  [`c3277d0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c3277d0dd51e88ea14e89bff5564990704c7deda))

- **okf**: Bundle export via an okf-bundle download ref (#982)
  ([#982](https://github.com/pvliesdonk/markdown-vault-mcp/pull/982),
  [`b76bbfd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b76bbfdd1b67d962592260866368426dcf1a0630))

- **okf**: Detect OKF bundles and annotate read surfaces (#968)
  ([#968](https://github.com/pvliesdonk/markdown-vault-mcp/pull/968),
  [`2c35edf`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2c35edf081a8b24c39f263049157494285a1e277))

- **okf**: Enforced write layer — provenance stamping, verification invalidation, okf_verify (#964)
  (#984) ([#984](https://github.com/pvliesdonk/markdown-vault-mcp/pull/984),
  [`21b9bf7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/21b9bf72f747373d2da3649aeb8848a45f240220))

- **okf**: Enforced-write convention maintenance — log.md append + index.md refresh (#964) (#987)
  ([#987](https://github.com/pvliesdonk/markdown-vault-mcp/pull/987),
  [`c0c9940`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c0c99409f351250783a274d84244f59c874d6537))

- **okf**: Harden okf_verify against model self-attestation (#990) (#1004)
  ([#1004](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1004),
  [`03f088d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/03f088d231db575f9992ed4ac9425047bfa773c6))

- **okf**: Migration transforms (wikilink conversion, index/log generation) (#973)
  ([#973](https://github.com/pvliesdonk/markdown-vault-mcp/pull/973),
  [`ee34663`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ee34663eee16950e5e9640b11aef33493b816b61))

- **okf**: OKF filter dimensions and graph node typing (#970)
  ([#970](https://github.com/pvliesdonk/markdown-vault-mcp/pull/970),
  [`7330665`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7330665e18059762edddf415ebfdb20dbb3e4828))

- **okf**: Okf_validate conformance audit tool (#971)
  ([#971](https://github.com/pvliesdonk/markdown-vault-mcp/pull/971),
  [`33691e2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/33691e25aa35a177119e2498c7d5c5a717018baa))

- **okf**: Ranking downweights for deprecated/stale/reserved files (#965) (#988)
  ([#988](https://github.com/pvliesdonk/markdown-vault-mcp/pull/988),
  [`38e0c00`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/38e0c00c7aa0ccde31f0fec0b1c4bf741fc83b0b))

- **okf**: Scope okf_seed_log's log.md content to the folder's subtree (#974) (#1005)
  ([#1005](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1005),
  [`a96dca4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a96dca4b86c4f56a4a3efaf601a01bbfeec36f2b))

- **transfer**: Adopt fastmcp-pvl-core transfer subsystem; retire local store (#981)
  ([#981](https://github.com/pvliesdonk/markdown-vault-mcp/pull/981),
  [`0b48780`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0b487800196680c8d20051ff8fe3080475501106))

### Refactoring

- Single-source the user-facing write-tool enumeration from the write tag (#1014)
  ([#1014](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1014),
  [`5ffb9f7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5ffb9f74f25691a2d6b3697c3f12f255d5bfc993))

- **git**: Single-source WriteOperation alias in strategy.py (#1008)
  ([#1008](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1008),
  [`74f46a0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/74f46a0fa5a971228cb5a815a08ee8c1eaf3cfd5))


## 3.2.0-rc.6 (2026-07-16)


## 3.2.0-rc.5 (2026-07-16)

### Features

- Rebuild on INDEXED_FIELDS change; SEARCHABLE_FIELDS defaults to it (#928)
  ([#928](https://github.com/pvliesdonk/markdown-vault-mcp/pull/928),
  [`6ccf140`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6ccf140962f8c397792634cf4889c34cf1f72690))


## 3.2.0-rc.4 (2026-07-16)

### Features

- Per-call max_notes with in-result coverage hint for summarize (#926)
  ([#926](https://github.com/pvliesdonk/markdown-vault-mcp/pull/926),
  [`46ce4c2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/46ce4c2991217ca70164c7e0daf8eff9a6de255b))


## 3.2.0-rc.3 (2026-07-16)

### Bug Fixes

- Forbid assistant-style offers in summarize output (#923)
  ([#923](https://github.com/pvliesdonk/markdown-vault-mcp/pull/923),
  [`ba4fe2b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ba4fe2b8f90ccc8382825b5bbab25a07879ecd68))

- Stop reasoning models exhausting the summarize token budget (#920)
  ([#920](https://github.com/pvliesdonk/markdown-vault-mcp/pull/920),
  [`0f72922`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0f72922936e296e66e30feac10172d44927a7099))

### Features

- Map-reduce summarization for inputs beyond one model request (#924)
  ([#924](https://github.com/pvliesdonk/markdown-vault-mcp/pull/924),
  [`6686511`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6686511eff8ed4d21d80cb1d53d1fc6764391d3f))

### Refactoring

- Close out de-fork epic #898 — strict conformance gate + docs (#906) (#914)
  ([#914](https://github.com/pvliesdonk/markdown-vault-mcp/pull/914),
  [`c8f30ca`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c8f30cab69af73da136477b0371aae8eec27e6e5))


## 3.2.0-rc.2 (2026-07-15)

### Features

- Replace anthropic summarizer with generic OpenAI-compatible backend (#917)
  ([#917](https://github.com/pvliesdonk/markdown-vault-mcp/pull/917),
  [`d64d528`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d64d528080a72b72aba599619b435674365fd897))

### Refactoring

- De-fork _server_apps.py onto the v2.10.5 apps seam (#905) (#913)
  ([#913](https://github.com/pvliesdonk/markdown-vault-mcp/pull/913),
  [`8fcc85b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8fcc85b913eabe20f713e294f97ba4df8556428e))

- Unify embedding providers on the OpenAI-compatible API (#918)
  ([#918](https://github.com/pvliesdonk/markdown-vault-mcp/pull/918),
  [`275eba8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/275eba896a7dba442f7802518ae1a70e41cabd29))

- **cli**: De-fork cli.py onto the template skeleton (#904) (#909)
  ([#909](https://github.com/pvliesdonk/markdown-vault-mcp/pull/909),
  [`59131d6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/59131d6b5f8e626d8c574f35be5a1c14eb5e81d5))

- **config**: De-fork config.py onto the template skeleton (#900) (#908)
  ([#908](https://github.com/pvliesdonk/markdown-vault-mcp/pull/908),
  [`7cd5101`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7cd5101738d881cd43280780090228d4d14f9944))

- **deps**: Move vault lifecycle into a domain Service; de-fork _server_deps (#902) (#910)
  ([#910](https://github.com/pvliesdonk/markdown-vault-mcp/pull/910),
  [`8fdff85`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8fdff850bf59c4ff37ab21adabfc2b4e7ef6ffc0))

- **init**: De-fork __init__.py to the minimal template-skeleton root (#903) (#912)
  ([#912](https://github.com/pvliesdonk/markdown-vault-mcp/pull/912),
  [`2a44e95`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2a44e953a346f86b0880ceaf59d6fe2bb8751a9c))

- **server**: De-fork server.py onto the template skeleton; config-honoring DOMAIN-WIRING (#901)
  (#911) ([#911](https://github.com/pvliesdonk/markdown-vault-mcp/pull/911),
  [`0978472`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/09784729bac1fe07b303e633cf641b2461658975))

### Testing

- **conformance**: Ratcheting template-conformance gate (#899) (#907)
  ([#907](https://github.com/pvliesdonk/markdown-vault-mcp/pull/907),
  [`144e1da`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/144e1da2238d3b516456fc31bd5f83f96e9da6c4))


## 3.2.0-rc.1 (2026-07-10)

### Bug Fixes

- Hybrid search vector channel misses folder normalization (#882)
  ([#882](https://github.com/pvliesdonk/markdown-vault-mcp/pull/882),
  [`07a5d96`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/07a5d961f838e8e2aa1e038d875e20d13914ea0c))

- Recover keyword/hybrid search for hyphenated terms (#866) (#884)
  ([#884](https://github.com/pvliesdonk/markdown-vault-mcp/pull/884),
  [`92f62a9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/92f62a957d24dd03e9f03416ad12d4196502d533))

### Features

- Add LLM-backed summarize tool gated on an API key (#869)
  ([#869](https://github.com/pvliesdonk/markdown-vault-mcp/pull/869),
  [`9415c8e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9415c8e81f1469b4b1d75643916d8ec494ede6e1))

- Configurable title field, searchable frontmatter, and ranking weights (#867)
  ([#867](https://github.com/pvliesdonk/markdown-vault-mcp/pull/867),
  [`a06be7f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a06be7ff0a1f524b4de03580ea3b1d2daaf9f2fa))

- Folder conventions — per-folder authoring policy for LLM clients (#877)
  ([#877](https://github.com/pvliesdonk/markdown-vault-mcp/pull/877),
  [`ad59381`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ad59381f3d62d0151f9cad60047bf02570a6e90d))

### Refactoring

- Decompose get_file_diff into mode-specific helpers (#886)
  ([#886](https://github.com/pvliesdonk/markdown-vault-mcp/pull/886),
  [`6a600f2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6a600f296f42a23cbac721545b2639bcb5c3899a))

- Dedupe document.py write plumbing and unify link-rewrite loops (#885)
  ([#885](https://github.com/pvliesdonk/markdown-vault-mcp/pull/885),
  [`9234c4b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9234c4b79b6648ffb12225ab4a848d0410e63bae))

- Dedupe skip taxonomy and stale-purge, phase-split reindex (#888)
  ([#888](https://github.com/pvliesdonk/markdown-vault-mcp/pull/888),
  [`620324c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/620324c429e99f78a936c3eadd14821a448cea81))

- Extract per-thread sqlite connection registry from FTSIndex (#889)
  ([#889](https://github.com/pvliesdonk/markdown-vault-mcp/pull/889),
  [`4df4e43`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4df4e4343f6275e2a585cc9ca73abc7630e480da))

- Extract ranking pipeline and unify search channel stages (#887)
  ([#887](https://github.com/pvliesdonk/markdown-vault-mcp/pull/887),
  [`d6208bd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d6208bd49adc5906a8c91b8b10cab28b1236acaf))

- Move graph-view assembly from app closures into GraphFacet (#891)
  ([#891](https://github.com/pvliesdonk/markdown-vault-mcp/pull/891),
  [`8f8c7a0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8f8c7a07ffac0e46f906d3f0acd0e2e1606a7bb7))

- Split extract_links into per-format parsers (#890)
  ([#890](https://github.com/pvliesdonk/markdown-vault-mcp/pull/890),
  [`74c22f3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/74c22f3b3e8dba20211c0f855e7881a497f0896a))

- Unify sync_once onto the force_pull pipeline (#892)
  ([#892](https://github.com/pvliesdonk/markdown-vault-mcp/pull/892),
  [`e3890ce`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e3890ce40013e21b216ce38bc8d55db462317b0f))


## 3.1.0 (2026-07-08)


## 3.1.0-rc.3 (2026-07-05)

### Bug Fixes

- Inherit host neutrals, keep Paper terracotta accent (drop blue info alias) (#865)
  ([#865](https://github.com/pvliesdonk/markdown-vault-mcp/pull/865),
  [`b2260d7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b2260d7dc49ba3fe6b777c58bd0f260c3ff225cb))


## 3.1.0-rc.2 (2026-07-03)

### Bug Fixes

- Graph/context app tools crash on notes over MAX_NOTE_READ_BYTES (#857)
  ([#857](https://github.com/pvliesdonk/markdown-vault-mcp/pull/857),
  [`45e60d5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/45e60d5e8fa1cbeb10c89360d2a49278ce7baa72))

- Make Vault Explorer app honor host container dimensions (mobile sizing) (#860)
  ([#860](https://github.com/pvliesdonk/markdown-vault-mcp/pull/860),
  [`8eafc25`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8eafc2528694ffedee837f6dec97117f2510b343))

- Resolve graph canvas colors via probe so Desktop nodes aren't black (#858)
  ([#858](https://github.com/pvliesdonk/markdown-vault-mcp/pull/858),
  [`18e3688`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/18e368840d00d456ae25cdc571a86f210147ccf5))


## 3.1.0-rc.1 (2026-07-03)

### Bug Fixes

- Bound the derived chunk-char cap; add -1 unbounded opt-in (#790) (#794)
  ([#794](https://github.com/pvliesdonk/markdown-vault-mcp/pull/794),
  [`61b7ce6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/61b7ce6a7429f7d08d73386f60d72e9bbc7ef297))

- Build prompt callables with a synthetic signature instead of exec() (#788) (#800)
  ([#800](https://github.com/pvliesdonk/markdown-vault-mcp/pull/800),
  [`1113e6e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1113e6e923c1d004dc23b42289f5189923115ab2))

- Derive vector sidecar paths with with_suffix, not string append (#824)
  ([#824](https://github.com/pvliesdonk/markdown-vault-mcp/pull/824),
  [`23510e1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/23510e122b5f363e163c0eebf599715e3a30446d))

- Floor the semantic candidate pool so recall does not depend on limit (#826)
  ([#826](https://github.com/pvliesdonk/markdown-vault-mcp/pull/826),
  [`711a568`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/711a568ebe75c090922f310f8f22a279595a2cad))

- Guard each prompt registration so one bad prompt can't abort the rest (#808)
  ([#808](https://github.com/pvliesdonk/markdown-vault-mcp/pull/808),
  [`bb66c2d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bb66c2d98fa2d4f582563fee08c826a9b5bc699a))

- Keep an already-indexed file that fails to re-hash instead of purging it (#840)
  ([#840](https://github.com/pvliesdonk/markdown-vault-mcp/pull/840),
  [`a14bd12`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a14bd12f1e2b47aada2540c832813b00874aa230))

- Make excluded files invisible in build_index (file-shaped patterns) + test gaps (#844)
  ([#844](https://github.com/pvliesdonk/markdown-vault-mcp/pull/844),
  [`4aac59d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4aac59de92dd82f6a2eb87b6fddd76075f8fac8d))

- Never watch the vault's own state dir or .git (self-feedback reindex loop) (#838)
  ([#838](https://github.com/pvliesdonk/markdown-vault-mcp/pull/838),
  [`a8774c2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a8774c2ed6c44828280a214955934501092481fb))

- Quiet httpx per-request logs at the default level (#792) (#795)
  ([#795](https://github.com/pvliesdonk/markdown-vault-mcp/pull/795),
  [`41554a0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/41554a091c0f4dc918f38ef5a2f1470bf734d233))

- Resolve watch roots to realpath so FSEvents events are not dropped (#850)
  ([#850](https://github.com/pvliesdonk/markdown-vault-mcp/pull/850),
  [`dba3939`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dba39398c5d6905be47668c23556b4b4ca4f3b5d))

- Retry transient descriptor-exhaustion when hashing during a scan (#827)
  ([#827](https://github.com/pvliesdonk/markdown-vault-mcp/pull/827),
  [`5527f66`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5527f665585a5ddc3d6d82166262963ebe25a9d6))

- Surface internal embedding failures + remove unused build_event_store shim (#774) (#805)
  ([#805](https://github.com/pvliesdonk/markdown-vault-mcp/pull/805),
  [`07726c0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/07726c06ec043db8c5efc4ab9f0999c4df0913ed))

- Surface internal semantic-search failures in get_context (#804) (#806)
  ([#806](https://github.com/pvliesdonk/markdown-vault-mcp/pull/806),
  [`4f8824b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4f8824b3317e1b3118947e55d7df75078bc98750))

- Surface silently-dropped subtrees and a fully-blind watcher (#845)
  ([#845](https://github.com/pvliesdonk/markdown-vault-mcp/pull/845),
  [`4879d9b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4879d9bd599dd1e5257bd5f2b1b5303b72e8967b))

### Chores

- Address deferred cli.py/test_cli.py review nits (#807)
  ([#807](https://github.com/pvliesdonk/markdown-vault-mcp/pull/807),
  [`460d60d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/460d60d4bcffa08b9cbb7d95227a53c43419c66a))

- Bump fastmcp-pvl-core to >=4.1.0 for get_server_info title (#754) (#755)
  ([#755](https://github.com/pvliesdonk/markdown-vault-mcp/pull/755),
  [`b5bf092`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b5bf092c0879a096cb9ce8cd72d07837336e22a8))

- Conform gitleaks/codeql/pre-commit infra to the template (#770) (#781)
  ([#781](https://github.com/pvliesdonk/markdown-vault-mcp/pull/781),
  [`b9a1c25`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b9a1c25e4cdd9c1be917637e26fadb12ddcbdba8))

- Copier update template v2.10.0 → v2.10.1 (detect-rc fix + gemini purge) (#854)
  ([#854](https://github.com/pvliesdonk/markdown-vault-mcp/pull/854),
  [`0b0c9bb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0b0c9bb04144c76c03b1bce0ccf81af3ed930a8b))

- Relocate docs/design.md into docs/design/ (resolves template#234 downstream) (#841)
  ([#841](https://github.com/pvliesdonk/markdown-vault-mcp/pull/841),
  [`23d0bee`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/23d0beef07cbc39a9ac2474701141dc9891b31a0))

- **copier**: Adopt the diff-scoped structural-health gate (template v2.10.0) (#789)
  ([#789](https://github.com/pvliesdonk/markdown-vault-mcp/pull/789),
  [`2d136af`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2d136af123e53b97a912f78aa3dddbbafe86b015))

- **copier**: Update template v2.5.3 → v2.9.1 (#771); adopt wizard ServerConfig + SPA vendoring
  (#787) ([#787](https://github.com/pvliesdonk/markdown-vault-mcp/pull/787),
  [`7806f8f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7806f8f2d4f060bf016964d94a933738f895110b))

### Documentation

- Correct stale comments from #824/#827/#828 (#839)
  ([#839](https://github.com/pvliesdonk/markdown-vault-mcp/pull/839),
  [`4dee88f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4dee88f40b9c35fea1d8c709f47aa18f53d3690a))

- SPA build note in design.md + _find_spa_dir branch tests (#817) (#818)
  ([#818](https://github.com/pvliesdonk/markdown-vault-mcp/pull/818),
  [`3f29919`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3f299198174a3446e44e5a28dd4385ce5177e230))

- Watcher scoping + root_floor + tracker carry-forward (design.md, .env) (#846)
  ([#846](https://github.com/pvliesdonk/markdown-vault-mcp/pull/846),
  [`8139d13`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8139d136bb6d22ada761fbc30dd3a6ecc2a5d445))

### Features

- Add human-readable title annotations to all tools (#751) (#753)
  ([#753](https://github.com/pvliesdonk/markdown-vault-mcp/pull/753),
  [`c02426e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c02426e0328faa9b498f07b116bffd5e543b945d))

- Converge config with template drift gate (ProjectConfig + pvl-core 4.3.0) (#772)
  ([#772](https://github.com/pvliesdonk/markdown-vault-mcp/pull/772),
  [`580afd0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/580afd0994c68653f28ee0752da8381f4d944c6e))

- Distinguish internal_error from parse_error in skipped_files (#802) (#803)
  ([#803](https://github.com/pvliesdonk/markdown-vault-mcp/pull/803),
  [`8b88af5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8b88af510cfcdce559958c8de0bcdacd151ac506))

- Folder/subtree table-of-contents + get_toc tool (#773) (#780)
  ([#780](https://github.com/pvliesdonk/markdown-vault-mcp/pull/780),
  [`8b07f47`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8b07f47ac5f5aaaae6e6dd79bfb74394acfea04a))

- Move_folder folder-level move with vault-wide link rewrite (#756)
  ([#756](https://github.com/pvliesdonk/markdown-vault-mcp/pull/756),
  [`23069f7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/23069f797296da0b27a6783eb0e3334ed2ce601c))

- Paper theming + type system for the SPA shell (#811) (#825)
  ([#825](https://github.com/pvliesdonk/markdown-vault-mcp/pull/825),
  [`7f92b60`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7f92b60b59febab2c7ba7941d1e8411c19cdad6f))

- Restyle Note Preview to Paper with ToC, collapsible frontmatter/tags, copy actions (#847)
  ([#847](https://github.com/pvliesdonk/markdown-vault-mcp/pull/847),
  [`e21600d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e21600d2d17daa2a3f0e8436ae3500a13298276d))

- Restyle the Context Card view to Paper (#812) (#829)
  ([#829](https://github.com/pvliesdonk/markdown-vault-mcp/pull/829),
  [`d992355`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d99235583cfd3922a428eccde66c2158b4004fd9))

- Restyle the Graph Explorer view to Paper with level-of-detail labels (#851)
  ([#851](https://github.com/pvliesdonk/markdown-vault-mcp/pull/851),
  [`297169c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/297169c916664cd45ed8c504c76f6a5d8573c478))

- Restyle the Vault Browser view to Paper (#813) (#842)
  ([#842](https://github.com/pvliesdonk/markdown-vault-mcp/pull/842),
  [`2daca92`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2daca9215b2dd825a552bbe2ee40ef7db34c5dbe))

- Scoped file discovery and watches for large vaults (prune excluded subtrees, deliver dot-roots)
  (#828) ([#828](https://github.com/pvliesdonk/markdown-vault-mcp/pull/828),
  [`ff41ee5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ff41ee50a4c91c7833dce380838c4a12b056b241))

- Surface skipped files via get_index_status (#775) (#801)
  ([#801](https://github.com/pvliesdonk/markdown-vault-mcp/pull/801),
  [`41c2a39`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/41c2a39b251fd3ff933b0ad29f28689fd8bdd0eb))

- Within-section chunk overlap (#791) (#796)
  ([#796](https://github.com/pvliesdonk/markdown-vault-mcp/pull/796),
  [`a874237`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a874237d9c7671b2179f786cee002a209eb9d411))

### Refactoring

- De-fork the CLI to stock typer cli.py + DOMAIN-COMMANDS sentinel (#776)
  ([#776](https://github.com/pvliesdonk/markdown-vault-mcp/pull/776),
  [`793cc2a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/793cc2ac106ded764cc6a3153c067ccb2f420ea2))

- Modular SPA partials + build_spa.py (#810) (#816)
  ([#816](https://github.com/pvliesdonk/markdown-vault-mcp/pull/816),
  [`27e3038`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/27e303864defbac9fd0fc17b0ab331db28ff3267))

- Type the TOC payloads as dataclasses (#779) (#786)
  ([#786](https://github.com/pvliesdonk/markdown-vault-mcp/pull/786),
  [`0a33d6e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0a33d6e8b9a2d9075065668b3e60802d13301ba8))

### Testing

- Restore stripped template smoke coverage, adapted to MVM (#777)
  ([#777](https://github.com/pvliesdonk/markdown-vault-mcp/pull/777),
  [`42c03d7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/42c03d7e874ef731edf7731a5372f36272333441))


## 3.0.4 (2026-06-27)

### Bug Fixes

- Resolve git sync ref as origin/<branch> not @{upstream} (#750)
  ([#750](https://github.com/pvliesdonk/markdown-vault-mcp/pull/750),
  [`30b9118`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/30b9118cdc5e2acff63b7b138276b1e57ac3dcea))


## 3.0.3 (2026-06-26)

### Bug Fixes

- Degrade whole-document read() to None on malformed frontmatter (#742) (#744)
  ([#744](https://github.com/pvliesdonk/markdown-vault-mcp/pull/744),
  [`6e48448`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6e48448f86cdc70e5fa3b6ab56aa63b9e1909650))

- Guard whole-document read()'s content read against mid-read failure (#745) (#747)
  ([#747](https://github.com/pvliesdonk/markdown-vault-mcp/pull/747),
  [`18f8c2a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/18f8c2a894794d77f5c1d3c655dfc323ce992c4d))

- Return the whole section from read(section=), not just its first chunk (#741) (#743)
  ([#743](https://github.com/pvliesdonk/markdown-vault-mcp/pull/743),
  [`9da1312`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9da13126399c1451cd4aab873c168e4f51d5d30a))

### Testing

- Assert the degrade-to-None warning is logged in read() (#746) (#748)
  ([#748](https://github.com/pvliesdonk/markdown-vault-mcp/pull/748),
  [`0dee650`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0dee650da9d3bf66d76c523ebb816184552532cb))


## 3.0.2 (2026-06-25)

### Bug Fixes

- Atomic VectorIndex sidecar writes + self-heal corrupt sidecars on load (#732)
  ([#732](https://github.com/pvliesdonk/markdown-vault-mcp/pull/732),
  [`ca21652`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ca21652d6b5ce5826c1cede7ecfd8ead713a1fb2))

- Enforce vector sidecar row-count parity; self-heal on both load paths (#737)
  ([#737](https://github.com/pvliesdonk/markdown-vault-mcp/pull/737),
  [`033f0a8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/033f0a8e2dd249aec8a99136e130b33d95db8ddf))

- Harden load_or_self_heal error path (#735) (#739)
  ([#739](https://github.com/pvliesdonk/markdown-vault-mcp/pull/739),
  [`74dbc96`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/74dbc96d046b573bf7e244b6341ffa386063098c))

- Strip escaped-pipe backslash from wikilink targets (#731) (#740)
  ([#740](https://github.com/pvliesdonk/markdown-vault-mcp/pull/740),
  [`b107f03`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b107f03b42ff0bf64b9baa5041be64f4bfbe29aa))

### Refactoring

- Unify the two _load_vectors implementations (#736) (#738)
  ([#738](https://github.com/pvliesdonk/markdown-vault-mcp/pull/738),
  [`409dfa7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/409dfa74bfce1dd8338e5c1ecfae2b0b1d9964f0))


## 3.0.1 (2026-06-22)

### Bug Fixes

- Break file-watcher reindex self-feedback loop (#721)
  ([#721](https://github.com/pvliesdonk/markdown-vault-mcp/pull/721),
  [`e018307`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e018307056581cd429859acac940c31efb972380))

### Chores

- **copier**: Update to v2.1.1 (#711)
  ([#711](https://github.com/pvliesdonk/markdown-vault-mcp/pull/711),
  [`529e879`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/529e8798d90499fa5468e3a90cb6b1b4c61e0328))

- **copier**: Update to v2.1.2 (#712)
  ([#712](https://github.com/pvliesdonk/markdown-vault-mcp/pull/712),
  [`aa8f6fc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/aa8f6fc5919a6381c10168ff2d49da4d0d9e5d40))

- **copier**: Update to v2.3.0 (#715)
  ([#715](https://github.com/pvliesdonk/markdown-vault-mcp/pull/715),
  [`5370ad1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5370ad1bcc6f8896aaefb5b11b439e1b882dd25e))

- **copier**: Update to v2.5.1 (#724)
  ([#724](https://github.com/pvliesdonk/markdown-vault-mcp/pull/724),
  [`61bc634`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/61bc63485f044115c321cea6fde26a6ffb60f9a8))

- **copier**: Update to v2.5.3 (#730)
  ([#730](https://github.com/pvliesdonk/markdown-vault-mcp/pull/730),
  [`7f6a136`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7f6a136323fced3af35f8923835cc0f3ba1793f4))

- **deps**: Refresh lockfile; bump pydantic-settings past GHSA-4xgf-cpjx-pc3j (#729)
  ([#729](https://github.com/pvliesdonk/markdown-vault-mcp/pull/729),
  [`a03a135`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a03a135c6967999edfa004e5723e5b447b0c4a29))

### Documentation

- Fix README deep links broken by mike versioned-docs upgrade (#710)
  ([#710](https://github.com/pvliesdonk/markdown-vault-mcp/pull/710),
  [`9bf7b21`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9bf7b211caba28a8080fe39d01a96c44bd212cbc))

### Testing

- De-flake cold-start handshake test (#713) (#723)
  ([#723](https://github.com/pvliesdonk/markdown-vault-mcp/pull/723),
  [`57b0d93`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/57b0d93d1509db41679b2ec5b1ac4aabdd9a3c8e))


## 3.0.0 (2026-06-17)


## 2.0.0-rc.5 (2026-06-17)

### Bug Fixes

- **indexing**: Read ReadinessState (built, error) as one atomic snapshot (#706)
  ([#706](https://github.com/pvliesdonk/markdown-vault-mcp/pull/706),
  [`0a153a9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0a153a979d50dde04f1df2f493647ecc25ebf4e7))

- **security**: Close SSRF DNS-rebinding in fetch (resolve + validate + pin IP) (#704)
  ([#704](https://github.com/pvliesdonk/markdown-vault-mcp/pull/704),
  [`e1c0a24`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e1c0a24f4ca58f4a0baff2b18758831c84eacb0f))

### Documentation

- Scrub LLM prose tells across docs/ — Vale clean (#686) (#703)
  ([#703](https://github.com/pvliesdonk/markdown-vault-mcp/pull/703),
  [`a65c1df`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a65c1df301a95f9742863e33bc7de0573eec43d4))

### Performance Improvements

- Filter excluded paths in detect_changes (#257) + single config read on HTTP serve (#609) (#707)
  ([#707](https://github.com/pvliesdonk/markdown-vault-mcp/pull/707),
  [`6d2aabf`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6d2aabf43ec4657b50a394a63f2528e8f488370b))

- Parallelize vault_graph_hubs fetches (#285) + drop README sentinel-name leak (#486) (#708)
  ([#708](https://github.com/pvliesdonk/markdown-vault-mcp/pull/708),
  [`5e7edfd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5e7edfd2a85f0c9c6f87f19d9c4090396c5af53b))


## 2.0.0-rc.4 (2026-06-16)

### Features

- **server**: MARKDOWN_VAULT_MCP_DISABLE_APPS_UI env var to hide MCP-Apps tools (#702)
  ([#702](https://github.com/pvliesdonk/markdown-vault-mcp/pull/702),
  [`75aedcb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/75aedcbdcad2a23f1084314c5af560917eb2e6d5))


## 2.0.0-rc.3 (2026-06-16)

### Chores

- **deps**: Bump actions/cache from 4 to 5 (#692)
  ([#692](https://github.com/pvliesdonk/markdown-vault-mcp/pull/692),
  [`d1e3cef`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d1e3cefb4690704b58afce23190e29fa0f39a9f4))

- **deps**: Bump codecov/codecov-action from 6 to 7 (#691)
  ([#691](https://github.com/pvliesdonk/markdown-vault-mcp/pull/691),
  [`f86050b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f86050b2c78866d1220ed7a744edd661e785cf8e))

- **deps**: Bump cryptography, python-multipart, starlette to clear CVEs (#699)
  ([#699](https://github.com/pvliesdonk/markdown-vault-mcp/pull/699),
  [`342668a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/342668a745d6a25ac591f725484c7a9719e6022b))

### Continuous Integration

- **docs**: Versioned documentation with mike (stable + rolling unstable) (#701)
  ([#701](https://github.com/pvliesdonk/markdown-vault-mcp/pull/701),
  [`47f46dd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/47f46dd508f4cf28cd81432322649d5aaa16a371))

### Features

- **docs**: In-browser configuration generator (#694)
  ([#694](https://github.com/pvliesdonk/markdown-vault-mcp/pull/694),
  [`981e08c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/981e08c763e7f6998105eff590b1cfb9612b7ac7))

### Refactoring

- **server**: Split _server_tools.py into a facet-aligned _server_tools/ package (#689)
  ([#689](https://github.com/pvliesdonk/markdown-vault-mcp/pull/689),
  [`2522aec`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2522aecb9792b1732a8335f3a6bee2ccda0ed5ca))


## 2.0.0-rc.2 (2026-06-12)

### Bug Fixes

- **embeddings**: Converge vector index to FTS chunk set at boot (#665 PR3, closes #665) (#668)
  ([#668](https://github.com/pvliesdonk/markdown-vault-mcp/pull/668),
  [`11e5469`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/11e5469dde9468b78644f60cf7542eb0989c0f37))

- **git**: Per-commit attachment diff is rename-aware (closes #683) (#685)
  ([#685](https://github.com/pvliesdonk/markdown-vault-mcp/pull/685),
  [`0a15802`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0a158023b5f73e48ee792155e6f8368ae4ba0004))

- **git**: Stage only updated originals into the conflict commit (closes #675) (#679)
  ([#679](https://github.com/pvliesdonk/markdown-vault-mcp/pull/679),
  [`f7f86ec`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f7f86eced9dedc9339c3e199b1ebfbf8a76aed1c))

- **git**: Write_conflict_files reads original once + guards read failures (closes #662) (#672)
  ([#672](https://github.com/pvliesdonk/markdown-vault-mcp/pull/672),
  [`70285f7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/70285f7fc0f210bbd6b7ad70c458ef6d064d489d))

- **indexing**: Reconcile offline changes at boot; tracker remembers skipped files (#665 PR2) (#667)
  ([#667](https://github.com/pvliesdonk/markdown-vault-mcp/pull/667),
  [`9bc9f8f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9bc9f8f7f864d091b512261aa7c8936363b201fa))

- **io**: Normalize UTF-8 BOM on all vault-markdown reads (closes #673) (#680)
  ([#680](https://github.com/pvliesdonk/markdown-vault-mcp/pull/680),
  [`77a4373`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/77a437322d0e3702a565e5fc6b76081e92bfcf74))

- **io**: Normalize UTF-8 BOM on ingress (fetch + transfer-upload) (closes #681) (#682)
  ([#682](https://github.com/pvliesdonk/markdown-vault-mcp/pull/682),
  [`b53f01a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b53f01a51821a7f9b281859303fde99b869210d1))

- **packaging**: Lazy package root via PEP 562 stops dotted --cov from breaking the interpreter
  (#665 PR1) (#666) ([#666](https://github.com/pvliesdonk/markdown-vault-mcp/pull/666),
  [`5631817`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/56318172b4f9a1925bb5c2600af5f54de723f4c2))

- **write-callback**: Enforce close/fire thread contract (closes #601) (#663)
  ([#663](https://github.com/pvliesdonk/markdown-vault-mcp/pull/663),
  [`ca86892`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ca86892ac112a6ab6f655179686c96242e43c014))

### Chores

- **copier**: Update template v1.6.1 → v1.8.0 (rc2 release support + Vale gate) (#688)
  ([#688](https://github.com/pvliesdonk/markdown-vault-mcp/pull/688),
  [`773d86a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/773d86a09535a792ab58f832245f7bc907787326))

### Documentation

- Audit README + configuration + guides for consistency with current behavior (#653) (#654)
  ([#654](https://github.com/pvliesdonk/markdown-vault-mcp/pull/654),
  [`2710468`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/271046836c1a68b19af9625bbaf5756f314bcc20))

### Features

- **config**: Explicit embedding provider fails fast instead of silent degrade (#638 PR2) (#656)
  ([#656](https://github.com/pvliesdonk/markdown-vault-mcp/pull/656),
  [`1fe7c6b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1fe7c6b3e6e74fc457f372cba3592f2003361813))

- **git**: Quiesce writes (pause + drain) before a pull so the merge runs on a clean tree (closes
  #571) (#677) ([#677](https://github.com/pvliesdonk/markdown-vault-mcp/pull/677),
  [`ac51358`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ac51358a7b4d36315ce233cd89ae4e56dfe17cbc))

- **git-tools**: Get_diff/get_history on attachments (closes #342) (#684)
  ([#684](https://github.com/pvliesdonk/markdown-vault-mcp/pull/684),
  [`6387182`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/63871821d3fb938ca5d6c8179fcee01091d8130a))

- **write-callback**: Drain() primitive + fire write callbacks inside the file-write lock (refs
  #571) (#676) ([#676](https://github.com/pvliesdonk/markdown-vault-mcp/pull/676),
  [`b600ce4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b600ce4da59492bb0856cff9d6dd6bafb4c88eb7))

### Performance Improvements

- **indexing**: Run FTS5 optimize after bulk purges (#669) (#670)
  ([#670](https://github.com/pvliesdonk/markdown-vault-mcp/pull/670),
  [`d06a407`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d06a4073cf73e63950cc09a1dac67858811e0ff9))

### Refactoring

- **config**: Freeze sub-config sequence fields into tuples (#638 PR3, closes #639) (#657)
  ([#657](https://github.com/pvliesdonk/markdown-vault-mcp/pull/657),
  [`beba6de`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/beba6de198b2e818d653c081df3120949496844f))

- **config**: Strict fail-fast validation + canonical ConfigurationError (#638 PR1) (#655)
  ([#655](https://github.com/pvliesdonk/markdown-vault-mcp/pull/655),
  [`bfdd4ac`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bfdd4ac32e7ff0e1aecc1f47d2d0ebe69f52ed42))

- **git**: Extract conflict.py — final #577 decomposition (closes #577) (#661)
  ([#661](https://github.com/pvliesdonk/markdown-vault-mcp/pull/661),
  [`0aa876d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0aa876d30496a3be278ffe60ba3e734f42994aa1))

- **git**: Extract query.py; harden askpass (#659); get_history author docs (#484) (#577 PR2) (#660)
  ([#660](https://github.com/pvliesdonk/markdown-vault-mcp/pull/660),
  [`6533d67`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6533d673c7a3e8770b2e0abc403360deece96c18))

- **git**: Git.py → git/ package; extract types + plumbing (#577 PR1) (#658)
  ([#658](https://github.com/pvliesdonk/markdown-vault-mcp/pull/658),
  [`f6787f1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f6787f1c7838673c5fcd682776132b73e06eb2fb))

### Testing

- **git**: De-flake test_multiple_writes_single_push deterministically (closes #430) (#671)
  ([#671](https://github.com/pvliesdonk/markdown-vault-mcp/pull/671),
  [`8f2d726`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8f2d726031a5f26d336ec866d1352dc02ce74dcf))

- **lifespan**: De-flake cold-start-submits-both-jobs deterministically (closes #674) (#678)
  ([#678](https://github.com/pvliesdonk/markdown-vault-mcp/pull/678),
  [`a6acc2e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a6acc2ec817caa14d90eec52a56c94fde8882187))


## 2.0.0-rc.1 (2026-06-09)

### Bug Fixes

- Broaden wait_until_queryable never-built error string
  ([`4a9f473`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4a9f473595527a8381887b01962d48fcd677242a))

- Classify SQLITE_FULL as broken, not busy
  ([`31f04ea`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/31f04ea3561b30fd0d1233f813a733c4070fcb36))

- Defensive getattr on sqlite_errorname; classify missing attribute as broken
  ([`d6694e2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d6694e276ce72bce8b6ad363c1717d0635b98d52))

- Retry on SQLITE_LOCKED for FTSIndex (closes #560) (#564)
  ([#564](https://github.com/pvliesdonk/markdown-vault-mcp/pull/564),
  [`e0a49cb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e0a49cb7d175e53a63fea4ec1295b7b62d02085b))

- Signal index staleness out-of-band via _meta on read tools + resources (#646)
  ([#646](https://github.com/pvliesdonk/markdown-vault-mcp/pull/646),
  [`a8f7f53`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a8f7f5369aef476bbe1188705f518fccc8bf5773))

- **chunker**: Word-budget fallback so no chunk exceeds max_chunk_words (#496)
  ([#496](https://github.com/pvliesdonk/markdown-vault-mcp/pull/496),
  [`e375a8a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e375a8adfed5960dfdd5b27404051c0a4dda5e2a))

- **ci**: Hotfix copier-update.yml — add id-token: write for claude-code-action OIDC
  ([`5b30890`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5b30890537e202931f2150c51c47efd26f0c02ce))

- **ci**: Hotfix copier-update.yml — add id-token: write for claude-code-action OIDC (#447)
  ([#447](https://github.com/pvliesdonk/markdown-vault-mcp/pull/447),
  [`7224def`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7224def2643582c277d03e2cf777a564c6ef3e36))

- **ci**: Hotfix copier-update.yml — bare Write for Jobs B/C + revert show_full_output
  ([`89b821f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/89b821f617754ea9da2aab957e13a61c828fe818))

- **ci**: Hotfix copier-update.yml — bare Write for Jobs B/C + revert show_full_output (#454)
  ([#454](https://github.com/pvliesdonk/markdown-vault-mcp/pull/454),
  [`73486d5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/73486d55ddcf6556d0de9678e86b6512cdd06f7f))

- **ci**: Hotfix copier-update.yml — path-scoped Write for Jobs B and C
  ([`5d3e6b3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5d3e6b3801b7ad9c139a4e7eaefb2344bd0e54f4))

- **ci**: Hotfix copier-update.yml — path-scoped Write for Jobs B and C (#449)
  ([#449](https://github.com/pvliesdonk/markdown-vault-mcp/pull/449),
  [`2cf8e0f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2cf8e0f3b1193679260679332a515237d5986cec))

- **collection**: Completeness sentinel + audit external bucket-4 callers
  ([`8fd6ad5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8fd6ad5a01fc91ddb244524ab0e199fbefebaaf0))

- **collection,server**: Build_index clears prior error; decorator docstring direction
  ([`bf10a05`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bf10a0533971503956ef39b4b13668e368e3a8d0))

- **collection,server**: Warm build_index clears prior error; decorator handles positional
  collection
  ([`84beb5e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/84beb5eda49383df50eb4e50d96188c891baf323))

- **docs+server**: Catch remaining 'ready'/IndexNotReadyError stragglers + document get_index_status
  divergence (#538) ([#538](https://github.com/pvliesdonk/markdown-vault-mcp/pull/538),
  [`0b99aa8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0b99aa8275447370715a04eee2570cf8c6224c0a))

- **edit**: Report the real divergent line in multi-line edit diagnostics (#502)
  ([#502](https://github.com/pvliesdonk/markdown-vault-mcp/pull/502),
  [`d23a414`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d23a4141208e7092fdee2dd899ee1c9048fec917))

- **embeddings**: Derive chunk char cap from model context to stop oversize-chunk build aborts
  (#649) (#652) ([#652](https://github.com/pvliesdonk/markdown-vault-mcp/pull/652),
  [`772c523`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/772c5233863b62ac63086a0f3144371515486e07))

- **embeddings**: Throttle build_embeddings progress logging to deciles (#311) (#651)
  ([#651](https://github.com/pvliesdonk/markdown-vault-mcp/pull/651),
  [`80e4b63`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/80e4b636a3359fcb8a39285f70b2346d1da0e8d4))

- **fts**: Address PR #523 review feedback
  ([`ecb9b0b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ecb9b0bf878a1196ab56672260c2d7f508cad13a))

- **fts**: Clear TLS slot in _conn() slow-path BaseException cleanup
  ([`975f92d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/975f92dfa2ba0d2a2bdd70d2d5ab54dc0824b696))

- **fts**: Guard _all_conns.remove() with _reg_lock + clear TLS in __init__ cleanup
  ([`f40e160`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f40e160140183e08c271abdf58a95d3ea84d56a2))

- **git**: Address gemini HIGH findings on _force_pull_rebase_fallback
  ([`6e32461`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6e324612260024a7b5a612fefb13b14e06d35c70))

- **git**: Close redaction asymmetry in _force_pull_rebase_fallback
  ([`f73d2ea`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f73d2eaeeec796c6a29f9742b06c6dbfc5cd8e6a))

- **git**: Post-#444 polish — sync_once rebase detection, would_apply semantic, commit check, error
  tests (#475) ([#475](https://github.com/pvliesdonk/markdown-vault-mcp/pull/475),
  [`cae2700`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cae2700791e8f1326503a067ba835b296d5ba43f))

- **git**: Suppress false-positive committer identity warning (#644)
  ([#644](https://github.com/pvliesdonk/markdown-vault-mcp/pull/644),
  [`5f00909`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5f00909b33b78ddbe14ecb9c56be6b86e2296cfe))

- **git_sync**: Address local-circus blockers
  ([`438fc2c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/438fc2cdfa769d41e5c4e53fec59f1d5c118a89b))

- **git_sync**: Guard reindex + log defensive rebase --abort failures
  ([`fb524e8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fb524e876b2c62b2b002266f1c0fda7ed9ad9d9f))

- **indexing**: Distinguish build_failed from never_built in IndexUnavailableError (#597)
  ([#597](https://github.com/pvliesdonk/markdown-vault-mcp/pull/597),
  [`bae2b1b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bae2b1bc13cce825ea2183a9df197ec4d1db12da))

- **indexing**: Distinguish failing list_notes from empty index + clear stale sync-build error
  (#592) ([#592](https://github.com/pvliesdonk/markdown-vault-mcp/pull/592),
  [`2cc2ee0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2cc2ee0743a7ac5a229f4dbadcd6c24cf97a7aa0))

- **indexing**: Don't conflate cancellation with failure in async done-callbacks (#589)
  ([#589](https://github.com/pvliesdonk/markdown-vault-mcp/pull/589),
  [`b46d79d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b46d79dde0ca96dba4a95dbe0d7532d3dd4264ba))

- **indexing**: Record build-job failures so status reports "failed" not "building" (closes #585)
  (#588) ([#588](https://github.com/pvliesdonk/markdown-vault-mcp/pull/588),
  [`9a67bd6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9a67bd644f1aec07a3b8c644b2f3c1a3a2bd3eda))

- **links**: Resolve vault wikilinks after every write/edit/delete/rename (#495)
  ([#495](https://github.com/pvliesdonk/markdown-vault-mcp/pull/495),
  [`3bae1bb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3bae1bbb55b205d4d784d347146a289fa0918048))

- **read**: Tolerate whitespace-run differences in section= lookups (#497)
  ([#497](https://github.com/pvliesdonk/markdown-vault-mcp/pull/497),
  [`211e538`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/211e538c76dbb07a264db96fbe7743ac4b4788a7))

- **review**: Address pre-PR review findings for issue #469
  ([`41672a0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/41672a0b236b3e8fdbeefcd0189f4894ece33f5c))

- **review**: Claude-review feedback on PR #471
  ([`12bf93d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/12bf93d1d923673bd6993385e930a61a54349b19))

- **scanner**: Follow symlinks on Python 3.13+ (#512)
  ([#512](https://github.com/pvliesdonk/markdown-vault-mcp/pull/512),
  [`98bbad9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/98bbad9d3284f418027bd48dc058d703dc804b0e))

- **scanner**: Follow symlinks on Python 3.13+ (closes #508)
  ([`ea9c6d3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ea9c6d3e43f0b1d580a6482df206c26cf16a352c))

- **search**: List attachments when SOURCE_DIR is itself a symlink
  ([`c0df6f1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c0df6f15f8ffea06394c5bb8f3ab073f0c1b120e))

- **search**: Skip length-downweight in get_similar / get_context.similar (closes #472) (#473)
  ([#473](https://github.com/pvliesdonk/markdown-vault-mcp/pull/473),
  [`dd4ac42`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dd4ac42beddbd0c33e56a8fb17d44e566bbe307d))

- **uploads**: Claude-review follow-ups (#443)
  ([#443](https://github.com/pvliesdonk/markdown-vault-mcp/pull/443),
  [`0979aa7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0979aa7774ed25a949877dbd400aead3012dfcde))

- **uploads**: Pre-PR review fixes
  ([`ea85614`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ea8561436d75c0edbf49f23c97989f557bde5459))

- **uploads,server**: #443 follow-ups — bypass attachment size cap on upload route, widen
  fx_transport (#476) ([#476](https://github.com/pvliesdonk/markdown-vault-mcp/pull/476),
  [`98f73b7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/98f73b7233087eafe67bfbb41c451b1c423c9390))

### Chores

- **copier**: Update to v1.6.1
  ([`e46f7b7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e46f7b79ed7981b19e32a2575a3a3343e37099ec))

- **copier**: Update to v1.6.1 (#455)
  ([#455](https://github.com/pvliesdonk/markdown-vault-mcp/pull/455),
  [`8b3d4d6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8b3d4d66764b159ed458948d9316bfb56d69ec0d))

- **deps**: Bump astral-sh/setup-uv from 8.1.0 to 8.2.0 (#640)
  ([#640](https://github.com/pvliesdonk/markdown-vault-mcp/pull/640),
  [`5089fe1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5089fe131cd0a855e5b47388d166430291b880df))

- **deps**: Bump authlib from 1.6.11 to 1.6.12 (#485)
  ([#485](https://github.com/pvliesdonk/markdown-vault-mcp/pull/485),
  [`ca9d9f9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ca9d9f985b864321f3621c5845595410cc16a98e))

- **deps**: Bump gitleaks/gitleaks-action from 2.3.9 to 3.0.0
  ([`d78a9bb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d78a9bb65e2096f3d6c5e55cce5f475e18410dc5))

- **deps**: Bump gitleaks/gitleaks-action from 2.3.9 to 3.0.0 (#556)
  ([#556](https://github.com/pvliesdonk/markdown-vault-mcp/pull/556),
  [`62ded9c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/62ded9c849544a774e5c2e7de17f36cc3ae183e9))

- **deps**: Bump idna from 3.11 to 3.15
  ([`96bdfea`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/96bdfea6392ffd504c08fec1dae629bdde62af62))

- **deps**: Bump idna from 3.11 to 3.15 (#507)
  ([#507](https://github.com/pvliesdonk/markdown-vault-mcp/pull/507),
  [`4f44f58`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4f44f58a8c12e359843be7fa3e87cf9e626f5521))

- **deps**: Bump pip + python-multipart in uv.lock (closes #435, #436, #460) (#461)
  ([#461](https://github.com/pvliesdonk/markdown-vault-mcp/pull/461),
  [`f1a74eb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f1a74ebf5cd419d92515122cf5f17f447f074fbc))

- **deps**: Bump pip 26.0.1→26.1.1 and python-multipart 0.0.26→0.0.28
  ([`1d6aaff`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1d6aaffded4be4ead7cfd72afea6d9b62395bab2))

- **deps**: Bump pyjwt to 2.13.0 to clear audit advisories (#594)
  ([#594](https://github.com/pvliesdonk/markdown-vault-mcp/pull/594),
  [`65cf485`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/65cf4854e41675c13339de1f43a2aa1904763cdf))

- **deps**: Bump pymdown-extensions from 10.21 to 10.21.3
  ([`2204c98`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2204c987cf87bfa3467663c1d5735955bf25f6db))

- **deps**: Bump pymdown-extensions from 10.21 to 10.21.3 (#506)
  ([#506](https://github.com/pvliesdonk/markdown-vault-mcp/pull/506),
  [`586845d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/586845d35071cbd22ca8745369a87eb914274b99))

- **deps**: Bump urllib3 from 2.6.3 to 2.7.0
  ([`673b78e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/673b78e93769697de42ff7c68d55db0fb59d7da4))

- **deps**: Bump urllib3 from 2.6.3 to 2.7.0 (#470)
  ([#470](https://github.com/pvliesdonk/markdown-vault-mcp/pull/470),
  [`2472b6c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2472b6c3d0ef9ad259ab6605bea466c4698310aa))

- **deps**: Refresh uv.lock to address starlette PYSEC-2026-161
  ([`fb4ce90`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fb4ce90412c4f2ddd82943fe62f33eed44f8a4f1))

- **deps**: Refresh uv.lock to address starlette PYSEC-2026-161 (#552)
  ([#552](https://github.com/pvliesdonk/markdown-vault-mcp/pull/552),
  [`7b3adee`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7b3adeecf66aeb4136b4b5b90ecd76aebb5c6fa9))

- **deps**: Update fastmcp-pvl-core requirement from <3,>=2.1.0 to >=2.1.0,<4 (#557)
  ([#557](https://github.com/pvliesdonk/markdown-vault-mcp/pull/557),
  [`535c3a0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/535c3a020dad33628cd98cb4d0c4044ac731dd86))

- **mcp-apps**: Polish series — graph perf, test dedup, conformance (#478)
  ([#478](https://github.com/pvliesdonk/markdown-vault-mcp/pull/478),
  [`893fd95`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/893fd95a9f0076010b1d93a04c9c5dbe883afb96))

- **search**: Remove unused _apply_chunks_per_doc_cap; export GroupedResult/SectionHit
  ([`4790902`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/47909023eb6d4f439d957d013475b8063b503f28))

- **server**: Drop unused TypeVar T in _server_readiness
  ([`58fbcbc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/58fbcbcd88a46a7c493dc5efa3c7b5d34f22b61c))

- **template**: Adopt copier template v1.5.1 (pvl-core 2.0, fastmcp 3.2.4)
  ([`18957d6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/18957d6ecad701563ae43ce2c1a595b3d6aa2d18))

- **template**: Adopt copier template v1.5.1 (pvl-core 2.0, fastmcp 3.2.4) (#439)
  ([#439](https://github.com/pvliesdonk/markdown-vault-mcp/pull/439),
  [`d6a1752`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d6a17521a481f20942099d8afdb8409a81193c36))

### Code Style

- Ruff format config + tests after openai-compatible PR
  ([`d88e39e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d88e39e207cd38e1bc13308859b00b1d776b8232))

### Documentation

- Align IndexUnavailableError docstring with decorator three-trigger framing
  ([`73ee7fa`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/73ee7fa08d61204a21b41c6b1b2219498a7da48e))

- Background FTS build contract + tool-layer wait boundary
  ([`bed54e3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bed54e3ea4f4482404581327e79bff9d9d345459))

- Bucket-3 enumeration + warm-restart sentinel description
  ([`2eeb9f6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2eeb9f695b80c5a70bbaf6d79fa34d2c27d4c031))

- Clarify get_index_status error field is independent of status
  ([`8e716db`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8e716db490aeac4d3790c6f9dac9d743dab2e325))

- Correct inherited Raises/Returns docstring gaps (closes #614) (#621)
  ([#621](https://github.com/pvliesdonk/markdown-vault-mcp/pull/621),
  [`23591ac`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/23591ac62182970e205747bba5872d9976a6fab2))

- Document reason discriminator in IndexUnavailableError surfaces
  ([`4a4fc14`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4a4fc146e0e3fbb62096c690494c6fcb7909d018))

- Document reason="broken" / reason="busy" in IndexUnavailableError surfaces
  ([`2f96bfa`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2f96bfa934414bccc5a0ad9430e53194eb48cc27))

- Drop dangling errors-as-events pointer from design.md
  ([`ca17b5a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ca17b5a65719aadb236c32c35f4c0ef09405af77))

- Drop IndexBuildFailedError references from prose surfaces
  ([`21e1cac`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/21e1cacaa5f0fa3414c8110c858569a9181df2e1))

- Field collapsing (issue #469) — design.md, tools, prompts, env vars
  ([`65833de`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/65833de511997af9d063330a9fb5e91b0954924b))

- Fix _VAULT_APP_TOOL_NAMES comment to name the right validation site
  ([`a924891`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a924891cde9e68add3498e98fd0d2c7361097b84))

- Fix stale 10.0 default in load_config() docstring + add MAX_NOTE_READ_BYTES
  ([`67d3a64`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/67d3a6411e2619c91be19a90b928d22992ed0add))

- Name reason discriminator in needs_queryable decorator docstring
  ([`0bae999`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0bae9992494152f97b8fcf5a0e50f15db5435a8b))

- Point register_file_exchange deferral NOTE at #431, not #438
  ([`3366090`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/33660909e69532da05836811187702d960493b33))

- Tighten get_index_status error-field semantics
  ([`b9a820c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b9a820ce714eb5883f8cb421615928c5c6ad529e))

- **442**: Document new env vars + Context cost convention
  ([`e7edaad`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e7edaad1fabd2b052a4cb97893f12e8203cdeabf))

- **claude**: Drop bot-reviewer merge-gate language
  ([`28ff79b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/28ff79bffa3a67ff3aa894c5f2c23f47dcf77638))

- **claude**: Drop bot-reviewer merge-gate language (#522)
  ([#522](https://github.com/pvliesdonk/markdown-vault-mcp/pull/522),
  [`f97db22`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f97db229c804d23c53f7b64e1bde5b83e9f0c125))

- **design**: Record MAX_ATTACHMENT_SIZE_MB default change + MAX_NOTE_READ_BYTES
  ([`b6f0358`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b6f03585a3b448db8bc9d867dbed34ca4bffd847))

- **design**: Use present tense for wait_until_queryable; drop stale pre-#513 framing (#538)
  ([#538](https://github.com/pvliesdonk/markdown-vault-mcp/pull/538),
  [`cb3483a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cb3483ad365021221698080cdb69ae53236bc1c5))

- **fs,configuration**: Correct symlink rationale and document SOURCE_DIR behavior
  ([`7466b8a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7466b8a3d77794eacbfffc5b99da3b1d495c3b0a))

- **git-tools**: --since/--until filter on committer date (#483)
  ([#483](https://github.com/pvliesdonk/markdown-vault-mcp/pull/483),
  [`f14f236`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f14f23687a48d330e7a89e7d2ef6d94297b01952))

- **git_sync**: Tool entry + manual-sync flow guide
  ([`665b395`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/665b3957ef1325aa0e8b1cb9f630d9c69e2d0ab0))

- **packaging**: Wire OPENAI_BASE_URL + OPENAI_EMBEDDING_MODEL into manifests
  ([`041ccd7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/041ccd7ae3bb7aa169e13dacfb7fa33b6d7dd1ac))

- **plans**: Three PR plans for the out-of-band ops design (#442/#443/#444)
  ([`71cc158`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/71cc158afda8046e78222427c7d357c51d111ebc))

- **readiness**: Document queryable rename, priority flip, IndexUnavailableError, BUILD_TIMEOUT_S
  (#538) ([#538](https://github.com/pvliesdonk/markdown-vault-mcp/pull/538),
  [`60f9e70`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/60f9e7030e3d886cc8b4d85246bc0a4bc69a3d0b))

- **server**: Replace 'ready' prose with queryable/built/unavailable per surface (#538)
  ([#538](https://github.com/pvliesdonk/markdown-vault-mcp/pull/538),
  [`b534ca9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b534ca9c6e2328bc88527a9dbd6e47cb050b28a2))

- **spec**: Out-of-band file ops + git sync design (#442 / #443 / #444)
  ([`b23a14b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b23a14b9a6801bc16516c4a25bf1025db86046dc))

- **tools**: Add Context cost disclaimer to read/write/fetch tools
  ([`a3a6967`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a3a6967cbd8a80f6747bf78223c449b2ce5a8fde))

- **uploads**: Create_upload_link tool entry + claude-desktop example
  ([`2887764`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2887764e477eb3924715ecfd1c2eb89679a75869))

- **write**: Drop "(when available)" hint and fix param name
  ([`49adfa8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/49adfa8c8adfbca5a35fe4c9fc5665e1c195fb54))

### Features

- Add IndexUnavailableReason Literal + reason field on IndexUnavailableError
  ([`5f64156`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5f64156638f23006e35b7c00ca9305cdd8959e63))

- Add IndexUnavailableReason Literal + required reason field (#554)
  ([#554](https://github.com/pvliesdonk/markdown-vault-mcp/pull/554),
  [`5d4d1a8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5d4d1a8297c73661e4bcefc76e16a6ff2864b596))

- Adopt fastmcp-pvl-core 3.x — conforming logging + KV_STORE_URL support (#636)
  ([#636](https://github.com/pvliesdonk/markdown-vault-mcp/pull/636),
  [`179f632`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/179f63257d95fa636a3b92dcaffbd8ef932d7e8e))

- Capture async writer-job failures into get_index_status (#563)
  ([#563](https://github.com/pvliesdonk/markdown-vault-mcp/pull/563),
  [`32385d9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/32385d9f9b1d8fada6d9013c1f953878a4950f09))

- Catch sqlite3.OperationalError in needs_queryable decorator (#555)
  ([#555](https://github.com/pvliesdonk/markdown-vault-mcp/pull/555),
  [`e076017`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e076017a9503c6123db03315020dd9f33b0e6fe5))

- Catch sqlite3.OperationalError in needs_queryable, classify by errorname
  ([`b633717`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b63371760e776af8d9e615622fb0a8bd588c436c))

- Context-cost docstrings + read-side size guards (#442) (#450)
  ([#450](https://github.com/pvliesdonk/markdown-vault-mcp/pull/450),
  [`c39e894`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c39e8949192cbab830b8d7505d6d74a7f89533cc))

- Drift-aware B3 readers (closes #534) (#572)
  ([#572](https://github.com/pvliesdonk/markdown-vault-mcp/pull/572),
  [`307edd1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/307edd1537024bf22dca4962e111f11168e21b81))

- Export IndexUnavailableError + IndexUnavailableReason from package root
  ([`758b8e8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/758b8e8a21bbae2de5d17c0208bc5ecbf0a1e9f9))

- Extend IndexUnavailableReason Literal with "broken" and "busy"
  ([`9d671bf`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9d671bf145518267db42c8f9fa34f9cec5a06401))

- Field-collapse multi-chunk search results (closes #469) (#471)
  ([#471](https://github.com/pvliesdonk/markdown-vault-mcp/pull/471),
  [`9d20177`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9d201776a82d32228481585362455c0c33f1eee9))

- Filesystem-event watcher for external file changes (#574)
  ([#574](https://github.com/pvliesdonk/markdown-vault-mcp/pull/574),
  [`10b739c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/10b739c826dbbdf3b7b36be526af74cb423b4b75))

- GitHub webhook endpoint for push-triggered pull (#570)
  ([#570](https://github.com/pvliesdonk/markdown-vault-mcp/pull/570),
  [`aaf8346`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/aaf8346158bff45120367c105f66cb2b66e1674c))

- One-time HTTP transfer links (upload/download) (#635)
  ([#635](https://github.com/pvliesdonk/markdown-vault-mcp/pull/635),
  [`50110af`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/50110afc38cc5bcdfe81ca81bf62aae2da1f266c))

- Rename Collection to Vault (#629) (#630)
  ([#630](https://github.com/pvliesdonk/markdown-vault-mcp/pull/630),
  [`170368b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/170368b287d534501c59d778e23e4a2ff2544d61))

- Single writer for FTS and vector indexes (#562)
  ([#562](https://github.com/pvliesdonk/markdown-vault-mcp/pull/562),
  [`95eb655`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/95eb655950eb3e7e02792fdf3e447cbdbc25ff94))

- 支持 OpenAI 兼容向量服务配置
  ([`ea7f65c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ea7f65c971f3eeed73ecb9186452763626a501f0))

- **apps**: Adapt SPA to field-collapsed search results
  ([`64b36c2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/64b36c2c2daeb407626818e619f98351580def05))

- **collection**: Add is_index_ready() and background-build state
  ([`4de5fe5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4de5fe5484beede23e5416a96bee3931b2aed28e))

- **collection**: Close() joins background build thread under lock
  ([`1b942e6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1b942e6a0ec2d13669ba6f5ae0fd853c14762fbf))

- **collection**: Event-based wait_for_index_ready with 4-step control flow
  ([`9be26a2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9be26a286ffcec4d80e77dc207abf96b16ac3de1))

- **collection**: Should_use_background_build() lifespan predicate
  ([`6b03596`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6b03596616e53a9b91aa53ba3f6749772f0f8d15))

- **collection**: Start_background_build_index daemon thread
  ([`7abe0bc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7abe0bc8c23375104657d257f143ab27f5a8878f))

- **collection,server**: Get_index_status method + MCP tool
  ([`4e8e4d6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4e8e4d6ee57f60a65094d3a6c5e844e6110074d4))

- **config**: Add MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES env var
  ([`90103c5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/90103c5ddde57ae39cb61782540d4b769724b14e))

- **config**: Lower MAX_ATTACHMENT_SIZE_MB default 10 → 1
  ([`0c328ca`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0c328ca11a8b4ac4bf214b69e22d157334c15a31))

- **embeddings**: Support OpenAI-compatible API configuration (#505)
  ([#505](https://github.com/pvliesdonk/markdown-vault-mcp/pull/505),
  [`1cfe45c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1cfe45c6faba16afd9f9e11f4cb3641c36067f3e))

- **exceptions**: Add IndexBuildFailedError for #513 PR1
  ([`435df3a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/435df3a240f963b23bb114ef7fb8dd79b7b8e232))

- **fts**: Per-thread SQLite connections for thread-safe Collection
  ([`445a06e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/445a06e8f121a3e397165e3a6ded225c997d33b4))

- **fts**: Per-thread SQLite connections for thread-safe Collection (#519) (#523)
  ([#523](https://github.com/pvliesdonk/markdown-vault-mcp/pull/523),
  [`6a46eb4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6a46eb472887ec2a1c574b51df9144e9d5cd85f1))

- **git**: Git_sync MCP tool + GitWriteStrategy.force_pull/force_push (#444) (#465)
  ([#465](https://github.com/pvliesdonk/markdown-vault-mcp/pull/465),
  [`e78ff85`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e78ff85e4d6cface45fb197f15154a0c6d46b70c))

- **git**: GitWriteStrategy.force_pull + PullResult/PushResult dataclasses
  ([`ff3aa3a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ff3aa3ad831419e800e014e3602af0cb4c6a0d3f))

- **git**: GitWriteStrategy.force_push synchronous push
  ([`4c981c8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4c981c82dabca5310060fd7df85c238ca2e69819))

- **git**: Split commit author from committer for per-user attribution (#569)
  ([#569](https://github.com/pvliesdonk/markdown-vault-mcp/pull/569),
  [`cd6ae3d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cd6ae3d10dba8cc835f37b9bbb7dd4c0f8fe3687))

- **git**: Use OIDC claim values as git commit identity per request (#567)
  ([#567](https://github.com/pvliesdonk/markdown-vault-mcp/pull/567),
  [`25d30cd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/25d30cdb3ca6111dc9a7e03856507d470162c1ae))

- **graph**: Expose limit on get_backlinks / get_outlinks (closes #617) (#619)
  ([#619](https://github.com/pvliesdonk/markdown-vault-mcp/pull/619),
  [`180d2ec`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/180d2ec0e1bf369e44dbfe61b8d2a2b9a299e5eb))

- **index**: Propagate start_line into vector metadata
  ([`db691cb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/db691cba89445b5858e5a5815b58873a32ff3c22))

- **packaging**: Expand mcpb + .claude-plugin env coverage (closes #345 #346) (#479)
  ([#479](https://github.com/pvliesdonk/markdown-vault-mcp/pull/479),
  [`cc96f06`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cc96f060f015098eeb232611a1dc6353a6d1dbd9))

- **read**: Error messages point LLMs at the right alternative
  ([`353ab60`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/353ab603f06d744d0ecd65a919173f4c0b96ea45))

- **read**: Size guard on full-document reads (MAX_NOTE_READ_BYTES)
  ([`e0f000a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e0f000a7048c23d2b19c5b75f02cf7c39d99a34d))

- **search**: Add _group_by_path helper for field collapsing
  ([`201ef95`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/201ef9523babc7c63db0713a972f9469ff450b2a))

- **search**: Deterministic section_id tiebreaker for tied search results (#499)
  ([#499](https://github.com/pvliesdonk/markdown-vault-mcp/pull/499),
  [`deb58b7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/deb58b7b73346b8a9fa16caafcb3649ed8374bd3))

- **search**: Field-collapse get_similar into GroupedResult
  ([`6a36658`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6a36658e9e0280e407bacab8b20be5ee4b3672d9))

- **search**: Field-collapse search results into GroupedResult
  ([`ef019d6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ef019d6a0480d623f4ee128e09f3a3227e3092a9))

- **search**: Get_context.similar returns GroupedResult; remove SimilarItem
  ([`438833c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/438833c964ee11b33e4c6f2225ce2871bd96b7bd))

- **server**: Add Collection singleton accessor for non-DI callers
  ([`c882071`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c882071e30ac1e2b1d614d324df7391320bf2cdb))

- **server**: Apply needs_index_ready to remaining 8 bucket-3/4 surfaces
  ([`c2d4c7d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c2d4c7d59a1a326cd3bc7225124204b051672291))

- **server**: Cold-start background FTS via tool-layer wait (#513 PR1, attempt 7) (#529)
  ([#529](https://github.com/pvliesdonk/markdown-vault-mcp/pull/529),
  [`7da481f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7da481f95d4093a308f08c5d1e0d30484f9260a1))

- **server**: Hide git_sync when git mode isn't managed
  ([`d6420b3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d6420b34156c75c0b00e69d33a64f3b2e47f1e60))

- **server**: Lifespan routes cold on-disk to background; gates embeddings
  ([`ea2922e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ea2922e0ff811a8181920ace3e7e031f99b9e319))

- **server**: Needs_index_ready decorator + apply to get_backlinks (COMMIT A — preflight)
  ([`7b2496d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7b2496d7069886a72cad4e1a8e09d9f23aa5f2c4))

- **server**: Surface chunks_per_file + GroupedResult shape on MCP tools
  ([`0633420`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/063342059890395e586fa30219595e01a4a3b374))

- **server**: Wire register_file_exchange_upload with MV receiver
  ([`5cdb0c3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5cdb0c3752b578bf013857571848032ca5fa5133))

- **tools**: Git_sync MCP tool
  ([`a237db6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a237db6efd94e62f17eebf6d93c2c73eedaf35fa))

- **types**: Add SectionHit and GroupedResult for field-collapsing
  ([`cd2a3d7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cd2a3d755f15965d494b21f20082ccb686d8cddf))

- **uploads**: _validate_upload_target rejects bad paths in-band
  ([`e21a147`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e21a147faea4ec0da7e417c2a42b7becd1e6b5e3))

- **uploads**: _vault_upload_receiver dispatches by extension
  ([`e11429d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e11429dd5ca83036580a234015027feba9de317f))

- **uploads**: Adopt register_file_exchange_upload (#443) (#457)
  ([#457](https://github.com/pvliesdonk/markdown-vault-mcp/pull/457),
  [`3842116`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/38421166e27a1d682b2cfefe1325a5d23735c482))

### Performance Improvements

- **fts**: Add sections(document_id) index for start_line subquery
  ([`bf849ad`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bf849ad815bd89594789e85164357c16cb014da6))

- **tests**: Module-scope the shared read-only vault fixture (#632)
  ([#632](https://github.com/pvliesdonk/markdown-vault-mcp/pull/632),
  [`b74c845`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b74c8459554bcbf5a765326e93fb4ff84224a8ea))

### Refactoring

- Delete IndexBuildFailedError exception class
  ([`2ed1b26`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2ed1b262d1e9c28699709b5350e38eebfca34ef5))

- Drop IndexBuildFailedError, simplify queryable contract (#550)
  ([#550](https://github.com/pvliesdonk/markdown-vault-mcp/pull/550),
  [`3a13515`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3a13515c22f0ee25bae5df992930ef193526cd26))

- Extract index-write state machine into indexing/ package (closes #576) (#582)
  ([#582](https://github.com/pvliesdonk/markdown-vault-mcp/pull/582),
  [`e215020`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e2150207b1b1e88d449851b6508e39d73058bc9a))

- Migrate production callers to the facet surface (#605) (#623)
  ([#623](https://github.com/pvliesdonk/markdown-vault-mcp/pull/623),
  [`f835ee8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f835ee872a25770285e1b8a9cc1da26960f521ac))

- Migrate test suite to the facet surface (#606) (#626)
  ([#626](https://github.com/pvliesdonk/markdown-vault-mcp/pull/626),
  [`f65af88`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f65af886cae79f1836ed9db9600fda912bcf822b))

- Move attachment context-cap from the vault library to the MCP tools (#634)
  ([#634](https://github.com/pvliesdonk/markdown-vault-mcp/pull/634),
  [`66aed51`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/66aed5184028eeea5d6f1a8ba99010bbba6f4618))

- Re-align get_index_status to call is_queryable()
  ([`8004b4a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8004b4a19e3fd58be042df92cb428ccb095a3301))

- Remove all file-exchange / one-time-link tooling (closes #620) (#624)
  ([#624](https://github.com/pvliesdonk/markdown-vault-mcp/pull/624),
  [`09fd4c0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/09fd4c079f069a2e9da5dbbb651d67d31d776897))

- Remove the 39 flat Collection delegators; re-home API docs to facets (#627) (#628)
  ([#628](https://github.com/pvliesdonk/markdown-vault-mcp/pull/628),
  [`5c0882b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5c0882b181ca26c8f88bf379514d41e23854c626))

- Simplify is_queryable() to two-field check
  ([`a42c6af`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a42c6afde09b2fa50db7a7035aee54f3ad8e3ee1))

- Simplify wait_until_queryable() to three-step contract
  ([`34dd99f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/34dd99f059b95028c5626cd48fe01c4b9d5d5e05))

- Type fts_index, vector_index, providers, collection (closes #575) (#581)
  ([#581](https://github.com/pvliesdonk/markdown-vault-mcp/pull/581),
  [`2624dcf`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2624dcf08288bbbc7f196bbf31c846c0f2e7785b))

- **_server_tools**: Collapse git_sync to orchestrator using helpers
  ([`7f36d8b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7f36d8b485c48eeec738f896397d99c60938e54e))

- **_server_tools**: Drop async from _resolve_managed_strategy
  ([`78c120a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/78c120aa41b43c1225ab71e5c5bc9cd89cd84683))

- **_server_tools**: Extract git_sync helper functions
  ([`cce4608`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cce460880e480428875c2412c750ed7ee63db484))

- **collection**: Address PR #526 review findings
  ([`79ddc5e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/79ddc5e59aacc7701bb6a9f8b52d1b0289f233ba))

- **collection**: Extract reader/writer/graph/index facets (#604) (#615)
  ([#615](https://github.com/pvliesdonk/markdown-vault-mcp/pull/615),
  [`018d74f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/018d74f94c5091d982ee7fea472a0dae25c0b5d0))

- **collection**: Extract WriteCallbackDispatcher into write_callback.py (#602)
  ([#602](https://github.com/pvliesdonk/markdown-vault-mcp/pull/602),
  [`02498e7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/02498e768213fa44d9999c8f6e53de7fbd75a5cc))

- **collection**: Remove lazy-init; per-bucket readiness policy
  ([`ec77fc4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ec77fc4433b0d0aabe4366a0839c8c12ca061d08))

- **collection**: Remove lazy-init; per-bucket readiness policy (#526)
  ([#526](https://github.com/pvliesdonk/markdown-vault-mcp/pull/526),
  [`18fcffd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/18fcffd48c1bf4ea03c9dcaf3fa7982aadf1c525))

- **collection**: Rename _require_index_ready -> _require_built (#538)
  ([#538](https://github.com/pvliesdonk/markdown-vault-mcp/pull/538),
  [`4697a13`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4697a138af4de5e5b8456058a34e3735fd3ddfc8))

- **config**: Group CollectionConfig fields into config_sections sub-configs (#613)
  ([#613](https://github.com/pvliesdonk/markdown-vault-mcp/pull/613),
  [`84a301e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/84a301ec2310c925e2efc3d6d9511588dcee436b))

- **config**: Remove dead auth-field duplicates from CollectionConfig (#611)
  ([#611](https://github.com/pvliesdonk/markdown-vault-mcp/pull/611),
  [`79be803`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/79be803d87d15d482e77d714d8b34228a582d488))

- **config**: VaultConfig.from_env + frozen sub-configs, retire load_config (#637)
  ([#637](https://github.com/pvliesdonk/markdown-vault-mcp/pull/637),
  [`bf29b37`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bf29b377f5252e7d994118a22650d071838d0194))

- **fts**: Address remaining PR #523 review nits
  ([`1263051`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1263051dba433e015de856e54e47ea69bda8eec9))

- **git**: Extract _abort_in_progress_rebase + _restore_upstream_paths
  ([`3908595`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/39085950619223c933e6dbc1bf7cf80878fe11b6))

- **git**: Extract _check_rebase_in_progress helper
  ([`cc5bcf3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cc5bcf3e229de7db78b984c93635d34ad6ea5520))

- **git**: Extract _redact helper for token sanitization
  ([`b1e8bf8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b1e8bf82430f7b5f837c114a26536f3948b65715))

- **git**: Extract _resolve_conflicts_safely defensive wrapper
  ([`2d5dd7d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2d5dd7ddf7911d3b4ffbfcc3af8ad06aa9e440c1))

- **git**: Extract _run_git_capturing helper
  ([`a68c79b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a68c79bc0bfd19cbdddaded0cfd4eeb48a3caced))

- **git**: Final orchestrator polish — failure factory + success helper
  ([`3a8eae8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3a8eae89baa9d491d71ead91cbb5fbd1c958bc08))

- **git-tools**: Wrap get_history / get_diff(per_commit=True) responses in {commits, total} envelope
  (#482) ([#482](https://github.com/pvliesdonk/markdown-vault-mcp/pull/482),
  [`cb165ae`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cb165ae239afc104694febd4bd71538a61023134))

- **managers**: Move stats() into SearchManager + extract GitQueryManager (#612)
  ([#612](https://github.com/pvliesdonk/markdown-vault-mcp/pull/612),
  [`0594fab`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0594fab95832b2311dbfed1e39462782d5d84055))

### Testing

- _reindex_after_pull handles not-ready gracefully
  ([`5ace2c9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5ace2c9d8d529bd80b7b4024e440d56ed0149ba4))

- Add error-path coverage for SPA tool-addressing validators
  ([`3d092d1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3d092d1580c2820aa74e8d0d741fd1b0ff60349d))

- Assert .reason at every raise-catch site for IndexUnavailableError
  ([`1722a64`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1722a6402163fb2ad70f3c2e4efa585bbc633e82))

- Close Vault-owning fixtures via yield/finally (#631)
  ([#631](https://github.com/pvliesdonk/markdown-vault-mcp/pull/631),
  [`3e498df`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3e498df9583306fe408f7447ceb0fbcf70fa2ff7))

- Lock down corrected boundary + git pull loop + on-disk write race
  ([`feb0201`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/feb0201509e5136c48263355d12d55bd62d5b5fe))

- Sync on wait_for_drain in foreground-write race test (fixes #647) (#648)
  ([#648](https://github.com/pvliesdonk/markdown-vault-mcp/pull/648),
  [`41ff3b5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/41ff3b5505107c88279e57e6c6184f47e5b5f418))

- 补充 OpenAI 兼容配置优先级验证
  ([`b672b18`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b672b188e976387396431773bc23157de6dc3b81))

- **config**: Xfail test_includes_all_collection_params on flaky HuggingFace download (#596)
  ([#596](https://github.com/pvliesdonk/markdown-vault-mcp/pull/596),
  [`993a1df`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/993a1dfcbce7a38f0fbdc98e85e17576540260dd))

- **fts**: Cover __init__ TLS slot clear on post-probe failure
  ([`4cefc77`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4cefc77d55235c18810e2318a9fdfc957d13db94))

- **fts**: Cover close()-continues-on-conn-close-error path
  ([`755fd2f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/755fd2f6e075e6764acbb5603d0eeefd9e32816b))

- **git**: Add error-branch tests to bring patch coverage above 80%
  ([`cb69a73`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cb69a73858979fdd58f89f6259b561a231ca9ca2))

- **git_sync**: Cover reindex_failure + rebase fallback error branches
  ([`52f37f7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/52f37f77853b3887d049ba78c401b0863a08f1c0))

- **git_sync**: Pull conflict surfaces conflict_files in response
  ([`777cf52`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/777cf52ecf0fdd5db857f1df70ffa9e5e00bc7d2))

- **search**: SABSA-style multi-chunk dedup regression test
  ([`5b197da`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5b197da739a3ec5f656dea0ca69cb960eef12b14))

- **uploads**: End-to-end create_upload_link → POST → vault file
  ([`f387c47`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f387c4701c43cec7c15f56fe03b88b580c413b1b))


## 1.28.0 (2026-05-03)

### Bug Fixes

- Address gemini findings on legacy chunker behaviour and tokenization
  ([`ebec934`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ebec934ab81f8afcf6c020cc9140d48f2b6a06e5))

- **scanner**: Revert _SHORT_DOC_LINES to 30 and unconditional bypass
  ([`937bb3a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/937bb3a8d8d2c7ed4d76a2dc8f8c103de7f96828))

- **search**: Symmetric query/content tokenization for snippet helper
  ([`b652180`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b652180bd4d980ddb94f9fc975746a2c6278e568))

### Chores

- **copier**: Update to v1.2.0
  ([`4920206`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4920206d231215637f0f57a6108d7baf16d6719d))

- **copier**: Update to v1.2.0 (#424)
  ([#424](https://github.com/pvliesdonk/markdown-vault-mcp/pull/424),
  [`a02ca23`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a02ca23306cd4f16cb80277fa0cb816b8c2da65f))

- **ruff**: Adopt template's preemptive per-file-ignores
  ([`70cdb30`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/70cdb303465ceca6e8e923905891a7389aab7b3c))

- **scanner**: Align ChunkStrategy protocol param name with implementations
  ([`adf3eff`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/adf3eff0ce3c29615941adf137c237d2d367e29a))

### Documentation

- Search ranking and snippet truncation
  ([`a01ad40`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a01ad4066a4552ce769e7b5aa0c32c66bcb7d2ac))

- **plan**: Search ranking + snippet truncation implementation plan
  ([`f6a070f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f6a070f16b6f40a69e4fe3b311bf2ba575bc1678))

- **readme**: Close DOMAIN block and align Development with uv conventions
  ([`2a2f866`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2a2f8665d6b343faa39e79c8a1193bd101c9bda2))

- **spec**: Search ranking + snippet truncation design
  ([`26416f2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/26416f2a375f8e289bb01ceaeabaa935fe7c2a89))

### Features

- **collection**: Wire ranking and snippet config knobs
  ([`0eccb7c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0eccb7cc516b81672d39d9aa30929d1d12217d35))

- **config**: Add search ranking and snippet config knobs
  ([`40e0827`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/40e0827817e91fba53e376b4ccc83c116c93b65a))

- **config**: Name env var in search-ranking parse errors
  ([`cfb763c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cfb763caf526066ced0f6a181a1060c7acb16716))

- **document**: Read(path, section=...) for chunk recovery
  ([`ed0e061`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ed0e0614ebea67b3beaa94a5fa8632f024282771))

- **fts**: Add chunk_count column on documents
  ([`e14c122`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e14c122bb1b9938bc321ee48d5ed13030ea89899))

- **fts**: Snippet_words projection and chunk_count on FTSResult
  ([`f0a0285`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f0a028579377c6d0bd8a7db191ff6841c9017dee))

- **scanner**: Adaptive heading-level chunker
  ([`caedb7c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/caedb7ca9dc18a16b2c97cefbf8873171dddef2f))

- **search**: Per-channel length-downweight helper
  ([`bfd5d7f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bfd5d7f2041bb05868603dd96df58a4c189b1e66))

- **search**: Per-doc cap, length downweight, snippet truncation, adaptive chunking (#433)
  ([#433](https://github.com/pvliesdonk/markdown-vault-mcp/pull/433),
  [`4bf7b61`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4bf7b6143b5c906801c747aa06bfb28285ca9678))

- **search**: Per-document result cap helper
  ([`0a55aa5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0a55aa59285f9ba29e9d85b4b25b2ea0c9efd0a7))

- **search**: Wire pipeline through hybrid mode
  ([`471cb1a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/471cb1ad01b1fc92d592fd6c3a8c9f962ca95f95))

- **search**: Wire pipeline through keyword mode
  ([`612c53a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/612c53ab3907da6ddfed8cca275ab04e483bd2f2))

- **search**: Wire pipeline through semantic mode
  ([`8849ea0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8849ea015b8dc9a657caa7f684a2d9b2e6c9a890))

- **search**: Word-window snippet helper for semantic-only hits
  ([`b7ebe4a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b7ebe4a24eb3b71d5585ac85546a4905122a21b8))

- **server**: Chunks_per_doc and snippet_words on search tool
  ([`9cfbde5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9cfbde5a7b005f139389b968875185926db384a6))

- **server**: Section parameter on read tool
  ([`fad3e02`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fad3e02397fe4437d407b24545103a760e48a770))

### Refactoring

- **search**: Replace _RankT duck-typing with Protocols
  ([`fea04f8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fea04f8dc373b017c4c03042655ee0d78a2c81dc))

### Testing

- **search**: End-to-end pipeline integration test
  ([`3089743`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3089743b9fece116efd990546c49f9c10b3f7378))


## 1.27.1 (2026-04-23)

### Bug Fixes

- **ci**: Docs deploy on v* tag push + 3-part concurrency
  ([`6987252`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/698725213e04c6ca07e2702ff89902b5d947e4cc))

- **ci**: Stage conflict markers before git checkout -B in copier-update
  ([`e2c3654`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e2c365468ac4c5b51b56f1098b2b3b007f8aabc9))

- **ci**: Stage conflict markers before git checkout -B in copier-update (#422)
  ([#422](https://github.com/pvliesdonk/markdown-vault-mcp/pull/422),
  [`aa608a9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/aa608a9a898bc546b0b68b161fc44283c1b075cc))

- **gitignore**: Add docs/superpowers/ to ignore list
  ([`75bbc97`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/75bbc97679d7edaee5a190268aa6ec49bf43527c))

- **gitignore**: Narrow .claude/ → specific per-user paths
  ([`d84b03a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d84b03a0a1c8b08f4aba64e3cfe8c54959b924e9))

- **gitignore**: Narrow .claude/ → specific per-user paths (#412)
  ([#412](https://github.com/pvliesdonk/markdown-vault-mcp/pull/412),
  [`9867490`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9867490d98542a72389e9640ef2167465f39fa52))

- **release**: Publish linux packages on prerelease too
  ([`6da56c9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6da56c945c15ac35a56d6a997894788ee3cba3bd))

- **release**: Publish linux packages on prerelease too (#411)
  ([#411](https://github.com/pvliesdonk/markdown-vault-mcp/pull/411),
  [`b076246`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b0762462b62b0318f3000608ad97e6f77ad6e103))

### Chores

- Retire SYNC.md
  ([`0fd9caa`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0fd9caad597ec1383ea016194b0aabc601db57e7))

- Retire SYNC.md (#414) ([#414](https://github.com/pvliesdonk/markdown-vault-mcp/pull/414),
  [`90a69ee`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/90a69ee1bd6ee297b1aed290f69a041aea40d257))

- **copier**: Bump _commit + refresh workflow to v1.1.1
  ([`7762e67`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7762e67e4194ff2dc28baabfa7a3472091dfed94))

- **copier**: Converge drifted template-owned files to v1.1.5 (#421)
  ([#421](https://github.com/pvliesdonk/markdown-vault-mcp/pull/421),
  [`123dcd3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/123dcd3c45c152532393e19459095497bae6ea52))

- **copier**: Converge drifted template-owned files to v1.1.5 shape
  ([`8dbd2bb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8dbd2bb277244e4e1e95fbc695237714c5b6aca9))

- **copier**: Heavy backfill v1.0.0 -> v1.1.0 + bootstrap copier-update workflow
  ([`fab8b00`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fab8b0099807daab82fa3a7688b4ed62ec941155))

- **copier**: Heavy backfill v1.0.0 → v1.1.1 + bootstrap copier-update workflow (#415)
  ([#415](https://github.com/pvliesdonk/markdown-vault-mcp/pull/415),
  [`5e8da0b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5e8da0b7083e5da3adf819ced8f4aaaf41394c7e))

- **copier**: Update to v1.1.4 + pre-create test_smoke.py
  ([`082c54a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/082c54a835ddd7fb5978aac6eee08dc676407751))

- **copier**: Update to v1.1.4 + pre-create test_smoke.py (#418)
  ([#418](https://github.com/pvliesdonk/markdown-vault-mcp/pull/418),
  [`2db3d30`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2db3d303f4fc18bb4eb8902366e65ea32e3c325e))

- **copier**: Update to v1.1.8
  ([`19ad650`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/19ad6509bbc5a204347f6b834534bcbfa9dd0785))

- **copier**: Update to v1.1.8 (#423)
  ([#423](https://github.com/pvliesdonk/markdown-vault-mcp/pull/423),
  [`425e284`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/425e284290e2d09d82d074040446fd838466d89c))

- **deps**: Bump actions/deploy-pages from 4 to 5
  ([`0231c2d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0231c2d680ebd6f9e2250af5d179019e2c4fbe18))

- **deps**: Bump actions/deploy-pages from 4 to 5 (#409)
  ([#409](https://github.com/pvliesdonk/markdown-vault-mcp/pull/409),
  [`561eb0e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/561eb0ebc8b4f653e62461f2a4959ecb48865523))

- **deps**: Bump astral-sh/setup-uv from 8.0.0 to 8.1.0
  ([`2e912a2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2e912a2aa866b51a410771739e778998d2a5d6c9))

- **deps**: Bump astral-sh/setup-uv from 8.0.0 to 8.1.0 (#410)
  ([#410](https://github.com/pvliesdonk/markdown-vault-mcp/pull/410),
  [`c340466`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c34046613fb5ac31de8d6d69ed99d75f4660003b))

- **deps**: Bump github/codeql-action from 3 to 4
  ([`4a1fdcf`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4a1fdcfd52b152b545a64c40a1309e35fc242fa0))

- **deps**: Bump github/codeql-action from 3 to 4 (#408)
  ([#408](https://github.com/pvliesdonk/markdown-vault-mcp/pull/408),
  [`6110c6d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6110c6dc239d2c7068ce6d96315ace7881367041))

### Documentation

- **plans**: Step 5 IG retrofit implementation plan
  ([`44c7210`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/44c7210543a9f87dce60a7bb2af7f47cbca8f85a))

- **plans**: Step 6 scholar-mcp retrofit implementation plan
  ([`7c7c1aa`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7c7c1aac6a46b9296bb732c84db01a0a7f404a89))

- **specs**: Add Step 5 IG retrofit design
  ([`a7a8761`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a7a8761f80f5cfd7255cd49e21012d636f7da11c))

- **specs**: Add Step 6 scholar-mcp retrofit design
  ([`b506f60`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b506f6084909425f1174903d02a75967d8688c5e))

### Refactoring

- **claude-md**: Add DOMAIN + TEMPLATE-OWNED sentinel structure
  ([`6e036f1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6e036f1b4a4c3a146f24cf6b13e3e3f04de0d228))

- **claude-md**: Add DOMAIN + TEMPLATE-OWNED sentinel structure (#419)
  ([#419](https://github.com/pvliesdonk/markdown-vault-mcp/pull/419),
  [`1172fb9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1172fb97e6ad9224e5fc74304fb6ce371f0b4df1))

- **config**: Add CONFIG-FIELDS + CONFIG-FROM-ENV sentinel markers
  ([`56d3290`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/56d3290195a89cebf6c36e30a3ac0fb850957935))

- **config**: Add CONFIG-FIELDS + CONFIG-FROM-ENV sentinel markers (#417)
  ([#417](https://github.com/pvliesdonk/markdown-vault-mcp/pull/417),
  [`e635e18`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e635e1810e23085d14dea7525053035971bcb5e6))

- **server**: Align with template: mcp_server.py -> server.py, create_server -> make_server
  ([`148c5de`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/148c5de137e02601da207a6a446c1b897cff328c))

- **server**: Align with template: mcp_server.py → server.py, create_server → make_server (#416)
  ([#416](https://github.com/pvliesdonk/markdown-vault-mcp/pull/416),
  [`c189478`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c189478c8f0c54a05322983c9c834506255572aa))


## 1.27.0 (2026-04-21)


## 1.26.0-rc.1 (2026-04-21)

### Chores

- Adopt fastmcp-server-template v1.0.0 (#407)
  ([#407](https://github.com/pvliesdonk/markdown-vault-mcp/pull/407),
  [`9e9f013`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9e9f013dffe378b018de598b4d0e77ac87e693f8))

### Documentation

- **design**: Add Shared Infrastructure section pointing at fastmcp-pvl-core (#404)
  ([#404](https://github.com/pvliesdonk/markdown-vault-mcp/pull/404),
  [`9c12739`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9c127396cea13824078a43fc2102c7604b9d34a3))

- **plan**: Step 3 fastmcp-server-template copier scaffold implementation plan (#406)
  ([#406](https://github.com/pvliesdonk/markdown-vault-mcp/pull/406),
  [`8c8612a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8c8612a1ece3df3ca80dab31c0b5d1b0d63b6b13))

- **plans**: Step 4 bootstrap-replay implementation plan
  ([`bb000af`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bb000afbbddef1c11ab0a3568714d193abce40e8))

- **spec**: Step 3 fastmcp-server-template copier scaffold design (#405)
  ([#405](https://github.com/pvliesdonk/markdown-vault-mcp/pull/405),
  [`5b250e6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5b250e69fc020b9ad89ca9a80ee83cfbb8cf9bb5))

- **specs**: Add Step 4 bootstrap-replay design
  ([`978e6b8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/978e6b80dab7598081ee9d29cf2df2bc34cecef2))


## 1.25.0 (2026-04-20)


## 1.24.0-rc.1 (2026-04-20)

### Chores

- Repin fastmcp-pvl-core to >=1.0.0,<2 (stable) (#403)
  ([#403](https://github.com/pvliesdonk/markdown-vault-mcp/pull/403),
  [`830f882`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/830f8828d49d7e76da38ae8ef02154b00d15b662))

### Refactoring

- **artifacts**: Adopt core ArtifactStore with eager bytes (MV-PR5) (#400)
  ([#400](https://github.com/pvliesdonk/markdown-vault-mcp/pull/400),
  [`0a8e01f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0a8e01fd74655296ce51011ba3f0f719b3f9a436))

- **auth**: Delegate auth builders to fastmcp-pvl-core (MV-PR2) (#397)
  ([#397](https://github.com/pvliesdonk/markdown-vault-mcp/pull/397),
  [`c2e3238`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c2e3238361bf2055a3f4bc7eb83a69c391e5f5e9))

- **cli**: Adopt core normalise_http_path + rename --path to --http-path (MV-PR6) (#401)
  ([#401](https://github.com/pvliesdonk/markdown-vault-mcp/pull/401),
  [`650bbe2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/650bbe2e8012359af9079fa86a35b67f2d15ce46))

- **config**: Adopt fastmcp-pvl-core env helpers + ServerConfig (MV-PR1) (#396)
  ([#396](https://github.com/pvliesdonk/markdown-vault-mcp/pull/396),
  [`89e7596`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/89e759607b4d0705c3ef6a2bacc82c037742ae85))

- **server**: Adopt core wire_middleware_stack + configure_logging_from_env (MV-PR3) (#398)
  ([#398](https://github.com/pvliesdonk/markdown-vault-mcp/pull/398),
  [`951f16f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/951f16f86173ddaf5fe6e7d9973031cbda848502))

- **server**: Collapse auth dispatch into build_auth() (MV-PR7) (#402)
  ([#402](https://github.com/pvliesdonk/markdown-vault-mcp/pull/402),
  [`12fac6a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/12fac6aab00120e401377416f104436eb49c5448))

- **server**: Delegate instructions + event store to core (MV-PR4) (#399)
  ([#399](https://github.com/pvliesdonk/markdown-vault-mcp/pull/399),
  [`5706948`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5706948b0c8e90510c062109c8e0b57b3de6ef7c))


## 1.23.1 (2026-04-20)


## 1.23.2-rc.1 (2026-04-20)

### Testing

- Accept PSR prerelease version format in mcpb packaging tests (#395)
  ([#395](https://github.com/pvliesdonk/markdown-vault-mcp/pull/395),
  [`132fc92`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/132fc92cdeaff4c1bb14bcaa6f65d03434dbc0ab))


## 1.23.1-rc.1 (2026-04-19)

### Bug Fixes

- Rewrite bump_manifests as Python for PSR Docker container (#394)
  ([#394](https://github.com/pvliesdonk/markdown-vault-mcp/pull/394),
  [`a23b571`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a23b571fbb78768b3f7e9d6eee6c02b8b6feb9ee))

### Continuous Integration

- Docs workflow checks out main on release to avoid tag-moved race (#391)
  ([#391](https://github.com/pvliesdonk/markdown-vault-mcp/pull/391),
  [`fa08788`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fa08788fa20411df247da926d0079742284baa31))

- Move manifest bump into semantic-release build_command (#393)
  ([#393](https://github.com/pvliesdonk/markdown-vault-mcp/pull/393),
  [`b27b20b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b27b20b27edfcaa0311978b4198204794e1dfb31))


## 1.23.0 (2026-04-19)

### Bug Fixes

- Increase uvicorn graceful shutdown timeout to 3s
  ([`59a4f98`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/59a4f98a7d4992adbdd72173360334c592769a12))

- Packaging script bugs from scholar-mcp cross-review
  ([`da26c59`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/da26c591b233861f6390a6dbb183100ee2f5613e))

- **ci**: Fix marketplace.json path in publish-claude-plugin-pr
  ([`cbbb286`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cbbb286df302e1e3d7d66aea85db62d70725918b))

- **examples**: Quote {{date}} placeholder in Zettelkasten template frontmatter
  ([`65af8f6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/65af8f6fb84bcc58d66c458efc60f3665668ece2))

### Chores

- Update manifests to v1.23.0 [skip ci]
  ([`481ccb4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/481ccb41759ede8fd3174ec8d7a3301af6c61ac5))

### Continuous Integration

- Publish docs only on release, not on every push to main (#389)
  ([#389](https://github.com/pvliesdonk/markdown-vault-mcp/pull/389),
  [`c88d768`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c88d768bcba0833d9847228ca8d4fa913593895f))

### Documentation

- Add PARA workflow design spec
  ([`683b003`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/683b00333c924f3eef5a8f890513f1fac9c6d869))

- Add research workflows guide (#390)
  ([#390](https://github.com/pvliesdonk/markdown-vault-mcp/pull/390),
  [`70dc144`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/70dc14488d5dfa15dacd65cca5703340e1a4a5c5))

### Features

- Add PARA workflow pack (Projects/Areas/Resources/Archive) (#385)
  ([#385](https://github.com/pvliesdonk/markdown-vault-mcp/pull/385),
  [`7faee36`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7faee36fd77dec04157276e66e30772b33303aef))

- Add propose-links prompt + surface LLM-native flows in docs (#388)
  ([#388](https://github.com/pvliesdonk/markdown-vault-mcp/pull/388),
  [`529156f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/529156fa0cf5821be3970250e0c5c16ae8befe26))


## 1.22.1 (2026-04-17)

### Bug Fixes

- **ci**: Use RELEASE_TOKEN for publish-claude-plugin-pr job
  ([`90452e9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/90452e95ab2b331c59979ae7139044e0fa23d489))

### Chores

- Update manifests to v1.22.1 [skip ci]
  ([`238e131`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/238e131b48925fb3024c1cc2166437344b44f721))

- **ci**: Bump GitHub Actions to Node.js 24-compatible versions (#379)
  ([#379](https://github.com/pvliesdonk/markdown-vault-mcp/pull/379),
  [`7192c44`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7192c4401ca136791061cae78437bfaad0f0fb0d))

- **deps**: Bump authlib from 1.6.9 to 1.6.11 (#381)
  ([#381](https://github.com/pvliesdonk/markdown-vault-mcp/pull/381),
  [`3d1ccb4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3d1ccb43bdb79152b2e37cbafce234aa0e2431ed))

- **deps**: Bump pygments from 2.19.2 to 2.20.0 (#384)
  ([#384](https://github.com/pvliesdonk/markdown-vault-mcp/pull/384),
  [`120e358`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/120e3582b4e488d2b750fa600ef94fcb7a4c9c03))

### Documentation

- API documentation overhaul — auto-discovery, types/exceptions pages, docstring audit (#380)
  ([#380](https://github.com/pvliesdonk/markdown-vault-mcp/pull/380),
  [`165c701`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/165c7016081d6614312eb99539959250864982c9))

- Improve LLM-facing tool docstrings and add icons to vault_* tools (#383)
  ([#383](https://github.com/pvliesdonk/markdown-vault-mcp/pull/383),
  [`1ddb97f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1ddb97fc05a6631d962c2615c81ce4b4e1e46e83))


## 1.22.0 (2026-04-16)

### Chores

- Update manifests to v1.22.0 [skip ci]
  ([`e77f048`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e77f048c78fea853df5a7759d537886aa9d61384))

- **deps**: Bump pillow from 11.3.0 to 12.2.0
  ([`dc94e48`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dc94e482327f471cace1aa51770595d718c634a1))

- **deps**: Bump pillow from 11.3.0 to 12.2.0 (#355)
  ([#355](https://github.com/pvliesdonk/markdown-vault-mcp/pull/355),
  [`581d46b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/581d46b2988c1cfb652f5b08929dd9262822ecd4))

- **deps**: Bump pytest from 9.0.2 to 9.0.3
  ([`7949d63`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7949d63b8a17643a8576aa980c7414007303fde1))

- **deps**: Bump pytest from 9.0.2 to 9.0.3 (#356)
  ([#356](https://github.com/pvliesdonk/markdown-vault-mcp/pull/356),
  [`6bf0824`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6bf0824e52f73d4cddf1d9f2bce01f5e6d919fe4))

- **deps**: Bump python-multipart from 0.0.22 to 0.0.26 (#360)
  ([#360](https://github.com/pvliesdonk/markdown-vault-mcp/pull/360),
  [`dfcf543`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dfcf543bc1b95696ff39a19c5d87aaa216dc2617))

### Refactoring

- **collection**: Split Collection into managers with DI (#376) (#378)
  ([#378](https://github.com/pvliesdonk/markdown-vault-mcp/pull/378),
  [`10c9e75`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/10c9e751156a47a7b0e4943f02d46577dfc6c74f))

- **config**: Centralize environment and auth configuration (#377)
  ([#377](https://github.com/pvliesdonk/markdown-vault-mcp/pull/377),
  [`2788a6f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2788a6f5fa39d06d7a4b598bed6c6c601e598b06))


## 1.22.0-rc.1 (2026-04-11)

### Bug Fixes

- Address PR #343 review comments
  ([`cd1c3ec`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cd1c3ec9c0ed05492f78ea44947d89e97ac08c67))

- Address PR #350 review — envsubst scope, server.py argv, tag ordering, pin mcpb, qualified plugin
  name
  ([`6956622`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/695662225e163bf3e9bcb28fec866cd2305e25ed))

- **packaging**: Remove spurious command/args from mcp_config
  ([`97db0d6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/97db0d620793085348b68bffde063b8825737e4f))

### Chores

- Ignore .worktrees directory
  ([`04fc6df`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/04fc6df80aadd9804a5d9ce7e326dd35a9566e7d))

- Update server.json to v1.21.0 [skip ci]
  ([`0bbbca3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0bbbca3e71482d0730704123ed21537d93ae3ca6))

### Continuous Integration

- Add build-mcpb and publish-mcpb release jobs
  ([`8dc286a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8dc286ad0dce1b582a08be2f3a0e4316cad076e3))

- Auto-open catalog bump PR on release
  ([`e6c6436`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e6c6436601618e562dacaed1b70b54d2bc38f5cd))

- Bump plugin.json and .mcp.json alongside server.json on release
  ([`8531e8e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8531e8ec35dd68071468bdd3837178fff7939b24))

### Documentation

- Add Claude Desktop + Claude Code plugin distribution design
  ([`d824ee4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d824ee4d9812021aa4483d7b8d24ca5e9726af44))

- Add claude-code-plugin guide and mcpb install step
  ([`c9dddff`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c9dddff044a25fa0f06870c2d49d9da6bb46775b))

- Add implementation plan for Claude plugins distribution
  ([`801932c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/801932c60728463c4b17c88d8b839ee65e4d9cd2))

- Add implementation plan for prerelease-mode release workflow
  ([`92f020b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/92f020b918ed29b32313cfc780293aa6e5c72f08))

- Add mcpb and Claude Code plugin install options to README; remove superpowers plan/spec
  ([`a7b6fce`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a7b6fcea55bedf5392690b9d8008ccb2d9606e39))

- Add prerelease-mode release workflow design spec
  ([`6dc2c14`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6dc2c14e03988b0dc588deba8911b0064905f0d8))

- Align Collection.get_diff docstring with boundary-inclusive wording
  ([`8efc767`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8efc7672fedcfaf10f1556a2a9779eb146d3456b))

- Document stable and pre-release channels for the release workflow
  ([`587fdc8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/587fdc8904803d45bba4c2625b5ed1151e272f76))

- Fix task 1 review findings — tick checkboxes, clarify mcp_config args
  ([`c00d07d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c00d07d9d450d06d7a57f3f24add0a0842233491))

- Record mcpb + claude code plugin verification results
  ([`89a85ae`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/89a85ae21a8de6108cd22f1ec4fdc628f882e115))

- Update CLAUDE.md gates and SYNC.md for claude-plugins
  ([`2891ecd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2891ecd7247c2730f2951b38d65029cdd6af1f43))

- **plugin**: Add Claude Code plugin README
  ([`e9c193c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e9c193c4ec964a5cd0bfd0f10803288e9f40a0a4))

- **sync**: Track prerelease mode as pending port to image-generation-mcp
  ([`751ae3c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/751ae3c15e20ef64505d94659a332eafca75261b))

### Features

- Distribute via Claude Code plugin and mcpb bundle (#350)
  ([#350](https://github.com/pvliesdonk/markdown-vault-mcp/pull/350),
  [`951f0da`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/951f0dae3adce338f934946e8cd64e5f25c8d85c))

- **ci**: Add prerelease mode to release workflow
  ([`97f9545`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/97f9545e0deb696cd2aa04b59ef205d27c02bc3d))

- **ci**: Add prerelease mode to release workflow (#353)
  ([#353](https://github.com/pvliesdonk/markdown-vault-mcp/pull/353),
  [`9fe220e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9fe220e9cdb69d01f6781253b47c8c9f6c442517))

- **git-tools**: Add until on get_history and limit on get_diff per_commit
  ([`04821e2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/04821e2454e28e9d06fc9fe313010f4bd0b3dd3c))

- **git-tools**: Add until on get_history and limit on get_diff per_commit (#343)
  ([#343](https://github.com/pvliesdonk/markdown-vault-mcp/pull/343),
  [`4e231a4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4e231a451c9d1d9e47cd56d60cdbd86eb4f19ab6))

- **packaging**: Add local mcpb build script
  ([`172544f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/172544f5f802d68c894c55cf4ab33a21386999c4))

- **packaging**: Add mcpb entry shim delegating to cli.main
  ([`02fe093`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/02fe093e00ae5d3a4a9c1e92f87b71be2766dfd6))

- **packaging**: Add mcpb manifest template
  ([`c94d28c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c94d28c45502bfafb066fce49797a5711b5685c6))

- **packaging**: Add mcpb pyproject template pinning [all] extras
  ([`94e3449`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/94e3449667a3de0c6845427bbe3b774194213ca6))

- **plugin**: Add Claude Code .mcp.json with pinned uvx --from
  ([`b9b6f58`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b9b6f588d75aefdcd40be59a13449cdbe7a9c5fe))

- **plugin**: Add Claude Code plugin.json metadata
  ([`51caacb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/51caacb13abe52cabdce5d0daa4f0ac55b9f4496))

- **plugin**: Add vault-workflow SKILL.md for Claude Code
  ([`370eb3c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/370eb3cf1e1c47fec60338821e8d25e3073feef2))

### Testing

- **packaging**: Scaffold mcpb + claude-plugin smoke tests
  ([`476ab6e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/476ab6ed1e5bd6ef8d2aadb9322a3d2befb8606f))


## 1.21.0 (2026-04-10)

### Bug Fixes

- Address fifth-round PR #337 review comments
  ([`33dd816`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/33dd816cc20cb2aede29ae93498e4d4f08a3241f))

- Address fourth-round PR #337 review comments
  ([`af60721`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/af60721eb57a5dc72b3523ab707bb13c2df13f51))

- Address PR #337 review feedback on get_history and get_diff
  ([`6aa40ee`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6aa40eeeeac717cc089ee4231306942b75534778))

- Address second-round PR #337 review comments
  ([`a166b48`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a166b48390cdc1f0194e7ebe4986234d5b8a305f))

- Address third-round PR #337 review comments
  ([`9ab4c77`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9ab4c773c05f1bae2e143a2538be3d0cbbbf2a73))

### Chores

- Update server.json to v1.20.1 [skip ci]
  ([`d64d4f1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d64d4f1d0a724eb62b756e69f7c6c26cc956d479))

- **deps**: Bump cryptography from 46.0.6 to 46.0.7
  ([`60e28d2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/60e28d2bd33e169817da8a0523daa2150624bb2c))

- **deps**: Bump cryptography from 46.0.6 to 46.0.7 (#332)
  ([#332](https://github.com/pvliesdonk/markdown-vault-mcp/pull/332),
  [`794c4a5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/794c4a566ce80c0d166b222232d94f9170d50135))

### Features

- Git history tools — get_history and get_diff MCP tools
  ([`0cd2b90`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0cd2b9071b7a14d05f08a18a0a2ea2d57ae33f66))

- Git history tools — get_history and get_diff MCP tools (#337)
  ([#337](https://github.com/pvliesdonk/markdown-vault-mcp/pull/337),
  [`1db46e3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1db46e37db34faf4f2aa196fa69a27a62936d809))


## 1.20.1 (2026-04-10)

### Bug Fixes

- Cli _build_collection propagates all CollectionConfig fields
  ([`995a6c7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/995a6c7da02217b9fe305ec9e999ff3000a9433d))

- Cli `_build_collection` propagates all `CollectionConfig` fields (#336)
  ([#336](https://github.com/pvliesdonk/markdown-vault-mcp/pull/336),
  [`f7ac6a3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f7ac6a332ce89dcde3a140dd37ef21afc77a7466))

- Post codecov/patch status for fork PRs via workflow_run
  ([`470c061`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/470c061dcccccf015c1ba70e35e56c71cdebbb79))

### Chores

- Update server.json to v1.20.0 [skip ci]
  ([`fe6835e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fe6835e0027ab625f365209f90ef2284a8246fe1))

### Testing

- Cover --index-path override in _build_collection
  ([`133cf1b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/133cf1bd34cb6acb32e95c6150e7f9f4117793b6))


## 1.20.0 (2026-04-08)

### Bug Fixes

- Address review comments and add cross-repo sync entries
  ([`f3c1f08`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f3c1f0899affa81fccdff0b1621b41768f1283a7))

### Chores

- Update server.json to v1.19.1 [skip ci]
  ([`b04d578`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b04d578ffcbe421d35dd8b43a2736faed7c1bdde))

- Update uv.lock
  ([`ad2d722`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ad2d7226f5ff4ac2bdf598b1d4d7851871f312bd))

### Code Style

- Wrap ErrorHandlingMiddleware call to satisfy ruff format (88-char limit)
  ([`07eab65`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/07eab65ecebecf7e0fc3d8882a8194ad56132033))

### Features

- Add logging standard with FastMCP middleware and PR acceptance gates
  ([`226eef3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/226eef3043cd587f78b8dff9133ef07ddfe85fd5))

- Add logging standard with FastMCP middleware and PR acceptance gates (#331)
  ([#331](https://github.com/pvliesdonk/markdown-vault-mcp/pull/331),
  [`c581122`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c581122ee05cb73d221b89a92a1ffc0a53b72bad))


## 1.19.1 (2026-04-04)

### Bug Fixes

- Bind Docker container to 0.0.0.0 by default (regression from #323)
  ([`eab9b76`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/eab9b76a9e1ae066b116b0c95db32fe3e5858695))

- Bind Docker container to 0.0.0.0 by default (regression from #323) (#329)
  ([#329](https://github.com/pvliesdonk/markdown-vault-mcp/pull/329),
  [`51dcbf8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/51dcbf8682db3926f75c59ede7b2739697952129))

### Chores

- Update server.json to v1.19.0 [skip ci]
  ([`c77b512`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c77b512dbb49cff478c2614ccf7b65eff8ec807d))


## 1.19.0 (2026-04-04)

### Bug Fixes

- Address review feedback on #321 and #322
  ([`52b31b4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/52b31b431514263634a72b04457b4953f2b76a83))

- Address review feedback on PR #328
  ([`dc12439`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dc12439272ff4d86730e59f8e7aa92e3dc515b57))

- Address security audit findings #321, #322, #323 (#327)
  ([#327](https://github.com/pvliesdonk/markdown-vault-mcp/pull/327),
  [`5f2b9c6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5f2b9c68685ed4769222c5a7491af39f72e7ee1a))

- Default HTTP host to 127.0.0.1, upgrade no-auth log to WARNING (closes #323)
  ([`244f7c0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/244f7c0a3b97d9b418b1e0edb95e04a1454596be))

- Handle malformed FTS5 queries gracefully (closes #321)
  ([`01aa983`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/01aa983a1c6a80111383d4583933928f855a000d))

- Make file writes atomic using tempfile + Path.replace (closes #322)
  ([`23341be`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/23341bef85afaea6a3abc2255833d1d011b662fa))

- Preserve original file permissions on atomic overwrite
  ([`50cd6be`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/50cd6be16a9b8edc6f32cb1dc1b68f4e8248b3c7))

- Reindex and index CLI commands now build embeddings
  ([`0e5e837`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0e5e83790dd2d928be69a5df7cb02c20fcd166ad))

- Reindex/index CLI commands now build embeddings + Claude Desktop guide improvements (#328)
  ([#328](https://github.com/pvliesdonk/markdown-vault-mcp/pull/328),
  [`ffe5ca9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ffe5ca90ab18d28d0c0f83f547810f6bbd625239))

- **edit**: Address PR review comments
  ([`fe96ce3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fe96ce3266a06893bb39e7e19adae0049b797713))

- **edit**: Fix orig_end for decomposed Unicode in _build_position_map
  ([`18c3c69`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/18c3c699ef006b322c2672f5a061b909a2e2abe1))

### Chores

- Update server.json to v1.18.1 [skip ci]
  ([`1f7f773`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1f7f773f459ba21c2038b5c1e6d7e3905c984227))

### Documentation

- Add design spec for edit tool improvements
  ([`5decef0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5decef02213d7c80667679d5dd91bfd07b492e67))

- Add implementation plan for edit tool improvements (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`4b2f9bd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4b2f9bd10f56da7032ce77ddd43b3fe1a78c03ba))

- Update design.md with edit tool improvements (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`8acc748`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8acc7489e13964a2c8c5f0ef833e1bb031062c09))

- Update README and tools docs with edit improvements (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`c51b18f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c51b18f3445a68eb1655de9bee83758142abce66))

### Features

- Improve edit tool reliability for LLM consumers (#325) (#326)
  ([#326](https://github.com/pvliesdonk/markdown-vault-mcp/pull/326),
  [`6174efb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6174efb44702b63780be1b6555f8a715367b994b))

- **edit**: Add _normalize_text and _build_position_map helpers (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`6c4ebda`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6c4ebdacd092ee36755ae3a4f8fd8f5e58694732))

- **edit**: Add diagnostic fields to EditConflictError (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`bf2dcf3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bf2dcf31b6d42cb6e619adfad983c60fc2e64fc7))

- **edit**: Add line-range edit mode to Collection.edit() (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`0c45d14`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0c45d14288c85070363666f3f7bdf5ebd0a95719))

- **edit**: Add match_type field to EditResult (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`aee1734`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/aee17345d5be7ba3d183c1c7d055eaac2d0729fc))

- **edit**: Update MCP edit tool with line-range, normalized match, diagnostics (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`3a0e7b5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3a0e7b5e8700ba88ddc9bef40201a53f68958a8e))

### Testing

- Cover write_attachment() copymode and cleanup branches
  ([`d7194be`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d7194bed3611fc8c9a67cccc7c0806596d7aa833))

- **edit**: Add scoped, normalized, and diagnostic edit tests (#325)
  ([#325](https://github.com/pvliesdonk/markdown-vault-mcp/pull/325),
  [`3096482`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3096482bcc1b47de9fd63af6de7beb6f40fa7556))


## 1.18.1 (2026-04-02)

### Bug Fixes

- Clarify browse_vault/show_context are UI-only tools (#324)
  ([#324](https://github.com/pvliesdonk/markdown-vault-mcp/pull/324),
  [`900c46c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/900c46c91dbf845036e948ddfe5cd2732e3d5fa2))

- Clarify browse_vault/show_context are UI-only tools, not data-retrieval
  ([`9e84aed`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9e84aedbdc5afdb69fbc7b264d7ae89a82f6ba26))

- Migrate app-only tools to fastmcp 3.2.0 get_app_tool routing
  ([`a557b90`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a557b90421096f49313d3ad04b4637ba1cd43d91))

- Update AppConfig/ResourceCSP import for fastmcp 3.2.0
  ([`5323c67`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5323c67f1d6e347a88b11c79580ba7fe80a92180))

### Chores

- Ruff format test files after tool name migration
  ([`b09d298`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b09d29806518d9e7fbf172aab36c47a54cb1b590))

- Update server.json to v1.18.0 [skip ci]
  ([`8eaafa4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8eaafa42bf072b92d27dc776a2e7c6d71d3d7be0))

- **deps**: Bump fastmcp from 3.1.0 to 3.2.0
  ([`d707875`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d7078750b504516ed2936ac2a721881b00e6915f))

- **deps**: Bump fastmcp from 3.1.0 to 3.2.0 (#320)
  ([#320](https://github.com/pvliesdonk/markdown-vault-mcp/pull/320),
  [`50472ba`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/50472ba220e4367573ce2359c24f255359ee66c2))


## 1.18.0 (2026-03-31)

### Bug Fixes

- Add backlink_count to neighborhood nodes (AC9)
  ([`d75b887`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d75b8872d05857da014a72adcf11359570e02c5d))

- Add ConcurrentModificationError to rename Raises section
  ([`adf5f3f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/adf5f3f38ce8bde64f9b47e78fb3654c5cf07ce6))

- Add ValueError guards to graph tools per design doc §13.1
  ([`23cbfa9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/23cbfa9910bee729a5c9eff12bec606569f49aeb))

- Address architect-reviewer findings for #273-#277
  ([`d528fc1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d528fc13499a8b0d93ebf62fb7bede3a65343732))

- Address code review findings in vendor_spa.py
  ([`001a89f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/001a89f32322e32507d86a7afae0023b849eba5d))

- Address PR #280 bot review findings
  ([`e892189`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e89218922365130f950ea7632dfacf97a9849e3b))

- Address PR #282 review comments
  ([`5e1a8b5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5e1a8b53d875bb4e78e2757c4c31be789107c3f0))

- Address PR #284 review comments
  ([`8ffae8a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8ffae8aebe16a78177f71fb2b3066611c91315fc))

- Address PR #294 review comments
  ([`29f1de4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/29f1de4be0b23bd0704c52b41141a89db4b647de))

- Address PR #299 review comments
  ([`126bca0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/126bca06cdb54b905969a8486edb91ec692d36d7))

- Address PR #301 review comments
  ([`88fe029`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/88fe0293b1cd777b9d875cb041e3eb29a33cbd7b))

- Address PR #303 review comments
  ([`a3fd4ed`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a3fd4ed5b3805567468b31f620159d29f913642d))

- Address PR #304 review nits
  ([`6638f98`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6638f980023aac1bd49afa402892a7efc56bc264))

- Address PR #313 review findings — split exception handler, min-height CSS, coverage tests
  ([`f53a586`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f53a5869847d8c71b0743dd28c759aef9f1ceac1))

- Address PR review feedback for Note tab
  ([`cec9f91`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cec9f917f9148f99bd974ff7ea6fb86bc94625b1))

- Address pre-PR review findings
  ([`408d277`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/408d277f83997fe4e3f5d2e28517314f2425dea9))

- Address remaining architect-reviewer findings
  ([`83ff782`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/83ff782bc00f4863185ba7d98845be6c360c1852))

- Change FastEmbed default to BAAI/bge-small-en-v1.5 to prevent OOM (#306)
  ([#306](https://github.com/pvliesdonk/markdown-vault-mcp/pull/306),
  [`240da6a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/240da6a6bba8711ae60b8b24c00699f0d0d0be2e))

- Change FastEmbed default to BAAI/bge-small-en-v1.5 to prevent OOM (#306) (#307)
  ([#307](https://github.com/pvliesdonk/markdown-vault-mcp/pull/307),
  [`e215a6a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e215a6aa0a5d70056052ce6a2b445152a2f4a99c))

- Context card XSS, event listeners, error handler, hardcoded colors, tests
  ([`1dc279d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1dc279d1d6968e8a3712aca2db4c36e767f980ae))

- Extract top-level folder names in _vault_list child_folders
  ([`c22c7f0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c22c7f06111aa52749d51c09e8a091369d8601cf))

- Frozenset type params for mypy + tests for include_semantic path
  ([`3897e0c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3897e0c77ba9704cdbf7f452ee08944bd0ffe6b9))

- Get_context tags field formatting in Returns section
  ([`e244f5d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e244f5dee1c993537613899cb2aac4fd46f959e3))

- Harden builtin prompt loading with error handling
  ([`1f42feb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1f42febd4167f0e73c8339b422052b2f3f83c196))

- Harden parseToolResult with try/catch and null guards
  ([`685553a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/685553a8711b61a76e0bb437784cce660e5d6b98))

- Lower outer embedding batch size from 64 to 4
  ([`a6a978b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a6a978b811fd7819f81ad44c8b8106a5d8fa9b18))

- MCP Apps SPA Android compatibility and missing tool input handler
  ([`33f48f4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/33f48f46fdb0139fbcd48edad42abeb20a422aeb))

- MCP Apps SPA Android compatibility and missing tool input handler (#300) (#301)
  ([#301](https://github.com/pvliesdonk/markdown-vault-mcp/pull/301),
  [`1c5dde0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1c5dde0fe2047564219aebe98eb183491ac24971))

- MCP Apps SPA Browse/Graph views always empty (4 bugs fixed) (#304)
  ([#304](https://github.com/pvliesdonk/markdown-vault-mcp/pull/304),
  [`2f56e57`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2f56e57c799d9940772d6f3dae695392b14338d7))

- MCP Apps SPA — escape sequences, colors, cross-view sync, semantic graph (#313)
  ([#313](https://github.com/pvliesdonk/markdown-vault-mcp/pull/313),
  [`1bbe43d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1bbe43d7896d77275f657bff8dac3a1a07a975a4))

- Migrate MCP Apps SPA to @modelcontextprotocol/ext-apps SDK
  ([`6d34529`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6d345293541fca5d56e218b25ab68359a6fa18e9))

- Migrate MCP Apps SPA to @modelcontextprotocol/ext-apps SDK (#298) (#299)
  ([#299](https://github.com/pvliesdonk/markdown-vault-mcp/pull/299),
  [`71061a8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/71061a83506fe3ebe7675f7b6b17f10a9a1c4c34))

- Move parseToolResult to module scope so Browse/Graph views can access it
  ([`d9962d7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d9962d7e18206d1d33eb92c9ae11ed0bfb5de71f))

- Mypy union-attr error and pygments CVE ignore
  ([`18b78df`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/18b78df79775f5af67fc822cf99e1d8943e9888d))

- Parse CallToolResult correctly in MCP Apps SPA
  ([`f47fb9a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f47fb9ac6b7a9b2007632440c041c95514ee0645))

- Repair MCP Apps SPA UX — escapes, colors, sync, semantic graph
  ([`c52d14d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c52d14d1bbf9b386dc0c9a4abf287507e4cd4a09))

- Replace parseToolResult return result fallback with return null
  ([`cd50a5d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cd50a5d8c762a17efea5ffc0738b7ef3c6ef33a8))

- Research prompt regex escaping + strip trailing newlines
  ([`348113c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/348113c52b37e4fdd65ab216fd6b95cf674385d8))

- Show backlink count in tooltip for all nodes (AC7)
  ([`ed6528f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ed6528fdd9bd080738fa92df860c0abb960788e6))

- Show browse view with data even when ontoolinput is not sent
  ([`e9c8ba5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e9c8ba54c95904592c5510313a81c0b6d60f1d25))

- Update test for restored batch_size=32 and bump requests>=2.33.0
  ([`33bca92`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/33bca929e6d806adfdaed8b82bf56183387036d1))

- Use SDK theme variables and fix architect-reviewer findings
  ([`1cce1bd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1cce1bd4f748fc7a750e87dfd313befbd1c610cb))

- Visibility test — fastmcp Client lists all tools regardless of AppConfig visibility
  ([`14b492d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/14b492d0427bb636f42238a8df4b3a6859243e7f))

- Whitelist view parameter in processToolInput
  ([`fa7eab3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fa7eab3bcf826b36eaff768237dedbb69384de5a))

- XSS in vault browser — escape HTML in tree, preview, and search
  ([`dc0a16d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dc0a16d558660b3461aedb3a070865d83d5e6c25))

- **ci**: RPM glob separator + pin ext-apps SDK to v1.3.1
  ([`42b2331`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/42b23310a460fd58a1e8f568e91203a2b98b523a))

- **ci**: RPM glob separator + pin ext-apps SDK to v1.3.1 (#290)
  ([#290](https://github.com/pvliesdonk/markdown-vault-mcp/pull/290),
  [`2653b38`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2653b388b0d359e44100ae67df20891b6d92aef7))

- **ci**: Use --frozen in docs workflow to prevent uv from re-resolving
  ([`ffb4c88`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ffb4c88b11f83c058718daaf42ff0c6cff22ed88))

- **deps**: Override pygments<2.20 globally via tool.uv.override-dependencies
  ([`0ad6b7b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0ad6b7b8960e3149d03ba52c050e4d10bdb35c9a))

- **docs**: Patch Pygments HtmlFormatter to handle filename=None
  ([`7d4984a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7d4984ac1c8a14fa4e83ef66dc376e4690119826))

- **docs**: Pin pygments<2.20 to avoid filename=None crash in HtmlFormatter
  ([`de73d84`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/de73d84151a922ef0506ecd482c09e3f0ddc0683))

### Chores

- Align pre-commit hooks with CI — local system hooks + vendor-spa check
  ([`dc1037d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dc1037d75a26ac37e059a1cd4a60d5c80a225e09))

- Exclude app.src.html from wheel build
  ([`55e3ffe`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/55e3ffe66c872210f2c57d1e1c6b8dd4087c9cf8))

- Fix ruff lint errors in test_mcp_apps_graph.py
  ([`26663f7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/26663f7b117f2221b4dcc6f18bbc0da9e445d098))

- Linting issues
  ([`f4b2960`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f4b2960c9c201546ef3c9b7d1eb4293261136011))

- Remove temporary debug logging from _vault_list
  ([`79fa30b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/79fa30bb4aac747de053da092f12d4a86f2c4e30))

- Ruff format test_mcp_apps_foundation.py
  ([`b546b34`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b546b3437ff4d71fd955cefe2c00a100a141c104))

- Ruff format test_mcp_apps_graph.py
  ([`dd48e4e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dd48e4e96b221be9916fcd888647260c14168020))

- Trigger CI
  ([`9852b6c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9852b6cb3eb59db3f8f4698a2ced73be7f7da4af))

- Update server.json to v1.17.0 [skip ci]
  ([`0f149fa`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0f149fa76f94793128575e6845909c2a1d053907))

- **deps**: Bump cryptography from 46.0.5 to 46.0.6
  ([`3841f0f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3841f0fa097f2b766e60d934224a6c8771926e62))

- **deps**: Bump cryptography from 46.0.5 to 46.0.6 (#314)
  ([#314](https://github.com/pvliesdonk/markdown-vault-mcp/pull/314),
  [`9467723`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9467723a4f8107e95d86aff54598a300ce4303de))

- **deps**: Bump pygments from 2.19.2 to 2.20.0
  ([`887957c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/887957c00f402b2d92e0c6540de7e766e8abb7fb))

- **deps**: Bump pygments from 2.19.2 to 2.20.0 (#317)
  ([#317](https://github.com/pvliesdonk/markdown-vault-mcp/pull/317),
  [`338d382`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/338d382b2ed099878692376fb04cc91960eea521))

### Code Style

- Address bot review nits
  ([`cbb794f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cbb794f71a8cd1207f019b97bdc6c9d81ea5188a))

- Remove redundant HTTPError from except clause
  ([`654175b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/654175bd8899bc52c47befc4a86526d76c6b3240))

- Rename SERVER_ICON to _SERVER_ICON for naming consistency
  ([`15c1900`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/15c1900cd2956b507c140663cb09573b18cd31a8))

- Replace trailing comma with period in get_context Returns
  ([`1c5135c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1c5135c934c7de2913ad7e0850b48f258b321e4a))

- Ruff format
  ([`6bde39c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6bde39c404f3076ca301f48c0e9af5f8e7800a8f))

- Ruff format
  ([`0726902`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/072690233b481fba01e2f5a6db73c033c59f5b75))

### Continuous Integration

- Add vendor SPA freshness check to lint job
  ([`5196229`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/519622916384d9a9da861224a426ad077002d9a8))

### Documentation

- Add MCP Apps documentation (#273 AC8/AC9)
  ([`f08b066`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f08b066642ad49a4ee4f14192a9bb9bd72a40c29))

- Add missing folder param to get_recent tool
  ([`60d31dc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/60d31dcc8da6ef173675dabe368443e186396357))

- Close documentation gaps from audit of 33 merged PRs
  ([`a112883`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a11288363eff57f66ccb40810a4df0bb49e5de2f))

- Close documentation gaps from audit of 33 merged PRs (#318)
  ([#318](https://github.com/pvliesdonk/markdown-vault-mcp/pull/318),
  [`2fc5066`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2fc5066b4ec3d694b320c721e65158bd277d7e8d))

- Disable anchor_linenums to fix Pygments 2.20.0 crash in docstrings
  ([`5a7b4fe`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5a7b4fef15c23b67d7eded901ae2e2fd35576bac))

- Expand CLAUDE.md documentation discipline to cover docs/ site
  ([`bbe079f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bbe079fed7df7d0ae022454de44117d073c2163a))

- Fix get_orphan_notes return type — full NoteInfo objects, not path strings
  ([`97a3cd0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/97a3cd0e0408cecedb427c5940d582f1e8117d6f))

- Fix mkdocs strict build — add mcp-apps to nav, disable show_source
  ([`64e30f9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/64e30f9f1f00bf1c4f3d883ba3b1d6d078328a5e))

- Fix review feedback — correct inaccurate return types and params
  ([`9abf604`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9abf6041de475ac88f7a8509700e3c8564c0aec4))

- Fix review feedback — correct inaccurate return types and params
  ([`14123ae`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/14123ae64726f8d23fd2d51968f129c709b665c3))

- Fix similar:// resource response shape and stale tool counts in mkdocs.yml
  ([`86044e3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/86044e3d723cfb682285ff32b2b5af57d71a0880))

- Improve LLM-facing tool docstrings and prompt descriptions (#295)
  ([#295](https://github.com/pvliesdonk/markdown-vault-mcp/pull/295),
  [`99f3af3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/99f3af34d37d164efd99afcd7e7b12844d31a609))

- Improve LLM-facing tool docstrings and prompt descriptions (#297)
  ([#297](https://github.com/pvliesdonk/markdown-vault-mcp/pull/297),
  [`1ddfdf1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1ddfdf164dee2ff06cfacf31bd17a90e5be032a6))

- Update all references to FastEmbed default model (#306)
  ([#306](https://github.com/pvliesdonk/markdown-vault-mcp/pull/306),
  [`1f2e25a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1f2e25a97ae93025960cfd71253306ed3e30e637))

- Update four guides with missing feature coverage
  ([`627fce4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/627fce47a0b24297b1c9d7d5008995e33f5709cf))

### Features

- Add server-level Lucide vault icon
  ([`f1688a5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f1688a5eaae95b2326720fcb4ca107d998261246))

- Add server-level Lucide vault icon (#296)
  ([#296](https://github.com/pvliesdonk/markdown-vault-mcp/pull/296),
  [`543b5f8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/543b5f8eac1a53eb8630bd27c5ab239cb81d8802))

- Cross-view navigation + send-to-LLM (#277)
  ([#277](https://github.com/pvliesdonk/markdown-vault-mcp/pull/277),
  [`360cedc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/360cedc84e6b55c2279a4392047d88b8bbf6e0d3))

- Cross-view navigation + send-to-LLM standardization (#277) (#284)
  ([#284](https://github.com/pvliesdonk/markdown-vault-mcp/pull/284),
  [`b524b63`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b524b635ca7c0039abc1a177bd844a6679141d76))

- Graph Explorer — interactive link visualization MCP App view (#275)
  ([#275](https://github.com/pvliesdonk/markdown-vault-mcp/pull/275),
  [`ae0262c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ae0262cbf6452962833976f85469125d3ada9051))

- Graph Explorer — interactive link visualization MCP App view (#275) (#282)
  ([#282](https://github.com/pvliesdonk/markdown-vault-mcp/pull/282),
  [`314f44c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/314f44c1c42398b5bd53cc3b78959fbe416766f9))

- MCP Apps foundation — SPA shell + app-only tool infrastructure (#273)
  ([#273](https://github.com/pvliesdonk/markdown-vault-mcp/pull/273),
  [`da4a67a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/da4a67a65c79f511627bd06c9b7869229e5763b4))

- MCP Apps foundation — SPA shell + app-only tool infrastructure (#273) (#280)
  ([#280](https://github.com/pvliesdonk/markdown-vault-mcp/pull/280),
  [`5199911`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/51999111480f51c7d2f57ee54a1bffdbe240d4c2))

- Note Context Card — visual dossier MCP App view (#274)
  ([#274](https://github.com/pvliesdonk/markdown-vault-mcp/pull/274),
  [`8644c2b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8644c2becfb84c557e868316349b59afcab3fa61))

- Note Context Card — visual dossier MCP App view (#274) (#281)
  ([#281](https://github.com/pvliesdonk/markdown-vault-mcp/pull/281),
  [`95a1b59`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/95a1b59d03dec37434c90172d6dc3c9f94b56181))

- Resolve wikilinks via frontmatter aliases (Obsidian behaviour)
  ([`394c366`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/394c3668c088a1ccc2ac480b88d1e9c7852faffb))

- Resolve wikilinks via frontmatter aliases (Obsidian behaviour) (#319)
  ([#319](https://github.com/pvliesdonk/markdown-vault-mcp/pull/319),
  [`8004d65`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8004d6592492da4f27bf1c92797634385ec35aa2))

- Separate Note tab for markdown preview in Browse view
  ([`ffedb34`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ffedb341ebcc1477906993826702b1fe1c749fd1))

- Separate Note tab for markdown preview in Browse view (#315)
  ([#315](https://github.com/pvliesdonk/markdown-vault-mcp/pull/315),
  [`af467c5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/af467c5091ae2e3d5c87ecf845024ec56478707f))

- Vault Browser — tree navigation with markdown preview (#276)
  ([#276](https://github.com/pvliesdonk/markdown-vault-mcp/pull/276),
  [`ac97354`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ac973542438596d139b314ac0aaf7af424452809))

- Vault Browser — tree navigation with markdown preview (#276) (#283)
  ([#283](https://github.com/pvliesdonk/markdown-vault-mcp/pull/283),
  [`8812758`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8812758f1a45644876b7ff79d3c81f3a328aebae))

### Performance Improvements

- Bundle CDN dependencies into self-contained SPA HTML (#302)
  ([#302](https://github.com/pvliesdonk/markdown-vault-mcp/pull/302),
  [`d5a333f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d5a333f2a0ef90e9daa879544a901d9f3d87bf5c))

- Bundle CDN dependencies into self-contained SPA HTML (#302) (#303)
  ([#303](https://github.com/pvliesdonk/markdown-vault-mcp/pull/303),
  [`b6cd034`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b6cd034bde9880c891e923087a185c071ad8daf1))

### Refactoring

- Externalize HTML, SVG icons, and prompt templates to static files
  ([`0a78271`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0a78271c8d5f3dbe9a7b47fe601adae134eadf1e))

- Externalize HTML, SVG icons, and prompt templates to static files (#294)
  ([#294](https://github.com/pvliesdonk/markdown-vault-mcp/pull/294),
  [`06f56dd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/06f56dd75550d51c7a126bfb4ddc0ddf9559f065))

- Extract duplicated _get_html into module-level _fetch_app_html
  ([`f04d7a5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f04d7a5c97d2a4b1424ac3838fd61707e3899e19))

### Testing

- Add app-only tool coverage tests with dynamic linked fixtures
  ([`0bf96f9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0bf96f9f7d1c265c78353c977458e044658c939a))

- Add link to test fixture for graph depth coverage
  ([`b57041f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b57041fd9715daa8ca74d74d2c93895f9d778d43))

- Update MCP Apps tests for new ext-apps SDK API
  ([`29e0b14`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/29e0b14c04c74d4d5fa29c3057b8867484708853))


## 1.17.0 (2026-03-24)

### Bug Fixes

- Address architect-reviewer findings for #278
  ([`f174c12`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f174c1213c44ffedef148d5b9ff2c7ef06f307d6))

- Address PR #272 review comments
  ([`855da84`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/855da844b0bf39f1537f6160a4885cd74da93a90))

- Address PR #279 review comments
  ([`3bb278d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3bb278dec55352c6217a65de55ddc00ed715757a))

- **ci**: Improve diff-cover handling and add versionless package copies
  ([`67a5af7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/67a5af7ca00bc041b60b88fa197f0caaf1cef52d))

- **ci**: Improve diff-cover handling and add versionless package copies (#272)
  ([#272](https://github.com/pvliesdonk/markdown-vault-mcp/pull/272),
  [`ec462e2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ec462e2ed0c23fb5947b3d6a3d12c882c0e1cd7b))

### Chores

- Update server.json to v1.16.0 [skip ci]
  ([`c2910d4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c2910d44e166749b7c75bcfdbacfde4bae0dd4b3))

### Code Style

- Ruff format
  ([`a95fe23`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a95fe23d3aefbd6529419df5a4b41d1444908463))

### Documentation

- Add cross-repo sync rule to CLAUDE.md, update SYNC.md pending ports
  ([`876d68f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/876d68faa77b52372adbdee32555c5966487e156))

- Add cross-repo sync rule to CLAUDE.md, update SYNC.md pending ports (#271)
  ([#271](https://github.com/pvliesdonk/markdown-vault-mcp/pull/271),
  [`baf1bc5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/baf1bc5f3638e39b5f13ca04e8e1bd40cc703449))

### Features

- Persistent EventStore for HTTP session persistence (#278)
  ([#278](https://github.com/pvliesdonk/markdown-vault-mcp/pull/278),
  [`053d1f9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/053d1f9f93a7820b7b6e4c33a391197878af42d4))

- Persistent EventStore for HTTP session persistence (#279)
  ([#279](https://github.com/pvliesdonk/markdown-vault-mcp/pull/279),
  [`a21c6a1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a21c6a14c0e335056cfff3e42760afb9570d2e56))


## 1.16.0 (2026-03-23)

### Bug Fixes

- Address PR #267 review comments
  ([`22b2aba`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/22b2abac84d0d972a09b60a18d6051def26439bd))

- Address PR #268 review comments
  ([`ad4b810`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ad4b81068c952d11732bd718a91c258d3d260841))

- Clarify Auth:remote table note per review
  ([`b627fcc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b627fccd66abf38f2c0e81f86ba43847e5e5385d))

- Replace Codecov patch gate with local diff-cover (#265)
  ([#265](https://github.com/pvliesdonk/markdown-vault-mcp/pull/265),
  [`e7c427a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e7c427a74adc838e40cd2f2d82cd3fbcc8d84e58))

- Replace Codecov patch gate with local diff-cover (#267)
  ([#267](https://github.com/pvliesdonk/markdown-vault-mcp/pull/267),
  [`92e2e2c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/92e2e2c1e8cdb5cc6f175137937fd6c79e3539a3))

### Chores

- Add SYNC.md for cross-repo tracking with image-generation-mcp
  ([`5d64560`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5d645601e888f5a95ea50ac3edceb777c34ef831))

- Add SYNC.md for cross-repo tracking with image-generation-mcp (#269)
  ([#269](https://github.com/pvliesdonk/markdown-vault-mcp/pull/269),
  [`44f0659`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/44f065953223d838511d8e2e5d1c30cfc1f5cf81))

- Update server.json to v1.15.0 [skip ci]
  ([`0719618`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0719618532590027fe141f59126c394169a8be62))

### Features

- Add RemoteAuthProvider as default OIDC mode (#264)
  ([#264](https://github.com/pvliesdonk/markdown-vault-mcp/pull/264),
  [`260b822`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/260b822e46df0a97ff1d6779a306e47052c0a348))

- Add RemoteAuthProvider as default OIDC mode (#268)
  ([#268](https://github.com/pvliesdonk/markdown-vault-mcp/pull/268),
  [`8152726`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/81527262799152d1b06d8451971ad37816583adc))


## 1.15.0 (2026-03-21)

### Bug Fixes

- Address architect-reviewer PARTIAL findings
  ([`ec2aa57`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ec2aa57cf156e29dc2e1c83eddb439f8bfd0fe65))

- Address PR #256 review comments
  ([`4b78cb8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4b78cb83c0275e403acbc08158021b0d3610757b))

- Address PR #259 review — streaming, SSRF, UTF-8, tests
  ([`170fc0e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/170fc0ee49f5f2c097b94316a3336b10de1323c0))

- Address PR #259 round 2 — redirect SSRF, 0.0.0.0, URL redaction
  ([`abedf76`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/abedf76fb133830878be3fcaf0b868fb69076b2d))

- Address PR #261 review comments
  ([`90e54aa`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/90e54aa9cceda9e33abe178db503c182254ac645))

- Address PR #263 review comments
  ([`5f5c014`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5f5c01443fae633bec8cbf9c28f297e897e9e2b9))

- Apply exclude_patterns during reindex()
  ([`0a7a8dc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0a7a8dc82922a93101e8af3f7626bad82723d7f5))

- Apply exclude_patterns during reindex() (#256)
  ([#256](https://github.com/pvliesdonk/markdown-vault-mcp/pull/256),
  [`aaac487`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/aaac487af054fdd5de022c5b4b316e2c0fadd9ec))

- Disable FastMCP consent page — Authelia handles consent
  ([`45f98d0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/45f98d02aa53b394873fcdd1e006a984e9d2d908))

- Load persisted vectors before stale-doc purge in build_index/reindex
  ([`6a2055d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6a2055d5a9606ff8ba96d049e9ea60dbfd3fc520))

- Preserve port in redacted fetch log URL
  ([`2d0d6f5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2d0d6f5df2bfd59fda250324aea6f0d9c5ba9497))

- Purge stale excluded docs in build_index() startup path
  ([`9b2f735`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9b2f73574f6a9daf22ababd71dcf294f4fd2d0c1))

- Redact token from download link log, add missing tests
  ([`eea8d25`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/eea8d25d8107e22ecc2e4ea91a78d295315081c2))

- Remove unused variable in test (ruff F841)
  ([`d2fe348`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d2fe348d2198838e797ca4f90740a6375922dbf0))

- **ci**: Create dist/ directory before nfpm package build
  ([`bccb0ea`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bccb0eab8f3e681df8369af308bae294be68ad38))

- **ci**: Remove bash :? guard from nfpm.yaml version field
  ([`7e90698`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7e90698145557a005470452e707171949d12d82a))

### Chores

- Update server.json to v1.14.0 [skip ci]
  ([`27347db`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/27347dbd3b4f9096ea216541a36b80b5609684d8))

### Code Style

- Fix ruff format after auto-fix
  ([`216befe`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/216befef1d99a024cef5b2339f79599f7ac50c24))

- Fix ruff formatting in _is_path_excluded()
  ([`2879dad`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2879dad1ad22f10f84147b781a277933134345a6))

- Ruff format test file
  ([`f4ad975`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f4ad97577e4234b62004fc27c06c329b6241a155))

- Ruff format test_artifacts.py
  ([`62dcfde`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/62dcfde592d46c8f7d7c7c41bfab3fc8c1c5195f))

- Ruff format test_cli.py
  ([`3dd29d9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3dd29d9cf3ad8a386d04c9ca8d9116b493adcba4))

### Documentation

- Add create_download_link to design spec, config, and README
  ([`1d58100`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1d5810029b2366c6d05191d0e7ce478729f34ae0))

- Add fetch tool to README, design doc, fix tool count
  ([`f0f8b78`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f0f8b7804e989712d828a40c20d1ba99d72589bc))

- Document build_index() stale-doc purge in design spec
  ([`cc1236e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cc1236e9ec4b2111fecba1fb1be8bfe220e39cac))

- Document exclude_patterns behavior in reindex
  ([`2144fde`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2144fde0406404a97dab27e6acd8036fbf0d0f53))

### Features

- Add create_download_link tool with one-time artifact HTTP endpoint (#260)
  ([#260](https://github.com/pvliesdonk/markdown-vault-mcp/pull/260),
  [`8d43861`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8d43861ee03a74cd9ccb45c69b5bc838806af3c4))

- Add fetch MCP tool — download from URL and save to vault
  ([`3c05562`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3c05562680cd9de2774f95051b0a82ecd022607b))

- Add fetch MCP tool — download from URL and save to vault (#259)
  ([#259](https://github.com/pvliesdonk/markdown-vault-mcp/pull/259),
  [`ca82ae2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ca82ae2264b198625bfdf7f4d89bf134717a9132))

- Create_download_link tool with one-time artifact endpoint (#261)
  ([#261](https://github.com/pvliesdonk/markdown-vault-mcp/pull/261),
  [`084810e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/084810ef1161ce7d6a760deca12ca1ce837a64a4))

### Refactoring

- Consolidate onto FastMCP logging stack (#262)
  ([#262](https://github.com/pvliesdonk/markdown-vault-mcp/pull/262),
  [`dd3f74d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dd3f74dc2108d9400dbe0b2ec478792ae080efb9))

- Consolidate onto FastMCP logging stack (#262) (#263)
  ([#263](https://github.com/pvliesdonk/markdown-vault-mcp/pull/263),
  [`6479fb2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6479fb2ad97079e6dfe63866896cc4045ddffb54))

### Testing

- Add embedding-aware purge tests for codecov/patch coverage
  ([`65aea11`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/65aea11ab3f931e0d557ba7aeee04d8c4f02c537))

- Add timeout and hostname blocklist tests for fetch tool
  ([`2f4a240`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2f4a24090e0edb27c3941b64212d0d26b54662cc))

- Cover verbose logging and root handler setup in main()
  ([`fb8ee3b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fb8ee3b3917a55e72b246a36bb4cadc6c192363c))


## 1.14.0 (2026-03-19)

### Bug Fixes

- Address PR #250 review comments
  ([`58375a4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/58375a4690d51e76e30e0c8fe0611f62df4b0470))

- Address PR #251 review comments
  ([`6153f8b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6153f8b06f8082520f71e1a85283f43504231bf6))

- Address PR #252 review comments
  ([`f2510b6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f2510b6b974534c6d48a8fdd1e899a147de20d72))

- Address PR #253 review comments
  ([`50b4b25`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/50b4b25fa3609f13078d2c27940511da8506fa62))

- Architect-reviewer conformance fixes
  ([`c87218d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c87218debe04af03064b628bbd6aab6589c56424))

### Chores

- Update server.json to v1.13.4 [skip ci]
  ([`8b4cdad`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8b4cdad711cde03106831bd1fab23b093cde9e18))

### Documentation

- Add systemd deployment guide
  ([`6ea006e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6ea006eec6568e1b27f79176c09a9d0e8f450d40))

- Systemd deployment guide (#253)
  ([#253](https://github.com/pvliesdonk/markdown-vault-mcp/pull/253),
  [`f3a8443`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f3a8443538b178d8e1c7ec5695880b1890b95e5d))

### Features

- Add nfpm configuration for .deb and .rpm packages
  ([`08e50e5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/08e50e5987a2320c0b39aaca0b5d282c57aa8612))

- Add publish-linux-packages job to release workflow
  ([`503c24f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/503c24f3dec353b0e33035d25fdae0d44f8472cb))

- Add systemd unit files and packaging directory scaffold
  ([`93ca287`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/93ca287070b807f9baced1f19d2aba4cace920cf))

- Nfpm configuration for .deb and .rpm packages (#251)
  ([#251](https://github.com/pvliesdonk/markdown-vault-mcp/pull/251),
  [`3b313be`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3b313be9ea2cac5662e1667676793a8468e4eb19))

- Release workflow publish-linux-packages job (#252)
  ([#252](https://github.com/pvliesdonk/markdown-vault-mcp/pull/252),
  [`113af97`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/113af976373f37c15e934a55ddefe537c42a6d70))

- Systemd unit files + packaging directory scaffold (#250)
  ([#250](https://github.com/pvliesdonk/markdown-vault-mcp/pull/250),
  [`b3a9952`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b3a99520d23fc9cd3aabe30be4d41a2fe6d458c8))


## 1.13.4 (2026-03-19)

### Bug Fixes

- Address PR #249 review comments
  ([`6a35c3a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6a35c3adf770a15224612cc5135f9ad7f7ed1167))

- Multi-auth rejects bearer tokens with 403 insufficient_scope
  ([`d387eda`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d387eda79ec5409739b577fa38b3524af507563f))

- Multi-auth rejects bearer tokens with 403 insufficient_scope (#249)
  ([#249](https://github.com/pvliesdonk/markdown-vault-mcp/pull/249),
  [`6d8a343`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6d8a34343087680207df67df2f79a6bf8093eef4))

### Chores

- Update server.json to v1.13.3 [skip ci]
  ([`0fda9ce`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0fda9ceeb8a5c3c3961567804d82a9b7d5b31002))


## 1.13.3 (2026-03-19)

### Bug Fixes

- Pin mcp-publisher version and add missing OCI path vars
  ([`2b2e4fe`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2b2e4fecfd7f26ed4837cb68eb9ca0380c7a6e00))

### Chores

- Complete server.json env vars, restore OCI, automate registry publish
  ([`5016271`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/50162713bbe82cc008a6b2a6523524c749a9023c))

- Complete server.json env vars, restore OCI, automate registry publish (#243)
  ([#243](https://github.com/pvliesdonk/markdown-vault-mcp/pull/243),
  [`8af12a0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8af12a0b693dcd72215c3d24686ee2e009965585))

- Update server.json to v1.13.2 [skip ci]
  ([`360c614`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/360c6147b401c173e5ac0e599f9b7c202a7b00b4))


## 1.13.2 (2026-03-19)

### Bug Fixes

- Server.json registry validation and OCI tag mismatch
  ([`e1b3244`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e1b32449bc9e96fd76acba3a1282b1b6f1322652))

- Server.json registry validation and OCI tag mismatch (#241)
  ([#241](https://github.com/pvliesdonk/markdown-vault-mcp/pull/241),
  [`19198e7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/19198e7ae8b0d6d709628d999dca2e96f0010996))

### Chores

- Update server.json to v1.13.1 [skip ci]
  ([`7b7697f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7b7697fd69e10ae3db922dd330b3ffeb6c8218a3))


## 1.13.1 (2026-03-18)

### Bug Fixes

- Address PR #239 review comments
  ([`4a5065d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4a5065df3d9f4352c4ce61afb99f4021021af887))

### Chores

- Update server.json to v1.13.0 [skip ci]
  ([`f40db72`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f40db72efbf78421a7f951e37d9293795c258d06))

- **meta**: Prepare for MCP Registry submission (#239)
  ([#239](https://github.com/pvliesdonk/markdown-vault-mcp/pull/239),
  [`868913c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/868913c707472eb2e78a6c50b7ffb338f0e37547))

- **meta**: Prepare server.json and README for MCP Registry submission
  ([`66aefc7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/66aefc7778a85bb0426660f7be9be75c2f416125))


## 1.13.0 (2026-03-18)

### Bug Fixes

- Address PR #234 review comments
  ([`3e03eb3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3e03eb335a1b816ff20f56602416e2088082239c))

- Address PR #237 review comments
  ([`9b946f5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9b946f5805153b81a19c5ec6db5f373ad80de371))

- OIDCProxy must be server= in MultiAuth, not in verifiers=
  ([`243241c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/243241cd0f9bb0181361c0e27f6a53446728e2d1))

- Wikilink vault-wide resolution (Obsidian semantics)
  ([`bc41835`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bc4183557e9a044ffe412dcfe4523813e3c1d689))

- Wikilink vault-wide resolution (Obsidian semantics) (#234)
  ([#234](https://github.com/pvliesdonk/markdown-vault-mcp/pull/234),
  [`627d631`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/627d6318dce75e64d772491511f12f8fe974348e))

### Chores

- Update server.json to v1.12.0 [skip ci]
  ([`e8f189f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e8f189ff38a92149d73ed7d4ebe617b306fb73f8))

### Code Style

- Ruff format test_links.py
  ([`dfc5f64`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dfc5f6443513d31fc5f449d9d6cf68466edaa8d3))

### Documentation

- Fix stale MultiAuth API description in design.md
  ([`d46ec7a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d46ec7ae2ac2e30aff828eb17bd06ebf76833245))

- Update README auth section for multi-auth
  ([`edce696`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/edce696a19f85643c014e4e621c7beaf222d340b))

### Features

- Multi-auth — accept bearer token OR OIDC when both configured
  ([`00e79f6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/00e79f612190855459823e78745348d733d49f5a))

- Multi-auth — accept bearer token OR OIDC when both configured (#237)
  ([#237](https://github.com/pvliesdonk/markdown-vault-mcp/pull/237),
  [`2b983fc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2b983fc1160ddadc88d2b0334b06d128fb5683e9))

### Performance Improvements

- Eliminate N+1 queries in resolve_vault_wikilinks()
  ([`02b23dc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/02b23dc93c325271adf90e1b0b6d7ff3cf48604e))

### Testing

- Tighten verifiers assertion to exact length + index check
  ([`a870b0f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a870b0f2f95f18b77fc82c0cf3cc931405553e0d))


## 1.12.0 (2026-03-16)

### Bug Fixes

- Address PR #226 review comments
  ([`ceb0aa9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ceb0aa9a34400e68feaf702611624a55ab55b99f))

- Address PR #227 review comments
  ([`eff6e46`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/eff6e4601cfd4619eb45e63c3997d1f84b63c421))

- Address PR #228 review comments
  ([`cba4ffb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cba4ffb11119d0cc2c339ab166803423228bd1c3))

- Address PR #230 review comments
  ([`a013ce1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a013ce1dac0ca5d0314dc59f6a44fec5ecabd73c))

- Address PR #232 review comments
  ([`b5ca8b8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b5ca8b8b2f2d6109ad5bc235ce7058243fde8254))

- Address round 2 review comments on PR #232
  ([`808c86f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/808c86fd41abd9d6d4c14f1b4d5c630081dbd79e))

- Block exec closure names tmpl/_Template as user prompt arg names
  ([`9486f38`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9486f38a08759ca5570ec2e9908023cf6af40af1))

- Rebase local commits onto upstream when ff-only pull fails
  ([`d07fa76`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d07fa76f2850a8d42421986cf4e80661053c0698))

- Rebase local commits onto upstream when ff-only pull fails (#230)
  ([#230](https://github.com/pvliesdonk/markdown-vault-mcp/pull/230),
  [`feff098`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/feff0989a93905f47fe20113ce64e886d32286d7))

- Reject Python keyword arg names in user prompts; add belt-and-suspenders exec try/except
  ([`e3e6ff8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e3e6ff8d79b11e325524fdcd02a5f7ded48fa0f3))

- Replace --filters CLI bug with Python API examples; add get_connection_path to README tools table;
  improve get_context and arguments docs
  ([`61b91e7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/61b91e7f73ffb9b1b3b5e31d927a3dbe24202b76))

- Resolve mypy and ruff lint failures on feat/224-connection-path
  ([`baa9b73`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/baa9b735d1dd7888580932dfb8b8efe3465c3cd0))

### Chores

- Add 'Ask DeepWiki' badge to README
  ([`637560c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/637560c2b3fb1dd20c44cd75d3ad90afc48c48f2))

- Update server.json to v1.11.1 [skip ci]
  ([`d9c240a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d9c240ab013b3e1da63c9f0febe10ff0ac2795b1))

### Code Style

- Fix import order in TestRegisterOneUserPromptArgValidation
  ([`3ae79d5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3ae79d5852e59d1939cc23568c3b48890752678a))

- Move import logging to top-level in test_git.py
  ([`10ca378`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/10ca37807daa6ac11c2c9f5728c480880c95189e))

- Reformat get_connection_path Returns as bulleted field list
  ([`e83e2f2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e83e2f29fc5c114af3fe966d3246ed71af35a9f3))

### Documentation

- Add usage pattern disclaimer and PROMPTS_FOLDER to README config table
  ([`6dcf666`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6dcf66617dae7c3e65ca80d196105e2d7fe2e731))

- Add user-defined prompts section and Zettelkasten guide link to README (#223)
  ([#223](https://github.com/pvliesdonk/markdown-vault-mcp/pull/223),
  [`2fc7e21`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2fc7e216baee7917b5b8d108f54850f790944483))

- Fix zettelkasten guide accuracy issues (round 2)
  ([`28c6f48`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/28c6f481afbf1b72398aa0ff1dbc40888203780d))

- Update design.md for get_connection_path graph traversal (#224)
  ([#224](https://github.com/pvliesdonk/markdown-vault-mcp/pull/224),
  [`d0112d4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d0112d4237cc4cda0f2ed3c9f733ce8d8cd179d1))

- Update design.md for user-defined prompts (#222)
  ([#222](https://github.com/pvliesdonk/markdown-vault-mcp/pull/222),
  [`d2a5373`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d2a5373f909d3d1259a126931b800fd4d976f1de))

- Zettelkasten guide, templates, and example prompt (#223)
  ([#223](https://github.com/pvliesdonk/markdown-vault-mcp/pull/223),
  [`eef870a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/eef870a12c97cfbea4f5f05190cd92b0b1c5cd2d))

- Zettelkasten workflow guide, templates, and example prompt (#223) (#228)
  ([#228](https://github.com/pvliesdonk/markdown-vault-mcp/pull/228),
  [`a5328b1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a5328b16108eb6773951da964bdffba413923743))

### Features

- Get_connection_path BFS graph traversal tool (#224) (#227)
  ([#227](https://github.com/pvliesdonk/markdown-vault-mcp/pull/227),
  [`1299264`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/12992643a7752768eca6fbbd6fdd39a7ab12af26))

- Get_connection_path — shortest path between notes via undirected BFS (#224)
  ([#224](https://github.com/pvliesdonk/markdown-vault-mcp/pull/224),
  [`a23222a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a23222a8ad41989622990cec00e87e73261fcd02))

- Load user-defined prompts from PROMPTS_FOLDER with override semantics (#222)
  ([#222](https://github.com/pvliesdonk/markdown-vault-mcp/pull/222),
  [`3f5324f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3f5324f55eaf82f6eb443849a8eb40d73535cc4a))

- Resolve rebase conflicts by saving conflict files (Syncthing-style)
  ([`ce51aa8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ce51aa84817dfa80e6442c88536aa23cef81a86c))

- Resolve rebase conflicts by saving conflict files (Syncthing-style) (#232)
  ([#232](https://github.com/pvliesdonk/markdown-vault-mcp/pull/232),
  [`7a50f4e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7a50f4e34df994ba1c72b6fdf756e11762a1f030))

- User-defined prompts loaded from a mounted directory (#222) (#226)
  ([#226](https://github.com/pvliesdonk/markdown-vault-mcp/pull/226),
  [`dbdfb2c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dbdfb2cdf0d7b52ac93c72d58e519fff10fc9833))

### Testing

- Add dangling link exclusion test for get_connection_path BFS
  ([`7d169ca`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7d169ca4e678d341ee49be0f052dbbb46ed44a8b))


## 1.11.1 (2026-03-15)

### Bug Fixes

- Resolve mypy errors in _server_prompts and _server_resources
  ([`a5de472`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a5de47231d2eb4e59de5cfd4b0916b50bd9e640d))

### Chores

- Update server.json to v1.11.0 [skip ci]
  ([`23624ae`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/23624aef89ac5304a9eb94c7a62b09fb65021da4))

### Performance Improvements

- Eliminate double load_config() at server startup (#220) (#221)
  ([#221](https://github.com/pvliesdonk/markdown-vault-mcp/pull/221),
  [`9c93b01`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9c93b0143c893d0bc47f05a3b2772a2d16d190ba))

- Eliminate double load_config() at startup (#220)
  ([#220](https://github.com/pvliesdonk/markdown-vault-mcp/pull/220),
  [`2c83bc2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2c83bc2e4d4b1bdaad0ca4a8c5793ff0aa3106f9))

### Refactoring

- Split mcp_server.py into focused modules (#100)
  ([#100](https://github.com/pvliesdonk/markdown-vault-mcp/pull/100),
  [`74a5eb8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/74a5eb8519370192c07219f753feb2ab62fb2db8))

- Split mcp_server.py into focused modules (#100) (#219)
  ([#219](https://github.com/pvliesdonk/markdown-vault-mcp/pull/219),
  [`09cdc78`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/09cdc78c9d7bec6d6d4cb779caedcbd255364163))


## 1.11.0 (2026-03-15)

### Bug Fixes

- Add sqlite3.Error to specific exception handler in _update_backlinks
  ([`de1c564`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/de1c5641c95b9c6eab5ee23dc50b30da5bf39fca))

- Address PR #211 review comments
  ([`8bd1185`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8bd11858f8c76b589d2e40c3f3372b5a7223360b))

- Address PR #212 review comments
  ([`ac28b53`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ac28b534fd5c9a1c960984469499f43759007006))

- Address PR #217 review comments
  ([`0aeb587`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0aeb58756b6ae425367cf0cf9ba9ede346688b75))

- Narrow OperationalError check to 'no such table: links'
  ([`168b819`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/168b8197543761899dac7f61a9feeca6dbf4b1f6))

### Chores

- Update server.json to v1.10.0 [skip ci]
  ([`d3c675b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d3c675b38a145be032d6a95d8538bf8d03f54807))

### Code Style

- Rename log prefix from update_links to _update_backlinks
  ([`9269efe`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9269efe240dd2f2fc7042343a75cb645cae6de37))

- Ruff format fts_index.py and test_links.py after rebase
  ([`1dc8485`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1dc8485302ba234f8ac50cbcf86fe21ebf5b3c96))

### Documentation

- Add raw_target and kind fields to link tool Returns sections
  ([`2b28362`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2b28362dff26c74c90bae1262f933be3aa6027ea))

- Address PR #213 review comment
  ([`fc70cf5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fc70cf5e412e0f6be30b9ec7cee471e72024ae74))

- Clarify related prompt search supplementation wording
  ([`520c667`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/520c66709591bcbcf414fb72aa1d8df2dd4737ce))

- Convert 5 tool Returns sections to bulleted field lists (#216)
  ([#216](https://github.com/pvliesdonk/markdown-vault-mcp/pull/216),
  [`bc629a4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bc629a4c511677b6caf4257d19e67b9edcc62d83))

- Convert 5 tool Returns sections to bulleted field lists (#218)
  ([#218](https://github.com/pvliesdonk/markdown-vault-mcp/pull/218),
  [`fa3d983`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fa3d983a065a8a70b7e82ac0bf97cb3da7e4ddda))

- Improve LLM-facing tool docstrings to fix observed misbehaviors (#214)
  ([#214](https://github.com/pvliesdonk/markdown-vault-mcp/pull/214),
  [`05a757c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/05a757ca3093f15bf6b5dee56866b5a2a9d3c9da))

- Improve LLM-facing tool docstrings to fix observed misbehaviors (#214) (#215)
  ([#215](https://github.com/pvliesdonk/markdown-vault-mcp/pull/215),
  [`80407d6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/80407d63790d53e58fd52da49417d03180140d03))

- Sync Docker volume layout across deployment docs (#193)
  ([#193](https://github.com/pvliesdonk/markdown-vault-mcp/pull/193),
  [`1228fef`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1228fefa5537559d10d2c0344a08815e4369b720))

- Sync Docker volume layout across deployment docs (#193) (#213)
  ([#213](https://github.com/pvliesdonk/markdown-vault-mcp/pull/213),
  [`1e6c535`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1e6c535a5a0b7799ff609186d950b46a454e4d12))

### Features

- Add raw_target to BrokenLinkInfo and get_broken_links (#207)
  ([#207](https://github.com/pvliesdonk/markdown-vault-mcp/pull/207),
  [`762e346`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/762e346fdf4d9e5d732803c592253a34013c3a98))

- Add raw_target to BrokenLinkInfo and get_broken_links (#207) (#209)
  ([#209](https://github.com/pvliesdonk/markdown-vault-mcp/pull/209),
  [`87c31fd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/87c31fd375b82c0d5ae5d1949ed8d636661ce9d8))

- Enhance stats with link counts (#191)
  ([#191](https://github.com/pvliesdonk/markdown-vault-mcp/pull/191),
  [`59b6bf6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/59b6bf69b6b295fdd8c7855d82a63815aa362c66))

- Enhance stats with link health metrics (#191) (#212)
  ([#212](https://github.com/pvliesdonk/markdown-vault-mcp/pull/212),
  [`87570bd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/87570bdf6242e9325af331577359ff5a0915b2c4))

### Performance Improvements

- Move provider.embed() outside _write_lock in deferred flush (#179)
  ([#179](https://github.com/pvliesdonk/markdown-vault-mcp/pull/179),
  [`0b2f141`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0b2f1410e0eb1e244308294d04e898a112a28e5e))

- Move provider.embed() outside _write_lock in deferred flush (#217)
  ([#217](https://github.com/pvliesdonk/markdown-vault-mcp/pull/217),
  [`2871318`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2871318d7088425ad13b10be46197643f7b93ada))

- Push link_limit into SQL LIMIT in get_backlinks/get_outlinks (#201)
  ([#201](https://github.com/pvliesdonk/markdown-vault-mcp/pull/201),
  [`8b578d5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8b578d5f3aae8e7736e6e3155e8ca0a3d5ccb2d0))

- Push link_limit into SQL LIMIT in get_backlinks/get_outlinks (#201) (#210)
  ([#210](https://github.com/pvliesdonk/markdown-vault-mcp/pull/210),
  [`be5b2cd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/be5b2cd9c9058f8f895a8c4d7b86a259a3951f76))

### Refactoring

- Extract _count_links_query helper to remove duplication
  ([`4055cd4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4055cd49a7327a086aeff4296b86b65d7a9ee4d0))

- Extract _update_backlinks helper from Collection.rename() (#208)
  ([#208](https://github.com/pvliesdonk/markdown-vault-mcp/pull/208),
  [`9fe9f01`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9fe9f01734d181d7250461ae996b572a140a1143))

- Extract _update_backlinks helper from Collection.rename() (#208) (#211)
  ([#211](https://github.com/pvliesdonk/markdown-vault-mcp/pull/211),
  [`a78b57a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a78b57a5edc6aefa5e4f442d4caa7b8c0b1dbc42))

### Testing

- Address PR #210 review comments
  ([`cd53e01`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cd53e0181247d62a0ea516430d3175d9863cc818))

- Fix test_link_counts_zero_without_links_table to exercise OperationalError guard
  ([`0c9501a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0c9501a5c8652fff2bc005c71991de6a48bc2b06))


## 1.10.0 (2026-03-15)

### Bug Fixes

- Add fragment to BrokenLinkInfo, use NOT EXISTS in get_broken_links
  ([`7b12b6b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7b12b6b4192d4aac868114cd0dadc05d8cadae31))

- Add fragment to get_broken_links tool docstring Returns section
  ([`e7704ae`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e7704aebdbbe49ebd710753e0ccecae5f7bc0f66))

- Add link_type allowed values to get_broken_links docstring
  ([`659178b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/659178b65e8d4d2715cebf0cfe81b11da0a47aa6))

- Address architect-reviewer gaps in get_context
  ([`609c475`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/609c4756c487271e9fd68d94c5984e9ef8d256bd))

- Address PR #200 review comments
  ([`6b385bc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6b385bcb813ca3640a3ac6454b24fb8c8fb5b68c))

- Address PR #204 review comments
  ([`ec6a466`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ec6a4668c37bb6808bd12c03a0d024b8f599f698))

- Address PR #205 review comments
  ([`4be8291`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4be8291a694527d1b33d37092730dce2e109b84a))

- Address PR #206 review comments
  ([`7e6eda4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7e6eda444208b1af7f0f671e8abdc50ab0312beb))

- Anchor markdown link regex to [text]( to avoid plain-text false positives
  ([`91dbce4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/91dbce480c3dcbc3effea8fa886b1348eb1d9d76))

- Correct icons and improve tool docstrings
  ([`f91b854`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f91b854ebd4878ee65b2d3c7cb955110aff78ddb))

- Correct inaccurate comment about code-span avoidance in _apply_link_replacement
  ([`af8f044`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/af8f044f01f929f55cb3e6149ab9ef5c215e6c8a))

- Drop unused content_hash, add modified_at index, nested subfolder test, list comprehension
  ([`cde11e8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cde11e803b7ac54c826567099b380e2b92e593ea))

- Eliminate N+1 queries in get_outlinks via LEFT JOIN
  ([`1a7e1f0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1a7e1f017498c000c495cc6548b36dfb32e0b1fc))

- Exclude image links from markdown link replacement
  ([`4594ccf`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4594ccfb44a769259e7f3aec78a1e00020bc7a0e))

- Handle self-referencing links when renamed file links to itself
  ([`1256e35`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1256e35b8e3978ca321c2ee24ac392d1a41cd003))

- Image link exclusion, ref title stripping, root path normalization, Literal types
  ([`21ee55b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/21ee55b37206aa6f19f5c93acb56511d8f736b83))

- Move os.path import to module level; update design.md rename prose
  ([`54452c3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/54452c39bd7dbaa869762e52ddc8ae6b66cd06b9))

- Populate frontmatter in get_similar, fix docstring, simplify path check
  ([`6a73222`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6a73222adb50e3c25d0b400ce1682ddd7c6e9a4c))

- Remove duplicate type annotation to satisfy mypy no-redef
  ([`a1c66f3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a1c66f3795a705ffd1b955a29a7732556bcae396))

- Remove hardcoded date from Known Limitations warning
  ([`bd6a8cd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bd6a8cd74a2849e3e5779edc5df1c9e9713ffc1f))

- Sort imports and __all__ to pass ruff lint
  ([`e82cc1e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e82cc1e4d2633f2098a0fde82798b622d2e54c5d))

- Surface raw_target in BacklinkInfo, OutlinkInfo, and design doc
  ([`086edee`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/086edee15f73073600ecd989a041ef38a1f9aac5))

- Use _fts_row_to_note_info in get_orphan_notes; update docs
  ([`28fbf95`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/28fbf95e04030e00cd2efccb21a1b2e1a62ad010))

- Use direct dict access for title/score and move mock import to top level
  ([`38cbbb6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/38cbbb60db232a9e69f5ba80a3eac87408fb068a))

- Use Literal type for BrokenLinkInfo.link_type
  ([`a6dd5d9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a6dd5d9fac5cb5ad3b8e8aac98388703c20e908e))

### Chores

- Update server.json to v1.9.0 [skip ci]
  ([`7960f6c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7960f6c5a7b48955c9e32cb09e57e9a9d3a548a3))

### Code Style

- Apply ruff format to collection.py and tests
  ([`3a32841`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3a32841a0689da8a1620f08536ca4a2308c63cd6))

- Apply ruff format to fts_index.py
  ([`7c962d6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7c962d63a9fdc730027e6685a05e066d99538571))

- Apply ruff format to test_links.py
  ([`c718d57`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c718d57eadde109996bb08c2e81053d1a5b8fc86))

- Fix import ordering in test_graph.py (ruff)
  ([`b31a091`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b31a0918a26028c9de91c9dbbe8c21431b5a24f8))

- Ruff format tests/test_links.py
  ([`5a78753`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5a787536685915ad19a04d9b5eaccc1f75bb0b55))

### Documentation

- Fix get_orphan_notes docstring — include all returned dict keys
  ([`890b3e6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/890b3e68ad46d5a720af42f665e60d2729d6387e))

- Fix id_token lifetime and document MCP OAuth limitations (#198)
  ([#198](https://github.com/pvliesdonk/markdown-vault-mcp/pull/198),
  [`19c533a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/19c533a957d339d6080ddacf83a4a17eba5a7e13))

- Fix id_token lifetime and document MCP OAuth limitations (#199)
  ([#199](https://github.com/pvliesdonk/markdown-vault-mcp/pull/199),
  [`df3948e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/df3948e90ac982b4f818f44caf8cec10ab82de3f))

- Update design.md for rename update_links param and RenameResult
  ([`a1f5a85`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a1f5a855db6921904874e1d05ac6ad3bd32e7b5f))

- Update design.md — MostLinkedNote return type + Data Types entry
  ([`db4ce3c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/db4ce3cd70d02c02a46e8a764db973e93fb6dfb1))

### Features

- Add get_context dossier tool (#192) (#200)
  ([#200](https://github.com/pvliesdonk/markdown-vault-mcp/pull/200),
  [`bd6af19`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bd6af19148c8fabaf8c49e3ff2234d6b90150497))

- Add get_context dossier tool (issue #192)
  ([`c44f395`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c44f395b7dbe919791f965359e683179d214328c))

- Add get_orphan_notes and get_most_linked (#188) (#205)
  ([#205](https://github.com/pvliesdonk/markdown-vault-mcp/pull/205),
  [`af6bec7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/af6bec79f6fb6e4558b9eb9d418a8f3603e62ab7))

- Add get_orphan_notes and get_most_linked (issue #188)
  ([`2112e66`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2112e66d0fab048c4a94428e68cef81093c343dd))

- Auto-update internal links on note rename (#187)
  ([#187](https://github.com/pvliesdonk/markdown-vault-mcp/pull/187),
  [`add44fd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/add44fd260d784c6b8714c2a92f09ab56d9f42a5))

- Auto-update internal links on note rename (#187) (#206)
  ([#206](https://github.com/pvliesdonk/markdown-vault-mcp/pull/206),
  [`114bddf`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/114bddfbe56e13eec60b2de3b785393aa17b351c))

- Find semantically similar notes by path (#189)
  ([#189](https://github.com/pvliesdonk/markdown-vault-mcp/pull/189),
  [`7ff59b5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7ff59b5c300638a38e42dc498d1eec4f73dc1ab6))

- Find semantically similar notes by path (#196)
  ([#196](https://github.com/pvliesdonk/markdown-vault-mcp/pull/196),
  [`807ecd7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/807ecd7c9803c6d6054c99a6b99aa7f575b11ee2))

- Link extraction and storage in FTS index (#185)
  ([#185](https://github.com/pvliesdonk/markdown-vault-mcp/pull/185),
  [`b29e6a8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b29e6a865985e8e3aeab4f17e51c458ee9ba69b8))

- Link extraction and storage in FTS index (#194)
  ([#194](https://github.com/pvliesdonk/markdown-vault-mcp/pull/194),
  [`5dad14c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5dad14c943c48f8a0a12fad9cab8cae8d45a02d5))

- MCP tools for backlinks, outlinks, and broken links (#186)
  ([#186](https://github.com/pvliesdonk/markdown-vault-mcp/pull/186),
  [`da99841`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/da998417214102bf361579e3c3cf2e46fbc77b32))

- MCP tools for backlinks, outlinks, and broken links (#195)
  ([#195](https://github.com/pvliesdonk/markdown-vault-mcp/pull/195),
  [`bd06a5e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bd06a5e83830a207b80ab4ef0196b2ef880ba8c3))

- Recently modified notes tool and resource (#190)
  ([#190](https://github.com/pvliesdonk/markdown-vault-mcp/pull/190),
  [`9b0e61f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9b0e61f613ccf4d2bbb89f87ff2d931f65018a1f))

- Recently modified notes tool and resource (#197)
  ([#197](https://github.com/pvliesdonk/markdown-vault-mcp/pull/197),
  [`4ef6514`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4ef6514da4f78e4031d1e3d929a615780c118064))

- Store raw_target in links table (#202)
  ([#202](https://github.com/pvliesdonk/markdown-vault-mcp/pull/202),
  [`7183003`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/71830034cef85d56859c676b7b46d1e594d991a5))

- Store raw_target in links table (#202) (#204)
  ([#204](https://github.com/pvliesdonk/markdown-vault-mcp/pull/204),
  [`a56b9d5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a56b9d521564a4d0696f1ef486c4a3b87b3b0cd1))

### Refactoring

- Convert SimilarItem from TypedDict to dataclass
  ([`65ceb26`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/65ceb26d50d1e959f1eb39047bc8a1b72667f9ac))

- Simplify wikilink new_path_part — always ends with .md
  ([`3cf114f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3cf114f86c6484ee08b6a89daeac40d187d4d16a))

- Use _update_vector_index helper in update_links path
  ([`0b6b23c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0b6b23c93ee32f650606a3829b6225f623f44ca8))

- Use MostLinkedNote dataclass for get_most_linked return type
  ([`649da97`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/649da9727be2e3d9e84dff54b92d90eea6942c42))

### Testing

- Add attachment rename test + README update_links docs + clarify image link comment
  ([`68bb887`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/68bb88709dfab505d2bb56f354cd680e59e7110e))

- Add self-referencing rename test; note image link in comment
  ([`11e4c9d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/11e4c9da4c6dedccf298fd3b3f1a3773b46b5719))


## 1.9.0 (2026-03-14)

### Bug Fixes

- Address PR #176 review comments
  ([`08e266e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/08e266e880c596515b7fb8078f6f55ba57b95dca))

- Address PR #177 review comments
  ([`7244744`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/724474475849dc619de9577c5fc877c48045dd45))

- Address PR #178 review comments
  ([`0804880`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/080488080df637a05fede114790ab59ffad842b1))

- Address PR #184 review findings
  ([`dca6349`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dca6349c486478d97fe24098da8dff6e0a84af7a))

- Consolidate Docker volumes and persist OIDC proxy state
  ([`02189c4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/02189c4b585758144e814849cac6f89542d78c99))

- Consolidate Docker volumes and persist OIDC proxy state (#180)
  ([#180](https://github.com/pvliesdonk/markdown-vault-mcp/pull/180),
  [`dfc3163`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dfc3163b5ca9b0993eaed1698e403be1d934654b))

- Ensure state subdir ownership on upgrades and add FASTMCP_HOME to OIDC compose
  ([`77f69d3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/77f69d329a156613cf810665d230b009494790d3))

- Include exception message in warning logs for consistency
  ([`de83f44`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/de83f442500ea17ece656ab3715f0c0a57c04b38))

- Include json_path in embeddings_status warning log
  ([`3075501`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/30755011a74264aac9ad303c60503b12cd3ace49))

- Move logging import to module level in test_config
  ([`ab6733d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ab6733db17fe1c8bec62d4e3c7fd5df25da5dc68))

- Prevent dirty-set and vector-index races in deferred embedding flush
  ([`336e4fa`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/336e4fa41647a06fda9a5e4b4fe6a9088162279b))

- Remove unused monkeypatch parameter in test
  ([`5562135`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/55621354a546981b15502f568d6b3d1f2ab7673f))

- Use == logging.DEBUG and move import logging to module level
  ([`f515182`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f5151823f6bdbd70107b3515e0f1d6aefcc6a053))

- Use glob for state subdir chown and add FASTMCP_HOME to all compose examples
  ([`81b9a35`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/81b9a359dd9a3933832bcb4c2cbd21162d8e3069))

- **deps**: Upgrade pyjwt to 2.12.1 to fix CVE-2026-32597
  ([`968bb8e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/968bb8eb23e4a589814e670418926d954273810c))

- **test**: Make stat_error test robust across Python 3.13/3.14
  ([`758fa8c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/758fa8ce52d0ec781fcf28f98b65968961719419))

### Chores

- Update server.json to v1.8.1 [skip ci]
  ([`d2606d5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d2606d513340a492a5ef3e7178002b618d525152))

### Code Style

- Apply ruff format
  ([`7004ddc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7004ddc4fbb2626bb3e46d253339f399a414bb5b))

- Apply ruff format
  ([`132c8cd`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/132c8cd8d758520660b8d3b3690bfac58ee3041f))

- Apply ruff format to test_collection.py
  ([`d31c664`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d31c66412cfbaf94883107e6842be4ab93895398))

### Documentation

- Document OIDC token lifetime recommendations and client re-auth limitations
  ([`15a6717`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/15a67173fc25899efa6985afadc7731fc8170808))

- Document OIDC token lifetime recommendations and client re-auth limitations (#176)
  ([#176](https://github.com/pvliesdonk/markdown-vault-mcp/pull/176),
  [`3287a7b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3287a7bb13bb5acb5b2ab92674c7ce26253237a0))

### Features

- Add auth observability and configurable LOG_LEVEL (#181)
  ([#181](https://github.com/pvliesdonk/markdown-vault-mcp/pull/181),
  [`b47e7c8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b47e7c868f56605c1d3674a464f43853cdc1f1df))

- Auth observability and configurable LOG_LEVEL (#183)
  ([#183](https://github.com/pvliesdonk/markdown-vault-mcp/pull/183),
  [`245bde0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/245bde04b9ab8b977da4c66ed2eaa917fbee8961))

- Logging audit — eliminate silent error paths, add DEBUG diagnostics (#182)
  ([#182](https://github.com/pvliesdonk/markdown-vault-mcp/pull/182),
  [`eb2241d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/eb2241d47ae121fd282689dd1da87fd785b82f1a))

- Logging audit — silent error paths and DEBUG diagnostics (#184)
  ([#184](https://github.com/pvliesdonk/markdown-vault-mcp/pull/184),
  [`5cd4fc6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5cd4fc65984be12f1a2c411dd06925f14892f8ed))

### Performance Improvements

- Defer embedding re-computation and git commit on writes
  ([`748499a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/748499acca4bd6cf9f375af2ddc3886aa3676e20))

- Defer embedding re-computation and git commit on writes (#178)
  ([#178](https://github.com/pvliesdonk/markdown-vault-mcp/pull/178),
  [`bd3eeb9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bd3eeb9af1416d6e5d6c0bcee02deb4bd526f095))

### Refactoring

- Consolidate OIDC debug logs into single log call
  ([`0045caf`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0045caf708df09ddf3622f5c5e60f6e6ec514ec1))


## 1.8.1 (2026-03-13)

### Bug Fixes

- Add progress logging during embedding build
  ([`ad6753c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ad6753c102d813080eedb751e4e424e887bd19bf))

- Add progress logging during embedding build (#171)
  ([#171](https://github.com/pvliesdonk/markdown-vault-mcp/pull/171),
  [`d8e6b71`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d8e6b71043913a5a98549138d0d63f6448a31087))

- Use ASCII hyphen in batch progress log message
  ([`9600b8c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9600b8c2f4a2159faff88a3d843014e38b11c8d9))

### Chores

- Update server.json to v1.8.0 [skip ci]
  ([`85cfe67`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/85cfe6702e2b902a4865cb3db31915f956cbb1ea))


## 1.8.0 (2026-03-13)

### Bug Fixes

- Add auth guide to llmstxt sections and fix docker guide count
  ([`d559726`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d55972608bb944408ad6b673185f72e3e816204a))

- Address PR #167 review comments
  ([`c62a21d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c62a21deed9352bc1bdc75a3da6da8ff847eae93))

- Address PR #169 review comments
  ([`1f7fc36`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1f7fc3699959354e0f248289cb0402ff3c15228b))

- Expand OIDC optional variable descriptions in auth guide
  ([`8a4cff5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8a4cff584d0ff229f2dc2c4716295ca9f1a3f36f))

- Limit FastEmbed ONNX batch_size to 4 to prevent OOM (#170)
  ([#170](https://github.com/pvliesdonk/markdown-vault-mcp/pull/170),
  [`4bfddad`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4bfddad753bb7808bc222697bb2afd9b6915b85c))

- Lower FastEmbed ONNX batch size from 16 to 4
  ([`e8b55a7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e8b55a773e625e53c6e2e343b111a102a6bc5e68))

- Named constant + test assertion for FastEmbed batch_size
  ([`d6e321a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d6e321ac3e3f8c0ec85cf2f677467061f2825702))

- Pass batch_size=16 to FastEmbed embed() to prevent OOM
  ([`f9c9f92`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f9c9f925858dfd42ecf9611f17a6e42b21ce88db))

- Resolve ruff lint errors in bearer auth precedence tests
  ([`1d829c7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1d829c7f48003225bea0ac9bc60a00722d62275c))

- Strengthen bearer auth precedence tests and fix README auth modes count
  ([`9aaa1a5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9aaa1a545a61b88b5f01e8d93ff8cd8332d697a7))

### Chores

- Update server.json to v1.7.1 [skip ci]
  ([`7fcf688`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7fcf6888766d2004044c1db8efc6eea5a3169787))

### Code Style

- Apply ruff format to mcp_server.py and test_mcp_server.py
  ([`8a4ca3b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8a4ca3b2ce1aa8b1eab37750a6ed0e1ebf4df0cc))

- Ruff format providers.py
  ([`dcba4c0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dcba4c0e6eb781dcdf46ae52ed0d0fc1729a7cc1))

### Documentation

- Dedicated authentication guide (#169)
  ([#169](https://github.com/pvliesdonk/markdown-vault-mcp/pull/169),
  [`1a808bc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1a808bc75933490b9f2bfc3b2e14e0b249d78e17))

- Dedicated authentication guide consolidating auth setup
  ([`62ad126`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/62ad1264466ab17d03ffd85e9836486bb068ba05))

- Document in-process vs out-of-process memory tradeoff
  ([`454c9f9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/454c9f970f754dac3b38654396de1684ca61b223))

- Fix wording in embeddings guide per review
  ([`1460ff4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1460ff4176f81905b3237b106e37a5c957c336f7))

### Features

- Simple bearer token authentication via env var
  ([`234d885`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/234d885c771c10dd971076c74838d75b74a0cda7))

- Simple bearer token authentication via env var (#167)
  ([#167](https://github.com/pvliesdonk/markdown-vault-mcp/pull/167),
  [`dd62b8f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dd62b8f6e4bc2ec67bba9a679ff62528898c875a))


## 1.7.1 (2026-03-13)

### Bug Fixes

- Add docker entrypoint to auto-fix volume permissions on startup
  ([`acb5b30`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/acb5b30ebed7ae41a3870f88e743ecc32ee1b358))

- Address architect-reviewer findings
  ([`bb938f2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bb938f2bd26d882a6c1ccf414e5911d6be3128bb))

- Address PR #163 review comments
  ([`60db116`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/60db1160d98c29af66bf55a9cca8ddb4d2f93e37))

- Address round 2 review comments on PR #163
  ([`5db6e76`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5db6e761f7996cdf40f955b55204da6905d47ef6))

- Docker entrypoint to auto-fix volume permissions on startup (#163)
  ([#163](https://github.com/pvliesdonk/markdown-vault-mcp/pull/163),
  [`8a9823f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8a9823fa2592031cc7bdc7c5d0962194ad676fa7))

- Exclude docker-entrypoint.sh from COPY . . in Dockerfile
  ([`460feb6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/460feb64289b5c476f4ada750ec58d4af41ba541))

- Revert .dockerignore exclusion and use /data/* glob
  ([`acf77af`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/acf77af99a7468eb81c6f78f82fa8cdd56ec6595))

- Use /data/* glob instead of hardcoded directory list
  ([`c0e7fb1`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c0e7fb126274beb781522570296ed36468833322))

- **ci**: Audit exported requirements instead of installed environment
  ([`a7443b9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a7443b98ca20132d712fe155c760b7c1853fc65a))

- **ci**: Exclude local package from pip-audit
  ([`2cbe3e9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2cbe3e99b249cbc3d30cdfdb84ffe35b2c4affb5))

- **ci**: Exclude local package from pip-audit (#165)
  ([#165](https://github.com/pvliesdonk/markdown-vault-mcp/pull/165),
  [`08d9cef`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/08d9cef0761fe59505f716fd89d745fca379ad20))

- **ci**: Use --skip-editable instead of --exclude for pip-audit
  ([`3f88902`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3f88902b2c28aee49238a05d0f45b873302e51d4))

- **ci**: Use runner.temp for requirements file path
  ([`95a6dd3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/95a6dd331d8d54345260bec68a52474b3b97824b))

### Chores

- Update server.json to v1.7.0 [skip ci]
  ([`eb30fb4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/eb30fb40242e48f1e8085dfd2ab28e517937917c))


## 1.7.0 (2026-03-13)

### Bug Fixes

- Address PR #157 review comments
  ([`df3ca78`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/df3ca78caa6ef654a6df2a40c61cefd508878da5))

- Address PR #161 review comments
  ([`9fa394f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9fa394f07198a0d0b4bc352897df0a8daf4d9c72))

- Address unresolved PR #147 review comments
  ([`c410cdc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c410cdc8192b13bbefee161578911e0bb36e8b8a))

- Address unresolved PR #147 review comments (#153)
  ([#153](https://github.com/pvliesdonk/markdown-vault-mcp/pull/153),
  [`f383e86`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f383e86ab88d9846dd7f2f4e31a61e36e9a1635a))

- Auto-build embeddings on startup when configured
  ([`a563715`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a563715f085b9577817ea6ce7d72d2ea4f27770c))

- Auto-build embeddings on startup when configured (#160)
  ([#160](https://github.com/pvliesdonk/markdown-vault-mcp/pull/160),
  [`05dd7e4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/05dd7e410cb822e5bbb9ba6cb0945cb61de4346b))

- Batch embedding generation to avoid pathological memory allocation
  ([`fa7538d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fa7538d943ec0f1dcd81e6a284a3cd439e3d9442))

- Batch embedding generation to bound memory usage (#161)
  ([#161](https://github.com/pvliesdonk/markdown-vault-mcp/pull/161),
  [`a411f8f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a411f8f230450471bcb48654f3b7271fe5192f70))

- Build_embeddings() loads persisted vectors before deciding to rebuild
  ([`799bb09`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/799bb09f417b730d7c15727a380cb7aa1bc2fa47))

- Default to id_token verification for OIDC opaque access tokens
  ([`e035ee7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e035ee7abc5bbbcc699ca22e843c86c5542ffa34))

- Default to id_token verification for OIDC opaque access tokens (#157)
  ([#157](https://github.com/pvliesdonk/markdown-vault-mcp/pull/157),
  [`c6f3e66`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c6f3e6638e5fa36ec759f214d514db284f5d94cd))

- Derive is_read_only from load_config() for full consistency
  ([`5328062`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/53280628ba773ca9b57a79b106a3cdc301fb7407))

- Fail fast on SSH remotes when GIT_TOKEN is configured
  ([`3bbf55d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3bbf55d0d58bafe2b0bd7b90a6359026cc94dbcf))

- Fail fast on SSH remotes when GIT_TOKEN is set (issue #136) (#139)
  ([#139](https://github.com/pvliesdonk/markdown-vault-mcp/pull/139),
  [`abf3149`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/abf314968f70b07818e68815f1e8da9ad94d42ab))

- Resolve codeql secret-storage alert and add git tests
  ([`ac8ba7c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ac8ba7c6a1be443ff378a83fe60d638da3362874))

- Suppress CodeQL token-storage false positive (issue #129) (#138)
  ([#138](https://github.com/pvliesdonk/markdown-vault-mcp/pull/138),
  [`5d0ec35`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5d0ec35bfd982823f810fa1b832c567ff3de8b10))

- Suppress CodeQL token-storage false positive in git strategy
  ([`c407ff2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c407ff251121a80a0e9bb6e332e49f8e23022afc))

- Warn when openid scope missing with id_token verification
  ([`86a95a7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/86a95a72eaac149ad936714558f56de637ea5b5f))

- **docker**: Create /data/vault in image for managed repo mode
  ([`1595432`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1595432b1a10b75d0fabde285968516c067105e7))

- **docker**: Create /data/vault in image for managed repo mode (#150)
  ([#150](https://github.com/pvliesdonk/markdown-vault-mcp/pull/150),
  [`16a5ae8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/16a5ae836ea021134cc0ab413de75051f699a932))

### Build System

- Align python metadata with ci matrix
  ([`43063c0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/43063c0561053ee5912cf4cc61979d9ade1a6e13))

### Chores

- Address PR feedback and stabilize CI checks
  ([`72100cc`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/72100cce5a88f2b30146d6450354e6fc8c3ce1db))

- Fix pre-existing ruff format drift in config.py and mcp_server.py
  ([`02a1e1d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/02a1e1d71af9986ab86bfd9fa453847f68a7b5ef))

- Fix pre-existing ruff format drift in config.py and mcp_server.py
  ([`23cb734`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/23cb7340c866f145a83c5af77107b0af3a6f3c6b))

- Update server.json to v1.6.0 [skip ci]
  ([`c7a763e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c7a763ed836018fd1eef003a2bf08236b8378ead))

### Continuous Integration

- Drop python 3.10 and add non-blocking 3.14
  ([`95770e5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/95770e531ae3a48bef1cc818585b4d0ca66268ce))

- Drop Python 3.10, add non-blocking 3.14 (#145)
  ([#145](https://github.com/pvliesdonk/markdown-vault-mcp/pull/145),
  [`f37e167`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f37e167c47579b61c9e3028f193e7766d543a759))

- Fix SHA for codecov/patch fallback status
  ([`1c248b5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1c248b521575ec48a67d1626d50831dcc8829ef9))

- Match any .py file for codecov fallback gate
  ([`5ec9903`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5ec9903446f3ce610b5ad3aab7fe0de6119548b3))

- Pass PR number to codecov-action so patch status posts correctly
  ([`ff84460`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ff84460c3d73b7cb8e65b224bfc179d3111cfbf9))

- Pass PR number to codecov-action so patch status posts correctly (#155)
  ([#155](https://github.com/pvliesdonk/markdown-vault-mcp/pull/155),
  [`7987512`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7987512ff2aa50951b91656e8d11f248dd0daa8d))

- Post codecov/patch fallback for non-Python PRs
  ([`fdb9832`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fdb9832a37e969a5bb3121e67c26db86574cb923))

- Temporarily ignore pillow CVE blocked by fastembed pin
  ([`a8c6119`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a8c6119e4bd1cf2aa5c90684251f300e34cc158d))

### Documentation

- Add large vault operational note for embeddings
  ([`109ae39`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/109ae39c5c413bab40125d69dd61857681d37dee))

- Add OIDC provider expansions and Obsidian Everywhere guide (#146)
  ([#146](https://github.com/pvliesdonk/markdown-vault-mcp/pull/146),
  [`e968bb8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e968bb83995f8f2168f406828d202cbbd272319c))

- Add OIDC_VERIFY_ACCESS_TOKEN to all env var tables
  ([`af96d5d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/af96d5d73e27167749d9ee6ccd8b7021b35b131a))

- Address PR feedback on JWT wording and git sync vars
  ([`e483b69`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e483b69805a43031d9c139180d70dedb74f9bbab))

- Address review comments on OIDC subpath section
  ([`390ebc5`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/390ebc53f5895398504b2938d0e58eefc715667f))

- Address second-pass review clarifications
  ([`d0d4777`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d0d477754bfa3a5752106f02518de6c33bd2e354))

- Address second-round bot review comments
  ([`818469f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/818469fdddd8db7f898aad261269aaf619e8a518))

- Clarify OIDC subpath deployment and shared-hostname limitation
  ([`0acf184`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0acf184578470013e2bfb60dd648e77861d6afeb))

- Clarify OIDC subpath deployment and shared-hostname limitation (#151)
  ([#151](https://github.com/pvliesdonk/markdown-vault-mcp/pull/151),
  [`c9148d4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c9148d44f53060ba4fe39c34edfd64832352500f))

- Keep docker oidc guide valid for both root and subpath mounts
  ([`dc7a353`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/dc7a3531e2d626741dc5296a856c9bfc7cdca7d6))

- Remove env var reference from vault volume table entry
  ([`8461f9d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8461f9dbba38e766c1673c3262a521e7372aaf48))

- Update deployment.md volume descriptions to match docker.md
  ([`d699750`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d6997505cead6cfdda125b0a679ae96bb9fef40c))

- **guides**: Add Authelia/Keycloak and Obsidian Everywhere\n\n- Add Authelia and direct Keycloak
  OIDC provider sections\n- Add Obsidian Everywhere reference architecture guide\n- Update guides
  index and mkdocs navigation/llmstxt entries\n\nRefs #111\nRefs #114
  ([`e703a56`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e703a5694a91052eaab0f6086d15a9d3660ae9dc))

### Features

- Implement managed git mode and commit-only fallback
  ([`e3d2c5e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e3d2c5edee14ada58d2da168e40a7a75254e3779))

- Managed git mode + unmanaged commit-only mode (#140)
  ([#140](https://github.com/pvliesdonk/markdown-vault-mcp/pull/140),
  [`058704c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/058704c38641d826a6a04f5a534c02fecbe5a49d))

- Note templates prompt and templates-folder config (#147)
  ([#147](https://github.com/pvliesdonk/markdown-vault-mcp/pull/147),
  [`a575697`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a5756974c14ef761f25de7f8e21bbee09e79b69c))

- Replace sentence-transformers with fastembed defaults
  ([`d068252`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d0682522a5094babfa98a973c2a6c108b2514d46))

- Replace sentence-transformers with fastembed defaults (#144)
  ([#144](https://github.com/pvliesdonk/markdown-vault-mcp/pull/144),
  [`b3f3854`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b3f38542614f5a011c8bf7aa13b18d016662fedb))

- **http**: Support configurable HTTP endpoint path for subpath deployments
  ([`c1a37c8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/c1a37c8bfb7eaa88cd2021d17ce27b3ab17ec818))

- **http**: Support configurable HTTP endpoint path for subpath deployments (#142)
  ([#142](https://github.com/pvliesdonk/markdown-vault-mcp/pull/142),
  [`e2d61a4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e2d61a4f50dcb31ef2d010abc9b188247e5f50ab))

### Testing

- Add comment explaining patch target for local import
  ([`b522f5e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b522f5e465c61fe1dfc912f51dff545c2eddd0c7))

- Strengthen skip-rebuild test with embed() call tracking
  ([`de9d6d4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/de9d6d43e4229d92fc4a0514b668a45e3c980e29))


## 1.6.0 (2026-03-11)

### Bug Fixes

- Add Collection.stop() per issue #118 Q3 design decision (Option C lifecycle)
  ([`67e29c2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/67e29c2c261ee695727ad3ca9074be3c235f8fa1))

- Add missing compute_etag import, remove spurious blank line
  ([`32065a9`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/32065a92b6be4f240aebf18d4a1bda7ba921c802))

- Address PR #128 review findings round 2
  ([`2660645`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/2660645a6ce0057a7e54d693f457806cdafb5ce2))

- Address PR 128 review findings
  ([`b37061a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b37061a218cb5489724172849ca2ad051fc9fbe7))

- Consolidate hashing, etag type str|None, fix hardcoded-bytes test
  ([`366c83a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/366c83a8abf52d719e4781c7d53873aea4beb2af))

- Enable SQLite WAL journal mode for concurrent read/write
  ([`274712c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/274712c436a5b60b968897ccfc86f63f4fca0a07))

- Enable SQLite WAL journal mode for concurrent read/write (#123)
  ([#123](https://github.com/pvliesdonk/markdown-vault-mcp/pull/123),
  [`90b6aef`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/90b6aef25881daaa15b56c217aec76ee1c3644a0))

- Keep pull loop alive on tick errors
  ([`48e7965`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/48e79654a03787ddf008b5121fb4c2fa0e46fffd))

- Make reindex() thread-safe against concurrent writes
  ([`f62b3fa`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/f62b3fa2bdc83d9d41687b0e25adbb13ad89cf2e))

- Make reindex() thread-safe against concurrent writes (#124)
  ([#124](https://github.com/pvliesdonk/markdown-vault-mcp/pull/124),
  [`463d0ff`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/463d0ff3a8620518b60d5cedf6a57ab13aa42715))

- Remove dead skipped var, document TOCTOU, strengthen concurrent test
  ([`5e0542b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5e0542b99828c529275db987a472e045f67575bc))

- Remove redundant file read in read(), centralise hashing in scanner.py
  ([`4aac39c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4aac39c189a3f7f551ae4fc4b122db377c32d760))

- Skip WAL pragma for :memory: databases; add concurrent read test
  ([`efa0438`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/efa043850c9c9311510ede094814d95ef2affa02))

- Suppress TC003 false positive on Path import in test_fts_index
  ([`741ce9b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/741ce9b07d555b1ca615f47d979b0c2d74ecae7d))

- Use full MARKDOWN_VAULT_MCP_GIT_LFS env var name in docs and error message
  ([`9e6c4d0`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9e6c4d078f843112a158addee59ca17f4c407000))

- **meta**: Drop oci package placeholder
  ([`e351540`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e351540c635f9ffb191f17d2b5d0e692d3154e25))

### Chores

- **ci**: Auto-update server.json version on release
  ([`6305d2a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6305d2ad06c980ebcd64c12df920b5474ee255f7))

- **ci**: Auto-update server.json version on release (#135)
  ([#135](https://github.com/pvliesdonk/markdown-vault-mcp/pull/135),
  [`5ee3530`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5ee35301dba3eb468ed1fa571b32dd3034756c53))

- **meta**: Add registry manifest and metadata
  ([`ce7969f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ce7969f31cc57665453c696c737e9ca91c6f910b))

- **meta**: Restore oci entry per registry spec
  ([`e2b140f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e2b140fff404a27d1fd65453c3bafdb89c582426))

- **meta**: Restore oci entry per registry spec (#133)
  ([#133](https://github.com/pvliesdonk/markdown-vault-mcp/pull/133),
  [`0e53e49`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0e53e49970e44ab42f2219fbac375f62666aa7f4))

### Code Style

- Ruff format collection.py
  ([`1eeba92`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/1eeba92d19c68f0d3542356e56e39f0eec53b9b0))

- Ruff format collection.py after compute_etag import
  ([`4cfe808`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4cfe808df7d3a12e28e57645cbf7029cd2783af1))

- Ruff format long assertion in test_fts_index
  ([`fdc9d66`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/fdc9d662636b323c71bf7fd813c068131c015797))

### Documentation

- Fix architect-reviewer gaps in PR #128
  ([`44d1b83`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/44d1b83e5fdaf287c7f773275c52332619579196))

- Update git lfs pull docstring
  ([`6fd5a21`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/6fd5a218561dbc477bcc29cc65a64c2c5d4ffd0a))

### Features

- Git LFS support in Docker image
  ([`b2ed49a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b2ed49a8bef3120db015d2ea19ef0a3804b5fdd8))

- Git LFS support in Docker image (#127)
  ([#127](https://github.com/pvliesdonk/markdown-vault-mcp/pull/127),
  [`a53923b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a53923bd2da478f99174c5580051c15bed656ce3))

- Optimistic concurrency — if_match parameter on write operations
  ([`0b3092f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/0b3092fb7f3c3f5e6fc3a75454b221e78230e2ef))

- Optimistic concurrency — if_match parameter on write operations (#126)
  ([#126](https://github.com/pvliesdonk/markdown-vault-mcp/pull/126),
  [`26336d3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/26336d32804e861353ee41c608c8abbb36cebff1))

- Periodic git pull loop (ff-only)
  ([`4e020df`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4e020df17d84d3bc46c3862e67767ba3967849fa))

- Periodic git pull loop with ff-only policy (#118) (#128)
  ([#128](https://github.com/pvliesdonk/markdown-vault-mcp/pull/128),
  [`b9de39d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b9de39d68d465e3803bab5b68c435f74499dc08a))

- Return etag (SHA256) from read operations
  ([`accea1c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/accea1c25d924b0fee7f741af5ee06c72dae8db6))

- Return etag (SHA256) from read operations (#125)
  ([#125](https://github.com/pvliesdonk/markdown-vault-mcp/pull/125),
  [`260a86d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/260a86db9082671e3e2aa7f042c0206155a121d0))

### Testing

- Cover WAL warning branch with mocked pragma result
  ([`cbe9537`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cbe95373f49519cc42807fd594209a3388c6c0c6))


## 1.5.0 (2026-03-11)

### Bug Fixes

- Address PR #108 review comments
  ([`71be5ee`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/71be5ee975d177f8a0a0045865a97f5b5e82c47d))

- Address PR #93 review — single-line badges, dynamic Docker badge
  ([`97eb41a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/97eb41a835b70d6d51f1630c7da6293592519f25))

- Correct Step 1 heading in docker guide (stdio → via HTTP)
  ([`bd8a2d4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/bd8a2d49f12c0d6583f206a0f097a3ba9685c9be))

- Pin anchore/sbom-action to full SHA
  ([`a10c1f2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/a10c1f29658f8dc3a7c1b99cff3454942d9db573))

- Scope workflow permissions and improve concurrency
  ([`9d4090d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9d4090dad97d7b32239bf13e5c6a7ae05c694f42))

### Chores

- Add .gitleaks.toml to allowlist documentation paths
  ([`e24eb24`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e24eb240e21a5f967f22cba9bbe9db98f3f444e0))

- Add MIT LICENSE file
  ([`b752ceb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b752ceb4137aae154acc5d9d18c749e81d61c599))

- Add MIT LICENSE file (#91) ([#91](https://github.com/pvliesdonk/markdown-vault-mcp/pull/91),
  [`e947151`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/e947151e5b81278bca5272c01e3c9fe7b6f3ce6f))

- Generate SBOM and attach to releases
  ([`ad760a3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/ad760a38aa64b2f0c3b95fd708fd3a28ed50fe8a))

- Generate SBOM and attach to releases (#99)
  ([#99](https://github.com/pvliesdonk/markdown-vault-mcp/pull/99),
  [`b364956`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/b3649565547a78a778334d62c33204e553cccc1b))

### Continuous Integration

- Add security scanning (pip-audit, gitleaks, CodeQL)
  ([`3e7198a`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3e7198abf90fc6d643cefeebb5f1613b23fe35ad))

- Add security scanning (pip-audit, gitleaks, CodeQL) (#107)
  ([#107](https://github.com/pvliesdonk/markdown-vault-mcp/pull/107),
  [`decfdb4`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/decfdb436fe9cc5a2566d7084667e364ef83e5bb))

- Address review — scope permissions, pin gitleaks SHA
  ([`daad6bb`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/daad6bbb3ea1a186119f7df5e57dd4fbcf6e36c4))

### Documentation

- Add badges to README (#93) ([#93](https://github.com/pvliesdonk/markdown-vault-mcp/pull/93),
  [`65a6035`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/65a6035f253158debf8e295835b090334f10eb8b))

- Add badges to README (CI, coverage, PyPI, Python, license, Docker)
  ([`eae6b6f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/eae6b6f8dbd665933305901c23d8e1ee0df295b1))

- Add Docs and llms.txt badges to README
  ([`afb328e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/afb328eef254641a2836f576504cc4ef0af21aec))

- Add documentation discipline section to CLAUDE.md
  ([`aeccb1e`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/aeccb1e09702d4c35ada4151e4a729867a3dc6bc))

- Add GitHub Pages site with MkDocs Material
  ([`9570456`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9570456f6a68a863d7650a53835a2c18b64ffb1b))

- Add llms.txt and llms-full.txt generation via mkdocs-llmstxt
  ([`4764c91`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4764c916b3a03d9d257ad9c2b1f6c829ef97f458))

- Add step-by-step quickstart guides for common deployment scenarios
  ([`92018c2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/92018c28c563c6e79b3fb89d8c227a544ce8b4b2))

- Exclude design.md and deployment.md from MkDocs build
  ([`4dfbeab`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4dfbeab87c503a205136713d0ca19895d21b6c4b))

- Generate llms.txt and llms-full.txt for LLM-friendly docs (#110)
  ([#110](https://github.com/pvliesdonk/markdown-vault-mcp/pull/110),
  [`78791ba`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/78791bacd2a17bc9128f46313579b757546f5235))

- GitHub Pages site with MkDocs Material (#103)
  ([#103](https://github.com/pvliesdonk/markdown-vault-mcp/pull/103),
  [`7fbb364`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7fbb36430af2099773a95958122c0d74c19b7e43))

- Step-by-step quickstart guides (#108)
  ([#108](https://github.com/pvliesdonk/markdown-vault-mcp/pull/108),
  [`8af663c`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8af663caf77d844f920fd33498a9049f819e14cd))

### Features

- Expose MCP resources (6) and prompts (5) (#97)
  ([#97](https://github.com/pvliesdonk/markdown-vault-mcp/pull/97),
  [`db22ba7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/db22ba78eb690be536dd4de6a6a7c1036bcd4556))

### Refactoring

- Adopt FastMCP 3 patterns — lifespan, Depends(), tag visibility (#96)
  ([#96](https://github.com/pvliesdonk/markdown-vault-mcp/pull/96),
  [`010ed03`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/010ed03e0188816220ee413af6e842e8ce02ba1f))


## 1.4.0 (2026-03-10)

### Features

- Add Lucide SVG icons to all 13 MCP tools (#90)
  ([#90](https://github.com/pvliesdonk/markdown-vault-mcp/pull/90),
  [`d519354`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/d5193543d5571be4f84aa354fc816fcafd8ff5ad))


## 1.3.1 (2026-03-10)

### Documentation

- Major README overhaul — split config tables, add missing env vars, fix examples
  ([`79c0f5f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/79c0f5f55947319dd7d9f43d1219d79860a72c34))


## 1.3.0 (2026-03-10)

### Bug Fixes

- Exclude hidden dirs and exclude_patterns from attachment listing (#84)
  ([#84](https://github.com/pvliesdonk/markdown-vault-mcp/pull/84),
  [`cf60192`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cf60192a740ab96ac61ab9e0fcfb12cd896f02f6))

- Set git committer identity for Docker auto-commits (#74)
  ([#74](https://github.com/pvliesdonk/markdown-vault-mcp/pull/74),
  [`4c2c466`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/4c2c466d2c24417c574705cc7f0024a0373f4270))

- Suppress noisy tracebacks for YAML parse errors in scanner (#73)
  ([#73](https://github.com/pvliesdonk/markdown-vault-mcp/pull/73),
  [`9f7cd0b`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9f7cd0b4aa6144d36518b8d6c78ee39c9b71df6b))

### Documentation

- Fill design spec gaps (issue #66)
  ([`9f84ca8`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9f84ca8a0b319401cbf987ef82acec0f2bb0bf41))

### Features

- Add non-markdown attachment support
  ([`9f589f3`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/9f589f33487da6cb0118a9ef07977fc4f61e8017))

- Add OIDC authentication via OIDCProxy (#75) (#76)
  ([#76](https://github.com/pvliesdonk/markdown-vault-mcp/pull/76),
  [`8f1b4f6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8f1b4f64b8189a085a10fcca0decf1ce106ef4cb))

- Upgrade to FastMCP 3 (#72) ([#72](https://github.com/pvliesdonk/markdown-vault-mcp/pull/72),
  [`12ae3f2`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/12ae3f2f51ca76a87ce45e2b5f2bf81decb1ee72))

### Testing

- Fix SentenceTransformersProvider mocks to work without library installed (#85)
  ([#85](https://github.com/pvliesdonk/markdown-vault-mcp/pull/85),
  [`26eed41`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/26eed41900de1eb8e51fa671d1635682b5c52847))

- Increase coverage 78% → 93% with quality tests (issue #65)
  ([`32d6f4f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/32d6f4ff844665b8baa2ab12a1798ac401af1e3b))


## 1.2.0 (2026-03-09)

### Features

- **cli**: Add streamable-http transport for Docker deployments (#70)
  ([#70](https://github.com/pvliesdonk/markdown-vault-mcp/pull/70),
  [`5b181d7`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5b181d7eec53c5c71a4707b3a949720c1c08d3e0))


## 1.1.1 (2026-03-09)

### Bug Fixes

- Handle date objects in YAML frontmatter during indexing (#68)
  ([#68](https://github.com/pvliesdonk/markdown-vault-mcp/pull/68),
  [`3786c55`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/3786c55f2cdbce4b1d623e1cedfb9da8b9c437b5))


## 1.1.0 (2026-03-09)

### Bug Fixes

- Remove sentence-transformers from [all] extra to slim Docker image (#59)
  ([#59](https://github.com/pvliesdonk/markdown-vault-mcp/pull/59),
  [`7dd1b4d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/7dd1b4d7bf5bef8e6420a06eaddfa1913c9cd4bd))

- **ci**: Add Python 3.13 to test matrix and align with ruleset (#62)
  ([#62](https://github.com/pvliesdonk/markdown-vault-mcp/pull/62),
  [`5305078`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/5305078aba606e4ddab7e7abfd92c7fdd577b078))

### Features

- Adopt python-semantic-release for automated versioning (#60)
  ([#60](https://github.com/pvliesdonk/markdown-vault-mcp/pull/60),
  [`4938115`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/493811570af293874f1b4c3ea533b23799d64184))

- **docker**: Add UID/GID control via build args (#63)
  ([#63](https://github.com/pvliesdonk/markdown-vault-mcp/pull/63),
  [`22ffa5f`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/22ffa5f110c5b354f1c9bcbadcfa961fd2fcab3e))

- **git**: Refactor to GitWriteStrategy with deferred push (#64)
  ([#64](https://github.com/pvliesdonk/markdown-vault-mcp/pull/64),
  [`cc8b2a6`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/cc8b2a6d6570e51fd67cd6051155dd453e2ce997))

- **mcp**: Configurable server name and instructions (#51)
  ([#51](https://github.com/pvliesdonk/markdown-vault-mcp/pull/51),
  [`68de191`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/68de191abe6bc49d2d55b8580b1b54c45fec09eb))

- **mcp**: Dynamic default instructions reflecting read-only state (#55)
  ([#55](https://github.com/pvliesdonk/markdown-vault-mcp/pull/55),
  [`57c994d`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/57c994d08ac31f18466da0022ea508fe7f6cd601))

- **mcp**: Improve all tool docstrings for LLM clarity (#56)
  ([#56](https://github.com/pvliesdonk/markdown-vault-mcp/pull/56),
  [`8c5a2af`](https://github.com/pvliesdonk/markdown-vault-mcp/commit/8c5a2af8ff13b1665fc934189bbec400c68c0ae4))


## 1.0.0 (2026-03-09)

- Initial Release
