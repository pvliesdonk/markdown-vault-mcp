---
type: Reference
title: Pull request size and review effectiveness
description: What published review data, engineering-practice guides and coding-agent benchmarks say about how the size of a change (lines, files, concerns) affects review quality and an agent's ability to finish it, as the pull-request sizing rule relies on them.
subject_version: "Google eng-practices, SmartBear and LinearB guidance as of 2026-10; MSR/ICSE studies 2013-2022; SWE-bench 2024, Multi-SWE-bench 2025, SWE-Bench Pro 2025; Chroma and Anthropic context reports 2025"
valid_for: "Human review practice as published 2013-2026; coding agents of the 2025-2026 generation"
generated:
  by: process:researching-references
  at: 2026-10-17T11:30:00+00:00
stale_after: 2027-04-17T00:00:00+00:00
verified:
  - by: process:researching-references
    at: 2026-10-17T12:00:00+00:00
status: draft
sources:
  - id: google-small-cls
    title: 'Google Engineering Practices, "Small CLs"'
    resource: https://google.github.io/eng-practices/review/developer/small-cls.html
    accessed: 2026-10-17
  - id: google-review-speed
    title: 'Google Engineering Practices, "Speed of Code Reviews"'
    resource: https://google.github.io/eng-practices/review/reviewer/speed.html
    accessed: 2026-10-17
  - id: smartbear-best-practices
    title: 'SmartBear, "Best Practices for Code Review"'
    resource: https://smartbear.com/learn/code-review/best-practices-for-peer-code-review/
    accessed: 2026-10-17
  - id: bosu-2015
    title: 'Bosu, Greiler, Bird, "Characteristics of Useful Code Reviews: An Empirical Study at Microsoft", MSR 2015'
    resource: https://www.amiangshu.com/papers/CodeReview-MSR-2015.pdf
    accessed: 2026-10-17
  - id: sadowski-2018
    title: 'Sadowski, Söderberg, Church, Sipko, Bacchelli, "Modern Code Review: A Case Study at Google", ICSE-SEIP 2018'
    resource: https://sback.it/publications/icse2018seip.pdf
    accessed: 2026-10-17
  - id: rigby-bird-2013
    title: 'Rigby, Bird, "Convergent Contemporary Software Peer Review Practices", ESEC/FSE 2013'
    resource: https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/rigby2013convergent.pdf
    accessed: 2026-10-17
  - id: macleod-2016
    title: 'MacLeod, Greiler, Storey, Bird, Czerwonka, "Code Reviewing in the Trenches", Microsoft Research technical report MSR-TR-2016-27'
    resource: https://www.microsoft.com/en-us/research/wp-content/uploads/2016/05/MS-Code-Review-Tech-Report-MSR-TR-2016-27.pdf
    accessed: 2026-10-17
  - id: kudrjavets-2022
    title: 'Kudrjavets, Nagappan, Rastogi, "Do Small Code Changes Merge Faster? A Multi-Language Empirical Investigation", MSR 2022'
    resource: https://arxiv.org/pdf/2203.05045
    accessed: 2026-10-17
  - id: dibiase-2019
    title: 'di Biase, Bruntink, van Deursen, Bacchelli, "The effects of change decomposition on code review: a controlled experiment", PeerJ Computer Science 2019'
    resource: https://arxiv.org/pdf/1805.10978
    accessed: 2026-10-17
  - id: herzig-2013
    title: 'Herzig, "The Impact of Tangled Code Changes", MSR 2013 (Microsoft Research publication page, which lists Herzig alone; the paper names Zeller as co-author)'
    resource: https://www.microsoft.com/en-us/research/publication/the-impact-of-tangled-code-changes/
    accessed: 2026-10-17
  - id: kernel-submitting-patches
    title: 'Linux kernel documentation, "Submitting patches", section "Separate your changes"'
    resource: https://www.kernel.org/doc/html/latest/process/submitting-patches.html
    accessed: 2026-10-17
  - id: linearb-benchmarks
    title: 'LinearB, "Engineering Metrics Benchmarks"'
    resource: https://linearb.io/engineering-benchmarks/
    accessed: 2026-10-17
  - id: graphite-50-lines
    title: 'Graphite, "The ideal PR is 50 lines long"'
    resource: https://graphite.com/blog/the-ideal-pr-is-50-lines-long
    accessed: 2026-10-17
  - id: swe-bench-2024
    title: 'Jimenez et al., "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?", ICLR 2024'
    resource: https://arxiv.org/pdf/2310.06770
    accessed: 2026-10-17
  - id: multi-swe-bench-2025
    title: 'Zan et al., "Multi-SWE-bench: A Multilingual Benchmark for Issue Resolving", 2025'
    resource: https://arxiv.org/pdf/2504.02605
    accessed: 2026-10-17
  - id: swe-bench-pro-2025
    title: 'Deng et al., "SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks?", 2025'
    resource: https://arxiv.org/html/2509.16941
    accessed: 2026-10-17
  - id: chroma-context-rot
    title: 'Hong, Troynikov, Huber, "Context Rot: How Increasing Input Tokens Impacts LLM Performance", Chroma technical report, 2025-07-14'
    resource: https://www.trychroma.com/research/context-rot
    accessed: 2026-10-17
  - id: anthropic-context-engineering
    title: 'Anthropic, "Effective context engineering for AI agents"'
    resource: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
    accessed: 2026-10-17
  - id: claude-code-best-practices
    title: 'Claude Code documentation, "Best practices for Claude Code"'
    resource: https://code.claude.com/docs/en/best-practices
    accessed: 2026-10-17
