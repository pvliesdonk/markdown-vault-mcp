---
name: roadmapping
description: >-
  Use when work spans more than one pull request or epic, when the order
  of work is not yet decided, or when charting, refining or revisiting
  the roadmap index, epics and release package milestones. Use before
  implementation planning for work not yet refined into one ready feature.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on copier update. Project-specific
     additions go inside the DOMAIN-ROADMAPPING sentinel at the end. ===== -->

# Roadmapping

A roadmap answers three questions: which direction, in what order, and
what we do not yet know. It does not answer how. The moment it starts
answering how, it has stopped being a roadmap and has become a plan that
will be wrong.

Planning skills have one resolution setting. Point them at six months of
work and they produce file paths and schemas for things nobody has looked
at yet; reviewers then correctly attack that precision and the whole
artefact loses credibility. The fix is to plan at a declared resolution
and defend it.

## The evidence rule

**If you do not have hard evidence, you cannot be certain. It is not your
job to obtain that evidence during charting.**

An explicit request to research or implement authorizes that work. This
rule prevents silently turning a roadmap pass into an investigation; it
does not override the user's scope or previously authorized work.

Both halves matter. When you reach a gap, the tempting move is to read the
code or open five files until you can say something concrete. Do not.
That is how a roadmap fills with confident detail about work nobody has
scoped.

**Assert it, or file it as a known unknown. Never research to promote it.**

Every unknown gets a *resolved by* pointer: which piece of work, when
finished, answers it. When nothing planned will answer it and not knowing
changes what you do next, the finding out is itself work: file a research
issue (see *Everything is work*). The evidence rule defers research; it
does not cancel it.

## The model

Three GitHub objects and one document, each holding one kind of fact.
This is the fleet-wide definition; the words mean nothing else here.

| Object | Is | Holds |
| --- | --- | --- |
| **Epic** | a parent issue with native sub-issues, labelled `epic` | the story, its frozen outcome ("Done when"), its children, its refinement issue |
| **Package** | a milestone titled `NNN <content-name>` | the payload of exactly one release cut |
| **Issue** | a feature, bug, research spike or refinement task, labelled by kind | executable work, at whatever resolution it has earned |
| **Index** | `docs/design/roadmap.md` | the argument: why this direction, why this order, what we do not know |

**Story and cut are orthogonal.** An issue may carry both a parent epic
and a package. An epic spans cuts; a package draws from several epics. Do
not name a milestone after an epic and do not use a milestone as a theme:
the test for a package is "could this plausibly be one release?"

**An epic whose only open child is its refinement issue is an idea, not
executable.** No agent starts building from an epic or from the index.
Work starts from a refined feature or it does not start. This is the
anti-precision rule expressed as a data shape, and it is more reliable
than prose telling you to stay vague.

Resolution is derived, never stored:

- epic whose only open child is its refinement issue → **direction**
- epic with feature issues, not yet ready → **refined**
- feature marked ready → hand off (see *Handing off*)

Do not add a resolution field. A stored value drifts; the graph does not.

The GitHub behaviours this model leans on (one milestone per issue,
sub-issues inheriting the parent's milestone, milestones closing with open
items, what the search qualifiers actually match) are recorded with
sources in `docs/design/reference/github-planning-objects.md`. Read it
before scripting against the tracker; re-research it when its
`stale_after` has passed.

### The refinement issue

Every epic carries, from the moment it is created, one sub-issue labelled
`refinement` that says this epic must be refined.

- It is the executable representative of an idea: the only thing you can
  honestly commit to doing about an unrefined epic is to look at it and
  decide. Direction-level ordering is therefore expressible structurally,
  as `blocked-by` links from a refinement issue to the features that must
  exist before shaping is even sensible.
- An agent looking for work in an unrefined epic finds only "refine this",
  so the executability rule enforces itself.
- Its definition of done is the coverage check: feature issues exist that
  plausibly satisfy the epic's "Done when".

