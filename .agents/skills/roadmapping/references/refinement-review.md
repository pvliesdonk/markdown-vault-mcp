# Refinement review

Read this before finishing a charting or refinement pass.

This review is the **inverse** of a plan reviewer. A plan reviewer asks
whether the work is specified well enough to execute. That is the right
question for a plan and the wrong one here, and applying it is what produces
the failure this skill exists to prevent: the reviewer demands detail, the
author invents it, and the artefact acquires a confidence nobody earned.

**Review at the declared resolution, not at the highest resolution you can
imagine.** An item at direction resolution is *supposed* to be thin. Thinness
is not a defect to be fixed. Missing structure is.

Work through the checks below. For each finding, name the check, quote the
offending line, and give a concrete fix. If a check passes, say so in a few
words and move on; do not pad.

## 1. Over-specification

For every item, compare its content against its derived resolution.

At direction resolution, flag any of: file paths, module names, API
signatures, schema fields, test scenarios, acceptance criteria, week numbers,
dates, effort estimates, pinned versions.

The fix is always the same shape: delete the detail, and if it was load
bearing, restate it as a known unknown with a resolved-by pointer.

## 2. Research-to-promote

The hardest check, and the most valuable.

Look for claims that are concrete, correct, and *unasked for*. Signs: a
specific function or file named in an item nobody has scoped; a technology
choice asserted with no decision recorded; a confident statement about how an
existing subsystem behaves in an area the conversation never touched.

Ask: could the author have known this without going to look? If they went to
look, that is the evidence rule broken, even though the result is accurate.
Accuracy is not the standard here. Warrant is.

Fix: demote to a known unknown, and note that the answer is cheaply
obtainable. Cheap to answer later is a useful property to record. It is not a
licence to answer it now.

## 3. Unknowns are structural

Every unknown needs a resolved-by pointer to the work whose completion answers
it, or an explicit note that nothing currently planned will resolve it.

Flag unknowns phrased as anxieties ("we may need to think about caching")
rather than questions with an owner and a resolution path.

Flag any unknown that is load-bearing for the ordering argument but has no
research issue behind it. This is the gap that sinks roadmaps quietly: the
whole sequence rests on an assumption nobody was ever tasked with checking.
The test is whether not knowing changes what happens next; if it does, the
finding out is work and needs a ticket with an appetite, not a bullet point.

Flag the reverse too. If every unknown has become a research issue, the
tracker is filling with planning work and the delivery signal is being
diluted. Unknowns that change nothing should stay recorded, not ticketed.

Flag the absence of unknowns. An epic at direction resolution with no
unknowns has not been thought about; it has been described.

## 4. Order is argued, not asserted

Every ordering claim needs a reason attached, and the reason should be
information gain: this first because it cheaply resolves that one's largest
unknown.

Flag ordering justified only by dependency, which is a schedule, and ordering
justified by nothing, which is a preference wearing a plan's clothes.

Flag any package due date, any version-named open package (except one
being finalized by the release workflow), and any package that is not one
release cut. An epic may span several packages. Flag any sequence that implies a total
order over epics, given that epics can be reordered, postponed, or
run in parallel with features woven between them.

## 5. Graph versus argument

The two representations make different claims and must not contradict.

- Does any dependency in the issue graph imply an order the index argues
  against? If so the graph wins: the argument gets rewritten, not the graph.
- Does the index state anything that is really state rather than argument
  (counts, percentages, in-flight lists)? Remove it; GitHub holds it already.
- Does the index claim an order over executable work that the dependencies do
  not encode? Either encode it or stop claiming it.

## 6. Acceptance coverage

Read the epic's "Done when", then read the features under it,
and ask the blunt question: if every one of these closed tomorrow, would the
criterion be met?

Decomposition is lossy and loses things silently. This is the check that
catches it, and refinement is the only moment when the fix is cheap.

Flag:

- parts of the criterion no feature addresses
- a criterion that has been edited during this pass. It exists to be an
  independent check on the decomposition, so rewriting it to match the
  features destroys its only function. If it is genuinely wrong, that is a
  change of direction and belongs in the index with a reason.