---

# Pull request size and review effectiveness

This reference records what the world knows about how the size of a code
change affects its review and, since the author here is usually a coding
agent, how it affects the author's ability to finish the change without
losing track of it. It leaves out branching strategy and release mechanics,
which `github-integration-branches.md` covers. The pull-request sizing rule
in `AGENTS.md` ("PR Discipline") and its procedure in the
`authoring-issues-prs` skill ("Size the change before coding") are derived
from the claims below; the `self-reviewing` skill's size backstop reads the
same numbers.

## Scope

- Covers: size guidance from engineering-practice guides; measured change
  sizes at large companies and in open source; the measured relation between
  change size, file count and review usefulness; the effect of tangled
  (multi-concern) changes on review; the relation between patch size, file
  count and coding-agent success; context-length degradation.
- Does not cover: the post-merge defect rate of large pull requests (no
  primary source found; see "Not covered"), merge mechanics, branch
  protection.
- Depended on by: `AGENTS.md` § PR Discipline, the `authoring-issues-prs`
  skill § Size the change before coding, the `self-reviewing` skill § Select
  the range, the `roadmapping` skill § Refining an epic.

## Claims

### What practice guides prescribe

- Google defines the right size as "one self-contained change": the change
  "addresses just one thing", is "usually just one part of a feature, rather
  than a whole feature at once", and includes its test code.
  [source: google-small-cls]
- Google gives no hard limit, but states that "100 lines is usually a
  reasonable size for a CL, and 1000 lines is usually too large", that "the
  number of files that a change is spread across also affects its size", and
  that "a 200-line change in one file might be okay, but spread across 50
  files it would usually be too large". [source: google-small-cls]
- Google tells authors to "think about how to split and organize those CLs at
  a high level before diving into coding", because "it can be a lot of work
  to split up a change after you've already written it".
  [source: google-small-cls]
