# OKF administration under more than one author

**Status:** revised design proposal, 2026-09-09. No implementation in this
PR. The owner's 2026-09-08 choices are retained below; the simplifications
in §6 are proposals from this re-review, not previously approved decisions.
Implementation is tracked by epic #1425. Its child issues contain details
from the earlier draft and need reconciliation before implementation (§10).

**Scope:** who administers note frontmatter, `index.md`, and `log.md` when
this server shares a vault with Obsidian, another server, or a human editor.
Git transports changes; a filesystem watcher is another way to observe them.

**Reads with:** [`okf.md`](okf.md) for the implemented OKF layers and
[`reference/okf-v0.2.md`](reference/okf-v0.2.md) for the external format.
This page proposes the replacement maintenance model. It does not change
the running server or supersede the implemented contract by itself.

## 1. The problem

The owner's statement of it (2026-09-08), quoted:

> if I create a new file outside (eg in obsidian, synced via git,
> 'imported' via pull) it may not have OKF Headers. because usually that
> file is human made. if I change a file manually then that likely means
> the front matter was not updated properly.
>
> so either the server must take complete ownership of that data, or it
> must back off and trust someone else to do it properly. likely an
> operator choice. we already discussed this in issues for index.md and
> log.MD but it also holds for front matter

The original analysis recorded four situations against the PR base
(`cec05c84`, 2026-09-08), with `OKF_WRITE=true` on a git-synced vault.
This is the motivating snapshot, not a claim about later fixes on `main`.

| # | What happened outside the server | What the vault says afterwards | Who notices |
|---|---|---|---|
| A | A note is created in Obsidian and pulled | No frontmatter at all: no `type`, no `generated`. The bundle is non-conformant (spec §11 rule 1 and 2). | `okf_validate` (`missing_type`), `stats.okf.untyped_count`. Nothing else. |
| B | A note the server wrote is edited in Obsidian and pulled | `generated: {by: human:alice, at: <last server write>}` describes bytes that no longer exist. `verified: [...]` still attests them. `read` reports the note as `human-reviewed`. | Nobody. The read layer trusts the frontmatter. |
| C | A note is created by a second maintainer with its own `generated` | Correct, as long as that maintainer followed the actor convention. | Not a problem; listed for completeness. |
| D | A folder's `index.md` / `log.md` is maintained on both sides | Rebase conflicts on the same insertion point; sibling files; `conflict_with` injected into a root `index.md` (#1395); listings stale after any non-server change (#1392). | The operator, eventually, from the mess. |

The server's enforced write layer (design `okf.md` §6) keeps the
invariants only on bytes it wrote itself. Everything above is the same
invariant broken by a write path the server does not see until the
bytes are already on disk.

## 2. What can be known

The distinction is between data the server can reconstruct and claims only
an author or verifier can make. The OKF reference records the underlying
rules: optional provenance may be absent, trust is advisory, generation and
verification are independent, and log entries are prose grouped by date.
These rules permit serving an incomplete note; they do not permit inventing
its provenance. A missing `type` remains a conformance/triage finding.

| Surface | Evidence available | Ownership policy |
|---|---|---|
| Semantic fields (`type`, title, description, sources, tags, status) | The author's meaning; not recoverable from a commit identity | Preserve, report gaps; an explicitly configured default type is the limited exception (§8) |
| `generated` | Existing producer claim and evidence of subsequent content changes | Stamp own writes; flag demonstrated staleness; at `own`, remove a demonstrated stale claim without inventing a replacement actor |
| `verified` | Each verifier's claim and evidence of subsequent content changes | Evaluate independently of `generated`; never create verification from a git author |
| `index.md` | Current visible note tree and metadata | Regenerate when server-managed |
| `log.md` | An author's explanation of a knowledge change | Preserve and curate; byte history alone cannot reconstruct the prose |

A git author identifies a commit author, not whether the content was human
written, generated, or verified. A timestamp orders neither those acts nor
concurrent edits reliably enough to establish provenance by itself. An
external writer can maintain one frontmatter family without maintaining the
other. The read and repair paths need evidence about each independently.

