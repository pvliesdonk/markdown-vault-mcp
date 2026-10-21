---
name: authoring-issues-prs
description: >-
  Use when filing a bug, opening or creating a GitHub issue, drafting an
  epic or ticket, writing up a finding or review observation worth
  tracking, sizing a change before coding, or opening a pull request for
  this repository. Routes the change to the right repo first (library /
  template / domain), picks the right issue form, sizes the change to one
  PR, links epic children as native sub-issues, and applies CONTRIBUTING.md
  before anything is posted.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on copier update. Project-specific
     additions go inside the DOMAIN-AUTHORING sentinel at the end. ===== -->

# Authoring issues and pull requests

`CONTRIBUTING.md` holds the issue rules, the agent-post footer and the
three-tier routing; `AGENTS.md` and `.github/PULL_REQUEST_TEMPLATE.md` hold
the PR rules. This skill adds the order of operations, the sizing
procedure behind the limits `AGENTS.md` sets, and the API steps issue forms
cannot perform; it states none of the other rules itself.

## Procedure

### 1. Read CONTRIBUTING.md — now, not from memory

Read `CONTRIBUTING.md` (repository root) before drafting a single sentence,
even if you believe you remember it:

- "Observation, not work order" and "The uncertainty rule" — issue voice
  and the `[verified: how]` / `[unverified]` markers.
- "One issue, one observed problem" and the "Remove before posting" table —
  run your draft through the table before submitting.
- "Pull requests" — which `AGENTS.md` sections and template govern a PR,
  and the merge style.
- "Where to send fixes" — the routing walked in step 2.
- "Agent-authored posts" — the footer every post ends with, and how to
  read earlier posts under the account holder's name (step 8).

Where this skill and `CONTRIBUTING.md` disagree, the file wins.

### 2. Route before writing

Decide the repo before writing: ask **which file a fix would change**, and
look that file up in `CONTRIBUTING.md`'s "Where to send fixes" (library,
template or domain). When the tier is unclear, file where you think it
belongs and say so in the issue with an `[unverified]` marker.

### 3. Search for duplicates

Search the target repo's issues — open **and** closed — before filing:

```bash
gh issue list --repo OWNER/REPO --state all --search "<key terms>"
```

Match on the observation, not the wording. If the problem is already on
file, comment on the existing issue rather than opening a twin; if a closed
issue shows it regressed, say that in the new issue and link it.

### 4. Pick the form

Forms live in `.github/ISSUE_TEMPLATE/` of the target repo:

| You have | Form |
|----------|------|
| Something not working as expected | `bug-report.yml` |
| A capability that's missing | `feature-request.yml` |
| A multi-feature effort telling one user-facing story | `epic.yml` |
| A consequential unknown to answer within an agreed appetite | `research.yml` |
| Structural decay worth refactoring later | `decay.yml` |
| A question or support request | `question.yml` |

When filing via API/CLI rather than the web form, mirror the chosen form's
section headings and apply its labels (`bug`, `feature`, `epic`, `decay`,
`question`, `research`) so the issue is indistinguishable from a form-filed one.

For features and epics, assess the surface-impact answer against the
last stable release. Apply `breaking` only for a known compatibility
break to an existing operator or library contract; an additive change
that merely touches the surface is not breaking. Keep uncertainty visible.

### 5. Size the change before coding

Estimate the pull request from the plan, before the first edit; a line
count arrives only after the author's focus has been spent, and a diff
split after the fact keeps the defects that grew in it. The limits are
`AGENTS.md`'s ("PR Discipline"); this step is how to apply them.

- **One behaviour.** Write the PR title now, as a conventional commit. If
  it needs `and`, or a refactoring rides with a behaviour change, that is
  two PRs; a local rename inside the change may stay. A new tool and its
  registration are one behaviour.
- **At most five source files.** Count the files whose logic the plan
  changes: code, scripts, workflows, config, and instruction files such as
  `AGENTS.md` and a `SKILL.md`. Leave out tests, docs pages, reference
  pages, generated files, lockfiles, and a file that only gains a pointer
  to the change (a skills-list line, a cross-reference). One mechanical
  edit repeated at every site (a rename, a signature update at each
  caller) counts as one file. A sixth file for one behaviour means the
  behaviour is bigger than one PR.
