# OKF administration under more than one author

**Status:** design, approved by the owner on 2026-09-08 after three
rounds of discussion; the decisions recorded as the owner's are quoted.
Nothing here is implemented. Implementation is tracked by the epic
linked from §10.1.
It exists to inform the clean re-implementation of #1391, #1392, #1395,
#1396 and #1403 after `main` was rewound on 2026-09-08, and to give #1393
and #1394 a design to argue against.
**Scope:** the OKF frontmatter families on notes, and the reserved files
`index.md` / `log.md`, in a vault that is written both by this server and
by something else (Obsidian on a laptop, a phone, a second server, a
human with an editor), with git as the transport.
**Reads with:** `okf.md` (the design), `reference/okf-v0.2.md` (what the
spec says), the 2026-09-07 issue set linked above.

---

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

Four situations make it concrete. All four are reachable today with
`OKF_WRITE=true` on a git-synced vault; none is handled.

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

## 2. What the spec permits a consumer to do

The relevant rules from `reference/okf-v0.2.md`, because they bound the
design space:

- Conformance is deliberately permissive. A consumer "must not reject a
  concept for missing any optional family"; a concept with no trust
  frontmatter at all "is still consumable". Situation A is therefore a
  *bundle* quality problem, not a *serving* problem.
- Trust tiers are "derived, never stored". A consumer computes the tier
  from `verified` at read time. Nothing in the spec forbids a consumer
  from applying further evidence when it derives.
- `generated.at` is "the content's last meaningful change". `generated`
  and `verified` "are independent: content can change without
  re-confirmation". This server already departs from that by clearing
  `verified` on its own content writes (recorded departure in
  `reference/okf-v0.2.md`). Situation B is that departure applied
  inconsistently: enforced on one write path, ignored on the other.
- `index.md`: "Producers may generate index files; consumers may
  synthesise one when none is present." A listing is regenerable by
  definition.
- `log.md`: "In a git-hosted bundle `log.md` largely duplicates the commit
  log; it earns its keep when bundles travel without version-control
  history."
- From the OKF skill's guidance rather than the spec: "Fabricated
  provenance is worse than absent provenance, because absence is itself a
  legible signal." This rules out several tempting fixes below.

## 3. The data, family by family

The owner's framing is "take ownership or back off". The right answer
differs per family, because what the server can *know* differs per
family. That is the whole analysis in one table; the rest of the
document argues the rows.