## 3. Decisions retained from the owner's discussion

- Ownership is an operator choice, with a useful middle ground: maintain
  the reserved files while leaving externally authored notes untouched.
- One `OKF_WRITE` ladder expresses that choice (§4). The owner explicitly
  allowed a cleaner surface to use the planned 5.0 breaking-change window.
- `log.md` records knowledge events. As the owner put it, “From my
  perspective log.md and git are semantically different.” A session of
  small edits should not produce a bullet for every write.
- Agents need a way to declare intent after a coherent piece of work.
  The owner rejected requiring an explanation on every small write.
- Server-managed reserved files remain readable but are protected from
  ordinary MCP mutations (§7).

The first commit usefully separated the surfaces, but its proposed
`human:<git-author>` provenance and git-rendered log do not survive these
constraints. Restoring that commit wholesale would restore those problems.

## 4. The ownership model

### 4.1 One ladder

| `OKF_WRITE` | Stamp own writes and enable verification | Maintain reserved files | Repair external provenance |
|---|---|---|---|
| `off` | no | no | no |
| `stamp` | yes | no | no |
| `maintain` | yes | yes | no |
| `own` | yes | yes | yes |

The default remains `off`. Existing `true` and `false` values map to
`maintain` and `off`. OKF activation and read-only policy still apply:
an inactive bundle receives no OKF writes, and read-only operation permits
no maintenance or reconciliation writes at any rung.

At every rung, an active OKF consumer should use available evidence when
reporting trust. Annotation does not authorize modifying a note. With no
usable history or prior observation, preserve the supplied claims and report
the limit of the evidence; absence of evidence is not proof of staleness.

The aliases ease migration but do not make the whole change compatible.
Ordinary writes to reserved files currently succeed with `OKF_WRITE=true`;
protecting them at `maintain` changes that behavior. The ladder and protection
must be assessed and shipped together under the repository's breaking-change
policy, against the last stable release. This documentation PR itself
changes no operator or library surface.

### 4.2 Defaults and independent capabilities

Automatic model calls require an explicit opt-in separate from configuring
a summarizer for an explicitly invoked tool. Log summaries and git subjects
are separate uses and need separate opt-ins. Model failure leaves content
writes, commits, pulls, and existing log prose intact.

A default type is another optional value, applied only at `own` (§8).
Conventions-file protection is independent of OKF activation (§7).
Authentication supplies the actor for actions that actually happened through
an authenticated request; it does not identify the author of imported bytes.

Tool availability and instructions follow configuration. The intent flow
uses explicit targets and does not depend on a connection, a conversation,
or a client answering an elicitation. Exact argument schemas and result
budgets belong to the tool implementation, including the existing
`tests/test_client_surface_budget.py` constraints.

## 5. Observe changes once; maintain each surface independently

The shared event is **a changed vault state**, with enough evidence to tell
which notes were added, changed, removed, or moved. It is not “a foreign
commit”: even another server's correctly stamped write can require a local
index projection to be refreshed.

The index/reindex boundary already observes paths and hashes. Expose that
observation once and let projection maintenance, provenance assessment, and
external-maintainer warnings consume it. A full build needs an explicit
full-refresh signal, distinct from an incremental result with no changes.

The earlier implementation attempts established constraints worth retaining:

- A startup pull can be absorbed by the full build. Do not require a later
  incremental delta to notice it. Exercise warm restart with `index_path`
  configured as well as the default in-memory index.
- Removed/excluded paths and both sides of a move matter to the affected
  folders, including a folder that becomes empty.
- Normalize repository-relative paths to the vault root, including when
  the vault is a subdirectory of the repository.
- Maintenance queues ordinary writes after pull/rebase coordination releases
  its locks. Do not acquire the pull lock from a paused write callback.
- Preserve existing commit-scope and principal handling. Do not add an
  unowned daemon or assume a closed tool scope can be joined later.

### 5.1 Provenance evidence

