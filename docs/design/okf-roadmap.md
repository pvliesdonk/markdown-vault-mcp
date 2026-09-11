# OKF roadmap: from the current service to the north star

**Status:** sequenced direction, adopted 11 September 2026. The owner selected
[the north star](okf-north-star.md) as the destination and requested this
roadmap and backlog reconciliation. The sequence below is planning synthesis,
not a claim that the capabilities have shipped. Tracking: [#1425](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1425).

## Starting point

At `c81f014a`, detection, annotations, filters, validation, migration transforms,
export, write stamping, human verification and ranking already exist. Retain
that useful format awareness. The gaps concern the meaning and boundaries of
those services: inventory follows search admission; ordinary enriched writes
replace generation and remove verification; navigation upkeep follows selected
write calls; history follows saves; export reads live searchable membership.

Evidence is the north star's source-linked baseline, checked against the current
annotation, stamping, identity, verification and export paths. Issue reports
remain reports at their recorded revisions, not newly reproduced production
failures. Closed #959 and its implementation children record delivered work.

## Sequence and completion boundaries

Each stage states an outcome and a dependency, not a list of code changes.
Numbers indicate the recommended order of attention, not release versions.

| Stage | Outcome and completion boundary | Depends on | Tracking |
| --- | --- | --- | --- |
| 1. Bundle foundations | Explicit interpretation scope; role-aware inventory independent of search admission; authored and synthesized navigation readable; untyped, malformed, excluded and inaccessible material distinguishable within access policy. Concept requirements do not force invalid metadata into navigation/history. | None | #1442, #1438, #1396, #1421, #1268; robustness #1407, #1408 |
| 2. Preservation and operator policy | Ordinary storage preserves supplied provenance and extensions. Authorship is not inferred from credentials. Semantic writes and generation authority are explicit and scoped. Existing deployments have an explained transition, and configured versus effective capabilities are observable. | Role/scope contract from 1; basic applicability presentation from 3 accompanies changed write semantics | #1443, #1412, #1401, #1419, #1431, #1432, #1434 |
| 3. Evidence and applicability | Every relevant change route refreshes local observations, including files outside search admission. Claims, observations and policy assessments are distinct; absent or malformed evidence stays visible. Older reviews survive without being presented as proof about a changed revision. | Inventory from 1; coordinate delivery with 2 | #1444, #1414, #1413 |
| 4. Deliberate authoring and review | A producer can explicitly author/enrich a concept; repairs are deliberate; a new human or machine review names its snapshot and check. Imported claims remain distinguishable from local checks. | Preservation/policy from 2 and evidence model from 3 | #1422, #1420, #1445 |
| 5. Navigation and editorial history | Explicitly designated generated listings converge on current inventory across writers; authored navigation and editorial history survive. A knowledge event can span concepts or share a day with another event. Optional generated explanations are attributable proposals. Recovery follows role and authority. | 1–3; authoring contract from 4 for assisted curation | #1392, #1394, #1395, #1416, #1418; existing log repair #1403 |
| 6. Provenance-aware retrieval | Claims lead to cited sources and affected concepts. Citation and navigation edges remain distinct. Current-answer, historical and review queries expose their retrieval policy and evidence limitations. | 1 and 3; review integration from 4 as available | #1446, #1447; related #1359 |
| 7. Faithful publication | Export defines scope, root and snapshot, preserves intended membership and link meaning, and reports conformance of the actual artifact. Strict publication is an explicit policy, with honest dependency and review-assurance handling. | 1–3; 4 only for publication requiring a new review | #1448 |

Stages 2 and 3 form a coordinated semantic transition: preservation can be
designed immediately, but retained reviews must not ship behind an unchanged
badge that appears to certify current content. This is a delivery constraint,
not a dependency cycle. Basic uncertainty presentation can ship before complete
historical observation. The whole roadmap does not ship atomically.

After foundations, publication and retrieval can proceed alongside curation.
Publication does not require automatic navigation, an LLM summarizer, Git
trailers, full attachment indexing, or every citation-traversal feature. A
faithful export must still preserve citations and report what it cannot check.
Navigation convergence and editorial history have separate completion boundaries;
neither requires forcing an event for every save.

## Transition rules

Before changing shipped defaults, #1412 records the operator and public-library
compatibility decision against the then-current stable release. Independent
capabilities remain the principle; the old three-switch names and grouping are
not a prescribed target. No version or date is promised by this roadmap.

Existing authored indexes and curated logs are preserved. Only operator policy
outside writable bundle content can designate a listing for regeneration. That
grant covers the listing body, preserves an existing root version declaration,
and does not invent one. A fresh clone can use that explicit grant; a filename,
file marker, authenticated identity or Git trailer cannot grant it.

Older server-produced placeholders, metadata and damaged logs need a reviewed
migration or deliberate repair where warranted; no blanket cleanup should erase
content whose origin is uncertain. #1403 retains the concrete legacy-log defect.
Unavailable observation history after restart or exchange is visible as unknown;
current absence alone is not proof that a concept was deliberately removed.

Each implementation slice updates the authoritative behavior description,
operator guide/README where relevant, and its public docstrings in the same PR.
Validation uses the corresponding north-star scenarios, including read-only
bundles with required concept fields, concurrent review/edit, independent clones,
non-Git exchange and subtree publication. This planning change does not change
runtime behavior or claim those scenarios already pass.

## Tracking and release scope

[#1425](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1425) owns live
child relationships, issue dispositions and completion state. The `okf-roadmap`
theme milestone promises neither a release date nor atomic shipment. This
page owns the sequence, dependencies and transition rationale; it does not
mirror issue status. The broader product roadmap remains #1388 / PR #1390.

The v4.2 stabilization boundary is restoration of existing contracts and honest
operator guidance. Parser resilience, consistent mechanical transforms and
read-only conformance diagnostics fit that boundary. New inventory, authority,
review semantics, conflict policy and automatic legacy-data repair belong to
later implementation slices. Release-specific inclusion and acceptance boundaries
live on milestone 6 and its issues; the north star is not a v4.2 completion gate.

## Design authority

The north star governs the intended OKF end state. `design.md` continues to
describe the implemented system; its old OKF policy decisions and `okf.md` are
historical where they conflict with the north star. In particular, decision 25's
exact switches, write-as-authorship, automatic verification deletion,
index-gate-driven format exceptions, daily accounting and index-only export are
not constraints on future work. Operator-controlled authority, permissive reading,
ordinary portable files and useful format-aware retrieval remain.

Implementation architecture, config names, tool schemas, database layout,
editorial event identifiers and unresolved upstream extension syntax are deferred
to the corresponding implementation designs. The existing sourced north star is
the reference for this roadmap; this session introduces no new external-format
claims. External reference refreshes belong with work that relies on them.