| Family | Who can know it | Derivable from git? | Server today | What "ownership" could mean | What "back off" means |
|---|---|---|---|---|---|
| `type`, `title`, `description`, `tags`, `sources`, `status`, `stale_after` | The author. Semantic. | No | Gate (`required_frontmatter`), report (`okf_validate`), advise (instructions) | Only a pre-authorised default (`type: Capture`) paired with a triage signal | Report the gap; leave the note alone |
| `generated {by, at}` | Whoever produced the bytes | **Yes**: commit author and committer timestamp of the last content commit | Stamped on own writes | Re-derive from git on ingest of an external change | Annotate at read that the stamp predates the bytes; never write |
| `verified [{by, at}]` | The verifier. Only *invalidation* is derivable | Invalidation: yes (content commit after `at`) | Cleared on own writes | Clear on ingest of an external content change | Treat entries older than the last content change as void at read time |
| `index.md` | Nobody; it is a function of the tree | Fully (it is a projection) | Regenerated after own writes only (#1392) | Regenerate on every ingest; resolve conflicts by regenerating | Never write it, even after own writes |
| `log.md` | The author of the change; a knowledge event, not a byte event (§6) | Only the *fact* of a change and its diff; the description is curated | Appended after own writes only | Curate one keyed entry per note per day from intent, diff, or file list; conflicts by take-upstream-then-reinsert | Never write it |

Two rows carry most of the weight: `generated`/`verified`, where git is
strong evidence and the server has been ignoring it, and `log.md`, which
is history and must stay history: git supplies the *when* and the
*what bytes*, never the entry.

## 4. The ownership model

### 4.1 Three postures, not two

"Own" versus "back off" hides a third posture that costs nothing and is
honest under any mix of authors:

1. **Annotate.** Never write, but *read* with all the evidence
   available. The server's own `okf` annotations on `read`, `search` and
   `get_context` stop trusting a `verified` entry whose `at` predates the
   note's last content change, and flag a `generated` stamp in the same
   state. On disk the frontmatter still lies for other consumers; in this
   server's answers it does not.
2. **Own.** On every ingest of an external change, rewrite what git can
   prove and regenerate what is a projection. Produces commits into the
   human's clone.
3. **Defer.** Another maintainer administers the bundle. The server
   stamps nothing it did not write, maintains no reserved file, and
   warns once when it sees the other maintainer's work arrive (#1394).

Posture 1 is not a mode. It is what the read layer should do regardless,
because it is the consumer-side derivation the spec already asks for,
extended with evidence the server has. Postures 2 and 3 are the
operator's choice, and they are per-surface: an operator can own the
projections (reserved files) and still refuse to rewrite anybody's note.

### 4.2 The config surface

`OKF_WRITE` shipped in 4.1.0 (`git tag --contains 65083123` names
`v4.1.0`) bundling three things: provenance stamps on own writes,
`okf_verify`, and reserved-file upkeep after own writes. #1393 asks to
split it. The first draft of this document argued the split had to be
additive because narrowing a shipped flag is a `!`. The owner
(2026-09-08): "We are heading for a breaking change in 5.0.0 anyway. So
if it truly makes sense we have room here." So the question is what the
*clean* surface is, and only then whether it needs the room.

The three behaviours form a ladder, each level a superset of the one
below, and no combination outside the ladder is sensible (reconciling
other people's notes without stamping one's own; maintaining the
reserved files without stamping):

| `OKF_WRITE` | Stamps own writes, `okf_verify` | Maintains reserved files | Reconciles external notes |
|---|---|---|---|
| `off` | | | |
| `stamp` | yes | | |
| `maintain` | yes | yes | |
| `own` | yes | yes | yes |

One setting, monotone, and the posture is legible from its value. The
shipped `true` is `maintain`; accepting `true`/`false` as aliases for
`maintain`/`off` makes the change additive after all, so the 5.0 room
is not needed for this and can be spent elsewhere. If the owner prefers
the enum without the boolean aliases, 5.0 is when to drop them.

"Maintains reserved files" means the new behaviour, regenerate on every
ingest rather than after own writes only, without a `!` either way:
#1392 is filed and milestoned as a **bug** in 4.2, so regenerating after
a pull is the intended behaviour of the shipped flag.

`own` writes into files the operator did not ask the server to touch,
and pushes those writes to every clone. That is the kind of surprise the
owner's "operator choice" is about, and it is why it is the top rung
rather than a default.

Two settings this ladder does *not* fold in: which party maintains the
reserved files when the server does not (there is no setting; "not the
server" is all the server needs to know), and the untyped-note default
of §8, which is a value, not a posture.

Two consequences for existing surfaces:

- `_instructions.py` tells every agent, on any OKF-active vault, "For
  edits, update 'log.md'/'index.md'". At `maintain` or above that
  instruction is wrong (the server does it, and will overwrite the
  agent's version); at `stamp` it is wrong too if someone else
  maintains them. The snippet has to become posture-dependent, and the
  client-surface budget (23,149 of 23,500 characters as of the last
  measurement) means the replacement cannot be longer than what it
  replaces.
- An agent `write` whose target is a reserved file is accepted today
  and simply not maintained. At `maintain` or above it should be
  accepted and then overwritten by the next regeneration; the owner's
  position (2026-09-07, from #1393) is "If we choose 'server managed'
  then clobbering is fine and maybe even preferred." That should be
  documented as the contract rather than left to be discovered.

### 4.3 What the 2026-07-28 protocol revision allows

The server's next major runs on FastMCP 4 and the `2026-07-28` MCP
revision (#1271), whose core is stateless. Read on 2026-09-08 from the
revision's changelog and its Streamable HTTP and MRTR pages; the facts
that bound this design:

- **There is no protocol session.** `Mcp-Session-Id` is removed;
  "servers that need cross-call state use explicit, server-minted handles
  passed as ordinary tool arguments". FastMCP 4 offers `UserSession`
  (one bucket keyed by the authenticated principal, requires auth) and
  `SessionId` (a handle the model passes on every call). Neither is used
  here: nothing below keys on a conversation.
- **Server-to-client requests are multi round-trip.** Elicitation is
  returned as an `input_required` result the client answers on a retry;
  "servers MUST NOT assume that clients will fulfill the inputRequests or
  retry". `okf_verify` keeps working through FastMCP 4's input-required
  rounds. Nothing below may *depend* on a retry.
- **Sampling is deprecated**, with "integrate directly with LLM provider
  APIs" as the suggested migration. The summarizer seam is that
  integration; "use the client's own model" is not an option.
- **Tool lists are static and cacheable** (`ttlMs`, deterministic
  order, no per-connection variation). A tool gated by configuration is
  fine; a tool that appears per conversation is not.

## 5. The ingest hook

Every posture-2 action, and the #1394 warning, reacts to one event: *a
set of paths changed, and the server did not write them*. The server has
no such event today. It has:

- a pull path (`on_pull → reindex`) that knows `from_sha..to_sha`;
- a file-watcher path that knows nothing but "something under this root
  changed";
- the incremental reindex, which computes per-path hash deltas and is the
  only place that knows *which* paths changed on either route.

The reindex is therefore where the event should be raised, and PR #1400
(still open on `fix/1392-index-regeneration-triggers`; the rewind removed
only the merged #1397 and #1398) found the traps by walking into each of
them. The owner recorded them on #1392 and
#1395 on 2026-09-08 as "facts established while implementing"; they are
listed here so the re-implementation designs around them instead of
rediscovering them:

- The startup pull runs before the boot build, so the build absorbs it and
  the boot reindex reports no delta. A full build must therefore name *no*
  delta and refresh everything, or the cold-start pull is invisible.
- The warm-restart path only exists with `index_path` set; the default
  in-memory index makes every construction a full build and hides the
  incremental path from tests.
- `_purge_stale_excluded` removes rows without reporting their paths, so
  a folder emptied by an `exclude_patterns` change has no delta to
  regenerate from.
- The write callback must never take the pull lock (`_quiesce_writes`
  holds `pause_writes` across the rebase); regeneration writes have to be
  queued behind the pull, not issued inside it.
- Plain `threading.Thread` starts with an empty context, so the commit
  scope and the principal do not reach a follow-up thread unless the
  context is copied; and a scope closed at job promotion cannot be joined
  afterwards.
- On the `force_pull` path the working tree handed to the git layer is
  the vault, not the repository toplevel, while `git diff --name-only`
  reports toplevel-relative paths.

One design consequence #1400 reached after eleven rounds and the owner
then rejected as "accreted rather than designed": regeneration triggers
and thread-lifecycle concerns are two changes. The event should be
raised by the reindex and consumed by a listener that queues ordinary
writes; the listener's threading is the reindex's existing threading, not
a new daemon.

### 5.1 Telling the server's own commits from external ones

Posture 2 must not react to the server's own writes (they were stamped
at write time) and must be idempotent (a second ingest of the same state
changes no bytes, or the vault ping-pongs commits with itself).

The discriminator is the **committer** identity. `_commit_staged` sets
the committer from the static `GIT_COMMIT_NAME` / `GIT_COMMIT_EMAIL`
configuration (default `markdown-vault-mcp` / `noreply@markdown-vault-mcp`)
and overrides only the *author* from OIDC claims. So a commit whose
committer is the configured server identity is a server write; any other
committer is external. No timestamp tolerance is needed.

The same discriminator fixes the *base* for every comparison in this
document: "the note's last content change" means the last commit
touching the path **whose committer is not the server**. Without that
qualifier the server's own asynchronous commit, landing seconds after
the write it records, would supersede the stamp it carries, and every
server-written note would read as stale. On the watcher route the
exclusion is structural: the server's own writes go through the index
writer and never arrive as a watcher event.

Three edges to state in the design rather than discover:

- Two server instances on one vault with the same committer identity
  would treat each other's commits as "own". That is correct: the other
  instance stamped at write time too.
- A human whose git committer equals the server's identity would be
  invisible. Not far-fetched: an operator running a personal vault may set
  `GIT_COMMIT_NAME` to their own name so the history reads uniformly. The
  `Vault-Operation:` trailer proposed in §9.2 is the second discriminator
  (committer *or* trailer marks a server commit), and it costs nothing to
  add now.
- Rebasing relabels the committer of every replayed commit. obsidian-git
  defaults to `merge`, which keeps committers; a human who rebases the
  server's commits on the command line turns them "external", and the
  next ingest re-derives their stamps from the commit (same actor, `at`
  moved to the commit time): one frontmatter-only pass, then quiet. The
  trailer closes this edge as well.

Idempotency follows from deriving the new stamp from the commit, not
from the clock: `generated.at` = the external commit's committer
timestamp, `generated.by` = `human:<author>` from the commit's author
field. `apply_okf_write_stamp` already returns the input bytes unchanged
when the resulting mapping is equal, so re-ingesting the same commit is a
no-op and produces no commit of its own.

### 5.2 What the reconciliation commit looks like

One pulled range can touch many notes. The reconciliation should be
**one commit per ingested range** under the server's committer identity,
with a subject that says what it is, and it should be pushed with the
next scheduled push, not immediately. Every clone then receives one
frontmatter-only commit after each burst of human edits. That is the
cost of posture 2 and it should be written in the guide as such.

### 5.3 Without git

The watcher route has no author and no timestamp beyond the file's
mtime. Applying the skill's rule, an external change on a non-git vault
at `own` should **drop** `generated` and `verified` rather than
invent an actor. Absence is the honest signal; `process:unknown` would be
fabricated provenance.

## 6. `log.md`: a knowledge history, not a rendering of git

### 6.1 The distinction that decides the model

The owner (2026-09-08): "From my perspective log.md and git are
semantically different." Git records byte events: every edit, every
auto-backup, every typo fix, every reserved-file refresh, exhaustively,
at commit granularity. `log.md` records knowledge events. The spec's own
examples are "**Update**: Added a BigQuery table reference for Customer
Metrics", "**Creation**: Established the Dataplex Playbook",
"**Initialization**: Created foundational directory structure". Many
commits map to no entry; one entry may span several commits; the entry
says what changed for a reader of the knowledge, not which bytes moved.
This repository lives the same split: `CHANGELOG.md` is derived from the
subset of commits that carry a counted type, not from `git log`.

Two earlier drafts of this section got that wrong and are recorded here
so they are not re-proposed:

- **Rendering `log.md` from `git log`** (with or without a sha-keyed
  carry-over of entry text, with or without a window) produces a commit
  log in OKF clothing. Every `vault backup` and every typo fix becomes an
  entry, hand-written entries die, and the window question was a symptom
  of treating history as a rendering.
- **Validating that a commit's `log.md` change mentions every file the
  commit touched** (the second half of the owner's 2026-09-08
  "commit-accounted" proposal) is undecidable over prose: a renamed file
  has two paths, a deleted one none on disk, "fixed typos" across twelve
  notes has no path at all. Enforcing it fights the human, relaxing it
  makes it a heuristic, and a failure has no good action. It is rejected.
  The decidable residue survives below: a commit whose `log.md` diff adds
  at least one bullet line has accounted for itself.

### 6.2 The model: one curated entry per note per day

**Grain.** The unit is (note, day): at most one entry per note per
calendar day, whatever the number of writes or commits behind it. That is
the grain the log already has (date sections), it matches the spec's
entry shape (an entry links a concept and says what happened to it), and
it is what makes a session of fifteen small writes produce one line. A
per-write intent was considered and rejected by the owner as the wrong
grain: "if we make many *small* changes (changing layout) it would get
swamped."

**Sources of an entry's text, in order of preference.**

1. **Declared intent.** The agent that made the change knows why. A tool
   `okf_intent(paths, intent, kind)` records the knowledge-level
   description for the pending (note, day) pairs; `kind` is the spec's
   bold word (`Update`, `Creation`, `Deprecation`), and `kind: none`
   declares that nothing knowledge-level changed, which clears the
   pending set without an entry. A declaration for a subset leaves the
   rest pending. The intent is the primary channel because it is the
   author's own words at the moment the work is coherent.
2. **Generated from the cumulative diff.** When the summarizer backend
   is configured and no intent arrives, the (note, day) diff — the note
   at the start of the day, or at the last entry, against the note now,
   plus any hints the agent attached — goes to the model, which answers
   two questions: did the information change, and how would a reader
   describe it. "No" is a legitimate answer and yields no entry. Under the
   `2026-07-28` revision this is the only model available (§4.3).
3. **The file list.** With neither, the entry names the note and the
   kind of change the diff shows. Honest and dull; never invented.

**Two filters before any model call.** A mechanical one: strip markdown
syntax, collapse whitespace, compare the word sequence; identical means
layout only, no entry, no call. Section reordering changes the sequence
but not the multiset and is a judgement to write down, not a heuristic.
A structural one: frontmatter-only diffs (a stamp, a `verified` entry, a
tag) and reserved-file diffs produce no entry.

**The nag.** The server tells the model what is pending. There is no
session to hang the list on (§4.3) and none is needed: which (note, day)
pairs lack an entry is a fact about the vault. Under git it is fully
derivable — paths in server-committed commits today whose entry is
missing or whose covered sha is behind — so no stored state, restart-proof,
correct across replicas. Without git it lives in the state directory.
The channel is in-band: every write result, and every read or search
result after writes, carries `intent_pending: [...]` with one instruction
line. A result field costs nothing from the instruction budget, where a
guidance sentence would. The nag cannot block (§4.3), and the fallback
sources above are what make it safe to ignore.

**Keys and idempotence.** A server-written entry carries its (path, day)
and the short sha of the latest commit it covers. Re-ingesting the same
range is a no-op; a later commit to the same note on the same day
recomputes the entry rather than adding a second. A commit whose `log.md`
diff adds a bullet line accounts for itself and is not re-described.

**Foreign commits** get the same treatment at ingest (§5), dated by the
commit, so a pull on Tuesday of Monday's Obsidian edits lands under
Monday. Their subject (`vault backup: 2026-09-08 07:12:03` by obsidian-git
default) is opaque by a small rule, not a heuristic, so source 2 or 3
applies. With #1405 the server's own subjects become useful hints to
source 2, and the two features share the backend but never the text:
commit subjects stay at git's grain.

**Post-commit, always.** A commit's sha cannot appear inside that
commit, so an entry keyed by the commit it covers is written after the
commit exists: on the write-callback worker after `_stage_and_commit`,
producing a follow-on commit that carries both reserved files. On a git
vault that is two commits per logical write, not today's up to three.

**Conflicts, without a list merger and without overwrite.** On the
server's clone every unkeyed line in `log.md` came from upstream, because
the server writes only keyed lines. So "take upstream, then re-insert my
missing keyed entries under their date headings" loses nothing on either
side: hand-written entries survive, the server's entries are re-derived
from their keys, and the only structure touched is the date heading,
which `append_okf_log_entry` already finds. This is the resolver policy
for `log.md` and it replaces both the sibling and the 312-line merger.

**Cost** is bounded by notes touched per day, not by writes; the
mechanical filter makes a layout-only day free. A large pulled range gets
a cap with the file list as the floor; bootstrap stays with
`okf_seed_log`, whose rendered-from-git output is the one place a commit
log is the right content, because it is a seed for a bundle that had no
history.

### 6.3 Where the append model stays

A vault without git has no commit to key on. There, own writes keep
today's append (one bullet per write, now filtered by the two filters
and keyed by (path, day) so repeated writes collapse), foreign changes
seen by the watcher produce no entry (nothing honest to say beyond a
path), and `okf_intent` works as above with the pending set in the state
directory.

## 7. `index.md`, and the reserved files as protected paths

`index.md` is a projection of the tree, regenerated on every ingest and
on every own write, conflicts resolved by regenerating; the owner's #1399
decision covers the mechanics. Two contracts the re-implementation
should state because #1400's review rounds turned on them: regeneration
is gated on markdown changes (deleting or moving an attachment does not
create a listing in a folder that never had one), and a reserved file is
server-owned content, not a note.

The owner (2026-09-08): "log.md and index.md should probably be
inaccessible (or at least read-only) to the mcp client." Read-only is the
form:

- **Under `maintain` and `own`, the write tools refuse `index.md` and
  `log.md`** with a reason naming the setting. "Accepted, then destroyed
  by the next regeneration" is worse than a refusal, and a guard at the
  write kernel covers every path at once, including a `rename` onto a
  reserved name that #1400 had to special-case.
- **Reads stay open.** Both files are what a client is told to read first
  in a bundle, and ranking already downweights them. Hiding them would
  break progressive disclosure and hide the log the intent flow fills.
- **Under `off` and `stamp` the guard is off**, because those postures say
  someone else may maintain the files, including an agent asked to by
  its human. Protected means server-owned; one concept.
- **The server's own writers pass through** on the suppressed intent they
  already run under (`okf_generate_index`, `okf_seed_log`, the
  maintainer), so no new bypass is invented.
- **The same guard protects the conventions file** (#1411, filed
  2026-09-08 with the overwrite and delete reproduced): a set of
  server-protected paths the write tools decline, one mechanism, two
  reasons.

Two surfaces change with it: the agent instruction snippet must stop
saying "update `log.md`/`index.md`" where the write would be refused, and
the write tools need one shared sentence about protected paths, in one
place, because the client-surface budget is nearly spent.

## 8. The untyped human note

Situation A has no derivable answer. What the server can offer:

1. **Report**, which it does: `okf_validate` lists `missing_type`,
   `stats.okf` counts it. Missing today is a *working* view: a
   `list_documents` / `search` filter for "untyped" (`matches_okf_filters`
   compares the derived `type` with the given value and an absent `type`
   matches nothing, so there is no way to ask for absence), so an agent can
   triage from the same tools it writes with.
2. **Default on ingest**, at `OKF_WRITE=own` only: a
   configured `OKF_DEFAULT_TYPE` (the PARA guide already recommends
   `Capture` for inbox notes) written into an untyped external note
   together with `status: draft` when `status` is absent. The pairing
   matters: a bare default type makes the gap vanish from the audit; the
   `draft` status keeps it visible as a triage queue
   (`list_documents(status=draft, type=Capture)`).
3. **Push the fix to the source.** Obsidian does not add frontmatter on
   note creation, and the core Templates plugin inserts a template only on
   command (`obsidian.md/help/plugins/templates`, fetched 2026-09-08:
   "Select **Insert template** to insert at the cursor position"). A
   Templater-style community plugin can apply a folder template on
   creation; that is the human's tooling, and the `examples/okf/`
   templates are the right thing to point it at. One more Obsidian fact
   worth a sentence in the guide: nested properties are "not supported"
   in the visual editor ("we recommend using the source mode",
   `obsidian.md/help/properties`), and `generated: {by, at}` and
   `verified: [{by, at}]` are exactly that shape. A human editing
   properties in Obsidian sees the trust families as opaque, which is
   another reason the server, not the human, should be the one keeping
   them true.

Recommendation: 1 always; 2 as the opt-in it is; 3 as documentation.

## 9. LLM-written commit subjects, and log entries from the diff

The owner's second idea (2026-09-08): "we can use the backend of the
summarizer function to have e.g. haiku write the git commit messages and
log updates from diff".

### 9.1 It fits the existing seam

`summarizer.py` exposes `Summarizer.summarize(system, user) -> str`
behind an OpenAI-compatible client; one `base_url` / `api_key` / `model`
triple reaches OpenAI, Ollama, vLLM, and Anthropic's OpenAI-compatible
endpoint. #915 deliberately removed the native `anthropic` backend in
favour of that single client, and a Haiku-class model is reachable
through the compat endpoint, so the feature needs no new backend. What
it needs is the seam renamed or generalised: the ABC is named after one
use, and a second caller (commit subjects) should not import
"summarizer" to write a subject. A `TextGenerator` with `summarize` and
`commit_subject` methods, or the ABC kept and the tool-specific prompts
moved out, is an implementation choice; the config section
(`SUMMARIZE_*`) would need a generic alias in a later major, since
renaming env vars is operator-surface.

### 9.2 Constraints the commit path imposes

- **The commit must never wait on the model.** `_stage_and_commit` runs
  on the write-callback worker, FIFO, with pushes queued behind it. The
  call gets a short budget (seconds, separate from `SUMMARIZE_TIMEOUT`),
  and on timeout, error, refusal or an empty reply the subject is the
  mechanical one. Amending after the fact is not an option once the push
  scheduler may have pushed.
- **Keep the mechanical subject as a trailer.** `operation: path` is
  what `git log` readers and the `seed_log` renderer see today. An
  LLM subject on line one plus a `Vault-Operation: write guides/a.md`
  trailer keeps both. Nothing in `git/` parses the subject
  (`build_log_markdown` only renders it), so this is a compatibility
  courtesy, not a requirement.
- **Commit scopes already batch.** A tool call that touched many files
  commits once, titled after the tool. A diff-based subject is strictly
  better for that case than `okf_convert_links: 2,595 files`.
- **Note content can steer the subject.** The model reads the diff, and
  the diff is user content. Constrain the output: one line, hard length
  cap, control characters and newlines stripped, and never let the model
  choose the trailer. `-m` is not a header-injection vector, so the risk
  is a misleading subject, not a malformed commit.
- **This sends every write's diff to a model, automatically.** The
  `summarize` tool sends note content only when a user calls it. Auto
  subjects are a privacy change: opt-in, off by default, and the guide
  should recommend a local endpoint for a private vault.
- **Cost** is one short call per commit. Haiku-class pricing makes it
  negligible; a self-hosted model makes it free.

### 9.3 Relation to `log.md`

Commit subjects stay at git's grain, one per commit, describing the
edit. Log entries are at the knowledge grain (§6.2). They share the
backend and possibly the agent's hints, never the text. #1405 is
therefore not on the OKF ladder: it is a git-integration feature, useful
on a vault that has never heard of OKF, with its own flag, off by
default.

## 10. The design, and its defaults

1. **Annotate always.** A `verified` entry whose `at` predates the note's
   last external content change (last commit not committed by the
   server, §5.1) is void in the server's own annotations; a `generated`
   stamp in the same state is flagged. On a non-git vault there is no
   evidence and the frontmatter is trusted. Recorded as a departure in
   `reference/okf-v0.2.md` beside the existing "verified is cleared" row.
2. **One ingest event** from the reindex, naming the changed paths and
   whether a full build ran, consumed by `index.md` regeneration (#1392),
   `log.md` curation (§6), provenance reconciliation (§5), and the
   warn-once (#1394). Queued ordinary writes; no new threads.
3. **One ladder.** `OKF_WRITE=off|stamp|maintain|own`, `true`/`false`
   kept as aliases for `maintain`/`off`. The instruction snippet follows
   the posture.
4. **`log.md` is curated, one entry per (note, day)**, from declared
   intent, else the summarizer over the cumulative diff, else the file
   list; two filters in front; keyed; post-commit; conflicts by
   take-upstream-then-reinsert. The append model stays for non-git
   vaults.
5. **`okf_intent` and the in-band pending nag**, config-gated with the
   ladder, state derived from git where git exists.
6. **Reserved files and the conventions file are protected paths** under
   `maintain` and `own` (#1411 for conventions).
7. **Reconcile external notes at `own`** from git evidence, one commit
   per ingested range; drop rather than invent without git.
8. **Untyped notes:** an absence filter for `type` now; `OKF_DEFAULT_TYPE`
   plus `status: draft` only at `own`; Obsidian template guidance in the
   guide.
9. **LLM commit subjects** (#1405), independent of OKF, off by default.

### 10.1 Every optional path, with its default

The rule: an absent capability removes a behaviour, never substitutes an
invented one; nothing here blocks a write, a commit, or a pull; anything
that writes files nobody asked for, or sends note content out, is off
unless set.

| Path | Default | Annotate | Stamps | `index.md` | `log.md` entries | `okf_intent` / nag | Reconcile | Protected paths |
|---|---|---|---|---|---|---|---|---|
| OKF not active | yes | off | off | off | off | hidden | off | conventions only (#1411) |
| OKF active, `OKF_WRITE=off` | yes | on with git, else trust | off | never written | never written | hidden | off | conventions only |
| `stamp` | | on | own writes | never written | never written | hidden | off | conventions only |
| `maintain` | | on | own writes | regenerated on ingest | curated at ingest and post-commit | on | off | reserved + conventions |
| `own` | | on | own writes | regenerated | curated | on | one commit per range | reserved + conventions |

| Dimension | Absent or off | Present |
|---|---|---|
| Git | No ingest from pulls; watcher deltas only. No foreign log entries; own writes keep the filtered append. Reconcile drops `generated`/`verified` rather than inventing an actor. Annotate trusts the frontmatter. Pending set in the state directory. | Full design. Without a remote, maintenance commits stay local. Pending set derived from history. |
| Summarizer | No generated text. Entry text: declared intent, else the file list. #1405 inert. | Used for entries and subjects behind the layout filter. Timeout, error, refusal, empty reply: mechanical text, logged once. |
| Auth | Nag vault-global; `okf_verify` under `elicit` stamps `human:local`. | Nag scoped by principal; `generated.by` from the claim. |
| Transport | stdio: single-tenant, all of the above. | Modern HTTP: no per-connection state anywhere. Legacy HTTP: same code, session unused. |
| Client capabilities | No elicitation: `okf_verify` fails closed; nothing else depends on it. | |
| File watcher | Off: non-git external changes seen at the next reindex. | On: deltas feed the same ingest event. |
| Read-only vault | Everything that writes is off; annotate on. | |
| `required_frontmatter` | Reserved files body-only. | Seeded keys, the existing departure. |
| Two server instances | Committer identity plus the `Vault-Operation:` trailer mark both as server; reserved files converge on take-upstream, stamps are idempotent. | |

### 10.2 Where this lands relative to the open issues

The 4.2 bugs stay 4.2 and stay narrow; the design is later work, tracked
by an epic whose children are the numbered items above.

| Existing | Relationship |
|---|---|
| #1391, #1403 (log frontmatter stacking, repair) | Unchanged; fix under the append model, which stays for non-git vaults. |
| #1392 (index.md stale) | Consumes the ingest event; the 4.2 fix may raise it minimally. |
| #1395 (resolver sibling on reserved files) | `index.md`: regenerate. `log.md`: take upstream, re-insert keyed entries (§6.2); sibling until then. |
| #1396 (audit misses root-index keys) | Unchanged. |
| #1393 (one maintainer) | Superseded by items 3 and 4; proposed for closing as a duplicate of the epic once filed, the owner's call. |
| #1394 (warn once) | A consumer of the ingest event. |
| #1401 (facade stamps provenance) | Unchanged. |
| #1405 (LLM commit subjects) | Item 9, filed 2026-09-08. |
| #1411 (conventions file overwritable) | Item 6, filed 2026-09-08. |
| Not yet a reference | An `obsidian-git` page under `docs/design/reference/`, per `researching-references`: the facts in §11 are cited inline and dated, not yet a reference. |

## 11. External facts used, and their limits

All fetched 2026-09-08; none has a reference page yet.

- obsidian-git `src/constants.ts` `DEFAULT_SETTINGS`: `commitMessage` and
  `autoCommitMessage` are `"vault backup: {{date}}"`;
  `commitDateFormat` is `YYYY-MM-DD HH:mm:ss`; `syncMethod` is `"merge"`;
  `mergeStrategy` is `"none"`. Its README: mobile runs on isomorphic-git
  with "No rebase merge strategy" among the listed limitations.
- isomorphic-git `merge` docs: a conflict the built-in diff3 cannot
  resolve aborts with `MergeNotSupportedError` unless
  `abortOnConflict: false`; only a `mergeDriver` callback is mentioned,
  not `.gitattributes`. So the `merge=union` guidance from the 2026-09-07
  probe is desktop-only. `[unverified]` whether obsidian-git on desktop,
  which shells out to the system git, honours `.gitattributes`; it
  should, and it was not tested.
- Obsidian help, properties: default properties `tags`, `aliases`,
  `cssclasses`; nested properties "not supported" in the visual editor.
  Obsidian help, templates: insertion is on command; variables `{{title}}`,
  `{{date}}`, `{{time}}`.
- OKF v0.2 field reference (skill copy, 2026-08 digest of `okf/SPEC.md`),
  cross-checked against `reference/okf-v0.2.md` for the rules quoted in
  §2.
