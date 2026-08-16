---
name: writing-release-notes
description: >-
  Use when drafting or revising a release-notes page under docs/releases/ —
  the Release Notes workflow invokes it after every stable release, and a
  human may invoke it for a re-draft or backfill. Walks the API-driven
  research fan-out (commits to PRs to linked issues, never commit subjects),
  the evidence contract, the per-minor page format, and the Vale loop, and
  ends with pages written for a pull request — never a direct push.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Writing release notes

The pages under `docs/releases/` are the canonical human-facing narrative of
each release; the GitHub release body is a summary plus a link to them, and
`CHANGELOG.md` is the machine-written commit-level audit trail. This skill is
the contract for producing a page: what to research, what counts as evidence,
what the page looks like, and which gates it must pass. Every requirement
below was established empirically by trial runs recorded on
pvliesdonk/fastmcp-server-template#347; where this skill says "must", a trial
produced the failure the rule prevents.

## Inputs

You are given, or must derive first:

- `TAG` / `VERSION` — the stable release (e.g. `v3.2.0` / `3.2.0`).
- `MINOR` — the series (e.g. `3.2`); the page is `docs/releases/MINOR.md`.
- `PREV` — the highest stable tag strictly below `TAG` (series-aware,
  `sort -V`); empty on a first release.
- Mode — **new page** (first stable of the minor: write the whole page) or
  **patch append** (the page exists: add one dated section for this patch,
  inside the patch sentinels; leave the rest of the page alone unless it is
  factually wrong).

## Non-negotiables