- a criterion written as a specification rather than an outcome, which
  usually means it was written after the features rather than before
- a refinement issue closed while coverage gaps remain open

## 7. Refinement issues

- Does every epic have one? An epic with no sub-issues reports
  a completeness it has not earned.
- Is any refinement issue carrying the epic body rather than a pointer to the
  index entry and the acceptance criterion? That recreates the duplicate
  source the index exists to prevent.
- Are direction-level ordering claims in the index encoded as dependencies on
  refinement issues where they could be? Prose that could have been a graph
  edge will drift; the edge will not.
- Are there tautological edges, such as a feature blocked by the refinement
  issue of its own epic? Remove them. They bury the edges that mean
  something.
- Are refinement issues distinguishable by type or label, so that `is:blocked`
  and dependency counts do not mix planning work with delivery work?
- Do research issues carry an appetite, and has any exceeded it without that
  being reported as a finding about the direction?
- Has any research issue closed without its evidence reaching the index? Its
  entire output is evidence; if the argument did not move, the work was
  wasted.
- Has anyone created a standalone document for a spike's working? It belongs
  in the issue as comments. Per-spike documents are the parallel-document
  sprawl that nobody reads.
- Is a finding still living only in an issue after the work it aimed at has
  merged? The tracker is working memory; the repo is the record. Anything not
  carried through review is not yet a fact of the project, and that is
  unfinished work rather than an archive.
- Was a provisional finding committed to the repo before the work landed?
  Review has not confirmed it, so it borrows an authority it has not earned.
- Could a verdict about the project's own code have been an executable check
  instead? A test that fails when the answer changes beats prose asserting a
  measurement nothing keeps honest.
- Do `evidenced` locators still point at issues for work that has since
  merged? Repoint them at the code, test, or committed document.
- Does the index restate a finding rather than linking to it? The index
  carries the argument plus a link. A number copied into it decays there,
  out of sight of the issue that owns it.
- Do `evidenced` claims carry a date or locator, so the next pass can tell
  whether they have decayed?

## 8. Cross-epic blockers

The most commonly missed check, because refinement looks at one epic
while the blocker lives in another.

For each new feature, ask explicitly whether anything outside this epic
blocks it or is blocked by it. Record what you checked, not just what you
found, so the next pass knows this was considered rather than skipped.

## 9. Provenance and attribution

- Is every item tagged `stated`, `derived`, or `evidenced`?
- Is anything tagged `stated` that the user did not actually say? This is the
  serious one: it will be cited back at the user as their own decision, and
  it removes the agent's own edit rights over content the agent invented.
- Has evidence turned up that contradicts a `stated` item, and been quietly
  worked around rather than surfaced? Users are wrong sometimes. Complying
  with a premise you have found to be false is the same miscalibration as
  inventing detail, pointed the other way, and it is much harder to spot
  later.
- Do `evidenced` items carry a locator that still resolves?
- Does the index header make clear it is agent synthesis rather than a record
  of decisions?
- Does anything posted to GitHub carry the operator's required attribution
  signature, verbatim?

## 10. Handoff boundary

- Is anything here actually a defect or an investigation rather than planned
  work? That belongs in `authoring-issues-prs`, not the roadmap.
- Is any item ready and therefore due to leave this skill for the
  project's feature-design workflow?
- Has anything been refined that had no reason to be refined yet? Parallel
  work makes multiple simultaneously refined epics legitimate, so the
  test is never positional. Ask whether the evidence existed, not whether the
  item was next in line.

## Verdict

Close with one of:

- **Calibrated** — resolution matches content, unknowns are structural, order
  is argued.
- **Over-specified** — list the items claiming more than they can support.
- **Under-structured** — resolution is fine, but unknowns lack pointers or
  ordering lacks argument.

Do not soften the verdict. An over-specified roadmap that passes review is
worse than one that fails, because the failure is the only signal that the
confidence is unearned.
