# OKF administration under more than one author

**Status:** design, approved by the owner on 2026-09-08 after three
rounds of discussion; the decisions recorded as the owner's are quoted.
Nothing here is implemented. Implementation is tracked by epic #1425
and the children listed in §10.2.
It exists to inform the clean re-implementation of #1392, #1395, #1396
and #1403 after `main` was rewound on 2026-09-08 (#1391's fix was
re-landed the same day as PR #1406 and is on `main`), and to give #1393
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
- Trust tiers are derived by the consumer: "Consumers derive a trust
  tier from `verified`", and "Trust tiers are advisory signals, not access
  control" (spec §5.3; credibility likewise is "*inferred* from the
  signals ... not stored", §5.1). Nothing in the spec forbids a consumer
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
- `log.md`: "A `log.md` file MAY appear at any level of the hierarchy to
  record the history of changes to that scope" and "Log entries are
  prose; the leading bold word (`**Update**`, `**Creation**`,
  `**Deprecation**`) is a convention, not a requirement" (spec §9). The
  spec recommends distributing a bundle as a git repository "since it
  provides history, attribution, and diffs" (§3). The reading that a
  git-hosted bundle's `log.md` "earns its keep when bundles travel without
  version-control history" is the OKF plugin skill's digest, not the
  spec's words, and is cited here as an interpretation.
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
| `generated {by, at}` | Whoever produced the bytes | The *time* of the last content commit, yes; the *actor*, no (a git author is not evidence of a human, §5.1) | Stamped on own writes | On ingest of an external change that left the stamp untouched, remove it rather than invent an actor | Annotate at read that the stamp predates the bytes; never write |
| `verified [{by, at}]` | The verifier. Only *invalidation* is derivable | Invalidation: yes (content commit after `at`) | Cleared on own writes | Clear on ingest of an external content change that did not itself re-verify | Treat entries older than the last content change as void at read time |
| `index.md` | Nobody; it is a function of the tree | Fully (it is a projection) | Regenerated after own writes only (#1392) | Regenerate on every ingest; resolve conflicts by regenerating | Never write it, even after own writes |
| `log.md` | The author of the change; a knowledge event, not a byte event (§6) | Only the *fact* of a change and its diff; the description is curated | Appended after own writes only | Curate one keyed entry per note per day from intent, diff, or a placeholder; conflicts by take-upstream-then-reinsert | Never write it |

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
2. **Own.** On every ingest of an external change, remove what git shows
   to be stale and regenerate what is a projection. Produces commits into
   the human's clone.
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
  maintains them. The replacement cannot be longer than what it replaces:
  the instructions budget is 1,536 UTF-16 units
  (`tests/test_client_surface_budget.py`) and the maximal surface measures
  1,507 today (`make_server` with every feature on, 2026-09-08), which
  leaves 29. The snippet has to become posture-dependent, and the
  client-surface budget (23,149 of 23,500 characters as of the last
  measurement) means the replacement cannot be longer than what it
  replaces.
- An agent `write` whose target is a reserved file is accepted today
  and simply not maintained. At `maintain` or above the write is
  refused (§7). The owner's earlier position (2026-09-07, from #1393),
  "If we choose 'server managed' then clobbering is fine and maybe even
  preferred", was superseded on 2026-09-08 by "inaccessible (or at
  least read-only)"; a refusal is the read-only form.

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
(closed unmerged on 2026-09-08, "Closed as too messy to merge. needs
reimplementation"; its branch `fix/1392-index-regeneration-triggers`
remains) found the traps by walking into each of them. Nothing from
#1399 or #1400 is on `main`: `git/conflict.py` today has no
reserved-file handling at all. The owner recorded them on #1392 and
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

The discriminator is a **trailer** on every server commit, written
unconditionally at every posture, and it records what the committing
instance *did*, not only that it was a server: `Vault-Operation:
<operation> <path>` (a batched call names the tool) and `Vault-OKF:
none|stamp|maintain|own`, the effective OKF level at commit time (`none`
when the bundle was inactive or the posture `off`). Every rule that
reads the trailer asks whether the recorded level performed the work
the rule would otherwise do: a commit at `stamp` or above already
carries its provenance stamp, so reconciliation and the comparison base
skip it; a commit at `maintain` or above already refreshed its reserved
files, so regeneration skips it; a commit at `none` did neither and is
external for both purposes although a server made it. Two instances at
different postures on one vault therefore leave nothing stale, and a
uniform posture is a recommendation, not a precondition. A commit with
no trailer is external. The committer identity (`_commit_staged` sets it
from the static `GIT_COMMIT_NAME` / `GIT_COMMIT_EMAIL`, default
`markdown-vault-mcp` / `noreply@markdown-vault-mcp`, and overrides only
the *author* from OIDC claims) classifies only commits that predate the
trailer, and those count as `maintain`, which is what `OKF_WRITE=true`
did. It is not an alternative signal for new commits: an operator who
sets the committer to their own name would otherwise make every human
commit look like the server's. No timestamp tolerance is needed.

The same discriminator fixes the *base* for every comparison in this
document: "the note's last external content change" means the last
commit **whose trailer does not record a level at which the stamp was
written** (no trailer, or `Vault-OKF: none`; committer identity only for
commits that predate the trailer) **and whose diff changes the note's
blob**. Two qualifiers, two reasons. Without the first, the server's own
asynchronous commit, landing seconds after the write it records, would
supersede the stamp it carries, and every server-written note would read
as stale. Without the second, a commit that only renames the note
touches its path without touching its bytes and would void a
verification the existing contract preserves on rename
(`tests/test_okf_write.py::TestInvalidationMatrix::test_rename_preserves_verified`);
the walk therefore reads `--name-status` with rename detection, as the
history queries already do (#1297), and a rename with no blob change is
not a content change. On the watcher route there is
no committer: the watcher sees the server's own writes like any other,
and today's reindex only drops them because their hash already matches
the tracker, which is a race against the write's own index update, not a
guarantee. A non-git classification must therefore compare a watcher
delta against the hashes the write path itself recorded when it wrote,
not against the tracker.

Three edges to state in the design rather than discover:

- Two server instances on one vault with the same committer identity
  would treat each other's commits as "own". That is correct: the other
  instance stamped at write time too.
- A human whose git committer equals the server's identity (an operator
  running a personal vault may set `GIT_COMMIT_NAME` to their own name so
  the history reads uniformly) is exactly why the trailer, not the
  committer, is the signal for new commits. obsidian-git puts the human's
  configured identity on its commits on both platforms and never a
  plugin identity (`reference/obsidian-git.md`), so the two identities
  coincide whenever the operator chooses to make them.
- Rebasing relabels the committer of every replayed commit. obsidian-git
  defaults to `merge`, which keeps committers; a human who rebases the
  server's commits on the command line changes their committer, and the
  trailer is unaffected.
- **A git author is not evidence of a human.** An external commit may be
  authored by a person, by a backup tool, by a second instance of this
  server, or by an agent, and the author field does not say which. Nor
  is the server's own author field always a person: `GitWriteStrategy`
  uses the configured commit identity as author when no principal is
  bound. Deriving `generated.by = human:<author>` from a commit would
  therefore fabricate provenance in every one of those cases, which §2
  rules out, and it would overwrite the correct stamp a second
  maintainer wrote (situation C). So reconciliation never derives an
  actor from git. What it can read from git is the *time* of the change
  and whether the external writer maintained the stamp itself.

The reconciliation rule at `own` is therefore two independent rules, one
per family, applied to an external content change (as defined above,
so a pure rename triggers neither): if the change did not touch
`generated`, the stamp describes bytes that no longer exist and the
server **removes** it rather than inventing an actor; if the change did
not touch `verified`, the attestations describe bytes that no longer
exist and the server **removes** them. A writer that maintained one
family and not the other (updated `generated`, left an old `verified`)
keeps the one it maintained and loses the one it did not, which is what
the §3 table already says. Absence is the legible signal. Idempotency is
immediate: a family that is absent needs nothing on re-ingest, and a
family the external writer maintains is never touched. An operator-configured mapping from git author to actor is a
conceivable later feature and is not designed here.

### 5.2 What the reconciliation commit looks like

One pulled range can touch many notes. The reconciliation should be
**one commit per ingested range**, carrying the trailer, with a subject
that says what it is, and it should be pushed with the next scheduled
push, not immediately. Every clone then receives one
frontmatter-only commit after each burst of human edits. That is the
cost of posture 2 and it should be written in the guide as such.

### 5.3 Without git

The watcher route has no commit at all, but it is not without evidence:
the index holds the note's frontmatter as last indexed, so whether an
external change touched `generated`, and separately whether it touched
`verified`, is a comparison of the prior row with the new bytes. The
two families are therefore reconciled independently here too, exactly
as with git: each is removed when the change left it untouched and kept
when the change maintained it. `process:unknown` would be fabricated
provenance.

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
   author's own words at the moment the work is coherent. A `kind: none`
   is a decision and must survive a restart and reach every replica, so
   it is persisted where the keys are: as a keyed HTML comment under the
   date heading (`<!-- none: /path.md (abc1234) -->`), invisible to a
   reader of the log, present to the pending derivation below.
2. **Generated from the cumulative diff.** When the operator has opted
   in (a separate setting, off by default; a configured summarizer
   backend is consent to the explicit `summarize` tool, not to sending
   every changed note's diff automatically, exactly as §9.2 requires for
   commit subjects) and no intent arrives, the (note, day) diff — the note
   at the start of the day, or at the last entry, against the note now,
   plus any hints the agent attached — goes to the model, which answers
   two questions: did the information change, and how would a reader
   describe it. "No" is a legitimate answer and yields no rendered entry;
   it is persisted as the same keyed negative comment as a declared
   `kind: none`, so it is not re-asked after a restart. Under the
   `2026-07-28` revision this is the only model available (§4.3).
3. **The placeholder.** With neither, the server does not know whether
   the information changed, and must not say it did: an Obsidian typo fix
   passes both filters, and an `**Update**` line for it would be exactly
   the commit-log-in-OKF-clothing that §6.1 rejects. So the fallback
   writes at most one *placeholder* per (note, day), under a distinct
   word (`**Change**: undescribed change to [Title](/path.md)`), which
   asserts only that the bytes changed. It stays pending (below) until an
   intent or a summary replaces it, and a reader can tell it apart from a
   curated entry. Whether an operator would rather have no line than a
   placeholder is a setting worth offering; the default is the
   placeholder, so the log stays complete.

**Two filters before any model call.** A mechanical one: strip markdown
syntax, collapse whitespace, compare the word sequence; identical means
layout only, no entry, no call. Section reordering changes the sequence
but not the multiset and is a judgement to write down, not a heuristic.
A structural one: frontmatter-only diffs (a stamp, a `verified` entry, a
tag) and reserved-file diffs produce no entry.

**The nag.** The server tells the model what is pending. There is no
session to hang the list on (§4.3) and none is needed: which (note, day)
pairs lack a curated entry is a fact about the vault. Under git it is
fully derivable from *every* content commit, the server's and foreign
ones alike, in one bounded range whose lower end is itself derived from
git: every accounting pass is a commit carrying the trailer
(`Vault-Operation: okf_accounting`), so the boundary is the newest such
commit reachable from `HEAD`, and the candidates are the commits in
`<that commit>..HEAD`. When `maintain` is first enabled the first pass
regenerates the reserved files, which produces that commit; if nothing
needs writing it makes an empty one, so activation is always a commit.
Git's range semantics make the scan bounded (the new commits, not the
history) and complete (a branch merged late is not reachable from the
boundary and so appears in the range, which a date watermark would
miss). A marker line inside `log.md` was considered and rejected: two
instances would each advance it to a different sha and conflict on that
line on every concurrent pass, while a boundary read from the graph
needs no line and cannot conflict; when two instances' passes both
reach a clone, the newer one is the boundary and the other's examined
commits are re-examined idempotently by their keys. A (note, day) is
pending when a candidate commit changed the note's blob and its entry is
missing, or its covered sha is behind, or the entry is a placeholder,
and not pending when a keyed negative comment covers that sha. So no
stored state outside `log.md` and the graph, restart-proof, correct
across replicas,
a foreign commit ingested just before a restart still pending
afterwards, and history before activation left to `okf_seed_log`, which
is the one place a rendering of git is the right content. Without git it lives in the state directory.
The channel is in-band: every write result, and every read or search
result after writes, carries `intent_pending: [...]` with one instruction
line. A result field costs nothing from the instruction budget, where a
guidance sentence would. The nag cannot block (§4.3), and the fallback
sources above are what make it safe to ignore.

