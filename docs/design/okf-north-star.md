# OKF in markdown-vault-mcp: a north star

- **Status:** adopted north star, 11 September 2026. The owner selected this as the intended OKF end state. The [sequenced roadmap](okf-roadmap.md) and [tracking epic #1425](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1425) track the transition; the research narrative below retains its proposal-time wording.
- **Relates to:** OKF support (#959), multi-author administration (#1425), and the existing OKF design and backlog, considered as evidence rather than constraints.
- **Scope:** the desired end state, reconsidered from first principles. Adoption, backlog reconciliation, migration, and implementation sequencing are separate work.

**markdown-vault-mcp should make a shared knowledge bundle easier to understand, improve, and exchange while preserving the distinction between what its authors claim and what the server knows.** Its central promise should be that people and agents can collaborate on useful, sourced knowledge through ordinary files, and that another consumer can understand those files without this server's private state.

The recurring failures point to a confused model of responsibility. A storage operation has become an authorship event; a byte change has become a verdict on verification; a reserved filename has become an ownership declaration; and an index admission rule has become a constraint on the format being stored. Each substitution is convenient locally. Together they force the implementation to compensate for decisions it cannot consistently uphold.

The recommended direction is a format-aware knowledge service with explicit curation capabilities. Reading, navigation, validation, preservation, and faithful exchange are foundational. Authoring, review, and maintenance are useful services with their own evidence and authority. Automatic administration is justified by the operation being performed and the information available, rather than by a general promise to keep the vault “correct.”

This direction is adopted as the intended end state. It supersedes conflicting assumptions in the existing design and backlog. The [roadmap](okf-roadmap.md) supplies the high-level sequence and transition boundaries; detailed migration, configuration spelling and implementation architecture remain subsequent work.

**The external baseline is narrower than the existing administration design.** OKF's canonical home is now `GoogleCloudPlatform/open-knowledge-format`. The former `knowledge-catalog/okf` README marks its copy as frozen. The current canonical specification is still v0.2 and its text matches that frozen snapshot; relocation is a source-maintenance problem, not evidence of a newly incompatible format.[^1]

The essential format contract is small. A bundle is a directory tree; concepts carry YAML frontmatter with a nonempty `type`; `index.md` and `log.md` have distinct reserved roles. Indexes and the version declaration are optional. Provenance, trust, and lifecycle are separate families; generation and verification are distinct records within the trust family. Verification records confirmations against sources or the resource; its timestamps are independent of generation. Trust tiers are advisory. The format prescribes neither a serving runtime nor an automatic maintenance workflow. Standard links and bundle-relative identity support exchange; consumers must tolerate substantial incompleteness.[^2]

That leaves room for an ambitious product, but does not make every possible workflow part of OKF conformance. The upstream reference agent illustrates the distinction: its authoring tool accepts producer metadata, fills missing generation actor and timestamp independently (using `reference_agent/<model>` when a model is known, otherwise bare `reference_agent`), and applies domain-specific augmentation guards. The bare-actor fallback itself lacks the version component of the actor convention. Those are producer choices, not proof that every reference-agent output fully follows that convention. They are not mandatory behavior for a generic file server.[^3]

**The existing work contains both a strong foundation and a misleading direction.** The original epic delivered detection, annotations, filters, auditing, transforms, export, write stamping, verification, and ranking. The useful foundation is real: markdown-vault-mcp already reads ordinary files, understands metadata and links, and offers agents a richer retrieval interface.[^4] The problem is the interpretation of the later “enforcement” layer, not the premise that the server should understand OKF.

| Evidence in this repository | Assumption exposed | North-star correction |
|---|---|---|
| Required-field failures led to generated frontmatter, then audit exceptions; #1438 documents the chain. | Every indexed Markdown file must satisfy the same note schema. | Classify document roles before applying role-specific rules. |
| Own writes replace `generated` and remove `verified`; #1413 and #1420 extend the concern to external changes. | File mutation determines production and verification semantics. | Separate transport, authorship, recorded checks, and applicability. |
| Reserved-file upkeep misses rename, deletion, and external changes; #1392. | Maintaining current navigation is a side effect of selected write calls. | Navigation is a view of bundle state, regardless of how it changed. |
| Conflicts create concept-like siblings and inject metadata into reserved files; #1395. | Every `.md` file has the same recovery semantics. | Recovery depends on role and on whether content can actually be regenerated. |
| #1416–#1418 introduce daily keys, pending work, intent, summaries, and accounting boundaries. | Every observed file change needs a knowledge-history disposition. | Knowledge history records meaningful editorial events; operational history records file activity. |
| #1433 splits ownership into independent switches while the broader epic retains older machinery. | Better configuration can settle the underlying responsibility model. | Decide what each service knows and promises before choosing switches. |

These observations are supported by the implementation, issue bodies, and the two superseded administration proposals.[^5][^6][^7] The review history matters: PR #1426 accumulated mechanisms across repeated rounds; PR #1429 explicitly tried to reduce them; both were closed after the narrower ownership decision landed in #1433. Neither closed proposal is the current authoritative whole-system design. Several issue bodies still contain ladder terminology or refer to an ownership document absent from the current tree.[^7]

The conclusion is not that reviews were insufficiently meticulous. The reviews repeatedly improved consistency inside a model whose promises remained too strong. A north star must question those promises before making their machinery more precise.

**Start with a bundle, its documents, and their roles.** A vault is the server's workspace. An OKF bundle is a bounded publication and interpretation context within it. These can coincide, but they should not be conceptually identical. A repository might contain general notes alongside an OKF reference collection; two served bundles might use different authoring conventions. The existing repository's own `docs/design/reference/` collection is a useful example of a bundle within a larger tree.[^8]

Within an explicitly selected OKF scope, distinguish concepts, directory navigation, update history, and supporting artifacts. Separately record whether a particular file is authored, fully generated, or edited collaboratively. Role and maintenance authority are independent: a human-authored `index.md` is still navigation; a server-produced concept is still a concept.

**Maintenance authority belongs to operator-controlled policy outside ordinary writable bundle content.** This proposal explicitly retains the trust boundary in `okf.md` §2: a declaration or a document can supply meaning and advice, but cannot grant the server permission to mutate files or restrict other writers. Authorized client requests can still perform explicit operations under that policy. Operator policy may explicitly designate the navigation listing at named paths as generated output; that grant permits regeneration on a fresh clone or another instance without evidence of prior generation. For a bundle-root `index.md`, the grant covers the listing body; regeneration preserves any existing `okf_version` declaration unchanged and does not invent one when absent. Without that designation, the server preserves the existing file. Records of what the server generated serve diagnostics and change detection, not authorization.

A file saying it is generated does not authorize replacement. Nor should ownership fields be added to reserved indexes: their restricted frontmatter is not an administration channel. Git operation trailers, proposed in #1415, can describe claimed operation history but cannot grant authority or prove exclusive ownership. They are contributor-controlled history, unavailable in non-Git vaults, and cannot account for every direct editor change. They remain potentially useful evidence, subordinate to operator policy and current-state inspection.[^22]

That distinction resolves the required-frontmatter problem at its source. A concept schema applies to concepts. Navigation receives navigation validation; history receives history validation. A human-authored reserved file and a generated reserved file get the same format rules. Exemption based merely on “the server generated it” would reproduce the confusion in another form.

No synthetic `type: null` should be needed to make a navigation file visible. Operator policy can limit which concepts appear in ordinary search, but a search filter must not redefine which files exist in a bundle. Maintain an inventory capable of reporting excluded, malformed, and untyped content. Otherwise the server cannot honestly audit or export what its search index omitted. This is an architectural change: note enumeration for `list_documents`, generated navigation, and export currently relies on FTS. Those features need access to an inventory independent of search admission, even if their public APIs remain familiar. The cost includes preserving distinctions between absent, inaccessible, malformed, excluded, and searchable content—not simply adding a reserved-file exception.[^21]

| Question | Recommended meaning |
|---|---|
| Does this file exist in the selected scope? | Inventory and access-policy question. |
| What role does it have? | Format interpretation question. |
| Does it satisfy that role's format rules? | Conformance question. |
| Does it satisfy this team's authoring standard? | Editorial policy question. |
| Should this query retrieve it? | Retrieval policy question. |
| May the server change it automatically? | Maintenance authority question. |

An optional declaration should remain a useful discovery signal, not a prerequisite for all valid bundles. Explicit scope selection supplies the missing context when no marker exists. Inferring OKF from generic keys such as `type` or `status` would misinterpret unrelated workflows. The desired outcome is predictable scope, including a visible explanation of which interpretation applies; a detailed nested-bundle discovery algorithm is not needed to settle that principle.

**Keep recorded claims, observations, and assessments distinct.** A note can say that Alice verified it last week. The server can observe that its body changed yesterday. A review policy can then decide that another review is required. These are three different statements, and all three can usefully be true at once.

The current implementation returns a compact annotation with status, a Boolean staleness flag, and a trust tier. Missing freshness, malformed freshness, and datetimes without a timezone offset all become `stale: false`; an empty verification mapping becomes `machine-confirmed` because it is a mapping entry. The freshness collapse is an existing decision in `okf.md` §3 and #1373 with which this proposal disagrees, not merely an incidental implementation behavior. `audit_bundle` also has no freshness-field finding, so its conformance report does not recover that distinction.[^9] A consumer seeing only the convenient summary loses the difference between evidence, absence, and malformed input.

The desired response should preserve the supplied metadata and give derived values an explanation. “No expiry supplied,” “expiry malformed,” “not yet expired,” and “expired” should be distinguishable. “Recorded human review” should be distinguishable from “review known to cover the current revision.” A missing review is not evidence that the content is false; an unexpired review schedule is not evidence that the content remains true.

For a note changed after a recorded review, an illustrative presentation is:

> Recorded review: Alice, 2 September. A later content change was observed. Coverage of the current content is unknown. This workspace's review policy requests another check.

This is more useful than either silently continuing to label the note trustworthy or erasing the record and calling it unverified. The names and wire shape can be designed later. The product-level requirement is that a consumer can distinguish the bundle's statement from the server's assessment and inspect the basis of each.

Unknown and malformed values should remain inspectable. Tolerance means continuing to serve useful content, not turning invalid metadata into positive evidence. Compatibility summaries can retain familiar tier names, but they must not be presented as certifications the server has independently established.

**Authorship should describe who produced the knowledge.** At present, the request principal maps an authenticated subject to a `human:` actor, and the write enrichment replaces existing generation attribution with that actor or the server's tool identity.[^10] The repository already recognized the authentication-versus-review distinction in #990, but addressed it specifically at the verification tool.[^11]

The same distinction matters for generation. An agent can use Alice's credentials to write research produced by an agent. A server can copy a document generated by another system. A rename can rewrite links without originating the underlying explanation. The credential holder, the party directing the work, the writer of the explanation, and the software storing it are not interchangeable.

The north star should support truthful production records at the authoring boundary. If the server's own enrichment service produces a description, it can identify that service. If an agent supplies content and a producer claim, the server can preserve the claim and identify it as supplied. If authorship is unknown, leaving attribution absent is preferable to declaring the authenticated person its author. Request identity remains useful in operational audit information without being substituted into knowledge provenance.

This also separates file editing from concept authoring. A storage operation should preserve what it is given unless an explicit operation or workspace policy specifies a semantic transformation. A concept-authoring operation can deliberately update generation metadata, propose a type, maintain citations, and decide how prior reviews are represented. Automatic assistance remains possible; it becomes an identifiable producer with a defined remit.

The server should not need to decide whether every arbitrary Markdown diff was a meaningful change. Sometimes it knows: it just generated a new explanation or performed a narrowly defined mechanical transform. Sometimes the author tells it. Otherwise uncertainty is an honest output, and a review request is an appropriate consequence.

**Verification requires a policy of applicability, not an equation between changed bytes and false claims.** Consider five edits to a reviewed concept: fixing a typo, changing a source URL, changing a number, moving it to another folder, and adding a second independent verification. A blanket “any change clears every verification” rule fails to distinguish them. Conversely, retaining a bare top-tier badge after the number changes would be misleading.

Spec §5.2 states: "`verified` is independent of `generated.at`: content can change without re-confirmation, and facts can be re-confirmed without regeneration."[^2] The repository's own reference identifies clearing verification on write as a deliberate departure, decided in `okf.md` §6.[^8] Independence is therefore the external baseline, not a new product invention. The applicability assessments and stricter review workflows proposed below are product choices beyond that baseline.

The existing write layer deliberately removes the record; the proposed external reconciliation would infer a stale claim from a content change that left metadata untouched.[^12] Both express a defensible conservative concern, but neither establishes the scope of the original verification. A byte comparison establishes a changed representation. Even a semantically consequential edit does not reveal whether every older confirmation covered the altered claim.

The recommended default is preservation plus explicit applicability assessment. Do not automatically delete externally supplied verification events solely because their timestamps precede a change. Do not claim those events certify the changed revision. An operator can require revision-specific review for publication or particular uses, but that is a stricter workspace policy and must remain identifiable as such.

When the server records a new review, it should make its target unambiguous: the reviewed revision or snapshot, the claimed reviewer, and the kind of check. Recording richer applicability information would be a local extension until standardized. It must supplement the portable fields rather than silently change their meaning. Unknown extensions should survive exchange even when another consumer cannot enforce the additional policy.

There is an unavoidable portability tradeoff here. A basic consumer reading retained `verified` metadata may still derive a human-reviewed tier while ignoring this server's applicability warning. Local annotations cannot solve that for every downstream reader. A publication workflow requiring current-revision assurance must therefore obtain a fresh review, withhold publication, or deliberately produce a derivative whose active review fields reflect that stricter policy and whose earlier evidence remains preserved in history. It must not promise that an extension silently gives all v0.2 consumers the stronger guarantee.

Human confirmation can provide evidence of a deliberate review action when the client genuinely mediates it. It is not an integrity guarantee for the entire writable bundle. The review target also matters: the current tool asks for confirmation before reading the note, then guards the subsequent write using the etag of that later read. That guards one write race; it does not identify the revision a person previously inspected.[^13] The north-star promise should be a review of a known snapshot, not simply a successful metadata append after a confirmation prompt.

Machine verification should also be a first-class workflow. A schema comparison, link check, or substantive source comparison can record the particular check actually performed. “A tool ran” is not enough to assert that the concept was confirmed. No generic server should make all three checks imply the same assurance about every claim in a document.

This is an unsettled format boundary, not something this repository alone has misunderstood. Upstream proposal #13 primarily proposes a `refuted` record and a fourth tier; its explicit open-question subsection asks whether older verification should continue to count after generation advances; #15 explores how imported verification should be represented. Both remain proposals.[^14][^15] Their existence supports exposing applicability and origin; it does not authorize treating their proposed fields or algorithms as v0.2 requirements.

There is a further caution about #15: copying Alice's unchanged review record need not mean Alice reviewed it locally. That interpretation is itself the proposal author's position, not a settled rule. The product should preserve origin context and distinguish local confirmation from imported claims without fabricating either.

**Navigation and history need different promises.** An automatically generated directory listing can be a deterministic projection of current bundle inventory and metadata. A curated log entry explaining why a decision changed contains information that may exist nowhere in the final note. They share a reserved-file status, but they do not share a regeneration model.

For navigation, the default should be to serve an existing index as authored, and synthesize useful navigation when necessary. Persistently generating an index is an optional maintenance service. It may replace the listing body only when operator-controlled generation policy designates that path for regeneration, preserving the root declaration as described above. A reserved filename alone does not grant permission to discard its grouping, ordering, or descriptions.

For a fully generated index, every relevant change to current state counts: additions, removals, moves, renamed folders, metadata changes, startup refreshes, filesystem edits, and changes arriving from another clone. Re-observing the same state should converge to the same result without another write. Another instance's “I maintained this” declaration cannot substitute for checking whether the local view is current.

For history, the default should be a readable, selective account of knowledge changes. One coherent event can span several concepts. One concept can undergo two separate decisions in a day. Therefore `(path, day)` is an unsuitable universal identity for a knowledge event. Dates are presentation and chronology; paths identify affected material. Neither establishes editorial unity.

A useful event might say: “Revised the retention policy from 30 to 90 days after the source policy changed; updated the operational guide and example configuration.” Fifteen small writes could produce that one explanation. A single write might correct two independent decisions. The authoring workflow should be able to express both situations without manufacturing an event for every save.

Unexplained changes can appear in an operational activity view or an optional review worklist. They should not force placeholder entries or invisible negative dispositions into the knowledge log. A team that explicitly requires exhaustive change accounting can choose that workflow, but it must identify what is exhaustive: observed file operations, declared editorial events, or reviewed knowledge changes. Only the first is mechanically enumerable without further author input.

An LLM can propose an explanation from a diff. That is a new generated interpretation, not recovered author intent. Such assistance is valuable if its remit, attribution, and optional external processing are clear. A failure to obtain a summary should leave the knowledge intact. Git history can provide supporting evidence or an explicitly labeled activity summary; it cannot by itself supply missing reasons.

This deliberately changes the product direction proposed by #1416–#1418 and follows the strongest simplification raised in #1429.[^16] It also goes further: the north star should organize curation around meaningful events, rather than retaining the limit of at most one entry per note per calendar day after removing its accounting machinery.

**Multi-author operation should preserve information and converge where convergence is possible.** Git, filesystem watching, imports, and server writes are ways to observe or change bundle state. They should not define different meanings for the same concept. With identical bundle content, a server with Git history and a server without it should agree about what the bundle records. The former may have additional evidence about changes and should label that evidence accordingly.

Different treatment is justified for different kinds of information:

| Material | Desired behavior when concurrent changes meet |
|---|---|
| Search indexes, caches, fully generated navigation | Recompute from the resulting authoritative state. |
| Authored or curated navigation | Preserve editorial information; do not assume it can be reconstructed. |
| Knowledge-history prose | Preserve contributions and surface unresolved conflicts. |
| Concept content and supplied provenance | Preserve competing information or obtain an explicit resolution. |
| Local review queues and observations | Rebuild where possible; make evidence loss visible where not. |

Neither “always regenerate reserved files” nor “always use ordinary note conflict siblings” is sufficient. A generated index and an authored index share a filename but have different preservation requirements. A log contains durable curation even when the server wrote its latest entry. The current conflict symptoms are evidence for explicit roles and maintenance authority, not for a global exemption from information-preserving conflict handling.[^17]

A local setting also cannot establish exclusive ownership across independent clones. It can control this server's behavior and its MCP surface. If a deployment needs a single publishing maintainer, that requires a deployment-level agreement. The product must still handle unexpected external edits without treating the setting as proof that they could not happen.

Observing all relevant changes is a necessary capability. #1414 is an existing proposal for a shared change event, not an adopted implementation requirement for this vision; the observation boundary must also account for files outside search admission.[^23] A current inventory alone cannot explain whether an absent concept was removed or never existed. Where that distinction matters, retain explicit history or absence evidence rather than inferring intent from absence. Upstream #11 raises this exchange gap; its proposed representation is not adopted here.[^24]

The desired defaults are conservative and useful: observe external changes; refresh local derived views; preserve supplied metadata; report possible review gaps; make no unrequested repair of authors' claims. Optional curation can change those defaults for an explicitly selected scope. Its authority to write still does not give it evidence it lacks.

**Make provenance useful to retrieval.** The purpose of the system is to help an agent answer questions and improve knowledge, not merely to obtain clean metadata counts. A promising north star gives the agent the concept, relevant claims, cited material, lifecycle context, and review limitations needed for the task.

Today the OKF annotation carries sources on whole reads and source counts on search hits, while graph typing adds the concept type. The external reference page explicitly records that per-claim attribution is not used here; the read-semantics design describes the existing annotation surfaces.[^18] This is a larger product opportunity than increasingly elaborate bookkeeping around reserved files.

A useful workflow should allow an agent to move from a retrieved claim to its cited source, distinguish a citation from a navigational link, and find concepts affected by an identified source change. The service should preserve citation identifiers across edits and expose unresolved citations as diagnostics. It should not imply that a source was fetched, is authoritative, or supports a claim merely because a URL is present.

Likewise, all search contexts are not the same. A current operational answer, a historical explanation, and a request to find material needing review have different needs. Deprecated concepts can be vital for the second; stale concepts are the target of the third. Fixed downweights may be a useful retrieval policy, but they should not be the definition of OKF support or a hidden judgment of truth. The current weights are explicit local product choices: deprecated ×0.5, stale ×0.75, and reserved files ×0.5, applied multiplicatively (deprecated plus stale gives ×0.375).[^19]

The graph should also avoid pretending it knows more than the source expresses. A body link, a source reference, and an inferred semantic relationship should remain distinguishable. More elaborate typed relationships are being proposed upstream, but are not an adopted dependency for this direction.[^20]

An attested computation can be stored, retrieved, linked, and explained without turning this generic server into an execution authority. If execution and checking are later offered, they constitute a separate runtime capability. The distinction between checking a definition and checking a particular run is especially useful here; a successful run must not silently promote the document's review status.[^2]

**Export should be a publication contract.** The current exporter enumerates indexed notes and filesystem-discovered attachments, reads current file content, rewrites resolvable wikilinks, keeps original paths, and includes nonconformant notes that reach that list. A note omitted by index admission never reaches the exporter and is not counted in its `excluded` total. Its documentation nevertheless calls the result conformant.[^21] There are two problems of meaning: consumable does not mean conformant, and search visibility does not necessarily describe the intended publication inventory.

The desired export operation starts with a selected scope and a defined snapshot. It makes its root explicit, includes the intended concepts and supporting files, and resolves links relative to that exported root. A subtree extraction must decide what happens to references outside the selection: retain them with a clear dependency report, rewrite to a known external location, include authorized dependencies, or decline a strict self-contained export. Silently pretending the subtree was already a complete bundle is inadequate.

It should offer a faithful package with a conformance report and, where requested, a strict publication that succeeds only if its stated requirements are met. A tolerant consumer can still read the former. Neither mode should invent types, sources, authorship, or verification merely to make a validator green. Editorial enrichment belongs before publication as an explicit authoring action.

Physical ZIP reproducibility is useful, but semantic fidelity comes first: membership, link destinations, source attribution, supported artifacts, and a consistent content snapshot. Validation should describe the actual exported artifact, including unknown or unchecked areas. Private runtime state may improve the serving experience, but must not be necessary to interpret the exported knowledge honestly.

**The proposed product should pass these scenarios.** They are behavioral tests of the model, not an implementation checklist.

| Scenario | Expected experience |
|---|---|
| A person creates an untyped note in Obsidian. | It remains visible to inventory and triage. Its missing type is reported. No human authorship or review is invented from Git identity. |
| An agent authors a sourced concept using a person's credentials. | Authorship describes the actual producer when known; request identity remains separate. Source claims are preserved. |
| An external editor changes a reviewed concept without editing its metadata. | The recorded review remains inspectable; later change evidence and uncertain applicability are visible; local policy may request review. |
| A reviewer checks an exact revision while another writer edits it. | The new review identifies what was checked; it cannot silently attach to an unreviewed replacement. |
| Several small edits produce one coherent policy change. | One editorial explanation can cover the affected concepts, independent of save count or midnight. |
| Two servers edit different concepts in the same folder. | Derived navigation converges; independent editorial history is retained. Neither server claims global exclusivity from its local setting. |
| A fresh clone has no local generation history. | An explicit operator designation permits regeneration of navigation listings while preserving the root version declaration; other existing indexes are preserved. |
| A conformant bundle is mounted read-only with concept field requirements configured. | Concepts and navigation are interpreted by role; no invalid frontmatter is needed to expose the index. |
| An agent asks which current instructions need review. | The query can use lifecycle, explicit expiry, missing evidence, and review applicability without treating them as one trust score. |
| A bundle is copied to a fresh instance without Git history. | Its portable claims retain their meaning; unavailable local evidence is reported as unavailable. |
| A folder is exported for another consumer. | Membership and root are explicit; dependencies and conformance are honestly reported; exported links retain their intended meaning. |

**The decisions worth making now are few.** Adopt document roles independent of who wrote the file. Keep format conformance independent of local editorial and retrieval policy. Preserve producer claims while distinguishing server observations and applicability assessments. Make authoring and verification acts explicit and attributable. Give navigation a state-derived maintenance model and history an editorial one. Treat exchange fidelity as a core responsibility, not a side effect of the search index.

The three-switch decision in #1433 is a useful recognition that capabilities are independent. It should not settle their eventual names, grouping, or implementation. In particular, `OKF_MAINTAIN` combines index regeneration, log curation, and refusal of client writes to reserved paths. `OKF_RECONCILE` is defined as removing demonstrably stale provenance; that definition presupposes a defensible applicability test, which the byte-change rule does not supply. Independent toggles cannot make those assumptions true.

Read the backlog accordingly. The problems in #1438, #1392, and #1395 remain real and fit the role-and-state model. The desired freshness awareness in #1413 remains valuable, while its equation between an older verification and a void verification needs replacement. #1420 should be reconsidered as evidence-aware review and explicit repair, not accepted as an automatic metadata-deletion requirement. #1416–#1418 should be recast around editorial events and optional assistance. This is a judgment about their desired outcomes, not a proposal to sequence or implement those issues now.

Adoption supersedes `okf.md` as the authority for the intended OKF end state; its description of shipped behavior remains historical evidence. The [roadmap](okf-roadmap.md) records sequencing and transition boundaries; [epic #1425](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1425) records live backlog dispositions. Detailed implementation choices remain with the corresponding work.

The strongest alternative is an administered knowledge system with mandatory schemas, revision-bound reviews, exhaustive change accounting, and one publishing authority. That can be a coherent product if all participating writers accept its protocol. It is a poor default description of a generic Markdown vault shared with independent editors. A managed workflow can be offered as an explicit profile without making ordinary OKF consumption depend on it.

The opposite alternative, a completely passive file server, would abandon useful capabilities already present. It would leave agents to repeatedly reconstruct types, sources, navigation, and lifecycle interpretation. The recommended direction keeps those capabilities and adds more useful evidence traversal, while reducing the claims the server makes on behalf of authors.

A concise charter for subsequent design work is:

> Serve the bundle faithfully. Help authors improve it deliberately. Record who claims what, expose what can be established, and preserve uncertainty when the evidence ends. Keep derived views current without turning editorial knowledge into operational bookkeeping.

**Evidence and limitations.** The repository baseline is commit `cf061462b3f00ef713e85495b312497f89716f31`; upstream canonical HEAD is `ad30107c31c06aec8a7d5636e0d1058118604e6f`. The evidence cutoff is 11 September 2026. Current implementation statements refer to that repository baseline. Issues describe reported problems or proposed outcomes; closed PR designs are historical alternatives, not shipped behavior. The canonical spec's latest path commit is `0b87c52c6ef999286c745e19998fdfcd03d5dbee`; it remains text-identical to the old snapshot inspected here.

The direct behavior checks cover the pure stamping, annotation, actor, and reserved-frontmatter functions. Source inspection additionally establishes the unconditional invocation of the wired enricher on successful content writes described in footnote 12. Stamping itself remains gated by active, enabled enrichment and is bypassed by explicitly suppressed operations. No production vault or telemetry was supplied, so the report establishes conceptual failure mechanisms and desired behavior rather than their prevalence. Upstream proposals on refutation, import attribution, and relationships are unresolved; this proposal intentionally does not adopt their syntax.

**Sources**

[^1]: Google Cloud, [relocation notice in the old OKF README](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/README.md), and [canonical repository](https://github.com/GoogleCloudPlatform/open-knowledge-format). Accessed 11 September 2026. The notice identifies the maintained home; the canonical README distinguishes format from reference implementation.

[^2]: Google Cloud, [Open Knowledge Format specification, v0.2](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/0b87c52c6ef999286c745e19998fdfcd03d5dbee/SPEC.md), especially §§1–3, 5, 8–12. Latest specification-path commit dated 21 August 2026. Normative baseline, including the explicit independence of generation and verification. The proposed applicability assessments and publication policies go beyond that baseline.

[^3]: Google Cloud, [reference agent's concept-writing tool](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/ad30107c31c06aec8a7d5636e0d1058118604e6f/src/reference_agent/tools/bundle_tools.py), `write_concept_doc`. Accessed 11 September 2026. Evidence of one producer's choices, not a conformance oracle.

[^4]: markdown-vault-mcp, [OKF implementation epic #959](https://github.com/pvliesdonk/markdown-vault-mcp/issues/959) and [committed OKF design](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/docs/design/okf.md). Accessed 11 September 2026. Delivered scope and existing rationale.

[^5]: markdown-vault-mcp, [#1438: the required-frontmatter compensation chain](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1438), [#1392: stale maintained indexes](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1392), and [#1395: reserved-file conflicts](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1395). Open at the evidence cutoff.

[^6]: markdown-vault-mcp, [#1425: multi-author administration epic](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1425) and [#1412: independent ownership switches](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1412). Open at the evidence cutoff; several implementation details retain superseded terminology.

[^7]: markdown-vault-mcp, [PR #1426](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1426), [replacement proposal #1429](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1429), and [merged ownership decision #1433](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1433). Conversation comments and inline review comments inform the distinction between adopted decisions and abandoned mechanisms. Both former proposals were closed without merging.

[^8]: markdown-vault-mcp, [reference bundle index](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/docs/design/reference/index.md) and [existing external OKF reference](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/docs/design/reference/okf-v0.2.md), last generated 6 September 2026. The latter still cites the old upstream home and documents deliberate local departures.

[^9]: markdown-vault-mcp, [`derive_trust_tier`, `derive_stale`, and `derive_annotation`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/okf.py). Direct function checks on 11 September 2026 confirmed the empty-mapping and missing/invalid-staleness examples; source inspection confirms the naive-datetime case and absence of a freshness audit finding. The intentional current treatment is documented in `docs/design/okf.md` §3 and [#1373](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1373).

[^10]: markdown-vault-mcp, [`Principal.okf_actor`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/_identity.py) and [write-enrichment runtime](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/_okf_write.py). Current authentication-to-producer mapping.

[^11]: markdown-vault-mcp, [#990: human review versus authenticated identity](https://github.com/pvliesdonk/markdown-vault-mcp/issues/990). Closed; records the rationale for elicitation and verification configuration.

[^12]: markdown-vault-mcp, [`apply_okf_write_stamp`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/okf.py), [#1413: external-change annotations](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1413), and [#1420: external provenance reconciliation](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1420). The latter two are proposals, not current behavior. A direct transform check confirmed replacement of supplied generation and removal of verification with an unchanged body. Source inspection of `managers/document.py::_finalize_content_write` additionally confirms that successful content writes invoke the wired enricher without a body-change check. Active, unsuppressed stamping therefore does not require a meaningful content change. Disabled/inactive enrichment and explicitly suppressed operations remain exceptions. Active stamping reserializes through `fm.dumps` unless the supplied metadata already equals the resulting stamp: exactly the same actor and second-precision timestamp, with no `verified` key and no additional `generated` entries. An unchanged body alone never avoids that serialization.

[^13]: markdown-vault-mcp, [`_require_review_elicitation` and `okf_verify`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/_server_tools/writer.py). The source orders confirmation, read, then conditional write. The distinction between write-race protection and the human's review target is analysis of that ordering.

[^14]: Open Knowledge Format, [proposal #13: refutation and verification applicability](https://github.com/GoogleCloudPlatform/open-knowledge-format/issues/13). Open, accessed 11 September 2026. Used as evidence of an unsettled question, not an endorsed rule.

[^15]: Open Knowledge Format, [proposal #15: imported concepts and verification](https://github.com/GoogleCloudPlatform/open-knowledge-format/issues/15), including its discussion. Open, accessed 11 September 2026. The author's trust-locality interpretation remains a proposal.

[^16]: markdown-vault-mcp, [#1416: curated daily log](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1416), [#1417: intent and pending accounting](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1417), [#1418: diff summarization](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1418), and [PR #1429](https://github.com/pvliesdonk/markdown-vault-mcp/pull/1429). Proposed workflows and the alternative selective-log model.

[^17]: markdown-vault-mcp, [current reserved-file maintainer](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/_okf_convention.py), [#1395](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1395), and [#1419: protecting maintained paths](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1419). Current behavior and unresolved maintenance/recovery boundaries. Maintenance is invoked by `WriterFacet.write`, `edit`, and `append`, not exclusively by MCP handlers; callers such as the upload route can reach it, while migration tools use their separate manager path.

[^18]: markdown-vault-mcp, [OKF read semantics in the authoritative design](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/docs/design/design.md#okf-read-semantics-phase-1-960), and [reference coverage of sources](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/docs/design/reference/okf-v0.2.md). Current retrieval integration and its limits.

[^19]: markdown-vault-mcp, [`okf_downweight_factor`](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/okf.py) and [ranking phase #965](https://github.com/pvliesdonk/markdown-vault-mcp/issues/965). Local ranking policy rather than an external format requirement.

[^20]: Open Knowledge Format, [proposal #16: typed relationships](https://github.com/GoogleCloudPlatform/open-knowledge-format/issues/16). Open, accessed 11 September 2026. Its diagnosis of undirected links is not adopted here: a source document's link already has direction; the open question is richer standardized relationship semantics.

[^21]: markdown-vault-mcp, [bundle exporter](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/okf_bundle.py), [index-based navigation generation](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/managers/okf_migrate.py), and [frontmatter index admission](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/src/markdown_vault_mcp/scanner.py). Source inspection establishes the population and transformation choices; no export of a production vault was evaluated.

[^22]: markdown-vault-mcp, [`okf.md` §2 authority boundary](https://github.com/pvliesdonk/markdown-vault-mcp/blob/cf061462b3f00ef713e85495b312497f89716f31/docs/design/okf.md), and [#1415: operation trailers](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1415). Trailers are proposed operational evidence; the conclusion that they cannot grant maintenance authority is this proposal's explicit policy.

[^23]: markdown-vault-mcp, [#1414: shared change event](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1414). Accessed 11 September 2026. Its issue body describes an index-event design; the vision requires complete observation without prescribing that mechanism or inheriting its index-only population.

[^24]: Open Knowledge Format, [#11: deletion semantics](https://github.com/GoogleCloudPlatform/open-knowledge-format/issues/11). Open at the research cutoff. Used for the distinction between observed absence and a recorded removal, not as an adopted tombstone format.