1. **No evidence, no narrative.** Every causal claim ("X was slow because
   Y", "this was driven by user demand") must trace to a linked issue or PR
   you actually read, and the page links it. A claim you cannot source gets
   dropped, not hedged. Concrete numbers appear only verbatim from a source.
   Attribute quoted judgements (for example "[per the maintainer]" with the
   link) rather than presenting them as your own analysis.
2. **Output is a pull request, never a push.** The dominant failure mode is
   plausible-but-wrong rationale, and a confabulated "why" on a published
   docs site is worse than no narrative. A human merges the page; write the
   PR body to make that evidence check easy.
3. **Never touch `CHANGELOG.md`.** It is machine-generated and stays that
   way. The two artifacts answer different questions: "what landed" versus
   "should I upgrade".
4. **Never write from `git log`.** Commit subjects are the input ceiling this
   whole pipeline exists to break. A draft whose sections mirror the commit
   list is the recognised failure ("a haiku summary of the git log") — if you
   notice the page reading like grouped commit subjects, the research phase
   was skipped; go back.

## Research procedure

### 1. Enumerate the range through the API, not local git

- Commit list: `gh api "repos/OWNER/REPO/compare/PREV...TAG"` (paginate past
  250 commits; if PREV is empty this is the whole history — fall back to the
  release's own compare link or the full commit list). The compare API is
  authoritative; a shallow local clone silently truncates ranges.
- Commit to PR: `gh api "repos/OWNER/REPO/commits/SHA/pulls"` — never regex
  `(#N)` out of subjects (breaks on squash subjects without the suffix and
  on passing mentions).
- PR to issues: the PR's closing-issues references (GraphQL
  `closingIssuesReferences` on the PR object) — never grep `Closes #N` from
  bodies; UI-made links have no text form.

### 2. Group before you read deeply

Build the theme map from structure first:

- **Epics are the primary grouping input.** For each linked issue, check for
  a parent (native sub-issues; epics carry the `epic` label). An epic whose
  children closed in this range is one theme, and its issue form's "What
  changes for the user" paragraph — written at epic creation — is the seed
  for both that theme's section and the release summary. Verify that
  paragraph against what actually landed; edit it, do not just copy it.
- The release **milestone** (or the `ships-atomically` label) marks work
  that was planned to ship together — treat it as one story.
- Everything else falls into themes by subject (each significant feature,
  contributor-driven changes, platform/infrastructure work, fixes). Where a
  repository has no epics or milestones, this thematic grouping is the whole
  map — degrade gracefully; do not invent structure.

### 3. Fan out — one research pass per theme

A single agent holding the whole range regresses to commit subjects, because
reading the linked issues does not fit alongside drafting. Dispatch **parallel
research subagents, one per theme**. Each walks its theme's commits → PRs →
issue bodies **and comments**, and returns a compact structured brief:

- what shipped, in user terms, with the PR/issue links;
- the motivating problem, quoted from the issue body where possible;
- who reported or drove it: the issue's `user.login` and
  `author_association`, not just commit authors;
- exact enablement: env vars, defaults, config keys, tool names;
- any concrete numbers (timings, sizes, counts) with their source;
- the feature's docs page(s), and whether they changed in this range.

The synthesiser writes **from the briefs only**.

### 4. Attribution comes from issue authorship

Commit authors miss most outside contributors: people who file the issue that
the maintainer then implements are invisible in `git log`. Report reporters.
Corollary: do not infer outside demand from an issue the maintainer filed —
if an outside PR preceded the maintainer's tracking issue, the honest
attribution is the PR.

### 5. Synthesis pass, with licence to regroup

After the briefs return, look across them before writing: separate
deliverables may be one recipe with several delivery channels (write the
"which do I want" paragraph, not three bullets), and a fix filed under one
scope may really belong to another theme's story. Conventional-commit scopes
are not the outline; the reader's questions are.

## Writing the page

Each significant feature answers, in this order:

1. **What is it** — for someone who has never heard of it.
2. **Why does it fit this server** — almost always a verbatim quote from the
   motivating issue; it is never in a commit.
3. **How do I enable it** — exact env vars, defaults, config; a reader should
   be able to act without opening another page (but link the guide too).
4. Then, and only then, the tool/API surface it added.

A list of tools shipped is the failure mode, not the deliverable.

### Upgrade / breaking-changes section

Do **not** trust `!` markers or `BREAKING CHANGE:` footers — trials found
them wrong in both directions. Derive the section from the actual surfaces
between `PREV` and `TAG`, classified against the breaking-change policy in
`CLAUDE.md` (operator surface and public library interface, assessed against
the last stable):

- import surface: diff `tests/public_import_surface.txt` at the two tags
  (`git show PREV:tests/public_import_surface.txt`);
- operator surface: diff `.env.example` / the config surface at the two tags;
- tool surface: compare the registered-tools docs at the two tags (not
  breaking on its own, but worth a migration note when behaviour moved).

Where your classification disagrees with a commit's marker, follow the
classification and say so in the PR body.

### Docs links — verify before linking

Every feature with a docs page links it, not only the flagship. Locate pages
rather than assuming them, and **check each linked page is current**: a
feature whose code changed in the range while its guide did not is a
candidate staleness bug. Do not link a page you found stale as if it were
authoritative; list every staleness candidate in the PR body (observation
voice, `[unverified]` where you did not confirm) so the maintainer can file
or fix — do not file issues yourself from an automated run.

## Page format

One page per minor: `docs/releases/MINOR.md`. Patch releases append a dated
section to the same page, so a minor's story stays in one linkable document.
Skeleton for a new page (the comment markers are load-bearing — the publish
workflow extracts summary blocks by tag, and later patch drafts insert only
inside the patch sentinels):

    # 3.2

    <!-- RELEASE-SUMMARY v3.2.0 START -->
    One short paragraph, user-facing: what this minor means for someone
    deciding whether to upgrade. Seeded from the epic form's summary where
    one exists. This block becomes the GitHub release body.
    <!-- RELEASE-SUMMARY v3.2.0 END -->

    ## <theme sections, per the writing rules above>

    ## Upgrading

    <the derived upgrade section; omit heading if truly nothing>

    <!-- PATCH-RELEASES-START -->
    <!-- PATCH-RELEASES-END -->

A patch section goes inside the patch sentinels, oldest first, as:

    ## v3.2.1 — 2026-08-20

    <!-- RELEASE-SUMMARY v3.2.1 START -->
    One paragraph: what this patch fixes and who should care.
    <!-- RELEASE-SUMMARY v3.2.1 END -->

    <evidence-linked detail, same rules as above>

For a **new** page, also add the minor to the list in
`docs/releases/index.md` (newest first, between its markers; the first real
entry replaces the seeded placeholder line). Do not edit
`mkdocs.yml`: the navigation is project-owned. If the nav has no
Release Notes entry yet, note that in the PR body instead of adding one.

## Quality gates — run them, do not assume them

- **Vale** is the prose gate, including the `ai-tells` LLM-prose detector:
  run `vale docs/releases/` (the binary and synced style packs are provided
  by the workflow; locally, `vale sync` first) and iterate until clean.
  Distinguish the two hit classes: real prose findings get rewritten;
  domain vocabulary the spell-checker does not know goes into
  `.vale/styles/config/vocabularies/Base/accept.txt` (add it in the same
  change) — never contort prose around a vocabulary hit.
- **Strict docs build**: `uv run mkdocs build --strict` must pass.

## Output

Leave the working tree holding only: the release page, `docs/releases/index.md`
(new-page mode), any `accept.txt` additions, and your proposed PR body in
`.release-notes-pr-body.md` at the repository root (the workflow's mechanical
step commits the docs paths, opens the PR from the body file, and discards
the body file). The PR body must carry:

- the release tag and compare link;
- a claim-by-claim evidence summary (or a statement that every inline link
  is the evidence), so the human review is a link check, not archaeology;
- your breaking-change classification and any disagreement with `!` markers;
- docs-staleness candidates found in the research;
- anything you could not source and therefore left out.

**Honest failure beats confident junk.** If the range has too few linked
issues to support a narrative, write the modest factual page the evidence
supports. If you cannot produce even that, change nothing and say why in
your final report — the release stands with its interim body, and nothing
downstream breaks.