- **Past either limit**, file the work as an epic (step 6) whose children
  each pass both tests, stacked when one depends on another. When the
  children must ship together, run the epic on `integration/<epic>`
  (`docs/contribute/integration-branches.md`) so trunk stays releasable.
- **One PR at a time.** Push and self-review the current PR before starting
  the next; a stacked child branches from the pushed one.
- **The line count is the backstop.** Count the changed lines in the files
  the file test counts, so tests, docs and reference pages stay out of it.
  Past 400, check the diff against the one-behaviour test and say in the
  PR body why it still passes; a single new module with its tests often
  does. Past 1,000, stop: re-plan the issue into children that pass the
  tests above and implement each on a fresh branch in a fresh session.
  Carving the existing diff into pieces keeps the problems that grew in
  it.

The numbers and their sources: `docs/design/reference/pull-request-size.md`.

### 6. For epics: finish what the form cannot do

Issue forms cannot create sub-issue links or assign milestones. After the
epic is filed, perform these steps — this is the mechanical half of the
epic form's "After filing" checklist:

```bash
repo=OWNER/REPO        # the repo decided in step 2
epic=EPIC_NUMBER

# 0. Every epic starts with one refinement sub-issue (gh 2.94+):
gh issue create --repo "$repo" --parent "$epic" --label refinement \
  --title "Refine: <epic title>" \
  --body "Refine #$epic; see docs/design/roadmap.md. Done when feature issues plausibly cover the epic's Done when."

# 1. Link each child as a NATIVE sub-issue (the endpoint takes the child's
#    database id, not its issue number):
child_id=$(gh api "repos/$repo/issues/CHILD_NUMBER" --jq '.id')
gh api -X POST "repos/$repo/issues/$epic/sub_issues" -F "sub_issue_id=$child_id"

# 2a. Ships atomically — milestone (preferred): assign the epic AND its
#     children to the committed package milestone, creating it if needed.
#     Milestones are per-repo and one-per-issue; for a cross-repo epic the
#     milestone lives in the repo where the release is cut.
gh api "repos/$repo/milestones" -f "title=010 content-name"  # if absent
gh issue edit "$epic" --repo "$repo" --milestone "010 content-name"

# 2b. Ships atomically — label (fallback): when no package is committed
#     yet, or in the repos of a cross-repo epic that do not cut the release.
gh issue edit "$epic" --repo "$repo" --add-label ships-atomically
```

Working through the GitHub MCP server instead: `sub_issue_write` (method
`add`) performs the same link, and `issue_read` returns `has_parent` /
`has_children` / `sub_issues_summary` to verify it took.

Never track children as a markdown task list in the epic body — the native
link is what release tooling queries.

An epic spanning packages carries no milestone: children can inherit their
parent's milestone. Assign packages to the child issues individually.
The `roadmapping` skill defines package ordering and membership.

### 7. Pull requests

Open the PR in the same repo as its issue (step 2), then follow
`CONTRIBUTING.md`'s "Pull requests" section: pass the gates and run the
`self-reviewing` skill first, and fill every section of the PR template.

Pick the base branch before creating the PR: when the epic the work
belongs to runs on an integration branch (its body names
`integration/<epic>`), pass `--base integration/<epic>` and write
`Part of #<epic>` or `Refs #N` in the body instead of `Closes #N`, which
does nothing on a non-default base. The final `integration/<epic>` →
`main` PR carries every `Closes` line and is merged with a merge commit;
`docs/contribute/integration-branches.md` has the whole workflow.

### 8. Identify yourself on every post

`CONTRIBUTING.md`'s "Agent-authored posts" section is the rule; apply it
to every post this skill produces, not only bodies: the issue or PR body,
each comment on an issue or PR, and each review summary or inline reply.
End the post with the footer that section shows, verbatim in its fixed
`Agent-authored:` opening, naming the agent product you actually are and
its URL; never attribute a post to another agent. Write as a proposer, and
read earlier posts under the account holder's name as possibly your own
earlier output before treating them as a decision.

<!-- DOMAIN-AUTHORING-START -->
<!-- Project-specific authoring conventions (extra labels, forms, routing
     notes) go here. This block survives copier update. -->
<!-- DOMAIN-AUTHORING-END -->