**Keys and idempotence.** A server-written line carries its (path, day),
the short sha of the latest commit it covers, and its *source*: curated
(intent or summary) or placeholder, distinguished by the bold word, or
negative, as the keyed comment above; so the nag can tell, after a
restart or on another replica, which keyed entries still want an
intent and which decisions were already taken. Re-ingesting the same range is a
no-op; a later commit to the same note on the same day recomputes the
entry rather than adding a second. A commit whose `log.md`
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
vault that is two commits per logical write. Today it is one: the
maintainer's secondary writes fire inside the tool call's commit scope
(#1264) and are batched with the note, so the guide's "up to three
commits" and the matching sentence in `okf.md` §6 are stale and owe a
docs fix. The second commit is the price of a keyed entry.

**When the fallback fires.** "Pending" means the (note, day) entry has
not been written yet, and the follow-on commit does not write the
fallback at once: it waits a settle window (the push scheduler's
debounce is the natural one, so the entry lands before the push). An
intent declared inside the window is the entry; the summarizer (opted
in) or the placeholder fills it when the window closes; an intent
declared after that recomputes the entry under the same key, one more
commit. So the nag names entries not yet written *and* placeholders, and
both are cleared by one `okf_intent` call.

**Conflicts, without a list merger and without overwrite.** When the
server's clone is written only through the server, every unkeyed line in
its `log.md` came from upstream, because the server writes only keyed
lines. Then "take upstream, then re-insert my missing keyed entries under
their date headings" loses nothing on either side: hand-written entries
survive, the server's entries are re-derived from their keys, and the
only structure touched is the date heading, which `append_okf_log_entry`
already finds. That premise is decidable per conflict: the lines added
to `log.md` between the merge base and the local head are either all
keyed (`git diff <merge-base> HEAD -- log.md`, added lines; a keyed
negative comment counts as keyed) or not. When
they are, this is the resolver policy. When they are not, someone edited
the served clone directly (a shared filesystem with the watcher on), and
the sibling policy of today stays so that nothing is lost. No list
merger in either branch.

**Cost** is bounded by notes touched per day, not by writes; the
mechanical filter makes a layout-only day free. A large pulled range gets
a cap with placeholders as the floor; bootstrap stays with
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
- **The same guard can protect the conventions file** (#1411, filed
  2026-09-08 with the overwrite and delete reproduced): a set of
  server-protected paths the write tools decline, one mechanism, two
  reasons. Whether and when the conventions file is protected is #1411's
  decision and does not follow the OKF ladder; a vault that never heard
  of OKF still has a conventions file.

Two surfaces change with it: the agent instruction snippet must stop
saying "update `log.md`/`index.md`" where the write would be refused
(the instructions budget has 29 units of headroom, §4.2), and the write
tools need one shared sentence about protected paths, in one place,
because the tool-description budget is nearly spent too (23,149 of
23,500 at the last measurement).

## 8. The untyped human note

Situation A has no derivable answer. What the server can offer:

1. **Report**, which it does: `okf_validate` lists `missing_type`,
   `stats.okf` counts it. Missing today is a *working* view: a
   `list_documents` / `search` filter for "untyped" (`type` is deliberately
   not an OKF filter dimension; it is an ordinary `document_tags` equality
   lookup in the structured-filter path, and an equality lookup has no
   value that means "absent"), so an agent can
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
- **The mechanical text moves to the trailer, which exists anyway.**
  §5.1 makes `Vault-Operation: write guides/a.md` an unconditional
  trailer on every server commit; with #1405 on, the LLM subject takes
  line one and the trailer is where the mechanical text already lives.
  Nothing in `git/` parses the subject (`build_log_markdown` only renders
  it), so this is a compatibility courtesy, not a requirement.
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

1. **Annotate always** (#1413). A `verified` entry whose `at` predates the note's
   last external content change (last commit without the server's
   trailer, §5.1) is void in the server's own annotations; a `generated`
   stamp in the same state is flagged. On a non-git vault there is no
   evidence and the frontmatter is trusted. Recorded as a departure in
   `reference/okf-v0.2.md` beside the existing "verified is cleared" row.
2. **One ingest event** (#1414) from the reindex, naming the changed paths and
   whether a full build ran, consumed by `index.md` regeneration (#1392),
   `log.md` curation (§6), provenance reconciliation (§5), and the
   warn-once (#1394). Each consumer skips a commit whose `Vault-OKF:`
   level already did its work (§5.1). Queued ordinary writes; no new
   threads.
3. **One ladder** (#1412). `OKF_WRITE=off|stamp|maintain|own`, `true`/`false`
   kept as aliases for `maintain`/`off`. The instruction snippet follows
   the posture.
4. **`log.md` is curated, one entry per (note, day)** (#1416), from declared
   intent, else (opted in separately) the summarizer over the cumulative
   diff, else a placeholder that claims nothing; two filters in front; keyed; post-commit;
   conflicts by take-upstream-then-reinsert when every local addition is
   keyed, the sibling otherwise. The append model stays for non-git
   vaults.
5. **`okf_intent` and the in-band pending nag** (#1417; summarised
   entries #1418), config-gated with the ladder; state derived from git
   over the range above the newest accounting commit where git exists,
   negative decisions persisted as keyed comments in `log.md`.
6. **Reserved files are protected paths** under `maintain` and `own`
   (#1419). The conventions file's protection is independent of the OKF
   posture and is decided in #1411; the two share one mechanism.
7. **Reconcile external notes at `own`** (#1420): `generated` and
   `verified` independently, each removed when an external content change
   (blob changed, not a rename) left it untouched, never re-derived from a
   git author; one commit per ingested range; the same rule without git.
8. **Untyped notes:** an absence filter for `type` now (#1421);
   `OKF_DEFAULT_TYPE` plus `status: draft` only at `own` (#1422); Obsidian template guidance in the
   guide.
9. **LLM commit subjects** (#1405), independent of OKF, off by default.

### 10.1 Every optional path, with its default

The rule: an absent capability removes a behaviour, never substitutes an
invented one; nothing here blocks a write, a commit, or a pull; anything
that writes files nobody asked for, or sends note content out, is off
unless set.

| Path | Default | Annotate | Stamps | `index.md` | `log.md` entries | `okf_intent` / nag | Reconcile | Protected paths |
|---|---|---|---|---|---|---|---|---|
| OKF not active | yes | off | off | off | off | hidden | off | none from OKF (conventions per #1411) |
| OKF active, `OKF_WRITE=off` | yes | on with git, else trust | off | never written | never written | hidden | off | none from OKF |
| `stamp` | | on | own writes | never written | never written | hidden | off | none from OKF |
| `maintain` | | on | own writes | regenerated on ingest | curated at ingest and post-commit | on | off | reserved files |
| `own` | | on | own writes | regenerated | curated | on | one commit per range | reserved files |

| Dimension | Absent or off | Present |
|---|---|---|
| Git | No ingest from pulls; watcher deltas only. No foreign log entries; own writes keep the filtered append. Reconcile drops a stale `generated` or `verified`, each on its own. Annotate trusts the frontmatter. Pending set in the state directory. | Full design. Without a remote, maintenance commits stay local. Pending set derived from history. |
| Summarizer | No generated text. Entry text: declared intent, else a placeholder. #1405 inert. | Still nothing automatic: summarised log entries (#1418) and commit subjects (#1405) each need their own opt-in. When opted in, behind the layout filter; timeout, error, refusal, empty reply: mechanical text, logged once. |
| Auth | Nag vault-global; `okf_verify` under `elicit` stamps `human:local`. | Nag scoped by principal; `generated.by` from the claim. |
| Transport | stdio: single-tenant, all of the above. | Modern HTTP: no per-connection state anywhere. Legacy HTTP: same code, session unused. |
| Client capabilities | No elicitation: `okf_verify` fails closed; nothing else depends on it. | |
| File watcher | Off: non-git external changes seen at the next reindex. | On: deltas feed the same ingest event. |
| Read-only vault | Everything that writes is off; annotate on. | |
| `required_frontmatter` | Reserved files body-only. | Seeded keys, the existing departure. |
| Two server instances | The `Vault-OKF:` level in each commit's trailer says what the other instance did, so a `maintain` instance regenerates after a `stamp` instance's commit and reconciles after an `off` one's; reserved files converge on take-upstream; a stamp the other instance wrote is left alone. A uniform posture is recommended, not required. | |

### 10.2 Where this lands relative to the open issues

The 4.2 bugs stay 4.2 and stay narrow; the design is later work, tracked
by epic #1425, whose children are items 1 to 8 above plus the
`Vault-Operation:` trailer (#1415), the docs fix for the commit count
(#1423) and #1394. Item 9 (#1405) is related, not a child.

| Existing | Relationship |
|---|---|
| #1391 (log frontmatter stacking) | Fixed on `main` by PR #1406 (re-landed after the rewind); shipped in 4.2.0-rc.2. |
| #1403 (repair of stacked files) | Unchanged; a fix under the append model, which stays for non-git vaults. |
| #1392 (index.md stale) | Consumes the ingest event; the 4.2 fix may raise it minimally. |
| #1395 (resolver sibling on reserved files) | `index.md`: regenerate. `log.md`: take upstream, re-insert keyed entries (§6.2); sibling until then. |
| #1396 (audit misses root-index keys) | Unchanged. |
| #1393 (one maintainer) | Superseded by items 3 and 4; proposed for closing as a duplicate of the epic once filed, the owner's call. |
| #1394 (warn once) | A consumer of the ingest event. |
| #1401 (facade stamps provenance) | Unchanged. |
| #1405 (LLM commit subjects) | Item 9, filed 2026-09-08. |
| #1411 (conventions file overwritable) | Item 6, filed 2026-09-08. |
| #1423 (docs) | The guide and `okf.md` §6 say a note write can produce "up to three commits"; since #1264 it is one (§6.2). |
| #1424 (reference) | Closed by this document's change: `reference/obsidian-git.md`, which §11 now points at. |

## 11. External facts used

The obsidian-git, isomorphic-git and Obsidian facts this design rests on
(default subject `vault backup: {{date}}`, identity from the user's git
config on both platforms, `syncMethod: "merge"`, no rebase and only a
`mergeDriver` callback on mobile, no automatic frontmatter on a new note,
nested properties unsupported in the visual editor) are recorded with
sources and dates in [`reference/obsidian-git.md`](reference/obsidian-git.md),
including what stays `[unverified]` (whether desktop obsidian-git honours
`.gitattributes` merge drivers). The OKF v0.2 rules quoted in §2 are from
`okf/SPEC.md` as recorded in [`reference/okf-v0.2.md`](reference/okf-v0.2.md);
where this document uses the OKF plugin skill's digest as an
interpretation, it says so.