**It is a task, not a container.** Its body is a pointer to the index
entry and the "Done when", nothing else. Write the epic into it and you
have recreated the duplicate-source problem the index exists to solve.

**Close it when refinement completes.** Re-refining later is a new
refinement issue; the trail of how often an epic was rethought is signal
about where the real uncertainty sits.

```bash
repo=OWNER/REPO
epic=EPIC_NUMBER
gh issue create --repo "$repo" --parent "$epic" --label refinement \
  --title "Refine: <epic title>" \
  --body "Refinement task for #$epic. Direction and acceptance: docs/design/roadmap.md § <epic>. Done when feature issues exist that plausibly satisfy the epic's \"Done when\"."
```

### Everything is work

Refinement is work. Research is work. Both cost time, both can be blocked,
and neither is visible if it lives only in a document. So both get issues,
in the same tracker as delivery work, ordered by the same dependencies.

| Kind | Label | Done when | Produces |
| --- | --- | --- | --- |
| Research | `research` | the question is answered | evidence |
| Refinement | `refinement` | features cover the "Done when" | features |
| Feature | `feature` | the software works | software |

Research issues use the Research form and **carry an appetite**: how much
time this is worth, decided before starting. A spike without one is an
agent researching until it feels confident. If the appetite is exceeded,
that is a finding (the area is harder than the direction assumed) and it
belongs in the index as a possible change of direction, not in a request
for more time.

**Closing a research issue updates the index.** Its output is evidence:
items that were `derived` may now be `evidenced`, or now wrong.

**Do not ticket every unknown.** The test is whether not knowing changes
what you do next. If it does, it is a research issue. If it does not, it
is a recorded unknown in the index, and recording it is enough.

### Where research output lives

- **The working** (benchmarks, notes, dead ends) stays in the research
  issue as comments. Never create a per-spike document in the repo.
- **The verdict** goes in the closing comment, a few lines, so the issue
  is self-contained later.
- **The consequence** goes in the index, because the index holds the
  argument and a spike that changes the argument must change it.

The tracker is working memory; the repo is the record. When the work
lands, ask: what did the spike teach that the code does not now show?
Nothing: let the issue decay. Something a reader needs: commit it as
documentation, through review. A killed direction: an ADR. An
`evidenced` locator that points only at a spike issue is on borrowed
time; repoint it at code, a test or a committed document once the work
merges. The same question is asked of a feature's spec at merge (see
CONTRIBUTING.md, pull requests).

### Epic acceptance: "Done when"

Every epic carries a "Done when", written at charting time, before any
feature exists, as an **outcome**: what becomes true for someone once
this epic is real. Outcomes are writable without evidence, which is why
they belong at direction resolution where specifications do not.

**Freeze it through refinement.** It is the independent check on the
decomposition; rewriting it to match the features you happened to create
destroys the only thing it was for. If it turns out wrong, that is a
change of direction: record it in the index with the reason. The epic
form's release highlight ("What changes for the user") is a different,
editable field; do not conflate them.

An epic closes when its "Done when" is met, not when its sub-issue count
reaches zero.

## Packages

A package is what ships in one cut. Milestones are the fleet's package
object because the release workflows read them: Release Prepare warns
when the current package still has open items, and Release closes it.

- **Title `NNN name`.** A three-digit ordinal with gaps (`010`, `020`,
  `030`) and a content name (`010 okf-read`, `020 template-bump`). Never
  a version number: the number is computed by knope from what lands, so
  a title that predicts it goes stale the moment a breaking change lands
  early. Insert with `015 name`; reorder by renaming; the ordinal is not
  "distance from now", so nothing shifts when a package closes.
- **The current package is the lowest open ordinal.** The Milestones page
  sorts by "Recently updated" by default; choose "Alphabetical" to see the
  sequence, or read it from the index.
