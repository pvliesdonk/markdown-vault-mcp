---
name: authoring-issues-prs
description: >-
  Use when filing a bug, opening or creating a GitHub issue, drafting an
  epic or ticket, writing up a finding or review observation worth
  tracking, or opening a pull request for this repository. Routes the
  change to the right repo first (library / template / domain), picks the
  right issue form, links epic children as native sub-issues, and applies
  CONTRIBUTING.md before anything is posted.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on copier update. Project-specific
     additions go inside the DOMAIN-AUTHORING sentinel at the end. ===== -->

# Authoring issues and pull requests

`CONTRIBUTING.md` at the repository root is the single source for the rules:
issue voice, uncertainty markers, one-issue-one-problem, PR discipline, and
the three-tier routing. This skill adds only what a document cannot — the
trigger, the order of operations, and the API mechanics for the steps issue
forms cannot perform. It deliberately does not restate the rules; a second
copy would drift from the file silently.

## Procedure

### 1. Read CONTRIBUTING.md — now, not from memory

Read `CONTRIBUTING.md` (repository root) before drafting a single sentence,
even if you believe you remember it. You are about to apply these sections,
and their exact wording matters:

- "Observation, not work order" and "The uncertainty rule" — issue voice
  and the `[verified: how]` / `[unverified]` markers.
- "One issue, one observed problem" and the "Remove before posting" table —
  run your draft through the table before submitting.
- "Pull requests" — no orphan PRs, the deliberately-does-not section.
- "Where to send fixes" — the routing walked in step 2.

If anything in this skill appears to conflict with `CONTRIBUTING.md`, the
file wins.

### 2. Route before writing

Decide the repo first, then write for that repo. The test: **which file
would a fix change?**

1. Anything you'd change in `fastmcp_pvl_core` → **library**: file on
   `pvliesdonk/fastmcp-pvl-core`.
2. A template-owned file — workflows, `Dockerfile`, the `server.py`
   skeleton, anything `copier update` re-renders — → **template**: file on
   `pvliesdonk/fastmcp-server-template`.
3. Anything inside a `DOMAIN-*` / `CONFIG-*` / `PROJECT-*` sentinel block,
   or `tools.py` / `resources.py` / `prompts.py` / `domain.py` / `tests/`
   → **domain**: file on this repository.

`CONTRIBUTING.md`'s "Where to send fixes" defines these tiers with the
post-merge propagation for each. When the tier is genuinely unclear, say so
in the issue with an `[unverified]` marker instead of guessing silently.

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

### 5. For epics: finish what the form cannot do

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

### 6. Pull requests

Route first (step 2): the issue and the PR that closes it belong in the
same repo. Then follow `CONTRIBUTING.md`'s "Pull requests" section and the
repo's PR template (`.github/PULL_REQUEST_TEMPLATE.md`) — every section,
including "What this PR deliberately does NOT do" and the docs-impact
checklist.

Include a feature's approved spec in the Design section, folded when long,
ahead of the review report. The spec is agreed in session or offline
before the PR; the PR archives the decisions for reviewers and future
readers. Follow the template's human attachment option if the full body
exceeds its limit. Do not call undocumented upload endpoints. At merge,
port enduring decisions missing from code and docs to docs/design/ or an
ADR, as CONTRIBUTING.md requires.

### 7. Attribution footer

When an agent authors an issue or PR body, end it with an accurate
attribution footer, for example:

```markdown
---
_Generated by [Claude Code](https://claude.ai/code)_
```

Use the actual authoring agent's name and product URL; do not attribute
work by another agent to Claude Code.

<!-- DOMAIN-AUTHORING-START -->
<!-- Project-specific authoring conventions (extra labels, forms, routing
     notes) go here. This block survives copier update. -->
<!-- DOMAIN-AUTHORING-END -->