Compare the affected content and provenance against the relevant prior
version. A pure rename is not a content change. A provenance-only update is
not a new body change. Neither the commit timestamp nor “touched this path”
is sufficient to invalidate a verification.

A server operation trailer can describe origin and work performed, but is
not proof that every surface is current. In particular:

- A `stamp` writer does not maintain reserved files; an `off` writer does
  not stamp. The receiving instance applies its own allowed maintenance.
- No trailer, an unknown trailer, or matching legacy committer identity
  alone means **unknown capability**. Legacy server commits may have been
  made with `OKF_WRITE=false`; do not assume `maintain`.
- Server-origin metadata is bookkeeping, not authenticated verification.
  Use the actual affected content and provenance when deciding whether a
  claim still describes that version. Do not exempt arbitrary later edits
  because an earlier commit was made by the server.

At `own`, remove a family only when the evidence shows it was carried
unchanged across a relevant content change and no subsequent refresh of that
claim applies to the current content. Assess `generated` and each `verified`
entry separately: adding a new verification does not refresh older entries.
Preserve an external producer's updated claims under the same consumer rules
used for other supplied frontmatter; this is not independent certification
that those claims are true. Never derive a `human:` actor from git identity.

The implementation must walk enough of an ingested range to distinguish
“edit, then re-verify” from “re-verify, then edit.” Comparing only the range's
endpoints loses this ordering. Merge histories and unavailable history need
an explicit unknown-evidence outcome, rather than an invented answer.
Read annotations and disk repair consume the same assessment; search should
use cached assessment computed at ingest, not spawn git once per hit.

### 5.2 Writes and idempotence

Projection maintenance derives the desired bytes from current state and
writes only when they differ. Reconciliation removes only stale claims still
present in the inspected version. Re-check that version before writing so
an edit arriving during assessment is not overwritten.

Batch maintenance through the existing write machinery where possible;
external maintenance uses the normal deferred push. A repeat observation of
the same state produces no new changes. No empty activation commit or
accounting boundary is required to regenerate an index or remove a stale
claim. An index refresh does not acknowledge any log work.

### 5.3 Without git

Prior indexed bytes/frontmatter can establish a change only if they still
represent the observation before that change. Capture the evidence before
replacing the row. On a cold build with no prior state there is no comparison:
do not remove supplied claims merely because their source is unknown.

Distinguish the server's own recently written bytes by evidence recorded on
the write path; a watcher/tracker race must not invalidate a stamp the server
just created. Apply the same independent-family assessment as with git.
Do not substitute filesystem mtime for an author or verification event.

## 6. `log.md`: keep curation separate from bookkeeping

### 6.1 What the log promises

The log is a readable, selective history of knowledge changes. It preserves
human entries and accepts an author's description after several small writes.
The proposed server-managed grain remains one entry per note per day, with
an explicit target day when revising an earlier entry. Existing human prose
need not follow that grain and is not rewritten to enforce it.

A missing log entry does not establish unfinished work: the edit may have
changed no knowledge. A changed word sequence does not establish a knowledge
change either; a typo is the simplest counterexample. Markdown stripping is
also not a safe semantic filter: changing a link destination can matter while
leaving its visible words unchanged.

### 6.2 Proposed simplification

Keep an explicit intent operation for creating or revising a curated entry.
The request identifies the note and day and supplies the explanation. Its
completion means the entry was durably written through the ordinary write
path. An ambiguous target or a concurrent change returns a conflict instead
of silently choosing a day or overwriting someone else's entry. Exact key
encoding and retry semantics are implementation work for #1416 and #1417.

With no declared intent, leave the log unchanged by default. This deliberately
replaces the previous placeholder default. A log can be selective without
pretending to exhaustively describe every observed change. There is no need
for invisible negative entries to prove that an omitted entry was considered.

Keep guidance to declare intent after coherent work. If result reminders are
added, describe them as bounded advisory hints about observed edits, not a
complete, durable, cross-replica queue. Ignoring or losing a reminder does not
trigger a fabricated fallback entry. Do not claim that authentication can
assign externally authored changes to a principal without evidence.

