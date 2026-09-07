# Roadmap: direction after 4.2

> **Agent-authored synthesis, not a record of decisions.** Charted 2026-09-07
> at `60111e3f`, with the v4.2 milestone empty and 4.2.0 about to be cut, in
> answer to the owner's request to triage the open backlog and lay out several
> milestones forward. Every item carries a provenance tag:
>
> - `stated` — the owner said it (quoted or closely paraphrased, with where).
> - `derived` — this document's synthesis. Revise freely at any refinement.
> - `evidenced` — read from the repository or an issue thread, with the locator.
>
> Anything untagged is `derived`. A `stated` item is never revised here without
> the owner; if evidence contradicts one, the contradiction is recorded under
> *Open decisions* and left for them.

This page holds the **argument**: which direction, in what order, and what is
not yet known. It holds no state. Which issues sit in which milestone, what is
open, what is closed, and how far along anything is all live on GitHub and are
read from there. When the thinking changes, this page changes; when an issue
closes, it does not.

Companion pages: `release-vision.md` covers the release *mechanism* (how a
version is computed and shipped); this page covers *what* the next releases
carry and why. The architectural studies referenced below live on the branch
behind PR #1152 until that PR merges.

## How milestones are read in this repository

`evidenced` (`CONTRIBUTING.md`, "Epics"; the milestone list): version-titled
milestones are **release payloads**. "Safe to cut" reduces to "no open issues
in that milestone", so adding an issue to one is a shipping commitment and,
until it closes, a release gate. Absence of a milestone is the backlog; there is
deliberately no `Backlog` milestone.

