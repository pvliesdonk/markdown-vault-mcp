# OKF administration under more than one author

**Status:** analysis, 2026-09-08, revised the same day after the owner's
first reading (5.0 room, the commit-accounted log, the overwrite
variant). Nothing here is implemented; the decisions recorded as the
owner's are quoted.
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
| `log.md` | Depends on the model (§6 below) | Fully, if it is a rendering of `git log` | Appended after own writes only | Render from history on every ingest; resolve conflicts by regenerating | Never write it |

Two rows carry most of the weight: `generated`/`verified`, where git is
strong evidence and the server has been ignoring it, and `log.md`, where
the choice of model decides whether the file is history (merge it) or a
projection (regenerate it).

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

## 6. `log.md`: history or projection

The append model (today) records only this server's writes into a file
that claims to be the folder's change history. Under any second author it
is a partial history by construction (#1393). Four models are on the
table; the fourth is the owner's (2026-09-08) and is stress-tested in
§6.2.

| Model | The file is | Conflict policy | Records external changes | Hand-written entries | Extra machinery |
|---|---|---|---|---|---|
| Append (today) | History, hand-extendable | Merge it (the markdown list merger that cost #1399 three rounds and 312 lines) or keep the sibling | No | Survive | none |
| Rendered | A projection of `git log` for the subtree | Regenerate, like `index.md` | Yes, every commit, from any clone | Die | The renderer that exists: `okf_seed_log` / `build_log_markdown` |
| Hybrid | Rendered entries plus hand-written ones in a marked section | Regenerate the derived part, merge the rest | Yes | Survive | Both |
| Commit-accounted (§6.2) | History, one keyed entry per commit, written at ingest | Take upstream, then re-account | Yes | Survive if keyed; otherwise per §6.3 | Sha-keyed entries; the ingest hook |

**On reopening #1399.** The first draft flagged that a rendered or
accounted `log.md` reopens the 2026-09-08 decision ("`index.md` treated
as a projection of vault state, `log.md` left on the sibling policy").
The owner's answer, same day: "This is fine, it is reopened on
new/extended information". The earlier remark from #1393 points the same
way: "We might be able to get that information from commit messages,
with an honest question on whether (in a git managed environment) we
should not use those as a source of truth anyway".

### 6.1 Requirements common to every git-fed model

- **It must not log itself.** A maintenance commit touches only reserved
  files. Any model skips commits whose changed paths in scope are all
  reserved names.
- **Per-folder scope is the subtree**, as `okf_seed_log(folder=...)`
  already does; the root log is the whole vault.
- **Opaque subjects need a fallback.** obsidian-git's default subject is
  `vault backup: {{date}}` (its `DEFAULT_SETTINGS`, fetched 2026-09-08).
  For such a commit the entry text is the changed-file list, or a
  description generated from the diff (#1405, §9). What counts as opaque
  is a small rule (the plugin's default template, a subject that is only
  a timestamp), not a heuristic over prose.
- **The server's own subjects are opaque too.** `write: guides/a.md`
  renders as `**write: guides/a.md** (abc1234)`, which is what
  `build_log_markdown` produces today. #1405 is what makes any git-fed
  log readable.
- **Byte-stable for unchanged input**, so a pass with nothing new writes
  nothing (the `diff --cached --quiet` guard supplies the second half).

### 6.2 The commit-accounted log, stress-tested

The owner's proposal (2026-09-08), quoted:

> if a commit has changes but does not include log.md then it must be
> written (e.g. using the summary we described above on git diff, or
> 'external changes'), or if it does include one it can check whether
> those changes are actually compatible with the files changed (every
> file in the commits must be mentioned).

Restated as a rule per folder scope: *every commit in the ingested range
that changes a note in scope is accounted for in that scope's `log.md`,
either by the commit itself or by an entry the server adds at ingest.*
The rule has two halves, and they fare very differently.

**Half one, "if the commit does not touch `log.md`, the server writes the
entry", holds up, under six conditions.**

1. **Entries are keyed by commit.** "Is C accounted for" must be a token
   search, not a reading of prose: an entry carries `(abc1234)` as the
   seeded log already does, and the check is "does a bullet line in this
   file end with C's short sha". Without a key the check becomes
   path-matching over prose, which is the class of decision the #1397
   and #1399 retrospectives say to delete, not refine.
2. **Candidates are the ingested range**, `from_sha..to_sha` of this pull
   (or the full-build case of §5), never a date watermark. A merge that
   brings in an old-dated commit is then logged; a re-pull of the same
   range is idempotent by the key check.
3. **Reserved-only commits are exempt**, which the rule gives for free: a
   commit whose in-scope changes are all reserved files has nothing to
   account for. The server's own log commit therefore never triggers a
   further entry, and the loop closes by construction.
4. **The server's own note writes ride in the same commit as their log
   entry.** Today a note write and its `log.md` append are separate
   commits. If they stay separate, a *second* server instance sees the
   note commit as unaccounted and logs it again. Joining the append to
   the write's commit scope (`_commit_scope.py` already batches one tool
   call into one commit) makes every server commit self-accounting and,
   as a side effect, removes the "up to three commits per write" cost
   the guide apologises for.
5. **One accounting commit per ingested range**, all folders, pushed on
   the normal schedule. Same shape and cost as the reconciliation commit
   of §5.2, and it can be the same commit.
6. **Conflict policy: take upstream, then re-account.** A same-day append
   on both sides conflicts exactly as today's probes showed. Resolution
   takes the upstream file and re-runs the rule over the range: every
   entry the server had added is re-derivable (its text is cached by sha,
   §9.3) and every missing key is appended under its date heading. No
   list merger; the only structure touched is the date heading, which
   `append_okf_log_entry` already finds.

With those six, the model is decidable end to end, stateless beyond the
file itself (the keys *are* the state, shared through git with every
clone), and it keeps hand-written entries because it only ever appends.

**Half two, "if the commit touches `log.md`, check that every changed
file is mentioned", does not survive.**

- It is undecidable in the way that matters. "Mentioned" means a path
  string appears in prose under some heading: a renamed file has two
  paths, a deleted one has none on disk, an attachment may be mentioned
  for another reason, and a human who fixed the same typo in twelve
  notes writes "fixed typos", not twelve paths. Enforcing it fights the
  human; relaxing it makes it a heuristic; both are what the retros
  warn against.
- It duplicates git. If the log is required to be a complete file list,
  git already is one, and rendering it (§6.1) needs no validation.
- The failure mode it creates has no good action. When the check fails,
  the server either appends a completion entry (now there are two
  entries for one commit, the human's prose and the server's list) or
  warns (and the warning has no audience, per #1394's argument).

There is a decidable *fragment* worth keeping: "the commit's diff of
`log.md` adds at least one bullet line". That is a line-shape test on
the diff, not prose parsing, and it closes the one realistic hole in
half one: obsidian-git's auto-commit bundles everything changed in the
interval, so a human who touched `log.md` to fix a typo in the same
interval as five note edits would otherwise leave those five
unaccounted. With the fragment, a `log.md` change that adds no entry
does not count as accounting, and the server adds the entries.

**Where the accounted model beats rendering**, and this is the finding
of the stress-test rather than the premise: the description of a
foreign commit generated from its diff (#1405) has a durable, shared
home. Under rendering it must be cached per clone in the state
directory, or attached as a git note that obsidian-git neither pushes
nor reads, or regenerated (unstable text, a model call per pass). Under
accounting it is written once into `log.md`, committed, and pushed, and
every clone has it. The log becomes the OKF-native place for the
narrative git lacks, which is the only thing a git-hosted `log.md`
"earns its keep" for, in the spec's phrase.

**Where it is weaker:** it trusts a commit that adds a log bullet to
have described itself, it grows without bound (the seeded log's `limit`
has no counterpart; a per-folder window with older sections left as
they are is the natural bound), and a pruned entry is re-added on the
next pull only if its commit is in the ingested range, which is the
right behaviour but must be documented so pruning old sections is known
to be safe.

### 6.3 The overwrite variant

The owner's second proposal (2026-09-08), quoted: "take ownership of
log.md (and maybe index.md) and *always* overwrite. *Assume* that the
external party does not maintain it properly and just overwrite."

For `index.md` this is the projection model, already decided. For
`log.md` the honest form of "always overwrite" is: on every ingest,
re-render the whole file from the git window, taking each entry's
*text* from the previous file when an entry with that commit's key
exists there, and generating it otherwise. That is the accounted model
with a different write shape, and the difference is worth stating:

| | Accounted, append in place | Overwrite with keyed carry-over |
|---|---|---|
| Format damage by the external party (stacked frontmatter, a broken heading, a `+` bullet) | Persists until someone fixes it | Healed on every ingest |
| Hand-written entries without a key | Survive | Die, every time |
| In-place editing logic | Needed (find the date section, insert) | None; the file is rendered from scratch |
| Reading the old file | Key search | Key search plus taking the line's text |
| Window / growth | Unbounded unless windowed | Bounded by the render window by construction |

Under the stated assumption ("the external party does not maintain it
properly"), overwrite is the stronger form: every ingest converges the
file to canonical shape, there is no insertion code to get wrong, and
the sha-key carry-over is a per-line token match. The cost is the
death of unkeyed entries, and the answer to that is the contract the
model implies anyway: **in a git vault, your log entry is your commit
message.** A human who wants prose in the log writes it in the commit;
the render picks it up, or #1405's description does when the subject is
opaque. Hand editing `log.md` is then as pointless as hand editing
`index.md`, and the guide can say so in one sentence.

One caveat to write down: the carry-over reads the previous file's
lines for text, so a keyed line must be single-line by construction
(the server writes them that way). A human's multi-line keyed entry is
truncated to its first line on the next render, and that is accepted
under the assumption.

### 6.4 Recommendation for `log.md`

Overwrite with keyed carry-over (§6.3) for git-backed vaults at
`maintain` and above; the append model stays for non-git vaults, where
there is no commit to key on. Conflict policy for both reserved files is
then "regenerate", the list merger does not come back, and the LLM
description of a foreign commit has one durable home. The hybrid model
is not needed: the "hand-written" channel is the commit message.

## 7. `index.md`: settled, with two contracts to write down

The owner's #1399 decision covers the mechanics. Two contracts the
re-implementation should state because #1400's review rounds turned on
them:

- A reserved file is **server-owned content**, not a note. A `rename`
  or `move_folder` whose destination is `index.md` is not honoured as a
  note operation; a concurrent hand edit is overwritten by the next
  regeneration; there is no "is this really a listing" check under the
  lock because that question is undecidable. (#1400 R10 declined the
  concurrent-overwrite finding on this ground and documented it; keep
  that.)
- Regeneration is gated on **markdown** changes. Deleting or moving an
  attachment does not create an `index.md` in a folder that never had
  one (#1400 R10/R11 found this three times over three sibling hooks).

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

### 9.3 Log entries

With `log.md` fed from git (§6) the commit subject *is* the log entry for
the server's own commits, so "log updates from diff" is not a second
feature there: it is the same generated line rendered twice. That is the
synergy that makes the two ideas one design, and the reason to sequence
§6 before #1405.

Foreign commits (`vault backup: 2026-09-08 07:12:03`) cannot be
rewritten. Under the accounted or overwrite model (§6.2, §6.3) the
description generated from the pulled diff at ingest is written into the
`log.md` entry itself, keyed by sha, committed and pushed: durable,
shared with every clone, and never regenerated. A sha-keyed cache in the
state directory is still useful as the *source* the carry-over falls
back to when the previous file is missing, but it is no longer where the
text lives. Git notes remain the option that keeps the text out of the
tree, and obsidian-git neither pushes nor reads them.

## 10. Recommendation

1. **Annotate always.** Extend the read layer's derivation with the
   evidence the server has: a `verified` entry whose `at` predates the
   note's last external content change (last commit not committed by the
   server, §5.1) is void; a `generated` stamp in the same state is
   flagged. Where the evidence comes from is the one real
   design question: a per-read `git log -1 -- path` is honest but costs a
   subprocess per `read` and cannot serve search hits, so the practical
   form is a column computed at ingest (from the pulled range, the
   watcher delta, or the server's own write) and recomputed from git on a
   full build. Record the new departure in `reference/okf-v0.2.md`
   beside the existing "verified is cleared" row.
2. **Raise one ingest event** from the reindex, naming the changed paths
   and whether a full build ran, and consume it for: `index.md`
   regeneration (#1392), `log.md` regeneration (§6), provenance
   reconciliation (§5), and the warn-once (#1394). Design the listener as
   queued ordinary writes; keep threading out of it.
3. **Make ownership one ladder.** `OKF_WRITE=off|stamp|maintain|own`,
   with `true`/`false` kept as aliases for `maintain`/`off` (additive;
   the 5.0 room is free for dropping the aliases if wanted). Make the
   instruction snippet posture-dependent. Write the "reserved files are
   server-owned content; in a git vault your log entry is your commit
   message" contract into the guide.
4. **`log.md` is overwritten from git with keyed carry-over** (§6.3) on a
   git-backed vault at `maintain` and above; the append model stays for
   non-git vaults. Conflict policy for both reserved files is then
   "regenerate", the list merger does not come back, and the server's own
   log entry rides in the same commit as the note it describes. The
   owner has accepted that this reopens #1399.
5. **LLM commit subjects** (#1405) after 4, opt-in, on the existing
   OpenAI-compat seam, with the constraints in §9.2; the description of a
   foreign commit lands in its `log.md` entry.
6. **Untyped notes:** an "untyped" filter now; `OKF_DEFAULT_TYPE` plus
   `status: draft` only at `own`; Obsidian template guidance in
   the guide.

### 10.1 Where this lands relative to the open issues

Proposed, not filed or moved. The 4.2 bugs stay 4.2 and stay narrow; the
posture model is 4.3 or later work.

| Existing | Relationship |
|---|---|
| #1391, #1403 (log frontmatter stacking, repair) | Unchanged; fix under the append model. If §6 lands later the append path survives for non-git vaults, so the fix is not wasted. |
| #1392 (index.md stale) | Becomes "consume the ingest event for `index.md`". The event itself is the new piece; the 4.2 fix can raise it minimally (paths from the reindex delta, plus the full-build rule). |
| #1395 (resolver sibling on reserved files) | `index.md`: regenerate, no sibling, no injected keys. `log.md`: sibling under the append model until §6.3 lands; then regenerate too. |
| #1396 (audit misses root-index keys) | Unchanged. |
| #1393 (one maintainer) | Becomes the posture ladder, §4, plus §6.3. Split into: the `OKF_WRITE` enum, log-from-git with keyed carry-over, reconcile-on-ingest. |
| #1394 (warn once) | A consumer of the ingest event. |
| #1401 (facade stamps provenance) | Unchanged. |
| #1405 (LLM commit subjects) | Filed 2026-09-08 from §9. |
| New | Read-side derivation of void `verified` (§10, item 1). Untyped filter (§8). Obsidian-side guide section (§8). An `obsidian-git` reference page under `docs/design/reference/`, per `researching-references`: the facts in this document about its defaults and mobile limits are cited inline and dated, not yet a reference. |

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