Automatic diff summaries remain a separately opted-in extension (#1418).
Before implementing background summarization, specify its work storage,
retry/cancellation behavior, revision preconditions, and activation/backfill
policy. A model's “no knowledge change” is legitimate; errors create no log
entry. This optional job system must not become a prerequisite for index
refresh, provenance repair, or explicit intent. The current proposal does
not claim durable delivery for an unspecified background queue.

### 6.3 Conflict policy

Retain the existing sibling policy for conflicting `log.md` edits. Preserve
both versions for resolution; do not regenerate curated prose from git or
replace it with upstream plus guessed server entries. A keyed line is still
editable by a human. Its shape, or a removed-and-readded key, cannot establish
that its prose is safe to discard.

Ordinary conflict-free merges remain ordinary git merges. If automatic log
merging is revisited, it needs its own explicit ownership model and lossless
merge design. It is not required by this ownership proposal. Multiple writers
may leave a conflict for a human; this proposal makes no claim of automatic
log convergence under concurrent curation.

The explicit curation contract also works without git: no commit SHA is
required to explain a note's change. Historical git rendering remains the
separate, explicitly invoked seed operation; it is not the maintenance model.

### 6.4 Machinery removed from the previous draft

The previous draft used a global accounting commit as a progress boundary,
then added placeholder scans to recover work that fell behind that boundary.
An index refresh could still advance it before a delayed placeholder existed.
The patch-shape conflict classifier similarly grew exceptions without proving
who edited a keyed line. These were defects in the mechanisms, not missing
sentences in their summaries.

This proposal removes automatic placeholders, negative-disposition comments,
activation commits, the global log-accounting boundary, and patch-shape
ownership inference. It also removes the requirement for a second commit on
every logical write merely to put the first commit's SHA into its log entry.
An explicit intent declared later can naturally make a later commit; that is
different from requiring every note write to do so.

## 7. `index.md` and protected paths

At `maintain` and `own`, regenerate the managed index projection after relevant
note changes, including external changes and full refresh. Limit regeneration
to the applicable visible markdown scopes; an attachment-only change must not
create a listing in a folder that never needed one. Preserve bundle-root
metadata according to the existing reserved-file contract.

For conflicts on a managed index projection, regenerate from the resolved
note tree under the index-specific design (#1395). This permission does not
extend to `log.md` or arbitrary user prose.

Ordinary mutation tools at `maintain` and `own` refuse changes to reserved
paths with an explanation naming the setting. Cover writes, edits, deletions,
rename sources and destinations, and indirect updates. Reads remain available.
At `off` and `stamp`, OKF ownership supplies no such protection. Read-only
vault restrictions still apply independently.

Authorized index generation and log curation need a deliberate internal path
through the protection mechanism. A general “suppressed maintenance” flag is
not by itself permission to overwrite every protected file. The conventions
file (#1411) shares the guard mechanism but has its own protection policy,
independent of whether OKF is active.

Instructions must describe the configured behavior, including the intent
operation where direct log writes are refused. Apply tool-registration and
client-surface-budget checks when implementing these changes.

## 8. Untyped notes

Report missing `type` and make the missing-value set queryable (#1421). A
normal equality filter over `document_tags` cannot express absence; the query
contract needs an explicit absence operation rather than a magic type value.

At `own`, an explicitly configured default type may be applied to a note
without one (#1422). Add `status: draft` only when status is absent; preserve
an explicit status. A default classification is a triage convenience, not
evidence of authorship or verification. Leave other semantic fields alone.

Offer Obsidian template guidance as a way for the author to supply metadata.
The reference distinguishes documented template/property behavior from the
unverified observation that every fresh note lacks frontmatter. Do not promise
that installing the server repairs the originating editor's workflow.

## 9. Related feature: generated git subjects

#1405 is independent of OKF ownership. A useful commit subject describes the
edit at commit granularity; it is not automatically suitable as a knowledge
log entry. Keep separate prompts and opt-ins even if the features share a
text-generation backend.

Use a bounded model-call budget and a deterministic subject fallback. The
commit must succeed without a model response. Validate the generated subject
as a single line, cap its length, and keep machine bookkeeping outside model
control. Backend configuration and prices do not establish that the automatic
feature is free or already authorized.

The concrete backend interface, configuration aliases, and scheduler changes
belong to that feature's design; none is required to resolve ownership here.

## 10. Implementation boundaries and acceptance examples

This is a design revision, not authorization to implement the whole epic in
one PR. Preserve the narrow 4.2 fixes and reconcile the epic's issue bodies
with the chosen design before treating them as implementation instructions.

| Work | Tracking | Boundary |
|---|---|---|
| Ownership ladder and protected paths | #1412, #1419; conventions #1411 | Coordinate the operator behavior change; preserve independent read-only policy |
| Ingest evidence and annotations | #1414, #1413, #1415 | Define evidence and unknown cases; trailers do not certify all maintenance |
| Index refresh and index conflict handling | #1392, #1395 | Independent of log curation and model availability |
| Explicit curated log and intent | #1416, #1417 | Revise placeholder, boundary, nag, and conflict claims to match §6 |
| Optional background summaries | #1418 | Separate work-delivery design before implementation |
| External-note repair and triage | #1420, #1421, #1422 | Repair only at `own`; keep semantic defaults explicit |
| External-maintainer warning | #1394 | Consumer of the same observed changes |
| Existing append repair and audit fixes | #1391, #1403, #1396 | Keep their existing scope; no dependency on the new log model |
| Commit-count documentation | #1423 | Verify against implemented batching, not the removed two-commit proposal |
| External reference | #1424 | Reference page included in this PR |
| Generated commit subjects | #1405 | Independent git feature |

Do not close #1393 solely because an earlier draft called it superseded.
Its ownership question is answered by the adopted design and implementation,
not by the presence of a longer issue list.

Acceptance examples for the implementation reviews:

| Scenario | Required result |
|---|---|
| Obsidian changes a body but carries old provenance unchanged | Evidence-based annotation; disk repair only at `own` |
| External author refreshes `generated` but leaves old verification | Assess the two independently |
| External author adds a new verifier while keeping an old entry | The new entry does not renew the old one |
| Re-verification before versus after a content edit in one pulled range | Preserve operation ordering; do not infer it from endpoint equality |
| Pure rename, or provenance-only edit | No body-change invalidation |
| Legacy server commit with OKF disabled | No assumed stamp or maintenance based on identity |
| Cold non-git build with supplied provenance | Unknown prior state does not authorize deletion |
| `stamp` server pushes to a `maintain` server | Receiver refreshes its projection without rewriting external notes |
| Index refresh followed by restart before an intent declaration | Index is current; no promise of a durable log task is silently violated |
| Human corrects keyed log prose on one side of a conflict | Preserve both versions with the sibling policy |
| No model, failed model, or no intent | Content and sync continue; no invented knowledge event |
| Delayed intent for a previous day | Explicit target; no accidental replacement of today's entry |
| External edit arrives during repair or curation | Detect changed version instead of overwriting it |
| Read-only vault at any configured rung | No maintenance writes |

## 11. External evidence

Use the dated references, including their stated limits:

- [OKF v0.2](reference/okf-v0.2.md): provenance, verification, reserved
  files, and log structure. This server's invalidation policy is a consumer
  choice; do not attribute it to an unstated OKF requirement.
- [obsidian-git](reference/obsidian-git.md): configured commit identity,
  opaque default subjects, desktop/mobile sync behavior, and Obsidian
  properties/templates. Unverified observations remain unverified.
- [Git staging and commits](reference/git-staging-and-commits.md) and
  [history queries](reference/git-history-queries.md): commit identity,
  replay, revision and rename behavior relevant to implementation.

A change to read-side derivation must record the adopted departure in the OKF
reference when it lands. New external premises require a current reference;
this proposal intentionally makes no dependency on a particular future MCP
revision, FastMCP session API, model vendor, or price.