`evidenced` (milestone `git`, epic #1313): a theme-titled milestone is an
**epic**, whose representative issue is the epic itself. Its acceptance
criterion is the epic's "What changes for the user" section, written before any
child shipped. It is not a release gate and carries no due date.

Neither kind carries a due date. `derived`: a due date is the only ordinal
GitHub offers, and setting one converts a set of intentions into a schedule
this page has no evidence for.

## Constraints

All `stated` by the owner while requesting this roadmap (2026-09-07) unless
another locator is given.

1. The `v5` milestone as it stood was "generally a *not v4.2* milestone". Its
   membership carries no signal about what the next major contains.
2. "All new high priority bunch just bundled in the next release."
3. 4.1 → 4.2 shipped six features, which "is too much"; "there is nothing
   blocking us from making more minor releases", so a **soft target of 3–4 new
   features** per minor.
4. "Operator breaking changes are a major bump and we like to bundle those
   together and not too often."
5. `stated` (#1232, owner comment 2026-08-30): for v5 there is no plan to
   change the MCP surface; multi-vault stays plumbing-only and the surface
   change "will only come in a much later major".
6. `stated` (#1245, owner comment 2026-08-31): update propagation is "mostly
   no, with one exception": into a vault instance, no; into a *method pack*
   (templates, conventions, prompts), "maybe". That split, not the tooling, is
   the useful boundary.
7. `stated` (#1131, owner comment 2026-08-27): the cross-instance answer is one
   line in the server instructions naming the URL this instance serves, and
   "let the AI do the rest of the inference".
8. `stated` (#650, owner decision 2026-06-17): state-layout separation is an
   opt-in recipe, not a default change, and not a breaking-change item.
9. `stated` (epic #1313 body, recording the git-robustness study): many
   instances run in the field and "a vault is a vault", so one repository-shape
   policy applies to every instance and nothing the CLI git layer serves today
   is withdrawn. Milestone `git` is about the libgit2 backend, not about
   everything git.

`derived` from 3: bugs and `docs:`-typed work do not count toward the feature
budget. Under the commit convention only `feat` reaches the Features section of
the changelog, so a minor that carries a dozen fixes and three features still
reads as three features.

## Direction, by tier

### v4.3 — link fidelity and git operability

**Acceptance criterion** (`derived`, outcome-level, frozen through refinement):
a vault whose links Obsidian wrote from another folder resolves them to the
notes Obsidian resolves them to, and the broken-link report agrees with
Obsidian's; an operator of a managed-git deployment can see from the server,
without host access, whether the clone is still reaching its remote.

**Why this first.** `evidenced` (#1383, observed on Obsidian 1.13.7 with output
on #1358): the scanner resolves a markdown link relative to the source note
while Obsidian looks it up vault-wide, so an Obsidian-authored markdown-link
vault carries broken links here whenever source and target sit in different
folders. That is the highest-impact defect the 4.2 Obsidian verification pass
surfaced, and it reaches any Obsidian user who has wikilinks turned off and
links across folders. A smaller shape defect from the same pass (#1384) and two
git diagnostics bugs found while reviewing #1363 (#1381, a startup warning that
fires where the environment already satisfies git; #1382, one log line for two
resolver exits) are cheap to carry alongside; the raw-stderr decay (#1349) sits
on the same package's failure lines and belongs in the same commit series.

`derived`: the feature budget goes to git *operability*, because the incident
behind #1287 showed that a clone can silently strand writes for hours. Exposing
the sync state over MCP (#1293) comes **before** any option to refuse stranded
writes (#1299): the first tells us whether a "terminal" condition is even
definable from what the server observes, which is the open question the second
turns on. Constraint 7 makes the cross-instance line (#1131) a small feature
that fits the same minor. The shared-vault conflict guidance (#1294) and the
"is this the right tool" page (#1378) are `docs:`-shaped and cost no budget.

**Known unknowns.**

- Whether the vault-wide lookup should *replace* source-relative resolution or
  apply only as a *fallback* when the source-relative target does not exist.
  This decides OKF vaults' behaviour (their spec recommends root-absolute
  links, which resolve today). Owner decision; resolved by the decision
  comment on #1383 before any code.
- Whether the resolver honours `.gitattributes` merge drivers today. Not
  researched here; resolved by whoever picks up #1294, whose scope is exactly
  that question.
- Whether a "terminal" push failure is distinguishable from one the next pull
  reconciles. Resolved by shipping #1293 and reading what it reports in the
  field before refining #1299.

### v4.4 — creating a vault, and knowing the envelope

**Acceptance criterion** (`derived`): an operator with no vault reaches a
running, indexed, git-managed server by following one page, on either
supported host, without discovering ordering constraints by trial and error;
and an operator can read from the server how large their vector store is and
work out from the published docs whether they are near its limit.

**Why this order.** `evidenced` (#1177, #1243, #1245): every guide assumes a
vault exists, and the mechanism debate has changed shape twice. Constraint 6
has already settled the largest question (propagation into an instance is
"no", into a method pack "maybe"), which removes the copier route's unique
advantage for the primary path
and leaves the CLI scaffold and the guide as the shape most consistent with
the owner's answer. `derived`: the guide (#1177) cannot be finished before the
mechanism is chosen, because a scaffold command changes the guide from a paste
list into a command plus host steps.

`evidenced` (#1368 measurement thread, #1377, #1370): the vector store's
resident footprint is `4 × chunks × dimensions` bytes, memory binds before
latency, and the owner's own growth expectation lands the larger deployed
vault at the container's memory line. Nothing on the tool surface reports the
model, the vector width or the sidecar size, so no operator can locate their
own deployment on that curve. `derived`: the observability slice of #1377
(report those three) is the cheapest change in the whole cluster and the one
that #1378's sizing section and #1370's corrected risk row both need; storage
levers (half-precision, memory mapping) follow only when a deployment is
observed to approach the line.

**Known unknowns.**

- Which creation mechanism the project serves (#1245). Owner decision; it is
  the refinement gate for this tier. Resolved by the decision comment on
  #1245, after which one of #1243 / #1245 closes.
- Whether any external deployment is already past the memory line. Nothing
  reports it and there is no telemetry; resolved, as far as it can be, by the
  observability slice shipping and operators reading it. Not knowing does not
  change the order above.
- Whether attachments should become link-graph nodes independently of
  non-markdown documents entering the index (#1359 vs #1234). Not knowing
  changes what gets built, so this is a research question, not a recorded
  worry. Resolved by a bounded spike proposed in the triage table
  accompanying this page; until it exists, #1359 is not refinable into either
  tier.

### v5.0 — the breaking bundle

**Acceptance criterion** (`derived`): every default flip and library removal
announced during 4.x lands in one upgrade with one migration page, and the
project runs on the current FastMCP line through the current template.

**What forces it.** `evidenced` (#1271, comment 2026-09-04): the template's
v8.0.0 carries two `!` commits (FastMCP 4 adoption and a compose quick-start
rework), so adopting it forces the major regardless of anything else. That
adoption is the anchor. `derived`, from constraint 4: the announced breaking
changes (#1136's default flip, the deprecated constructor surfaces removed in
#1225 and #1236) ride with it rather than each earning their own major.

`derived`: the major is cut when the FastMCP 4 adoption is *wanted*, not when
the removals are ready. Nothing in the removals is urgent; the FastMCP line is
what eventually becomes a maintenance cost. Until then the 4.x minors keep
shipping, and the v5 milestone holds only work that genuinely breaks an
operator or library surface under the two-part test in `AGENTS.md`.

**Known unknowns.**

- Whether `OKF_VERIFY=elicit`, the shipped default, survives the protocol era
  FastMCP 4 clients negotiate, and whether FastMCP 4's resource path screening
  touches this project's resource URIs. Both are marked unverified on #1271.
  Resolved by the adoption PR itself; if the elicitation default breaks, that
  is an operator-visible consequence to document in the same migration page.
- Whether the FastMCP subpath discovery bug behind #152 is fixed in the
  FastMCP 4 line. Not researched here; resolved by re-reading #152's upstream
  tracker when the adoption PR is opened.
- Whether three CLI-layer items are breaking (see *Open decisions*).

### Milestone `git` — the opt-in libgit2 backend (epic #1313)

Already charted by its epic, whose "What changes for the user" is the
acceptance criterion; constraint 9 fixes its scope. This page adds only an
ordering argument.

`derived`, information gain: the real-git scenario suite (#1307) comes first.
It is the one child that pins behaviour for *both* backends, so it de-risks
the libgit2 backend (#1309) and the CLI layer's own hardening (#1310, #1311)
at once, and it is the only thing that can show whether the September bug
classes are systemic to text-driven git or incidental. Opening the three
seams (#1308) follows, because the backend cannot be wired without them.

`derived`: the CLI-layer decay trio (#1310 shared argv/output layer, #1311
merge-tree reconciliation, #1312 declared git floor) stays outside the
milestone, as its epic says, and outside the release tiers. Under the
structural-health practice these are pulled into whichever minor next touches
that code, not scheduled on their own. #1290 is a class-1 instance of #1310
and should be fixed *in* that shared layer rather than at its five sites.

### Platform tier — no milestone

`evidenced` (#1232, #1233, #1234, #1367, #1369, #225; studies on PR #1152):
multi-vault plumbing, per-user permissions, non-markdown documents, AST
chunking and attachment metadata together map the project's potential as a
layered vault platform. Constraint 5 places any surface change for
multi-vault in "a much later major". `derived`: none of these is refinable
into a release today, and under this repository's milestone scheme an
unrefinable idea belongs in the backlog, tagged `future`, not in a release
milestone. Whether they deserve a theme milestone of their own (the `git`
pattern) is an owner call; both options are offered in the triage table.

`derived`, dependency: #1367 and #1369 both hang off #1234, and #1359's
research question (above) may too. The single largest information-gain item
in this tier is therefore a decision, not code: whether "vault" is going to
mean *documents* rather than *markdown files*. That decision is what would
make the tier refinable.

## The ordering argument in one place

`derived`. We lean towards:

- **#1307 before #1309 and before #1310/#1311**, because a real-git suite is
  the only evidence that can separate systemic from incidental in the git bug
  history.
- **#1293 before #1299**, because observability tells us whether "terminal" is
  definable before we refuse writes on it.
- **The #1245 decision before #1243 and #1177**, because the guide's shape is a
  function of the mechanism.
- **#1377's observability slice before any storage lever and before #1378's
  sizing section**, because the envelope cannot be stated in public without the
  numbers the server reports.
- **The #1234 decision before refining #1359, #1367, #1369**, because all
  three change shape depending on it.
- **v5.0 when FastMCP 4 is wanted**, because that is the one change that
  forces the major; everything else in it is a passenger.

Order justified only by dependency would be a schedule. Each edge above is
kept because the earlier item cheaply resolves the later item's largest
unknown.

## Open decisions for the owner

Recorded here so they are not re-derived. None is made on this page.

1. **#1383 resolution semantics**: vault-wide lookup as fallback or as
   replacement. Decides OKF vault behaviour.
2. **#1245 creation mechanism**: CLI scaffold, template repository, copier, or
   a layering of them; constraint 6 already rules out propagation into a
   vault instance.
3. **Breaking or not, under the two-part test**, for three CLI-layer items:
   #1240 (a symlink's extension read from the target rather than the link name
   changes what an allowlist admits), #1311 (a merge commit in place of a
   rebased linear history changes the history shape operators see), #1312 (a
   declared git floor; the Debian release behind the image packages a git
   above the floor the study names, though #1312 records that the built image
   itself was not checked, so likely not breaking). If any is breaking it
   belongs in the v5.0 bundle by constraint 4.
4. **The platform tier's home**: `future` label in the backlog, or a theme
   milestone on the `git` pattern.
5. **Epic #809**: all six children are closed and no acceptance criterion was
   written at creation; closing it is a judgement that the Paper redesign is
   delivered, with the mobile display bug (#859) continuing as a standalone
   defect.
6. **#1368**: the issue records a technique and a revisit threshold, and its
   body says closing as "not now" is a valid outcome; its measured
   consequence already lives in #1370, #1377 and #1378.

## Revisiting this page

On each return, in this order:

- What closed since the last revision, and which unknown above did it resolve?
  Move the affected `derived` claim to `evidenced` with its new locator, or
  strike it.
- Does the dependency graph on GitHub now contradict an ordering argument here?
  The graph wins; rewrite the argument.
- Did anything get postponed out of a tier? Record why (an unknown got worse,
  or something else became more valuable), not just that it moved.
- Does any evidence now contradict a `stated` constraint? Surface it under
  *Open decisions*; do not edit the constraint.
- For a milestone whose issues are all closed: is its acceptance criterion met?
  An empty issue list is not delivery.