- **Kind is an index claim.** The index lists packages in order with the
  kind you intend (major, minor, patch). When a breaking change lands in
  a minor package, the edit is one word and no rename anywhere. There is
  no gate: a major is released when a breaking change merges. Collecting
  breaking changes into one package is a convenience you exercise by not
  merging, or by shipping the non-breaking half now and filing the
  breaking half as its own issue labelled `breaking`.
- **Membership is a commitment.** An issue is in a package only if you
  would commit to it shipping in that cut. Everything else is backlog,
  which is *no milestone*, sitting under its epic. Never create a
  `Backlog` or `Future` milestone: it re-poisons the one question
  milestones answer. Bugs default to the current package when they should
  ship next.
- **Epics carry no package** unless the whole epic ships atomically in
  that one cut, because sub-issues inherit the parent's milestone when
  linked. Set it on the children instead.
- **At the cut** the release workflow prepends the computed version to
  the title (`v4.3.0 okf-read`), unassigns any still-open items and lists
  them in the job summary, and closes the milestone. A failure warns and
  leaves the milestone open for recovery with the helper's `--resume`
  mode (see the `releasing` skill). Workflow retries and `--resume`
  select only a reservation already carrying that version; they never
  choose a new package. Re-commit leftovers
  to the next package deliberately; nothing moves them for you.
- **Horizon.** Create packages only as far ahead as you can genuinely see
  them, typically two or three. Beyond that, order lives in the index and
  the epic graph. Three or four features per minor is a sizing guide for
  slicing, not a rule; release when value has accumulated (see the
  `releasing` skill).

```bash
gh api "repos/$repo/milestones" -f title="020 okf-read" \
  -f description="Minor. OKF read semantics; see docs/design/roadmap.md."
gh issue edit "$n" --repo "$repo" --milestone "020 okf-read"
```

### Where order lives

Order lives in two places that never make the same claim:

- **The graph** orders executable work through native issue dependencies
  (`gh issue create --blocked-by`, `gh issue edit --add-blocked-by`,
  `is:blocked` to find bottlenecks). Refinement issues are executable, so
  direction-level ordering lives here too: mark `Refine: B` as blocked by
  the feature in A that must exist before B can sensibly be shaped.
- **The index** argues about ideas and lists the package sequence, at a
  resolution the graph cannot carry: why this direction, why we lean this
  way, what we do not know.

Encode only the edge that carries information. A feature in B blocked by
`Refine: B` is tautological. Do not try to order epics structurally; the
dependency almost always sits in a feature beneath the epic.

### What the index must not hold

The index carries direction, the ordering argument, the package sequence
with kinds, and epic-level unknowns. It never carries state: no
percentages, no open/closed counts, no in-flight lists, no delivery dates.
Dates on evidence and revision notes describe the record, not a schedule.
GitHub already tracks state correctly and for free. An index that tracks
state churns constantly and goes stale invisibly; one that carries only
argument changes when the thinking changes, which is the only diff worth
reading.

## Provenance

A roadmap is agent synthesis. Left unmarked, it reads next session as
settled user intent. Tag every index item:

- `stated` — the user said it. Never revise unilaterally; if evidence
  contradicts it, surface the contradiction and let the user decide.
- `derived` — your synthesis. Revise in place at any refinement.
- `evidenced` — read from code or repo, with the locator. Re-check the
  locator on revisit; one that no longer resolves is now `derived`.

Default to `derived` for anything you generate. The index header says it
is agent-authored synthesis, not a record of decisions. Anything posted to
GitHub carries the accurate agent attribution the authoring skill requires.

## Charting a roadmap

1. Establish the ambition and the constraints from the user. Ask; do not
   infer. Anything inferred is `derived` and marked so.
2. Propose epics as areas of intent. Descriptions stay short. If you
   cannot describe an epic without naming files or schemas, it is a
   feature, not an epic.
3. Write each epic's "Done when" now, as an outcome, while no features
   exist to bias it.
4. For each epic, name the unknowns: what would we need to learn before
   this is even shapeable? Give each a resolved-by pointer; where nothing
   planned resolves one and not knowing changes what you do next, file a
   research issue with an appetite and point at it.
