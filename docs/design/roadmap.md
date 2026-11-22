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

`stated` — "The next package would be the 015 incremental-embeddings package
and any relevant bugs and high priority bugs" (6 October 2026, while finishing
the 5.1.0 release).

`derived` — So `015` comes before `020`. The 13 September selection above still
sets what `020` delivers; only its position has moved.

`derived` — Correctness work establishes whether the storage and link behavior
that later curation relies on can be trusted. Git reliability supports both;
an alternate backend is one possible improvement, not a prerequisite for OKF.
These are preferences for selecting work, not a total order over all issues.

### Hosted terminal discovery

`evidenced` — [Proposal #1829](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1829) records the missing hosted CLI workflow: main's search constructs a local vault even when the data already lives behind MCP. It arrived as an outside contribution of nine pull requests.

`derived` — Nine incremental slices follow the dependencies: hosted search, existing search controls, note retrieval, graph traversal, structured keyword storage, reader/MCP query exposure, population CLI queries, bounded approximate paths, and path CLI. Hosted search and structured storage queries are independent main-based proposals. The reader/MCP query slice follows storage; CLI controls and discovery follow hosted search. Population and path CLI commands wait for both of their real upstream prerequisites. Prepared fork branches do not count as upstream submissions. The refinement and feature issues currently use ordinary cross-links because this contributor cannot create native sub-issue relationships.

`stated` — The owner adopted the contribution as package `016` and asked for
every pull request to be reviewed and merged into an integration branch, ready
for after `015` (2 November 2026). The maintainers carry the review work
themselves and keep the contributor's authorship.

`derived` — The children landed on `integration/1829-vault-cli`, and the epic
reaches the trunk as one merge-commit pull request ([#1990][1990]). Exact queries
became an optional keyword-index capability rather than new required
`KeywordIndex` methods, so the epic adds no break; whether they join the
required contract is an extender question, deferred with
[epic #2011][2011] (§ First-class library).

### Sustainable embedding storage and search

`evidenced` — [Epic #1499][1499] freezes the outcome: "An operator can sustain
small incremental edits without repeatedly persisting the whole vector corpus,
operate semantic search within a documented memory and latency envelope, and
upgrade or recover without losing compatible embeddings unnecessarily." The
measurement behind it is on [#1497][1497]: 517 full sidecar saves and at least
13.5 GiB of logical sidecar writes in eight hours on a deployed vault.
[#1368][1368] holds the vault-scale measurements that [#1370][1370] and
[#1377][1377] build on.

`evidenced` — Research [#1501][1501] and [#1502][1502] supplied the evidence, and
refinement [#1500][1500] chose the delivery: ordinary SQLite as canonical storage
with bounded-memory exact NumPy search for the built-in backend, and
PostgreSQL/pgvector as the one service backend. [#1501][1501] records the owner's
correction that no first cut or persistence-only release was requested;
[#1499][1499] records that PostgreSQL is an agent recommendation, not an
owner-stated engine choice.

`derived` — Order inside the package follows the native dependencies. Research
[#1508][1508] comes before the lifecycle contract [#1503][1503], because it is
cheap and decides whether that contract can also serve the database-owned
content of [#1474][1474] without delivering it here. Built-in persistence
([#1497][1497]) and the PostgreSQL integration ([#1504][1504]) build on the
contract; bounded-memory search ([#1377][1377]) follows #1497; migration and
recovery ([#1505][1505]) follow both backends. Qualification ([#1506][1506])
comes last, because it measures the assembled system and decides default
activation. The release kind follows from the commits, not from #1506.

`stated` — The figures proposed for [#1506][1506] on 25 October 2026, the
bounded-memory and latency numbers derived from [#1502][1502] with headroom
([proposals](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1506#issuecomment-6398756190)),
are proposals to validate, not agreed limits: the owner, 28 October 2026
([correction](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1506#issuecomment-6469508664)).
[#1899][1899]'s record, [operating-envelope.md](operating-envelope.md), sets
each beside the measurements. [#1900][1900] adopts none as a budget: the
measured envelope is the documented capacity.

`derived` — Inside #1506 the measurement harness ([#1895][1895]) comes first,
because no one had measured the whole server process, so the proposals left
its memory open until the harness supplied a baseline. The runtime policy
([#1896][1896]) is independent and can run alongside. The default store
([#1900][1900]) waited on the recorded run ([#1899][1899]) and the runtime
policy, since either could block activation.

`stated` — With the record in, the owner chose the SQLite store, searched
streamed, as the default (28 October 2026): it writes one note's pages per
edit and keeps search memory flat, at the cost of latency at growth size.

`evidenced` — Research [#1508][1508] answered the contract question
([verdict](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1508#issuecomment-6045346424)).
One source and publication contract serves file-backed vaults and
database-owned content without delivering the latter, and `015` needs only
its embedding side. Embeddings read the keyword index rather than the source.
They ask the source one question, whether a note is present, absent or
unavailable. Each published document carries a revision token compared only
for equality. Enumerating sources and discovering changes without a working
tree stays with [#1474][1474] through [#1476][1476]. The verdict found no
change of scope for `015`.

`stated` — For embedding work that a newer revision has overtaken, the owner
chose conditional replacement, re-reading the source on conflict, over one
writer per store: "A" (7 October 2026, answering the verdict's option (a)).

`stated` — The owner approved the triage proposal for `015` with "Add to 015"
(6 October 2026): [#1730][1730], [#1733][1733] and [#1728][1728] on the index
and embedding paths, and [#1564][1564], [#1542][1542], [#1480][1480] and
[#1492][1492] for their severity. For [#1734][1734]: "we'll do #1734 first"
(6 October 2026).

`derived` — These bugs do not depend on the storage transition, so they can land
while its features are still not ready. [#1728][1728] waited on the template's
packaged-install seam
([fastmcp-server-template#781](https://github.com/pvliesdonk/fastmcp-server-template/issues/781));
since the detach decision it waits on [#2246][2246] instead, which makes those
manifests this project's own (§ Independence from the template and pvl-core).
[#1734][1734] ships in the 5.1.0 cut instead, because 5.1.0 widens the exposure
it describes.

`derived` — Batched provider requests ([#1496][1496]) and the embedding cost of
generated OKF files ([#1498][1498]) come from the same measurement run. They sit
under #1499 with no package: the "Done when" does not need them, and #1498 first
needs a decision on whether reserved files need search by meaning at all.

### First-class library

`stated` — The library should be a supported product in its own right, with
proper documentation, rather than something that happens to be importable.

`derived` — [Epic #1471][1471] freezes the outcome: a downstream Python developer
can discover, install, configure, use and close the vault for supported workflows
from published documentation, with an explicit and checked API contract. The MCP
integration consumes that contract, and both paths have an installation and
upgrade story. The outcome does not require a particular distribution layout.

`evidenced` — [#1436][1436] records the missing declared and guarded library
surface. The existing [API documentation](../reference/api/vault.md) describes library
construction; that does not establish complete support for a consumer outside
the MCP server's lifecycle. The reported downstream indexing/search use case
provides a concrete consumer for the research.

`stated` — `020` supports **callers**: code that constructs, configures, uses
and closes a vault on the built-in backends. **Extenders**, who implement the
backend protocols, come in a later package (3 November 2026). [Epic #2011][2011]
holds that outcome, and [#1994][1994] moves there with it.

`stated` — Making the library async ([#1811][1811]) is deferred, except where
`020` work has to touch the affected code anyway (3 November 2026).

`stated` — How to organise the package has to be decided anyway; [#2000][2000]
asks it for the remote terminal user (3 November 2026).

`evidenced` — [Research #1473][1473] found the library usable outside MCP, with
only two imports tying it to the server stack, while a generic consumer pays 81
packages and the FastMCP and pvl-core version lock for a library that needs 3
([verdict](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1473#issuecomment-6611539511);
[packaging reference](reference/python-packaging-boundaries.md)).

`stated` — The consumer to design for is generic: neither an MCP server nor an
MCP client. Major releases are acceptable. The distributions stay in full
lockstep, documentation stays together, and the template is changed to support
this (3 November 2026). The client depends on MCP only by convenience; a later
version drops it, leaving shared protocol definitions ([#2015][2015]).

`derived` — So the package splits into separate distributions in one repository:
a library free of FastMCP and pvl-core, `markdown-vault-mcp` as the server, and a
remote client, all at one version with exact pins. That needed template support
([fastmcp-server-template#855](https://github.com/pvliesdonk/fastmcp-server-template/issues/855)),
which this project now builds itself (§ Independence from the template and pvl-core),
import independence (including [#2016][2016]) and a CLI whose local and remote
paths are separate. [Refinement #1472][1472] turned that into features under
[epic #1471][1471]; GitHub holds them and their dependencies.

`derived` — Order: import independence ([#2018][2018]) comes first because it
is unblocked, and the #1473 experiment showed it is small. It also proves the
library can stand alone before anything is renamed. The distribution split
([#2019][2019]) waits on it and on the support for several distributions this
project now builds itself (§ Independence from the template and pvl-core). The declared surface
([#1436][1436]) and the consumer documentation ([#2021][2021]) follow the split,
so they describe the library's own import package rather than paths that move.
The client distribution ([#2000][2000]) stays in `020` because the owner wants
it thought through together with the library. It ships only if
[#2016][2016] and that support land in time; the release workflow
unassigns it otherwise.

`derived` — The announced library removals in [#1225][1225] and [#1236][1236]
shipped with `010`, so the declared contract in [#1436][1436] describes the
interface after them. The compatibility baseline is now the latest stable release,
not the `010` cut.

`derived` — Refinement must cover consumer-oriented installation, a getting-started
path, configuration and lifecycle guidance, practical examples, API reference,
compatibility policy and consumer validation. A public-name inventory alone does
not complete this outcome. Establishing that contract before broader capability
work exposes which assumptions belong to the vault and which belong to its MCP
integration, reducing uncertainty for later OKF and other consumers.

### Subsystems governed by an adopted north star

`stated` — A complex subsystem needs "a consistent vision over the whole
project/repo/lifetime, and not an in-session thought or mini-design. That is
true for OKF by actually for many subsystems, this is the acute problem"; the
north star "is that document", and "If that document is wrong or outdated then
it needs to be reopened and rediscussed holistically, with an audit of where it
touches existing code" (owner, 22 November 2026).

`derived` — [Epic #2460][2460]'s **Done when**: a change to a complex
subsystem is always made against its adopted north star, a decision the north
star does not support reopens it instead of being settled in one session or
pull request, and any contributor or agent working from a clone meets that
rule in the repository. Only OKF has an adopted north star; the release vision
was never adopted, and storage, git, the library and submodules have direction
paragraphs here but no governing document. [Refinement #2461][2461] decides
which subsystems need one, in what order, and how the rule is enforced. Each
candidate gets a review first: OKF ([#2459][2459], which reopens the existing
one), git integration ([#2462][2462]), storage and search ([#2463][2463]), the
library and its public surface ([#2464][2464]), the release system
([#2465][2465], whose vision document was never adopted), vault views
([#2466][2466]) and the documentation system ([#2467][2467]).

`derived` — The OKF north star comes first because 025 was built against it
without it being consulted, and it is demonstrably out of date: it names OKF
0.2 as current, describes pre-025 behaviour, and lacks the 21–22 November
decisions. [#2459][2459] reopens it against OKF 0.3 and those decisions, with
an audit of where it touches the code.

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

### Vaults with git submodules

`stated` — The owner chose the split (23 September 2026): read-only support for a
submodule inside the vault first, and writes into a submodule later as an epic
of its own. In the read-only slice a write into a submodule of a writable vault
is rejected rather than silently accepted and lost.

`evidenced` — Today a submodule inside the vault is scanned like any folder while
every git command addresses the superproject: a write lands on disk and never
reaches git ([#1564][1564]), a managed clone leaves the folder empty and a pull
moves the pointer without the bytes ([#1565][1565]), and the history tools return
nothing for its notes ([#1566][1566]). The git behaviour behind each is recorded
with sources in [reference/git-submodules.md](reference/git-submodules.md).

`derived` — [Epic #1561][1561] is the read-only slice. The refusal ([#1564][1564])
comes first because it stops data loss on its own; content currency ([#1565][1565]) and the history answer
([#1566][1566]) follow and can ship independently. [Epic #1562][1562] (writes)
carries only its refinement task ([#1567][1567]), blocked by those three: a
two-repository write path is shaped against a working read-only layout, and its
detached-HEAD and credential questions stay unknowns until then. The read-only
slice is packaged as `025` and the write epic as `060`, both after `020`. The
refusal ([#1564][1564]) moved ahead into `015` on 6 October 2026, because it
stops data loss on its own (§ Sustainable embedding storage and search).

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

`stated` — "i want a new roadmap package 030 that collects all currently open
issues around the MCP app" (7 October 2026).

`derived` — `033 mcp-app` (then `030`) therefore carries the refinement ([#1469][1469]) and
every open issue on the app and its tools: the mobile display defect
([#859][859]), the runtime font dependency ([#1198][1198]), the app tools'
error outcomes ([#1640][1640]), and the SonarCloud reliability findings in the
SPA ([#1750][1750], [#1753][1753], [#1754][1754]). The epic itself carries no
milestone: only its acceptance check is open, and that check is what `033`
commits. Doing the refinement in the same cut as the fixes lets it decide
whether [#859][859] belongs to the epic's outcome with the fix in view, rather
than before it.

### Reader-shaped documentation

`stated` — Domain documentation lives only in places the template designates
for it, and non-domain documentation always belongs to the template, with no
exceptions (owner's ruling, 28 September 2026, work on [#1272][1272]).

`stated` — Superseded on 18 November 2026: after the detach "*everything* is
MVM owned", so where the template designates domain pages no longer constrains
this repository's documentation (§ Independence from the template and pvl-core).

`stated` — The owner chose a structure organised by reader goal, with one
Diátaxis kind per page, a Contribute section last on the site, a front-door
README, reference generated from code, and the template building it first so
this repository migrates onto it (28 September 2026).

`evidenced` — Round 1 of the persona review found blockers on most reader
paths, and found content outside the ownership rule with no recorded decision
behind it; the walks, counts and audit are in the review comments on
[#1272][1272] (28 September 2026).

`derived` — The template epic
([fastmcp-server-template#710](https://github.com/pvliesdonk/fastmcp-server-template/issues/710))
came first, because this repository cannot place its domain pages
compliantly until the template designates where they go; its pages shipped in
template v11.1.0. The migration epic [#1665][1665] is refined on that
release: the adoption first, because every other child places text in
blocks and directories only the adopted render has; then one feature per
section, in any order; then round 2 of the review, because it is the
measurement the epic's "Done when" names and the evidence the template's
review skill distils. The standalone accuracy bugs from round 1 were fixed on
the old site first, because they misled readers then.

`evidenced` — Round 2 ([#1685](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1685),
read at `369e4917` on the integration branch, 4 October 2026) did not meet the
epic's "Done when": blockers fell from 21 to 11 across the eight walks, three
walks still ended in a `misled` or `absent` blocker, the evaluator decided over
its tolerance, and the audit found 15 `non-domain-in-mvm` units and no
`domain-undesignated` one. No round-1 standalone bug recurred as a blocker. The
walks, counts and audit are in the round-2 comments on [#1272][1272]. Six of the
blockers were template defects, fixed in template v11.1.4; one was resolved by
adopting it.

`stated` — The owner ruled on the remaining four, which were filed as
documentation bugs: "The blockers are not documentation problems: the
underlying issues must be addressed, but that is not 1690" (4 October 2026).

`derived` — They left the epic for `013 surfaced-fixes`, the nearest cut for
behaviour work; the ruling names no package. So the migration's final pull
request merges with round 2's
measurement on record, and [#1665][1665] stays open: its "Done when" is
unchanged and not yet met. Unknown: which of round 2's remaining items (the
evaluator's overrun, the published and unpublished `non-domain-in-mvm` units,
the seven `unclear` ones) the epic must still answer before a third
measurement. Resolved by the owner's ruling on the open list in the round-2
summary on [#1272][1272]; not knowing does not change the order of `013`.

`evidenced` — [#1665][1665] was closed as completed on 6 October 2026, together
with the `011` milestone, with no closing comment. The index does not record
whether its "Done when" was judged met or the remaining round-2 items were
dropped; the unknown above stays open until the owner says which.

### Independence from the template and pvl-core

`stated` — The owner decided to detach this repository from
`fastmcp-server-template` and to fold in `fastmcp-pvl-core`, because with an
active contributing community the upstream dependencies hold the project back
(18 November 2026). After the detach, "*everything* is MVM owned".

`stated` — On pvl-core: "We fold it in and eventually get rid of it altogether.
many things are just wrappers around fastmcp that we can get rid of by doing it
directly, while others are helper functions that we can just transfer to a
different package name." (18 November 2026)

`stated` — The repository takes template v11.7.0 as its final sync before
detaching. On the support this project asked of the template in
[fastmcp-server-template#855](https://github.com/pvliesdonk/fastmcp-server-template/issues/855):
"we would not be depending on the template for this anymore. sibling project
might still want that behaviour" (18 November 2026).

`derived` — [Epic #2240][2240] freezes the template outcome: a contributor
changes any file here and passes every check without a template-conformance
rule, a `copier update` or an upstream pull request, and the instructions agents
load name this repository as the only place fixes go. [Epic #2241][2241] freezes
the core outcome: the server installs and runs without `fastmcp-pvl-core`, every
setting of the last stable release still works, and no shared-core layer
remains. Their refinement issues are [#2242][2242] and [#2243][2243].

`evidenced` — `FORKING.md`'s detach is kept current and smoke-tested upstream,
but only on a fresh render that never runs `pytest`. A trial detach of
`eb374b2` broke 10 tests here and the Lint job's config-surface check, which
reads `.copier-answers.yml`, and left the upstream routing in `CONTRIBUTING.md`
and the `TEMPLATE-OWNED` banners on the skills in place. The release-flow tests
that failed in the same trial fail identically without the detach in that
environment, so the detach does not touch them.
pvl-core 10.2.0 is about 11,700 lines with about 15,800 lines of tests.
Figures and method are in the epic bodies of [#2240][2240] and [#2241][2241],
and in [#2246][2246] for the config-surface check. The refinement's import scan
([#2274][2274]) corrects the epic's file counts: this project uses 53 of the 96
public names, from 21 source files, 23 test files and one script.

`evidenced` — Order: the template first. pvl-core is imported outside sentinel
blocks in `server.py`, `cli.py`, `config.py` and `_server_apps.py`, and the
`fastmcp-pvl-core` requirement in `pyproject.toml` sits outside its
`PROJECT-DEPS` block. Every core child therefore edits template-owned lines,
which the conformance push hook rejects until the detach lands.

`stated` — At refinement the owner chose, from the options proposed, to dissolve
pvl-core area by area directly instead of vendoring a copy first, and ruled that
`ConfigurationError` becoming this project's own class is not breaking
(18 November 2026).

`derived` — Within the core epic: the import shim ([#2274][2274]) first, because
it is the only change that touches many files, and afterwards each area changes
only the shim and its own call sites. Then the eight areas, in any order:
configuration and environment ([#2275][2275]), logging ([#2276][2276]), auth
([#2277][2277]), serving ([#2278][2278]), state stores ([#2279][2279]), the tool
boundary and instructions ([#2280][2280]), jobs ([#2281][2281]) and transfer
([#2282][2282]). Dropping the dependency ([#2283][2283]) comes last, and it
meets the epic's "Done when". Copying core in only to delete it again was the
cost the owner declined. The price is that core stays a dependency until the
last area lands. Folding pvl-core into the server side also serves
[#1471][1471]'s library free of pvl-core. [#2275][2275] resolves the coupling
left in [#2016][2016] (pvl-core's errors and environment helpers,
fastmcp-pvl-core#543).

`derived` — Known unknown: which names in each area are thin FastMCP wrappers
and which are helpers with their own logic, and so whether an area fits one pull
request. Each area feature answers this before coding and splits if needed.
[#2273][2273] may shrink what the configuration area has to keep, for example
the config-surface generator's provenance layers.

`stated` — This work goes first: the template part as package `010`, the
pvl-core part as `015`, ahead of every other open package (18 November 2026).
§ Packages has both. Bug [#2235][2235] keeps its lock-only override as the
near-term fix, in `010`; its CI-side option becomes a local edit once
[#2240][2240]'s detach lands.

`derived` — Refinement turned [#2240][2240] into features. The final sync
([#2245][2245]) comes first, because whatever it does not bring in has to be
ported by hand afterwards. Stopping the tracking ([#2246][2246]) comes next,
because its conformance gate is what blocks every other child: the project
settings file ([#2247][2247]), the fix routing ([#2248][2248]), the
single-owner skills and docs ([#2249][2249]) and the experimental Python leg
([#2250][2250]) all edit template-owned lines, and they can then land in any
order. The same gate blocks [#1728][1728], [refinement #2251][2251] and every
child of [#2241][2241].

`derived` — Coverage falls short in one place, and the criterion stays as
frozen: while pvl-core is a dependency, fixes to its behaviour still go
upstream, so "the only place fixes go" holds only once [#2283][2283] drops the
dependency. [#2240][2240] stays open until then.

`derived` — The sentinel blocks stay. Five generators and the config-contract
test parse them; only the ownership wording around them goes. The detach keeps
`claude.yml`, the `@claude` responder this repository advertises, unless the
owner decides otherwise on [#2246][2246].

### Other ambitions remain available

`derived` — Vault creation ([#1245][1245]) deserves a mechanism decision before
committing the scaffold or its walkthrough. The recorded
[creation discussion][creation-decision] separates one-time creation of user data
from possible refreshes of shared method packs. It supplies a useful boundary,
not a reason to commit every proposed creation mechanism to one release.

`derived` — Multi-vault hosting, per-user permissions, non-Markdown content
and attachment graphs remain independent ambitions, represented by
[#1232][1232], [#1233][1233], [#1234][1234] and [#1359][1359]. Storage scaling
([#1377][1377]) is now part of `015`.
No platform-wide release is implied by their proximity in the backlog. Refine
the concrete need before creating additional epics or packages. Documentation,
maintenance and infrastructure issues likewise remain actionable wishes without
a mandatory parent or a release promise.

## Packages

`stated` — **[010 template-detach](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/35)** and
**[015 pvl-core-fold](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/36)** come before every other open package
(18 November 2026).

`evidenced` — A closed package's title gives up its ordinal for the release
version at the cut (`v6.0.0 incremental-embeddings`), so the shipped
`010 reliable-vault` and `015 incremental-embeddings` below no longer hold those
numbers; the lowest ordinal on an open item was `024` (issue search by milestone
title, 18 November 2026).

`derived` — **010 template-detach** carries the children of [#2240][2240] and
[#2235][2235]. Most of it is `chore`, `ci` and `docs`, which cut no release on
their own; its kind follows whatever user-facing commit the final sync
([#2245][2245]) brings, and the template's v11.7.0 changes are features. The
epic itself carries no package: it stays open until `015` drops the pvl-core
dependency ([#2283][2283]; § Independence from the template and pvl-core).

`derived` — **015 pvl-core-fold** carries the whole fold: the import shim, the
eight area features and the dependency drop ([#2274][2274] to [#2283][2283]).
Its kind is not major: the owner ruled that the one candidate break,
`ConfigurationError` becoming this project's own class, is not breaking.

`derived` — Because the release workflow closes the lowest open package at a
stable cut, `010` and then `015` are what the next stable cuts consume. A
stable release cut before either is complete unassigns its open members.

`stated` — Stable releases pause until these packages ship: "This is the plan"
(18 November 2026).

`derived` — **[010 reliable-vault](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/12)
(major)** is the first cut: carry the adopted
platform transition, announced compatibility removals and overwrite-protection
default change together with bounded correctness repairs, so subsequent
capability work starts from a dependable baseline. Its GitHub milestone owns
membership. The exact version is computed from what lands.

`evidenced` — Major intent accounts for the template/FastMCP adoption already
recorded in [commit 2c7d46e5][template-adoption]. The already-announced library
removals ([#1225][1225], [#1236][1236]) and
[operator default change](../reference/configuration.md#write-safety) were checked against
stable behavior during
this reconciliation; their original next-major deferral is the reason to group
them here. Other former `v5` wishes do not acquire a release commitment.

`stated` — **[011 reader-shaped-docs](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/18)**
carries the documentation migration ([#1665][1665]) and the template epic it
adopts, as one cut (28 September 2026).

`stated` — **[013 surfaced-fixes](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/19)
(patch)** holds the code defects the migration surfaced, as their own cut after
`011` (4 October 2026).

`derived` — Round 2's four remaining blockers joined `013` as behaviour work;
§ Reader-shaped documentation has the ruling that took them out of the
migration.

`stated` — "Reopened already and we'll do #1734 first" (6 October 2026),
after the triage found `011` and `013` closed before the stable 5.1.0 cut.

`derived` — The release workflow closes the lowest open package at a stable cut.
With `011` and `013` closed, the 5.1.0 promotion would have consumed `015` and
returned its open members to the backlog. Reopening `013` makes 5.1.0 consume
it, carrying [#1734][1734].

`evidenced` — **[015 incremental-embeddings](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/14)**
ships the built-in backend and one service backend together, with incremental persistence, bounded-memory exact retrieval,
compatible migration and recovery, and a validated operating envelope (its
milestone description). Its release kind is what knope cuts from the commits;
the library changes already on the trunk since v5.1.0 carry `!`, and the
default-store change asks nothing of an operator ([#1900][1900]). § Sustainable embedding storage and search has the
argument.

`stated` — **[016 Query a hosted vault from the terminal](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/21)
(minor)** ships epic [#1829][1829] after `015`: the hosted terminal workflow,
reviewed child by child on its integration branch (2 November 2026).
§ Hosted terminal discovery has the argument.

`stated` — **[020 first-class-library](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/13)**
delivers the owner's selected next outcome: supported library use with proper
documentation, centered on [#1436][1436]. Research and refinement belong to this
package; implementation membership is completed from their findings.

`derived` — **Major intent.** The split chosen in [research #1473][1473] moves the
library to a new import package, and majors are acceptable to the owner, so plan
`020` as a major. Its timing depends on this project's own support for several
distributions, which replaces the template support once asked for in
[fastmcp-server-template#855](https://github.com/pvliesdonk/fastmcp-server-template/issues/855)
(§ Independence from the template and pvl-core).

`derived` — `020` follows `015` (§ Constraints and direction). The embedding
research informs its storage, lifecycle and installation expectations without
blocking the library research, which can run concurrently.

`stated` — **[021 backlog-fixes](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/22)**,
**[022 startup-and-memory](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/23)**
and **[023 reported-bugs](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/24)**
come before `028`, in that order (15 November 2026). Each draws its issues from
the contributed pull requests open on that date.

`derived` — **Patch intent for all three.** `021` holds owner-filed bugs whose
contributed fixes are small, and the test-only de-flake work lands first inside
it, so that CI is quiet for everything after. `022` holds owner-filed bugs whose
contributed PRs need an owner decision before review, and since `021` shipped
it also takes the small owner-filed fixes that need none. `023` holds bugs the
contributors reported, which join after their issues are accepted. The order
follows from that: the least open decision ships first.

`stated` — Since `022` shipped, `023` also takes the small owner-filed fixes,
decay, tests and docs contributed after packaging, as `022` did (17 November
2026).

`stated` — **[024 okf-evidence](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/25)**
follows `023` with the contributed OKF evidence work under [epic #1425][1425]
(16 November 2026). Since 20 November it holds only the starting points of the
contributed OKF chain: [#1444][1444], [#2059][2059] and the slices
[#2378][2378] (of [#1413][1413]) and [#2379][2379] (of [#1446][1446])
(§ Revisions, 20 November 2026).

`derived` — **Major intent**, because the owner kept the breaking marker on
the [#1444][1444] fix: malformed review records no longer promote
`derive_trust_tier`.

`stated` — `024` also takes two breaking fixes outside OKF, because it is
already a major (17 November 2026). [#1631][1631] accepts only a closed set of
history date forms, and [#2205][2205] refuses degenerate or colliding search
weight maps.

`derived` — The content name `okf-evidence` no longer covers everything in the
package. It is a label, not a scope.

`stated` — **[025 contributed-features](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/26)**
follows `024` with eight owner-filed features that have contributed PRs:
[#1293][1293], [#1241][1241], [#1923][1923], [#875][875], [#1605][1605],
[#1496][1496], [#2031][2031] and [#1405][1405] (17 November 2026).

`derived` — **Minor intent.** Each feature still has an open design question
for the owner, recorded in the package description. A feature's PR merges only
after that ruling, and one left unsettled at the cut moves out rather than
holding the release.

`stated` — `020 first-class-library` is renumbered `027` and `025
submodules-read` is renumbered `028`, so that `024` fits after `023` and the
order is otherwise unchanged (16 November 2026). Each milestone keeps its
identity and membership.

`stated` — The candidate cuts of [epic #1425][1425] and [epic #1313][1313]
(§ Follow-up slices) are packages, OKF inventory first (18 November 2026). To
put both tracks' first cuts directly after `025`, `027 first-class-library`
became `028`, `028 submodules-read` became `029`, and `030 mcp-app` became
`033`. Each milestone keeps its identity and membership:

`derived` — The list shows the packages as re-cut on 20 November 2026, after
that statement (§ Revisions); the OKF entries no longer have their 18 November
membership. Since 21 November 2026 every ordinal from `025` to `041`
is one higher, so that OKF v0.3 conformance takes `025` (§ Revisions).

- **[020 north-star-governance](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/43)**:
  epic [#2460][2460], refinement [#2461][2461] and the OKF north star's
  reopening [#2459][2459]. Mostly docs and process, which cut no release on
  their own, like `010 template-detach`; its kind follows whatever code fix the
  reopening's audit brings.
- **[025 okf-v03-conformance](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/42)**
  (major): epic [#2401][2401]: research [#2403][2403] first, then one bug per
  departure from the spec.
- **[026 contributed-features](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/26)**:
  the `025` of the sections above.
- **[027 okf-capabilities-inventory](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/27)**
  (major): capability settings, gating and reporting ([#2380][2380],
  [#1411][1411], [#1432][1432]), then inventory ([#1442][1442], [#1421][1421],
  [#1610][1610], [#1606][1606]) and [#1419][1419].
- **[028 git-contracts](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/28)**
  (patch; refactor and tests may need `override_version`): [#1308][1308],
  [#1310][1310], [#1569][1569], [#2224][2224].
- **[029 first-class-library](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/13)**:
  the `027` of the sections above.
- **[030 submodules-read](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/15)**:
  the `028` of the sections above.
- **[032 okf-provenance](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/29)**
  (major): explicit authoring ([#1422][1422]), then preserved provenance
  ([#1412][1412], [#1443][1443], [#2057][2057]), then reserved files without
  seeded placeholders ([#1438][1438]).
- **[033 git-optional-backend](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/30)**
  (minor): [#1309][1309].
- **[034 mcp-app](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/20)**:
  the `030` of the sections above.
- **[035 okf-write-groups](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/39)**
  (minor): [#1677][1677], the git conflict fixes its pull deferral sits on
  ([#1395][1395]) and [#1415][1415].
- **[036 okf-observations](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/40)**
  (minor): [#1414][1414] and [#2045][2045].
- **[037 okf-review-binding](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/41)**
  (major): [#1445][1445] and [#1413][1413].
- **[038 okf-publication](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/34)**
  (major): [#1448][1448].
- **[039 okf-repair](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/32)**
  (major): [#1420][1420], [#2061][2061] and [#2062][2062].
- **[040 okf-navigation](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/31)**
  (minor): [#1611][1611], [#1612][1612] and [#1394][1394].
- **[041 okf-retrieval](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/38)**
  (major): [#1447][1447], [#2058][2058] and [#1446][1446].
- **[042 okf-editorial-history](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/33)**
  (minor): [#1416][1416], [#1418][1418] and [#2381][2381].

`derived` — The OKF packages follow the order in which the contributed pull
requests are built, not the candidate cuts of § Follow-up slices (§ Revisions,
20 November 2026). The candidate rows still name the outcomes. An open decision
inside a package is work for that cut, not a reason to keep its issue out.

`stated` — "The milestone is a collection of issues to resolve for a release.
Not a collection of already accepted PRs" (20 November 2026). A
contributor-filed issue therefore joins the package it is meant for, and
accepting it is work inside that package. This
supersedes the 17 November rule that a contributor-proposed issue has no
package until accepted.

`stated` — "If a PR carries ! then it will be a major break" (20 November
2026). By that rule `024`, `025`, `027`, `032`, `037`, `038`, `039` and
`041` are majors (as renumbered on 21 November 2026).

`derived` — Two packages were added on 20 November 2026:

- **[030 terminal-followups](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/37)**
  (minor) holds the contributed follow-ups to the shipped hosted terminal epic
  [#1829][1829]. The free ordinal before `031` keeps them from waiting on the
  OKF and Git tracks, which they do not depend on. It is `031` since 21
  November.
- **[038 okf-retrieval](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/38)**
  (major) is the evidence-aware retrieval cut of § Follow-up slices, which had
  no package. It follows the evidence and inventory cuts it reads from. It is
  `040` since the re-cut later that day, and `041` since 21 November.

`derived` — **[029 submodules-read](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/15)
(minor)** follows `028` with the read-only submodule epic [#1561][1561], less the
refusal that moved to `015`.

`stated` — **[033 mcp-app](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/20)**
collects the open MCP app issues as one cut (7 October 2026).

`derived` — **Minor intent**, because [#1198][1198] is a feature; the rest are
defects and decay. The owner set the ordinal, which places it after `029` and
before `060` (renumbered from `030` on 18 November 2026). § Usable vault views
has the argument.

`derived` — **[060 submodules-write](https://github.com/pvliesdonk/markdown-vault-mcp/milestone/16)
(minor, provisional)** ships the writable-submodule epic [#1562][1562] atomically,
refined after `029` lands. § Vaults with git submodules has the argument.

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

`derived` — Capability cuts have provisional minor intent, unless a PR in the
cut carries a breaking marker, which makes it a major (§ Packages, 20 November
2026). The Git foundation may need an explicit release-version override
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
| Which support for several lockstep distributions must this project build itself, and which parts of the folded-in pvl-core (client, authentication, non-MCP API) does the split reuse? | [Refinement #2251][2251] of [#1471][1471]; [fastmcp-server-template#855](https://github.com/pvliesdonk/fastmcp-server-template/issues/855) no longer gates it | Decides when the split can ship; the import-independence work in `020` does not wait for it. |
| Which names in each pvl-core area are thin wrappers around FastMCP, and which are helpers to keep under this project's names, and does each area fit one pull request? | Each area feature, [#2275][2275] to [#2282][2282], before it codes | Effort and splitting only; the areas run in any order. |
| What documentation and consumer validation make the library independently adoptable? | [Refinement #1472][1472], using [#1436][1436] and the research verdict | The package must deliver a supported consumer experience, not only a list of public names. |
| How should Obsidian lookup coexist with source-relative links? | The compatibility decision in [#1383][1383] | Leave this defect outside the cut until its desired semantics are settled; the choice is not supplied by its old priority proposal. |
| What is the smallest useful bundle-inventory boundary, and which later services can use it? | [#1442][1442] and [refinement #1467][1467] | Answer before committing the foundation package; the adopted outcome remains fixed. |
| Which preservation and capability changes need an operator migration, and how are retained reviews presented honestly? | [#1412][1412], coordinated with [#1443][1443] and [#1444][1444] | Classify the transition before promising a minor cut. Full historical observation need not precede basic uncertainty presentation. |
| Which backend compatibility cases remain unpinned? | [#1307][1307] and [refinement #1468][1468] | Learn from the shared scenarios before delivering the optional backend. |
| What makes both file-authoritative/database-history and database-authoritative vault modes viable, and which database fits each? | [Research #1476][1476], then [refinement #1475][1475] | Resolve scope before committing database delivery; feed relevant consequences into the library and shared-interface work without an automatic blocker on `020` or libgit2. |
| Which replication failures justify refusing writes? | [#1293][1293], then the decision in [#1299][1299] | Observe before committing refusal semantics. |
| Does the Paper experience meet its original outcome, including the implications of the mobile report? | [Refinement #1469][1469], considering [#859][859] | Answer inside `033`, before closing the epic. |
| Which creation mechanism meets the agreed boundary? | [#1245][1245] | Decide before committing the scaffold and walkthrough. |
| Do attachment graph nodes require non-Markdown search admission? | Scope decision in [#1359][1359], with [#1234][1234] | Not knowing does not change the first cut or OKF foundation refinement; no speculative dependency is created. |
| Does the built-in backend stay within a documented memory and latency envelope on every supported install route, including SQLite builds without the WAL-reset fix? | Answered by [#1899][1899]'s measurements ([operating-envelope.md](operating-envelope.md)) and the runtime policy in [#1896][1896]; no number proposed on [#1506][1506] is adopted as a budget | The owner chose streamed SQLite as the default ([#1900][1900]). The release kind of `015` follows from its commits. |
| Does keeping a submodule current on pull need an operator switch, and what happens when the submodule tree is dirty? | [#1565][1565], under [refinement #1563][1563] | Decide before committing the read-only slice to a cut; the refusal in [#1564][1564] does not depend on it. |
| Which branch and credentials does a writable submodule use, given the detached HEAD `submodule update` leaves and a remote of its own? | [Refinement #1567][1567] | Not knowing does not change the read-only slice; do not shape the write path before it lands. |
| How does the docs migration interact with the `020 first-class-library` package's documentation work? | [Refinement #1666][1666] of [#1665][1665], with [#1436][1436] | Not knowing does not change the template-first order; decide at refinement whether library docs move with `020` or with the migration. |

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

### 23 September 2026 — chart submodule support as two epics

`stated` — The owner asked what it takes to support a vault where part of the
vault is a git submodule, chose read-only support first with writes as a later
epic, and added that the read-only slice must reject a write into a submodule
instead of accepting and losing it.

`evidenced` — The investigation reproduced every git command the server issues
against a superproject holding one submodule (git 2.55.0); the findings are the
[reference page](reference/git-submodules.md) and the issues under
[#1561][1561] and [#1562][1562].

`derived` — Neither epic joins a package yet; § Vaults with git submodules
argues the order.

### 28 September 2026 — chart reader-shaped documentation

`derived` — Added § Reader-shaped documentation after [#1272][1272]'s round-1 review.
The template epic precedes this repository's migration epic; neither joins a
package yet.

### 2 October 2026 — refine the documentation migration

`derived` — Template v11.1.0 shipped the structure, so [#1665][1665] is
refined: adoption, then one feature per section, then round 2 as the
measurement. The ordering argument above is restated accordingly; the
"Done when" is unchanged.

### 4 October 2026 — record round 2 of the documentation review

`derived` — Round 2 resolved § Reader-shaped documentation's unknown: the
migrated site does not yet meet the epic's "Done when". The owner ruled the
remaining blockers out of the migration's final pull request; they joined
`013 surfaced-fixes` as behaviour work, and the epic stays open on an unchanged
"Done when". Added `011` and `013` to § Packages, which did
not list them.

### 6 October 2026 — record `015` and the packages after 5.1.0

`stated` — The owner named `015` as the next package with the relevant and
high-priority bugs, approved the triage's additions to it, reopened `013` and
put [#1734][1734] first (§ Constraints and direction, § Packages).

`derived` — Added § Sustainable embedding storage and search, which [#1499][1499]
and the `015` milestone already pointed at but which had never landed. Listed
`015`, `025` and `060` under § Packages in ordinal order. Corrected § Vaults with
git submodules, which said neither epic had a package. Replaced the
vector-storage unknown, which [#1368][1368] and the `015` refinement answered,
with `015`'s open questions; removed the service-token and stale-read rows,
closed by [#1481][1481] and [#1482][1482]. Recorded [#1665][1665]'s closure.

### 7 October 2026 — package the MCP app work as `030`

`stated` — The owner asked for a package `030` collecting every open MCP app
issue (§ Usable vault views).

`derived` — Created `030 mcp-app` with [#1469][1469] and the six open app
issues, including three filed from the SonarCloud reliability review that day.
Listed it under § Packages and moved the Paper-acceptance unknown into it.

### 7 October 2026 — record the [#1508][1508] verdict

`derived` — The research closed the storage-contract unknown, so its row
left the open questions. § Sustainable embedding storage and search records
the verdict and the owner's choice on obsolete work. The order inside `015`
is unchanged: [#1503][1503] comes next, with its source-facing part settled.

### 25 October 2026 — refine [#1506][1506]

`derived` — Recorded the owner's budget choice and the order inside #1506 in
§ Sustainable embedding storage and search, and pointed the envelope question
at the work that now answers it. The order inside `015` is otherwise unchanged:
#1506 still comes last.

### 28 October 2026 — the #1506 figures are proposals

`stated` — The owner said the figures recorded on 25 October as agreed budgets
are proposals to validate. § Sustainable embedding storage and search and the
envelope question now say so, and point at #1899's measurements. Adopting
numbers, if any, moves to [#1900][1900] beside the default store. The order
inside `015` is unchanged.

### 28 October 2026 — the default store, and what sets the release kind

`stated` — The owner chose the SQLite store, searched streamed, as the
default, and said the release kind is knope's: what the package says is
informational, and the trunk already carries breaking commits. The release
kind was never #1900's to decide; #1900 records what its default change
means for it: the change asks nothing of an operator, so it adds no break,
and knope cuts the kind from the commits. § Packages and the envelope
question now say so. The order inside `015` is unchanged.

### 2 November 2026 — adopt the hosted terminal contribution as `016`

`stated` — The owner made epic #1829 package `016`, to be merged after `015`
was released, and asked for each pull request to be reviewed onto an
integration branch first. § Hosted terminal discovery and § Packages now
say so. `015` shipped as 6.0.0 the same day.

### 3 November 2026 — scope `020` to callers

`stated` — The owner scoped `020` to callers, deferred extenders to a later
package and deferred [#1811][1811] except where `020` has to touch that code anyway.
They also widened [#1473][1473] to how the package is organised, which takes in
[#2000][2000].

`derived` — [Epic #2011][2011] holds the extender outcome, with [#1994][1994]
under it; § First-class library and the known unknowns say so. The stale
"before `010`" prerequisites are removed because `010` has shipped.

### 3 November 2026 — record the [#1473][1473] verdict

`stated` — The owner accepted separate, fully lockstep distributions for library,
server and client, with documentation kept together, majors allowed and the
template to change. They also raised a pvl-core-level split with a premade
client, authentication and non-MCP API.

`derived` — § First-class library now carries the split, `020`'s kind moves from
provisional minor to major, and the resolved unknown is replaced by the template
and pvl-core question.

`derived` — Refinement [#1472][1472] filed [#2018][2018]–[#2022][2022], moved
[#1911][1911] and [#2000][2000] under [epic #1471][1471], and committed
[#2016][2016] to `020` as a prerequisite of the client distribution.

### 15 November 2026 — package the contributed pull requests as `021`–`023`

`stated` — The owner asked for packages from the triage of open contributed
PRs, in its order. They folded the test-only bucket into `021`, because a
test-only cut would release nothing. They placed the packages before `025`, and
left [#1606][1606] out because it is still deferred on [#1610][1610].

`derived` — Re-committed [#1911][1911] from the closed `020` milestone to `021`.
Moved [#1754][1754] from `030` to `021`. `022` carries [#1678][1678], where report
versus commit at startup is still the owner's call, and [#1938][1938], whose
"after 015" postponement has passed. Contributor-proposed features have no
package until their issues are accepted.

`evidenced` — The v6.1.1 cut, a fix outside any package, renamed `020` to
`v6.1.1 first-class-library` and closed it with its open items still attached
(Release run 42457174500's package step; milestone #13). The template defect is
[fastmcp-server-template#885](https://github.com/pvliesdonk/fastmcp-server-template/issues/885).

`stated` — The owner reopened it as `024`, above `023`, so that the next cut,
the `021` fixes, does not consume it again before that defect is fixed.

### 15 November 2026 — place the pull requests contributed after packaging

`evidenced` — `021` shipped as v6.1.2 (milestone #22, now titled
`v6.1.2 backlog-fixes`; commit 495afd1f).

`stated` — The owner committed the small owner-filed work contributed since
then to `022`, so that it ships in that cut rather than a docs-only one: [#1755][1755], [#1676][1676], [#1294][1294], [#1368][1368] and
[#1598][1598]. They also committed [#1403][1403] to `022`, because its
contributed PR proposes a deliberate repair and that choice is theirs.

`stated` — The owner accepts PR #2127 as a standalone refactor, reviewed on its
merits. The direction of [#1232][1232], several vaults behind one server, stays
open and unrefined.

`derived` — [#2121][2121] is a contributor-proposed feature, so it has no
package until it is accepted. [#1442][1442] waits on [#1610][1610], as
[#1606][1606] does.

### 16 November 2026 — package the contributed OKF evidence work as `024`

`stated` — The owner packaged three contributed features under [epic
#1425][1425] as `024 okf-evidence`, after `023`: [#1413][1413], [#1444][1444] and
[#1446][1446]. They kept the breaking marker on the [#1444][1444] fix, so the
package is a major. To fit it in, `020 first-class-library` (the `024` of 15
November) became `027` and `025 submodules-read` became `028`. Earlier
mentions of those ordinals describe the record at the time.

`derived` — [#1566][1566], which a contributed PR closes, was already in
`028` (then `025`) and stays there.

### 16 November 2026 — settle `022`'s open decisions

`stated` — The owner settled the three decisions `022` carried. [#1678][1678]
reports a dirty tree at startup and does not commit it. The contributed
first slice of [#1938][1938] is accepted as the start of its series. The
[#1403][1403] repair is a library method and an admin CLI command, not MCP
tools, because a one-off historical repair should not spend permanent tool
surface.

`evidenced` — All eight contributed pull requests merged on 16 November
2026, the last as 0bf06aa7.

`stated` — [#1938][1938] leaves `022` for a later package, because its
remaining three slices are not yet contributed. The `move_folder` half of
[#1676][1676] splits into [#2139][2139], so that [#1676][1676] closes with the
rename half.

`derived` — The later package is `023`, the next bug cut, where contributed
slices can land. With those moves `022` holds only closed issues and is ready
to cut. [#2139][2139] and the flaky-test report [#2138][2138] have no package
yet.

### 17 November 2026 — package the pull requests contributed after `022`

`stated` — The owner asked for packages from the triage of about fifty pull
requests contributed on 16 and 17 November. The owner committed the small
owner-filed work to `023`, the two breaking fixes to `024`, and the
owner-filed features to a new minor `025`. The owner parked the epic-scale
work.

`derived` — `023` received the issues whose PR resolves them in full, is
patch-safe and needs no other PR first: [#1729][1729], [#1955][1955],
[#1538][1538], [#1349][1349], [#1976][1976], [#1434][1434], [#2138][2138],
[#778][778], [#1853][1853], [#1751][1751], [#650][650], [#2139][2139],
[#2054][2054], [#1268][1268], [#1752][1752] and [#1131][1131]. Three stayed
out:

- [#1383][1383]: its PR rebuilds every index and leaves a link-precedence
  question open.
- [#2128][2128]: its PR is stacked on the `027` refactor for [#2018][2018].
- [#1310][1310]: it needs a third PR that links no issue.

Six issues whose PRs resolve only part of them stay unpackaged while those
PRs merge: [#1432][1432], [#1361][1361], [#1312][1312], [#1296][1296],
[#871][871] and [#752][752].

`stated` — For [#1631][1631] the owner chose the closed set of date forms, with
the breaking marker. For [#871][871] the owner chose to split the weight-map
items into [#2205][2205], which refuses rather than warns. [#871][871] stays
open for its remaining items.

`stated` — Under [epic #1313][1313], the foundation PRs for [#1307][1307] and
[#1308][1308] merge now. Neither issue closes, because each PR covers part of
it. The two libgit2 PRs for [#1309][1309] are parked until refinement
[#1468][1468] and a ruling on the backend's refusal set. Under [epic
#1425][1425], the new PRs are parked until the decisions they depend on:
[#1610][1610], [#1438][1438], [#1412][1412], [#1421][1421]'s prerequisite and
[#1677][1677]'s keying.

`evidenced` — The parked PR bodies cite slice plans and approvals that this
repository does not record (comments on [#1313][1313] and [#1425][1425], 17
November 2026). The roadmap treats those plans as input to refinements
[#1467][1467] and [#1468][1468], not as accepted designs.

`derived` — The second and third slices of [#1938][1938] arrived as PRs and
stay with [#1938][1938] in `023`. [#2124][2124] is a contributor-proposed
feature, so it has no package until it is accepted.

### 18 November 2026 — package the epic cuts by issue intent

`stated` — The owner corrected the 17 November packaging. A package holds the
issues a cut is meant to ship, solved together. An issue whose PR still waits
on a decision, is stacked or is oversized belongs in the package it is meant for:
the decision is work inside that package. This supersedes the 17 November
statement that the epic work was parked with no package.

`stated` — Every candidate cut of [#1425][1425] and [#1313][1313] became a
package, with OKF inventory first and both tracks' first cuts directly after
`025`. The existing `027`, `028` and `030` moved up to make room (§ Packages).
[#2128][2128] joins `023`.

`derived` — [#2018][2018] moves with it from `028` to `023`, because #2128's PR
is stacked on the #2018 refactor, which therefore ships in the same cut.

`stated` — The owner then held #2128's fix out of the patch, because it makes
every writing tool call wait up to 30 s for its own local commit. #2128 moves to
`027 git-contracts`. [#2018][2018] stays in `023`, where its PR already merged.

`stated` — The owner then reopened [#2018][2018] and returned it to `028`: its
PR delivered only the commit-scope part of the outcome. The remaining coupling,
through pvl-core's errors and environment helpers, is [#2016][2016] and
fastmcp-pvl-core#543.

`derived` — [#2224][2224], the subdirectory `force_pull` bug from #2211, is split
out of [#1395][1395] into `027 git-contracts`. [#1498][1498] stays under
[#1499][1499] with no package, as § Sustainable embedding storage and search
already argues, until its decision on reserved files and semantic search.

### 18 November 2026 — detach from the template and fold in pvl-core

`stated` — The owner decided to detach from the template, and to fold pvl-core in
and then dissolve it (§ Independence from the template and pvl-core). This supersedes the 28 September
ruling that domain documentation lives only where the template designates
(§ Reader-shaped documentation), and the 3 November statement that the template
is changed to support the distribution split (§ First-class library): "we would
not be depending on the template for this anymore".

`derived` — The template dependencies of [#1728][1728], of the distribution
split and of `020` are revised in place to point at the new section, and the
known-unknowns row on fastmcp-server-template#855 now resolves through the
re-refinement of [#1471][1471]. Epics [#2240][2240] and [#2241][2241] are filed
with their refinement issues only.

### 18 November 2026 — refine [#2240][2240]

`derived` — Six features cover the template epic, with one recorded shortfall
on pvl-core routing (§ Independence from the template and pvl-core). The epic body's planned
re-refinements moved to their own epics: [refinement #2251][2251] under
[#1471][1471], and [#1728][1728] now waits on [#2246][2246]. The trial detach
answered the known unknown on template-coupled tests, so its row is removed.

### 18 November 2026 — refine [#2241][2241]

`stated` — The owner chose direct dissolution over a vendored copy, and ruled that the `ConfigurationError` change is not breaking (§ Independence from the template and pvl-core).

`derived` — Ten features cover the core epic: the shim, eight areas and the dependency drop. The vendor-then-dissolve order in the epic body is superseded, and the epic's file counts are corrected. [#2016][2016] is resolved by [#2275][2275]. The known-unknowns rows on pvl-core types in the public interface and on vendored tests are removed: the owner's ruling settles the first, and the second no longer applies.

### 18 November 2026 — put the independence work first

`stated` — The owner placed the template part as package `010` and the pvl-core
part as `015`, ahead of every other open package, and paused stable releases
until they ship (§ Packages).

`derived` — `024` and later keep their relative order behind them.

### 20 November 2026 — place the pull requests contributed after `025`

`stated` — The owner asked, before work on `024` starts, for the open
contributed pull requests to be fitted into packages, and approved the
placements below. Contributor-filed issues are packaged too (§ Packages).

`derived` — Owner-filed issues under [epic #1425][1425] joined the cut their
candidate row describes:

- `024`: [#1414][1414], because the applicability assessment of
  [#1413][1413] needs its observations.
- `031`: [#1443][1443], fixed by the same PR as [#1412][1412].
- `034`: [#1394][1394], [#1611][1611] and [#1612][1612].
- `035`: [#1445][1445] and [#1420][1420].
- `036`: [#1416][1416] and [#1418][1418].
- `038`: [#1447][1447].

`derived` — Other owner-filed issues: [#1383][1383] joined `024` as a bug in
the current package, with its link-precedence question as work there.
[#1415][1415] joined `025`. [#1232][1232], [#1299][1299], [#871][871],
[#152][152] and [#1498][1498] stay in backlog, because their open PRs only
reference them or the 18 November hold on [#1498][1498] still applies.

`derived` — Contributor-filed issues: [#2045][2045] and [#2059][2059] joined
`024`. [#2056][2056], [#2124][2124], [#2311][2311] and [#2090][2090] joined
`025`. [#2057][2057] joined `031`, because it preserves a supplied
`generated.by`. [#2061][2061] and [#2062][2062] joined `035`, and
[#2058][2058] joined `038`. The six hosted terminal follow-ups joined `030`.

`evidenced` — [#1631][1631] had closed when [#2206][2206] merged, while its
fix [#2143][2143] was still open; it was reopened. The body of
[#2216][2216] listed other PRs as "Closes #N", which GitHub would have used to
close nine unrelated issues on merge; the account holder's agent reworded that
section, leaving only [#1422][1422].

### 20 November 2026 — cut the OKF packages in the order the pull requests are built

`evidenced` — The placements above put issues in earlier packages than work
their pull requests are built on. A pull request ships every commit its branch
contains, so the account holder's agent mapped, for each open contributed pull
request, which other pull requests' own commits its branch contains. At issue
level the OKF work formed one dependency cycle: each broad issue has slices at
different points of the chain. Nine branches contain pull requests their
"Stacks on" lines do not name, for example D3 [#2270][2270] contains F5 and C1.

`derived` — The slice letters in the branch names (A inventory to H
publication) name the outcome a slice serves, not when it was built. The
contributor built capability settings and gating before inventory, explicit
authoring (D1) before preserved provenance (B7), and write groups before review
binding (D3). Nearly everything later stands on D4, the review assessment.

`stated` — The owner asked what order the contributions expect and then
confirmed the re-cut that follows it, taking the undeclared edges as real
dependencies (20 November 2026): `024` starting points, `026` capabilities and
inventory, `031` provenance, `034` write groups, `035` observations, `036`
review binding, `037` publication, then `038` repair, `039` navigation, `040`
retrieval and `041` editorial history, all built on D4 (§ Packages). This
supersedes the 18 November order of the OKF packages.

`derived` — Existing milestones were renamed, keeping their identity; `034`,
`035` and `036` are new. Where a broad issue's slices fall in different cuts,
the early slice became its own issue: [#2378][2378] and [#2379][2379] in `024`,
[#2380][2380] in `026` and [#2381][2381] in `041`. [#1414][1414], [#2045][2045]
and [#1413][1413] leave `024`. Native blocked-by edges now mirror the pull
request graph between issues in different cuts. Two edges were left out:
[#1611][1611] and [#1612][1612] depend on each other inside `039`, and
[#1448][1448] would wait on [#1413][1413] only through a slice that follows its
own closing pull request.

`derived` — One placement differs from the confirmed list: E1 [#2211][2211]
stays in `027` rather than `034`. It fixes [#2224][2224], which is already in
`027`, and it needs nothing later than `027`.

`derived` — Publication needs only inventory and [#1241][1241] until its
review-assurance slice, so it could ship directly after `026`. It stays
after `036` as confirmed.

### 21 November 2026 — put OKF v0.3 conformance at `025`

`stated` — The owner ruled that the server matches the OKF specification
exactly: "Spec needs to match *exactly*, that is what it's a spec/standard
for", and "I consider deviation from the standard a bug". The owner placed the
conformance work at `025` "because it allows us to strictly verify any pr
afterwards", and chose to shift every later package up by one to make room.

`stated` — "Contributions are unasked for. They are convenient where they
exist, but we owe nothing more." The contributed v0.3 pull requests cite a
slice plan and a gap audit that this repository does not hold; they are input
where they fit an issue epic [#2401][2401] produces, judged against the
primary spec text.

`derived` — `025 okf-v03-conformance` holds epic [#2401][2401]: research
[#2403][2403] reads OKF v0.3 from its primary text, records the 0.2 to 0.3
differences and every departure, and supersedes #2388. The departures already
known from the OKF v0.2 reference page are filed as bugs blocked on it:
[#2404][2404] (bare-date `stale_after`), [#2405][2405] (`verified` cleared on
writes), [#2406][2406] (reserved files exempt from the `type` rule),
[#2407][2407] (actor-id grammar) and [#2400][2400] (this repository's own
reference bundle). Generated reserved-file frontmatter stays [#1438][1438] in
`032`.

`derived` — `025 contributed-features` became `026`, and `026` to `041` became
`027` to `042`. Each milestone keeps its identity and membership; three
descriptions that named stale ordinals now name the current ones.

### 22 November 2026 — put north-star governance at `020`, ahead of `025`

`stated` — "That means a new high-priority milestone first. 020 or something"
(owner, 22 November 2026), after the owner found 025's scoping drifting from
the OKF north star and `okf.md` §6.0 because they were not consulted, and ruled
that the fix belongs in the repository, not in any one agent's memory.

`derived` — `020 north-star-governance` holds epic [#2460][2460]. Its first
application, [#2459][2459], reopens the OKF north star; the `025` work that
depends on the outcome is blocked by it: [#2402][2402], [#2421][2421],
[#2423][2423] and [#2453][2453] to [#2455][2455]. #2457 and #2458, which
framed the problem as getting documents read, were closed as superseded.

`evidenced` — While aligning `generated` with `verified` for [#2386][2386], the
recommendation omitted that OKF 0.2 §5.2 requires only `by` within
`generated`. The owner rejected the resulting stricter rule. The open pull
request for #2386 now follows §5.2 and §5.1 exactly: `generated.at` is
optional, and each `sources` entry needs `resource`.

[809]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/809
[859]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/859
[1198]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1198
[1225]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1225
[1232]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1232
[1233]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1233
[1234]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1234
[1236]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1236
[1245]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1245
[1272]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1272
[1293]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1293
[1294]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1294
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
[1403]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1403
[1412]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1412
[1413]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1413
[1425]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1425
[1436]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1436
[1442]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1442
[1443]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1443
[1444]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1444
[1446]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1446
[1462]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1462
[1467]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1467
[1468]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1468
[1469]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1469
[1471]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1471
[1472]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1472
[1473]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1473
[1474]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1474
[1475]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1475
[1476]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1476
[1480]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1480
[1481]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/1481
[1482]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/1482
[1492]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1492
[1496]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1496
[1497]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1497
[1498]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1498
[1499]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1499
[1500]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1500
[1501]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1501
[1502]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1502
[1503]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1503
[1504]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1504
[1505]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1505
[1506]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1506
[1508]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1508
[1532]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1532
[1535]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1535
[1542]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1542
[1544]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1544
[1551]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1551
[1556]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1556
[1561]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1561
[1562]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1562
[1563]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1563
[1564]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1564
[1565]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1565
[1566]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1566
[1567]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1567
[1598]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1598
[1640]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1640
[1665]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1665
[1666]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1666
[1676]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1676
[1728]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1728
[1606]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1606
[1610]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1610
[1678]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1678
[1755]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1755
[1938]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1938
[2138]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2138
[2139]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2139
[1730]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1730
[1733]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1733
[1734]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1734
[1750]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1750
[1753]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1753
[1754]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1754
[1811]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1811
[1829]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1829
[1895]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1895
[1896]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1896
[1899]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1899
[1900]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1900
[1911]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1911
[1990]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/1990
[1994]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1994
[2000]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2000
[2011]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2011
[2015]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2015
[2016]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2016
[2018]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2018
[2019]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2019
[2021]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2021
[2022]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2022
[2121]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2121
[creation-decision]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1245#issuecomment-5475514917
[template-adoption]: https://github.com/pvliesdonk/markdown-vault-mcp/commit/2c7d46e56e16a958a9d085a65ff582b8de885a31

[650]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/650
[752]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/752
[778]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/778
[871]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/871
[875]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/875
[1131]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1131
[1241]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1241
[1268]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1268
[1296]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1296
[1312]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1312
[1349]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1349
[1361]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1361
[1405]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1405
[1421]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1421
[1432]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1432
[1434]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1434
[1438]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1438
[1538]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1538
[1605]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1605
[1631]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1631
[1677]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1677
[1729]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1729
[1751]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1751
[1752]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1752
[1853]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1853
[1923]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1923
[1955]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1955
[1976]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1976
[2031]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2031
[2054]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2054
[2124]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2124
[2205]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2205
[1395]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1395
[1411]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1411
[1419]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1419
[1422]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1422
[1448]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1448
[1569]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1569
[2224]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2224
[2235]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2235
[2240]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2240
[2241]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2241
[2242]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2242
[2243]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2243
[2245]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2245
[2246]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2246
[2247]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2247
[2248]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2248
[2249]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2249
[2250]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2250
[2251]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2251
[2273]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2273
[2274]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2274
[2275]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2275
[2276]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2276
[2277]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2277
[2278]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2278
[2279]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2279
[2280]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2280
[2281]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2281
[2282]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2282
[2283]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2283
[152]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/152
[1394]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1394
[1414]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1414
[1415]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1415
[1416]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1416
[1418]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1418
[1420]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1420
[1445]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1445
[1447]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1447
[1611]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1611
[1612]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1612
[2045]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2045
[2056]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2056
[2057]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2057
[2058]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2058
[2059]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2059
[2061]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2061
[2062]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2062
[2090]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2090
[2143]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/2143
[2206]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/2206
[2216]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/2216
[2311]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2311
[1310]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/1310
[2128]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2128
[2378]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2378
[2379]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2379
[2380]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2380
[2381]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2381
[2270]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/2270
[2211]: https://github.com/pvliesdonk/markdown-vault-mcp/pull/2211
[2386]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2386
[2400]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2400
[2401]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2401
[2403]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2403
[2404]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2404
[2405]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2405
[2406]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2406
[2407]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2407
[2402]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2402
[2421]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2421
[2423]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2423
[2453]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2453
[2454]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2454
[2455]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2455
[2459]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2459
[2460]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2460
[2461]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2461
[2462]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2462
[2463]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2463
[2464]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2464
[2465]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2465
[2466]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2466
[2467]: https://github.com/pvliesdonk/markdown-vault-mcp/issues/2467
