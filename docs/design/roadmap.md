# Markdown Vault MCP roadmap

This is agent-authored synthesis, not a record of decisions. Arguments are
tagged `stated` (the user's words), `derived` (synthesis), or
`evidenced` (with a durable locator). Read the
[`roadmapping` skill](../../.agents/skills/roadmapping/SKILL.md) before
charting or refining work.

Epics are parent issues for stories; packages are milestones for single
release cuts. GitHub holds membership, dependencies and progress. This
index holds the argument for the direction and order.

## Constraints and direction

`stated` — The owner explicitly released this reconciliation from existing
milestones: issues can be considered "things we still need/want to do"
(13 September 2026, work on [#1462][1462]). Old membership conveys neither
priority nor a promise to ship. Leaving an issue unassigned preserves the wish.

`evidenced` — The product serves ordinary Markdown vaults, curated collections
and downstream Python consumers; MCP is one consumer of the library
([design specification](design.md#use-cases)). The adopted
[OKF north star](okf-north-star.md) adds faithful knowledge exchange and explicit
curation to that foundation. It does not replace the generic vault use cases.

`stated` — The owner selected first-class library support and proper documentation
as the package immediately after correctness work in `010`, centered on
[#1436][1436]. Research must determine whether that requires separate vault and
MCP PyPI distributions (13 September 2026). This inserts library support ahead of
the earlier OKF-foundations preference.

`stated` — The follow-ups after `020` are the optional Git-backend epic
[#1313][1313] and OKF epic [#1425][1425], broken into smaller packages
(13 September 2026). Neither epic is intended as a single release cut.

`derived` — Correctness work establishes whether the storage and link behavior
that later curation relies on can be trusted. Git reliability supports both;
an alternate backend is one possible improvement, not a prerequisite for OKF.
These are preferences for selecting work, not a total order over all issues.

### First-class library

`stated` — The library should be a supported product in its own right, with
proper documentation, rather than something that happens to be importable.

`derived` — [Epic #1471][1471] freezes the outcome: a downstream Python developer
can discover, install, configure, use and close the vault for supported workflows
from published documentation, with an explicit and checked API contract. The MCP
integration consumes that contract, and both paths have an installation and
upgrade story. The outcome does not require a particular distribution layout.

`evidenced` — [#1436][1436] records the missing declared and guarded library
surface. The existing [API documentation](../api/vault.md) describes library
construction; that does not establish complete support for a consumer outside
the MCP server's lifecycle. The reported downstream indexing/search use case
provides a concrete consumer for the research.

`derived` — [Research #1473][1473] compares the combined distribution, optional
MCP dependencies and separate distributions before [refinement #1472][1472]
selects the delivery work. Installation cost, runtime independence, compatibility
and release maintenance determine the choice. Separate distributions need not
mean separate repositories or separate running services. The existing architectural
studies remain background hypotheses, not an adopted packaging design.

`derived` — Deliver the declared contract in [#1436][1436] after the announced
library compatibility removals in [#1225][1225] and [#1236][1236], so documentation
and guarantees describe the supported interface after the major cut. Native
dependencies record those edges and the research prerequisite. Research can start
earlier; if it identifies compatibility groundwork that matters before `010`,
surface that consequence explicitly rather than assuming another major is required.

`derived` — Refinement must cover consumer-oriented installation, a getting-started
path, configuration and lifecycle guidance, practical examples, API reference,
compatibility policy and consumer validation. A public-name inventory alone does
not complete this outcome. Establishing that contract before broader capability
work exposes which assumptions belong to the vault and which belong to its MCP
integration, reducing uncertainty for later OKF and other consumers.

### Faithful OKF bundles and explicit curation

`evidenced` — [Epic #1425][1425], whose **Done when** preserves its adopted
outcome: people and agents can inspect, improve and exchange ordinary bundles
without losing supplied authorship or review evidence, mistaking missing evidence
for assurance, or publishing an incomplete snapshot as a faithful one.
The [OKF roadmap](okf-roadmap.md#sequence-and-completion-boundaries) owns the
stage-level argument and completion boundaries.

`derived` — Start with scope, document roles and inventory, because those expose
what preservation, review and export must account for. Coordinate preservation
with honest applicability presentation: preserving a review cannot mean claiming
it certifies changed content. Once those foundations exist, retrieval and
publication can advance alongside curation. Git trailers and generated editorial
summaries supply no authority and do not gate publication.

`derived` — [Refinement #1467][1467] checks coverage of the frozen outcome and
divides the outcome into independently useful release slices, starting with
inventory. It checks the candidate boundaries below against actual feature
coverage and the coordinated preservation transition. The existing reports
provide useful observations; their presence alone does not make a stage ready
to build.

### Reliable Git integration, with an optional backend

`stated` — The owner clarified that libgit2 addresses the fragile CLI integration,
with eventual retirement of that integration as the longer-term ambition. Testing
an abstraction is not its product purpose. Git remains a likely fit for Obsidian
synchronization; database-backed MCP-only/library-only operation is a distinct
use case and an original reason for the abstraction (13 September 2026).

`evidenced` — [Epic #1313][1313] defines an opt-in alternative that preserves
the default CLI backend and the repository shapes it serves. Its **Done when**
retains that outcome and requires both backends to satisfy the same real-repository
scenarios. The epic spans cuts; the former `git` milestone was a theme.

`derived` — Preserve that bounded opt-in outcome. The longer-term ambition does
not retire the CLI now or silently change the epic's default/compatibility promise;
field experience and repository support must inform a later transition decision.

`derived` — The scenario coverage in [#1307][1307] comes before backend delivery
in [#1309][1309]: it reveals compatibility obligations while improvements still
benefit the default backend. The interface work in [#1308][1308] is the other
recorded prerequisite. Native dependencies carry these delivery edges;
[refinement #1468][1468] checks their coverage of the epic outcome and separates
the contract/test foundation from optional-backend delivery. If backend delivery
is still too large, refinement must find a supported end-to-end opt-in slice,
not release a selector whose advertised operations do not work.

`derived` — Independently, observable replication in [#1293][1293] comes before
write refusal in [#1299][1299], because evidence of failure modes determines
whether refusal can be useful without rejecting recoverable writes. General
Git diagnostics, operational traceability and CLI hardening can ship independently
of the optional-backend epic.

### Versioned vaults without Git

`stated` — The owner wants both database modes considered: ordinary Markdown
files with database-backed history, extending the existing non-Git-managed use
case; and everything in the database, including current content and history,
without a required Markdown working tree. SQLite could serve MCP-only or
library-only consumers; MongoDB is another suggested candidate. These are use
cases and candidates, not completed feasibility findings or a selected engine.

`derived` — [Epic #1474][1474] holds the Git-free versioned-vault outcome.
It is separate from [#1313][1313]: one improves the Git implementation, the other
supports consumers that do not need Git. Both candidate database modes receive
an explicit scope decision; consideration of both does not promise simultaneous
delivery or permit silently dropping one. Existing file/Git workflows remain
supported. Database-owned content is a new optional direction, not a replacement
for the file-based product or the OKF ordinary-file exchange outcome.

`derived` — [Research #1476][1476] assesses the source-of-truth and versioning
contract for each mode before [refinement #1475][1475] selects delivery issues.
It distinguishes durable content/history from rebuildable search data, local
history from synchronization, and file-backed history from database-owned
content. Backup/recovery, external edits, portability and the supported consumer
experience determine feasibility; neither a physical database layout nor Git-like
branching and replication is assumed. The research allowance is provisional and
needs confirmation before execution; this pass records work, not a verdict.

`derived` — Feed the database use cases into the library contract work
([#1436][1436], [#1473][1473]) and shared substitution work ([#1308][1308]) so
those decisions consider intended consumers beyond Git. That is context, not a
claim that database delivery blocks `020`, or that libgit2 must ship first.
No database package or order relative to the selected Git/OKF follow-ups is
committed yet; research must expose useful boundaries before making that choice.

### Usable vault views

`evidenced` — [Epic #809][809] records the Paper redesign and preservation of
existing app behavior. Its **Done when** names that existing outcome: the four
views provide the intended visual experience in both themes while keeping their
navigation and interactions. Native sub-issues carry its historical children.

`derived` — [Refinement #1469][1469] checks the outcome before retiring this epic; closed
implementation tickets alone do not establish it. The mobile defect
[#859][859] remains a separate report whose relationship to acceptance must be
assessed. This check need not delay vault correctness or bundle foundations.

### Other ambitions remain available

`derived` — Vault creation ([#1245][1245]) deserves a mechanism decision before
committing the scaffold or its walkthrough. The recorded
[creation discussion][creation-decision] separates one-time creation of user data
from possible refreshes of shared method packs. It supplies a useful boundary,
not a reason to commit every proposed creation mechanism to one release.

`derived` — Multi-vault hosting, per-user permissions, non-Markdown content,
attachment graphs and storage scaling remain independent ambitions, represented
by [#1232][1232], [#1233][1233], [#1234][1234], [#1359][1359] and [#1377][1377].
No platform-wide release is implied by their proximity in the backlog. Refine
the concrete need before creating additional epics or packages. Documentation,
maintenance and infrastructure issues likewise remain actionable wishes without
a mandatory parent or a release promise.

## Packages

`derived` — **[010 reliable-vault](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/12)
(major)** is the first cut: carry the adopted
platform transition, announced compatibility removals and overwrite-protection
default change together with bounded correctness repairs, so subsequent
capability work starts from a dependable baseline. Its GitHub milestone owns
membership. The exact version is computed from what lands.

`evidenced` — Major intent accounts for the template/FastMCP adoption already
recorded in [commit 2c7d46e5][template-adoption]. The already-announced library
removals ([#1225][1225], [#1236][1236]) and
[operator default change](../configuration.md#write-safety) were checked against
stable behavior during
this reconciliation; their original next-major deferral is the reason to group
them here. Other former `v5` wishes do not acquire a release commitment.

`stated` — **[020 first-class-library](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/13)**
delivers the owner's selected next outcome: supported library use with proper
documentation, centered on [#1436][1436]. Research and refinement belong to this
package; implementation membership is completed from their findings.

`derived` — **Minor intent, provisional.** First-class support does not inherently
require a breaking distribution change. [Research #1473][1473] must establish
whether a split is warranted and whether existing installations and imports can
remain compatible. Reclassify the package as major if the chosen transition breaks
the preceding stable operator or library contract. Do not promise either a split
or a minor version before that assessment.

### Follow-up slices after `020`

`derived` — The following are candidate release boundaries for the owner's two
selected follow-up epics, not milestone membership or implementation plans.
Refinement assigns ordinals and commits the nearest coherent cuts once their
scope is understood. Start with the Git contract/test foundation and OKF bundle
inventory; their relative release order is not yet selected. Neither track waits
for the other epic to finish, and the optional backend does not gate OKF.

All rows below are `derived`. Names describe an outcome; prerequisites express
needed contracts, not a requirement to finish every issue in an earlier stage.

| Track | Candidate cut | Independently useful completion boundary | Ordering argument |
| --- | --- | --- | --- |
| Git | Real Git contracts | Existing CLI behavior is exercised against real repositories, and backend substitution seams preserve that behavior and the supported library contract. | Reveals compatibility obligations before adding a second implementation. |
| Git | Optional backend | An operator can opt into a supported alternative, with shared scenario coverage, actionable capability refusals and installation/operation documentation; CLI remains the default. | Requires the contract/test foundation. Reassess backend capabilities during delivery, not from the historical spike alone. |
| OKF | Bundle inventory | Inspect scoped, role-aware bundle membership independently of search admission, without forcing concept metadata into navigation/history. | Exposes the material every later service must preserve or assess. |
| OKF | Faithful storage and explicit policy | Preserve supplied provenance and extensions; expose semantic-write authority and effective capabilities, with an explained upgrade path and honest uncertainty about retained reviews. | Requires role/scope inventory. Basic review-applicability presentation ships here; it cannot be deferred behind misleading certification. |
| OKF | Evidence and applicability | Distinguish claims, local observations and policy assessments across relevant change routes; show which revision evidence supports and where history is unavailable. | Extends the basic honest presentation with fuller observation coverage, without delaying preservation for complete history. |
| OKF | Deliberate authoring and review | Explicit authoring, repair and snapshot/check-specific review are usable workflows, with imported claims distinct from local checks. | Requires preservation/policy and evidence contracts. Refine authoring and review separately if this is still more than one useful cut. |
| OKF | Generated navigation | Explicitly authorized generated listings converge across writers while authored navigation survives; recovery respects role and authority. | Requires inventory, policy and observations, not generated editorial explanations. |
| OKF | Editorial history | Deliberate knowledge events preserve curated history and can span concepts; any assisted explanation is an attributable proposal. | Uses the curation contracts; navigation convergence is a separate completion boundary, not a reason to bundle both. |
| OKF | Evidence-aware retrieval | Citation sources and affected concepts are discoverable; retrieval purpose and evidence limitations remain visible. | Inventory and evidence unlock this alongside curation; assess citation traversal and policy-aware ranking as separate cuts if needed. |
| OKF | Faithful publication | Publish an explicit scoped snapshot with preserved membership/link meaning and an honest report about the actual artifact. | Inventory, preservation and evidence unlock this alongside curation/retrieval. New-review publication alone needs deliberate review. |

`derived` — These boundaries refine, rather than replace, the
[OKF stages](okf-roadmap.md#sequence-and-completion-boundaries). Publication is
listed last for readability, not as a dependency on every preceding row.
Each candidate needs documentation and validation of its own supported outcome.
Do not make one broad existing issue span multiple milestones: refinement splits
delivery issues where a candidate boundary cuts across their current scope,
preserving the epic's frozen acceptance outcome.

`derived` — Capability cuts have provisional minor intent, except the
preservation/policy transition whose compatibility kind remains unresolved in
[#1412][1412]. The Git foundation may need an explicit release-version override
if its changes are only refactoring/tests; a package label does not itself cut a
release. Every cut is classified against the stable operator/library contract
it will follow, especially the contract established in `020`.

`evidenced` — [The package convention](../../.agents/skills/roadmapping/SKILL.md#packages)
selects the lowest open ordinal as current. Membership commits an issue to one
cut; no milestone means backlog. Epics spanning cuts have no milestone. Package
descriptions and native issue dependencies carry membership and executable order;
this index carries their rationale, without copying progress or item lists.

## Known unknowns

All entries below are `derived`: questions for the linked work, not newly
verified implementation findings.

| Question | Resolved by | Consequence for order |
| --- | --- | --- |
| Where does service-token identity acquire human authorship, and does review authorization share the problem? | [#1463][1463], including its upstream-routing check | Resolve in the correctness cut; do not assume credentials prove authorship or prescribe a replacement actor here. |
| Which index consumers can see stale state after a successful write? | [#1464][1464] | Establish the affected contract before building more curation on immediate reads. |
| Does first-class library support require separate vault and MCP PyPI distributions, and would that require a breaking migration? | [Research #1473][1473] | Resolve before library delivery refinement and the final compatibility classification of `020`; flag any necessary groundwork before `010`. |
| What documentation and consumer validation make the library independently adoptable? | [Refinement #1472][1472], using [#1436][1436] and the research verdict | The package must deliver a supported consumer experience, not only a list of public names. |
| How should Obsidian lookup coexist with source-relative links? | The compatibility decision in [#1383][1383] | Leave this defect outside the cut until its desired semantics are settled; the choice is not supplied by its old priority proposal. |
| What is the smallest useful bundle-inventory boundary, and which later services can use it? | [#1442][1442] and [refinement #1467][1467] | Answer before committing the foundation package; the adopted outcome remains fixed. |
| Which preservation and capability changes need an operator migration, and how are retained reviews presented honestly? | [#1412][1412], coordinated with [#1443][1443] and [#1444][1444] | Classify the transition before promising a minor cut. Full historical observation need not precede basic uncertainty presentation. |
| Which backend compatibility cases remain unpinned? | [#1307][1307] and [refinement #1468][1468] | Learn from the shared scenarios before delivering the optional backend. |
| What makes both file-authoritative/database-history and database-authoritative vault modes viable, and which database fits each? | [Research #1476][1476], then [refinement #1475][1475] | Resolve scope before committing database delivery; feed relevant consequences into the library and shared-interface work without an automatic blocker on `020` or libgit2. |
| Which replication failures justify refusing writes? | [#1293][1293], then the decision in [#1299][1299] | Observe before committing refusal semantics. |
| Does the Paper experience meet its original outcome, including the implications of the mobile report? | [Refinement #1469][1469], considering [#859][859] | Check acceptance before closing the epic; no dependency on the first cut. |
| Which creation mechanism meets the agreed boundary? | [#1245][1245] | Decide before committing the scaffold and walkthrough. |
| Do attachment graph nodes require non-Markdown search admission? | Scope decision in [#1359][1359], with [#1234][1234] | Not knowing does not change the first cut or OKF foundation refinement; no speculative dependency is created. |
| Is a deployed vault constrained enough to justify a new vector-storage strategy? | [#1377][1377], informed by [#1368][1368] | Not knowing does not change the first cut. Establish the need before committing a storage technology. |

`derived` — The distribution and database-mode decisions change their respective
delivery scopes, so each has a research issue with an explicit appetite. Other
recorded unknowns retain their existing resolution pointers; create further
spikes only when the answer changes the next action and no planned work resolves it.

## Revisions

### 13 September 2026 — replace inherited buckets with deliberate cuts

`stated` — The owner's instruction on [#1462][1462] removes the old milestones
as constraints and preserves issues as things still wanted. The owner confirmed
the proposed correctness-first ordering during the reconciliation.

`derived` — Reassessed release membership from that premise. Archived the legacy
`v5`, `git`, `v4.3`, `v4.4` and `okf-roadmap` milestones, recording their former
open membership in their descriptions before unassigning it. Historical closed
membership and milestone identities remain recoverable. Only explicitly selected
work joins the new package; all other former members return to unassigned backlog.

`derived` — Restore one product-wide argument, retain the adopted OKF direction,
and normalize the open epics with outcome statements, refinement tasks and native
relationships. The outcomes are carried forward from their existing descriptions,
not rewritten to match a fresh decomposition. No feature is declared ready and
no epic is declared delivered by this migration.

`evidenced` — [PR #1390][1390] preserves the earlier roadmap proposal and its
record of prior discussions. This index replaces its milestone model and package
proposals under the owner's new instruction; it does not treat unmerged agent
synthesis as newly stated user intent. The studies linked there remain working
material on their branches. The adopted [OKF roadmap](okf-roadmap.md) retains its
stage argument; this index replaces its old theme-milestone and release references.

### 13 September 2026 — support the library as the next product outcome

`stated` — After accepting correctness first, the owner directed that the package
after `010` promote the vault to a first-class library with proper documentation.
The owner explicitly asked research to determine whether that also means splitting
the vault library and supporting MCP surface into separate PyPI distributions.
This revises the earlier preference for OKF foundations immediately after `010`.

`derived` — Commit the announced next-major changes to `010`, then establish
`020 first-class-library`. Preserve [#1436][1436] as the existing API-contract
issue under [epic #1471][1471], with [research #1473][1473] informing
[refinement #1472][1472]. The research is planned, not a completed finding.
Package separation and release kind remain open decisions; neither the package
title nor the broad architectural studies settle them.

### 13 September 2026 — slice both follow-up epics

`stated` — The owner selected [#1313][1313] and [#1425][1425] as follow-ups
and asked to break them into smaller packages.

`derived` — Record candidate cuts for both tracks after `020`, replacing the
earlier asymmetry of OKF next and Git merely available. Keep their independent
dependency paths, the OKF preservation/applicability delivery constraint and
the original epic outcomes. [#1468][1468] and [#1467][1467] own refinement into
bounded delivery issues and concrete package membership; no entire epic or
distant unrefined slice acquires a release promise here.

### 13 September 2026 — distinguish Git reliability from database-backed use

`stated` — The owner corrected the abstraction-test framing: libgit2 is for
reducing dependence on the fragile CLI integration, while database-backed
operation is an original purpose of the abstraction. Both file-authoritative
history and database-owned vaults are to be considered, with different needs
from Git-based Obsidian synchronization.

`derived` — Record [#1474][1474] with research and refinement, keeping both
modes open and the engine undecided. Preserve #1313's frozen opt-in outcome
alongside its longer-term motivation. This extends the product's optional storage
directions without changing current runtime design, the OKF exchange outcome,
or the selected `010` / `020` order.

### 23 September 2026 — re-cut `010` after the withdrawn 5.0.0

`evidenced` — The 5.0.0 release run tagged and announced the cut, then failed
before any artifact was published; [#1556][1556] records the failure and the
withdrawal of the tag, GitHub release and `5.0` docs.

`stated` — The owner chose to withdraw 5.0.0 and re-cut `010` as a
`5.0.0-rc.1` from `main`, carrying the work merged since, rather than
re-running the failed publish. The owner accepted committing [#1551][1551],
[#1532][1532] and [#1544][1544] to `010`, landing before the rc, and moving
[#1370][1370] to `015`.

`derived` — `010` therefore keeps its milestone and major intent and now also
carries link-fidelity repairs, large-vault indexing ([#1535][1535]) and the
template v9 adoption. The remaining open bugs stay backlog with their existing
resolution pointers; [#1383][1383] still waits on its compatibility decision.

[809]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/809
[859]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/859
[1225]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1225
[1232]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1232
[1233]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1233
[1234]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1234
[1236]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1236
[1245]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1245
[1293]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1293
[1299]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1299
[1307]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1307
[1308]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1308
[1309]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1309
[1313]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1313
[1359]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1359
[1368]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1368
[1370]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1370
[1377]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1377
[1383]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1383
[1390]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/1390
[1412]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1412
[1425]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1425
[1436]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1436
[1442]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1442
[1443]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1443
[1444]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1444
[1462]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1462
[1463]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1463
[1464]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1464
[1467]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1467
[1468]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1468
[1469]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1469
[1471]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1471
[1472]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1472
[1473]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1473
[1474]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1474
[1475]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1475
[1476]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1476
[1532]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1532
[1535]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1535
[1544]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1544
[1551]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1551
[1556]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1556
[creation-decision]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1245#issuecomment-5475514917
[template-adoption]: https://github.com/pvliesdonk/markdown-vault-mcp/commit/2c7d46e56e16a958a9d085a65ff582b8de885a31