5. Argue the order. The useful argument is **information gain**: X before
   Y because X cheaply resolves Y's largest unknown. Order justified only
   by dependency is a Gantt chart.
6. Slice what you can genuinely see into packages, ordinal-named, with a
   kind and one line of argument each.
7. Write the index. File the epics with the Epic form, each with its
   refinement sub-issue and nothing else. Create no feature issues.

## Refining an epic

Refinement turns an epic into feature issues that start as not ready.
This is ordinary agile refinement; calling it that helps reviewers.

Hold the evidence rule: features may be incomplete and marked not ready;
they may not be invented.

- Use the Feature form's headings (forms are enforced in the web UI only
  and bypassed by `gh issue create`; follow them deliberately).
- Parent each feature to the epic with `--parent`; set dependencies with
  `--blocked-by` as you go.
- **Check for blockers outside this epic**, including in other packages.
  This is the most commonly missed step.
- **Check coverage against "Done when".** Would every feature here
  closing actually satisfy it? If there is a gap, add a feature or record
  the shortfall; never edit the criterion to fit.
- Close the refinement issue only once the coverage check holds.
- Refinement may contradict the direction: the graph wins. Rewrite the
  argument in the index; never bend the graph to match the prose.

Then run `references/refinement-review.md` before finishing.

## Packaging ready work

Slice ready features into deliverable cuts, drawing from several epics
when useful. Keep research and refinement visible as work; unresolved
questions that decide scope belong ahead of committing delivery to a cut.
Give each package an ordinal, a content name and an intended kind in the
index. Link its milestone and argue the order. Three or four features per
minor is a sizing guide; release when useful work has accumulated.

## Revisiting

A roadmap that is not maintained is worse than none. On revisit:

- What closed, and which unknowns did it resolve? Which `derived` items
  are now `evidenced`, or now wrong?
- Does the graph now contradict the ordering argument?
- Did a package close with leftovers? Re-commit them deliberately or
  leave them in backlog; say which in the index revisions.
- Did any research issue exceed its appetite? That is a finding about the
  direction.
- For any epic whose sub-issues are all closed: is "Done when" actually
  met? An empty issue list is not delivery.
- Does any evidence now contradict a `stated` item? Surface it.

Update the argument; note what changed and why under the index's
revisions section. Do not rewrite history.

## Handing off

When exactly one feature becomes next and is marked ready, this skill is
finished. Hand off to the project's feature-design and implementation
workflow (for example, brainstorming and writing-plans when installed).
Do not require a plugin the project has not installed. The spec is reviewed in
session and then included in the pull request, not committed (see
CONTRIBUTING.md). Equally, a defect or something that needs investigating
is not roadmap work; that is the `authoring-issues-prs` skill.

## Stop rules

At **direction** resolution, refuse to write, and say which rule applies:

- file paths, module names, API signatures, schema fields
- test scenarios, or acceptance criteria at feature level
- week numbers, dates, effort in hours or points, version numbers
- pinned library or tool versions
- any claim you would have to go and research in order to make

At any resolution, refuse:

- an unknown with no resolved-by pointer, no research issue, and no note
  that not knowing it changes nothing
- a research issue with no appetite
- an ordering claim with no argument attached
- an untagged index item, or a `stated` tag on something the user did not say
- state written into the index
- a milestone due date, a version-named open milestone (except one being
  finalized by the release workflow), or a milestone
  that is not one cut
- an epic with no "Done when", or one edited during refinement to match
  the features that now exist
- an epic with no refinement issue, or a refinement issue carrying the
  epic body rather than a pointer

Name the violation and give the concrete fix.

<!-- DOMAIN-ROADMAPPING-START -->
<!-- Project-specific roadmapping conventions (extra issue kinds, package
     naming notes, where cross-repo dependencies are recorded) go here.
     This block survives copier update. -->
<!-- DOMAIN-ROADMAPPING-END -->
