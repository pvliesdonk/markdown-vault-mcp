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

`derived` — Prefer correctness in everyday reading and writing, then bundle
foundations. Correctness work establishes whether the storage and link behavior
that later curation relies on can be trusted. Git reliability supports both;
an alternate backend is one possible improvement, not a prerequisite for OKF.
These are preferences for selecting work, not a total order over all issues.

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
identifies a coherent first delivery slice. The existing reports provide useful
observations; their presence alone does not make the whole stage ready to build.

### Reliable Git integration, with an optional backend

`evidenced` — [Epic #1313][1313] defines an opt-in alternative that preserves
the default CLI backend and the repository shapes it serves. Its **Done when**
retains that outcome and requires both backends to satisfy the same real-repository
scenarios. The epic spans cuts; the former `git` milestone was a theme.

`derived` — The scenario coverage in [#1307][1307] comes before backend delivery
in [#1309][1309]: it reveals compatibility obligations while improvements still
benefit the default backend. The interface work in [#1308][1308] is the other
recorded prerequisite. Native dependencies carry these delivery edges;
[refinement #1468][1468] checks their coverage of the epic outcome.

`derived` — Independently, observable replication in [#1293][1293] comes before
write refusal in [#1299][1299], because evidence of failure modes determines
whether refusal can be useful without rejecting recoverable writes. General
Git diagnostics, operational traceability and CLI hardening can ship independently
of the optional-backend epic.

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

`derived` — A declared library contract ([#1436][1436]) helps assess future
compatibility changes. Announced removals and default changes can be bundled
when their migration story is ready; an old major-version milestone is not
sufficient reason to implement them now. Assess each against the stable release
in use at implementation time.

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
platform transition together with bounded correctness repairs, so subsequent
capability work starts from a dependable baseline. Its GitHub milestone owns
membership. The exact version is computed from what lands.

`evidenced` — Major intent accounts for the template/FastMCP adoption already
recorded in [commit 2c7d46e5][template-adoption], rather than reviving the old
`v5` wish list. This is an input to the cut, not a deadline for unrelated
deprecations or default changes.

`derived` — Create the next package after OKF foundation refinement identifies
a defensible cut and its compatibility impact. Git operability may contribute
an independent slice. Keeping that choice outside a milestone until refinement
avoids converting an architectural ambition into a shipping commitment.

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
| How should Obsidian lookup coexist with source-relative links? | The compatibility decision in [#1383][1383] | Leave this defect outside the cut until its desired semantics are settled; the choice is not supplied by its old priority proposal. |
| What is the smallest useful bundle-inventory boundary, and which later services can use it? | [#1442][1442] and [refinement #1467][1467] | Answer before committing the foundation package; the adopted outcome remains fixed. |
| Which preservation and capability changes need an operator migration, and how are retained reviews presented honestly? | [#1412][1412], coordinated with [#1443][1443] and [#1444][1444] | Classify the transition before promising a minor cut. Full historical observation need not precede basic uncertainty presentation. |
| Which backend compatibility cases remain unpinned? | [#1307][1307] and [refinement #1468][1468] | Learn from the shared scenarios before delivering the optional backend. |
| Which replication failures justify refusing writes? | [#1293][1293], then the decision in [#1299][1299] | Observe before committing refusal semantics. |
| Does the Paper experience meet its original outcome, including the implications of the mobile report? | [Refinement #1469][1469], considering [#859][859] | Check acceptance before closing the epic; no dependency on the first cut. |
| Which creation mechanism meets the agreed boundary? | [#1245][1245] | Decide before committing the scaffold and walkthrough. |
| Do attachment graph nodes require non-Markdown search admission? | Scope decision in [#1359][1359], with [#1234][1234] | Not knowing does not change the first cut or OKF foundation refinement; no speculative dependency is created. |
| Is a deployed vault constrained enough to justify a new vector-storage strategy? | [#1377][1377], informed by [#1368][1368] | Not knowing does not change the first cut. Establish the need before committing a storage technology. |

`derived` — No new research spike is needed for this reconciliation: the
consequential questions already have work that can resolve them. If refinement
finds a question outside those issues whose answer changes the next action, give
that investigation its own research issue and an explicit appetite.

## Revisions

### 13 September 2026 — replace inherited buckets with deliberate cuts

`stated` — The owner's instruction on [#1462][1462] removes the old milestones
as constraints and preserves issues as things still wanted.

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

[809]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/809
[859]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/859
[1232]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1232
[1233]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1233
[1234]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1234
[1245]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1245
[1293]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1293
[1299]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1299
[1307]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1307
[1308]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1308
[1309]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1309
[1313]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1313
[1359]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1359
[1368]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1368
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
[creation-decision]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1245#issuecomment-5475514917
[template-adoption]: https://github.com/pvliesdonk/markdown-vault-mcp/commit/2c7d46e56e16a958a9d085a65ff582b8de885a31