- Google separates refactorings from feature changes and bug fixes ("moving
  and renaming a class should be in a different CL from fixing a bug in that
  class"); a local variable rename may ride along. [source: google-small-cls]
- Google names four ways to split: stack one small CL on another, split by
  groups of files that need different reviewers, split horizontally by layer
  behind stubs, or split vertically into "smaller, full-stack, vertical
  features". [source: google-small-cls]
- Google lets reviewers "reject your change outright for the sole reason of
  it being too large" [source: google-small-cls], and its reviewer guide
  says that for a very large CL the preferred response is to ask the
  developer to split it into several smaller CLs that build on each other,
  rather than review the whole at once. [source: google-review-speed]
- Google exempts two large shapes: deleting a whole file "counts as being
  just one line of change", and a change generated by a trusted automatic
  refactoring tool. [source: google-small-cls]
- SmartBear advises reviewing "no more than 200 to 400 lines of code (LOC) at
  a time", keeping inspection "under 500 LOC per hour", and sessions under 60
  minutes; it expects "a review of 200-400 LOC over 60 to 90 minutes" to
  yield "70-90% defect discovery". It attributes this to "a SmartBear study
  of a Cisco Systems programming team". [source: smartbear-best-practices]
- The Cisco study itself (10 months, 2,500 reviews, 3.2 million lines, 50
  developers) measures how well reviewers read, not whether large changes
  ship more defects. [unverified] The primary write-up (Cohen, StickyMinds,
  "The Largest Case Study of Code Reviews—Ever") returned HTTP 403 on
  2026-10-17; the figures above come from secondary retellings.
- Microsoft's survey of 911 developers lists as an author best practice "Aim
  for small, incremental changes, and easy to understand code changes" and
  "Cluster related changes", and ranks "the review size" among the five main
  challenges reviewers face. [source: macleod-2016]
- The Linux kernel requires "Separate each logical change into a separate
  patch": a bug fix and a performance enhancement for one driver are two
  patches, and "an API update, and a new driver which uses that new API" are
  two patches; conversely "a single change to numerous files" is grouped
  into one patch, so "a single logical change is contained within a single
  patch". Each patch must build and run on its own.
  [source: kernel-submitting-patches]
- Across industry and open source "the guidance is general and vague":
  projects ask for "small" or "reasonable" sizes, and the only concrete
  figures quoted are Google's 100/1000 lines and a Linux sub-rule of 100
  lines. [source: kudrjavets-2022]

### Measured change sizes

- At Google the median reviewed change modifies 24 lines; over 10% modify a
  single line; over 35% modify a single file and about 90% modify fewer than
  10 files. [source: sadowski-2018]
- Rigby and Bird found "Change sizes are small" to be a convergent practice:
  median churn of 44 lines at Android and AMD, 25 at Apache, 32 at Linux, 78
  lines across 5 files at Chrome, against 263 non-comment lines in
  traditional inspection at Lucent. [source: rigby-bird-2013]
- Across 100 popular GitHub repositories the median pull request changes 22
  source lines (mean 584, outliers included), and one GitHub-wide analysis puts
  the median at 20 lines; medians "differ by orders of magnitude across
  projects". [source: kudrjavets-2022]
- LinearB's benchmark over 8.1 million pull requests rates "PR Size" under
  100 code changes as Elite, 100-155 Good, 156-228 Fair and over 228 as
  Needs focus. The unit is labelled "code changes", not lines; this is a
  vendor percentile, not an outcome study. [source: linearb-benchmarks]
- Graphite reports, on pull requests synced to its tool, that 50-line changes
  are reviewed and merged about 40% faster than 250-line changes, are 15%
  less likely to be reverted, and draw about 40% more review comments per
  changed line; the least-reverted band is 25-50 lines, pull requests under
  10 lines are reverted more often than 10-100 line ones, and the author
  recommends a 50-line median with a 25-100 line range. Correlational, on a
  user base that skews small. [source: graphite-50-lines]

### What size does to human review

- At Microsoft, across about 1.5 million review comments in five projects,
  "as number of files in the change increases, the proportion of comments
  that are useful drops" (figure spans 0 to 40 files); the authors
  "recommend that developers submit smaller and incremental changes whenever
  possible, in contrast to waiting for a large feature to be completed".
  Reviewers of large changesets "may opt for cursory review" and "miss some
  changes". [source: bosu-2015]
- At Google, initial review feedback takes "a median time of under an hour
  for small changes and about 5 hours for very large changes"; Rigby and
  Bird's argument, which the Google study replicates, is that quick review
  "could only be enabled by smaller changes to review".
  [source: sadowski-2018]
- Size does not buy merge speed: across more than 826,000 GitHub pull
  requests in 100 repositories, pull request size and composition have only "a weak
  correlation to time-to-accept and time-to-merge", whatever the language,
  weekday or affiliation. Smallness is a review-quality lever, not a
  throughput lever. [source: kudrjavets-2022]
- Google's reasons for small changes are reviewer thoroughness ("important
  points get missed or dropped" in large ones), fewer bugs, less wasted work
  on a wrong direction, easier merges, easier design polish and simpler
  rollback. [source: google-small-cls]

### One concern per change

- Up to 15% of bug fixes in five open-source Java projects consist of
  multiple tangled changes, and at least 16.6% of files are wrongly linked to
  bug reports as a result; the authors "recommend better change organization
  to limit the impact of tangled changes". [source: herzig-2013]
- In a controlled experiment with 28 developers, decomposing a tangled pull
  request (a refactoring plus an enhancement) into two led to significantly
  fewer wrongly reported issues, with no significant difference in defects
  found, review time or change understanding; the authors conclude "commits
  belonging to different concepts should be separated".
  [source: dibiase-2019]
- The experiment chose a refactoring tangled with a feature because
  practitioners report that pairing as the hardest to review.
  [source: dibiase-2019]

### What a coding agent can carry

- In the original SWE-bench the gold patch edits 1.7 files, 3 functions and
  32.8 lines on average; model-generated patches that apply are "less than
  half the total length" of the gold patches and "rarely edit more than a
  single file". [source: swe-bench-2024]
- Agent success falls with the context it must hold: "as total context
  length increases, Claude 2's performance drops considerably; behavior that
  is also observed in other models", because models "frequently struggle
  with localizing problematic code". [source: swe-bench-2024]
- On Multi-SWE-bench "resolved rates drop sharply when fix patches exceed 600
  tokens or touch more than one file"; the resolved rate "drops significantly
  as the number of modified files increases across all three methods", and
  agents mostly solve issues a human fixes in under 15 minutes, with hard
  issues (over an hour) resolved at rates "approaching zero".
  [source: multi-swe-bench-2025]
- On SWE-Bench Pro, whose reference solutions average 107.4 lines across 4.1
  files, "models maintain relatively stable performance for single-file
  problems but exhibit sharp declines as file count increases", and the gap
  "widens dramatically beyond 3 files". The best models resolve about 44% of
  the public set and under 20% of the commercial set.
  [source: swe-bench-pro-2025]
- Multi-SWE-bench labels difficulty by human time to fix and notes that some
  "easy" instances have large diffs because the edits are "highly repetitive
  and pattern-consistent", so token or file counts over-state the difficulty
  of a mechanical change. [source: multi-swe-bench-2025]

### Context length degrades the author as well as the reviewer

- Across 18 models, "performance grows increasingly unreliable as input
  length grows", even on tasks designed to be simple; a model does not
  "handle the 10,000th token just as reliably as the 100th".
  [source: chroma-context-rot]
- "As the number of tokens in the context window increases, the model's
  ability to accurately recall information from that context decreases";
  context is "a finite resource with diminishing marginal returns", an
  "attention budget" that "every new token introduced depletes".
  [source: anthropic-context-engineering]
- Claude Code's own guidance rests on one constraint: "Claude's context
  window fills up fast, and performance degrades as it fills", and "a clean
  session with a better prompt almost always outperforms a long session with
  accumulated corrections". Planning is advised "when the change modifies
  multiple files"; "if you could describe the diff in one sentence, skip the
  plan". [source: claude-code-best-practices]

## How this project uses the claims

The sizing rule needs a measure that exists before the first line is
written, because a line count arrives only after the author's attention
budget has been spent. Two leading measures come straight from the sources:

- **One concern.** Google, the Linux kernel, Herzig and di Biase all define
  the unit as one logical change; the one-sentence test is Claude Code's own
  heuristic. The rule asks for a title that names one change without "and",
  and puts a refactoring in its own pull request.
- **File count.** Google weighs it, Bosu measures review usefulness falling
  with it, and all three agent benchmarks show success falling with it, with
  SWE-Bench Pro placing the cliff beyond three files. The rule counts the
  files whose logic a plan changes (code, scripts, workflows, config and
  instruction files) and caps one behaviour at five, with the kernel's
  grouping rule as the escape hatch: one mechanical change repeated at
  every site counts as one file. Tests, docs pages, reference pages,
  generated files, lockfiles and a file that only gains a pointer are left
  out, since none of them is where the reasoning happens.

The line count is the lagging backstop, counted over the same files as the
file cap. SmartBear's 200-400 lines per session, LinearB's "Needs focus"
above 228 changes and Google's "1000 is too large" bracket the region. Past
400 counted lines the author states why the change is still one behaviour;
past 1,000 (Google: "usually too large") the rule forces the re-plan.
Because only source files count, a reference page never does: it is checked
claim by claim against its sources (the `researching-references` skill's
own process), not read as a diff, and two of the six pages already in this
directory exceed 400 lines on their own.

The first version of the rule (2026-10-17) capped files at three and
stopped every diff past 400 lines, tests and docs pages included. Applied
to one feature of pvliesdonk/markdown-vault-mcp (issue 1497), it blocked
the pull request the maintainer closed as too much to review well (1798:
four concerns, 9 logic files, about 1,620 logic lines), and it blocked
equally the three the work was then split into and the package's next
pull request, all merged (1803 to 1806: 1 to 6 logic files, about 210 to
850 logic lines). Lines did not tell them apart: 1798 changed 2,511 lines
and 1804, one new module with its tests, 1,944. Concern and file count
did. In pvliesdonk/fastmcp-server-template, the pull requests above about
1,000 lines (724, 695, 735, 725) were followed by fix-up pull requests;
none were found after those in the 400 to 1,000 band (693, 689, 737,
738). The rule was revised on 2026-10-21 to the limits above. These are
observations on two repositories, not a study.

Where this project departs from the subject: SWE-Bench Pro's three-file
cliff is not the cap. The benchmark's agent has to locate the change from
the issue text alone, which SWE-bench finds models struggle with; the rule
applies to a plan that already names its files.
Google's "include a usage of the API in the same CL" and the kernel's "API
update and new driver are two patches" disagree; the rule follows Google
for a tool and its registration (both must land for the tool to exist) and
the kernel for everything else. Graphite's 50-line target is not adopted:
the project's docs and test discipline put most honest single-behaviour
pull requests above it.

## Not covered

- No primary source was found that measures the post-merge defect rate of
  pull requests by size; the Cisco study measures reviewer reading, and
  Graphite's revert rate is a vendor correlation. A project wanting that
  claim must measure it on its own history.
- The Cisco study's own numbers remain `[unverified]` until the StickyMinds
  article or Cohen's book is read directly.
- No source isolates an optimal sub-task size for a coding agent working
  through ordered pieces of one feature; the benchmarks measure whole-issue
  resolution. Their file counts are the best available proxy.
- Whether review bots degrade with size the way human reviewers and agent
  authors do is observed in this project's own history, not in a published
  study.
