---
type: Reference
title: GitHub and git behaviour behind integration branches
description: Up-to-date-branch requirements, merge queue availability, workflow branch filters, closing keywords, PR retargeting, merge methods, knope's commit walk and git's review tools, as the integration-branch workflow relies on them.
subject_version: "GitHub.com as of 2026-09; git 2.36+; knope 0.23"
valid_for: "GitHub.com as of 2026-09, git 2.x, knope 0.x"
generated:
  by: process:researching-references
  at: 2026-09-26T08:00:00+00:00
stale_after: 2027-03-26T00:00:00+00:00
status: draft
sources:
  - id: merge-queue
    title: Managing a merge queue
    resource: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
    accessed: 2026-09-26
  - id: ruleset-rules
    title: Available rules for rulesets
    resource: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets
    accessed: 2026-09-26
  - id: workflow-events
    title: Events that trigger workflows
    resource: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
    accessed: 2026-09-26
  - id: linking-issues
    title: Linking a pull request to an issue
    resource: https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue
    accessed: 2026-09-26
  - id: branch-deletion
    title: Creating and deleting branches within your repository
    resource: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-and-deleting-branches-within-your-repository
    accessed: 2026-09-26
  - id: retarget-changelog
    title: Pull request retargeting (GitHub changelog, 2020-05-19)
    resource: https://github.blog/changelog/2020-05-19-pull-request-retargeting/
    accessed: 2026-09-26
  - id: merge-methods
    title: About pull request merges
    resource: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/about-pull-request-merges
    accessed: 2026-09-26
  - id: knope-git
    title: knope source, crates/knope/src/integrations/git.rs (get_commit_messages_after_tag), v0.23.0
    resource: https://github.com/knope-dev/knope/blob/v0.23.0/crates/knope/src/integrations/git.rs
    accessed: 2026-09-26
  - id: git-2-36
    title: Git 2.36 release notes
    resource: https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/2.36.0.adoc
    accessed: 2026-09-26
  - id: range-diff
    title: git-range-diff documentation
    resource: https://git-scm.com/docs/git-range-diff
    accessed: 2026-09-26
  - id: pro-git-rebase
    title: Pro Git, Git Branching - Rebasing, "The Perils of Rebasing"
    resource: https://git-scm.com/book/en/v2/Git-Branching-Rebasing
    accessed: 2026-09-26
---

# GitHub and git behaviour behind integration branches

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

## Scope

This page supports `docs/deployment/integration-branches.md`, the
non-strict required checks in `.github/rulesets/`, and the `integration/**`
triggers in `ci.yml`. It covers what GitHub, git and knope do when an epic's
pull requests merge into a non-default branch that later merges into the
default branch. It does not cover GitHub Enterprise Server or stacked-PR
tools.

## Claims

### Up-to-date branches and merge queue

- A ruleset's required status checks can require the pull request branch to
  be up to date with its base before merging; that is the
  `strict_required_status_checks_policy` parameter. [source: ruleset-rules]
  The shipped rulesets leave it off.
  [pins: tests/test_branch_protection_contract.py::test_branch_rulesets_do_not_require_up_to_date_branches]
- A merge queue "provides the same benefits as the Require branches to be up
  to date before merging branch protection, but does not require a pull
  request author to update their pull request branch and wait for status
  checks to finish before trying to merge". It needs the `merge_group`
  event in the workflows that report required checks. [source: merge-queue]
- Merge queues are available in public repositories owned by an
  organization, and in private repositories owned by organizations on
  GitHub Enterprise Cloud; the page names no availability for repositories
  owned by a personal account. [source: merge-queue]

### Workflow triggers

- A `pull_request` workflow's `branches` filter matches the pull request's
  base branch, not its head. [source: workflow-events] A child pull request
  targeting `integration/<epic>` therefore runs `ci.yml` only because its
  filter lists `integration/**`.
  [pins: tests/test_branch_protection_contract.py::test_ci_runs_on_integration_branches]
- A required check whose workflow never runs on a branch never reports
  there, and the merge waits for it with no timeout. This is why the
  integration ruleset requires `CI Success` alone. [unverified: stated from
  the template's own #454 experience with filtered domain workflows, not
  from a GitHub page]
  [pins: tests/test_branch_protection_contract.py::test_integration_branches_require_ci_success_alone]

### Closing keywords and retargeting

- Closing keywords in a pull request are interpreted only when the pull
  request targets the repository's default branch; a pull request to any
  other branch links the issue but merging it closes nothing.
  [source: linking-issues]
- When a merged pull request's head branch is deleted, GitHub retargets the
  open pull requests based on that branch to the merged pull request's
  base. [source: branch-deletion] [source: retarget-changelog]
- Deleting the branch with `git push --delete` rather than through GitHub
  is reported to close those pull requests instead of retargeting them.
  [unverified: community discussion only; a throwaway repository with one
  open child pull request would settle it]

### Merge methods

- A squash merge combines a pull request's commits into one commit; a
  merge commit keeps every commit of the head branch reachable from the
  base. [source: merge-methods]
- Requiring linear history permits only squash and rebase merges, so it
  rules out the merge commit an integration branch's final pull request
  needs. [source: ruleset-rules] The shipped rulesets do not require
  linear history.

### knope's commit walk

- knope collects the commits after the last release by walking the
  revision graph from HEAD through every parent, minus everything
  reachable from the last release tag, and keeps a set of commit ids.
  Commits a merge commit brings in are therefore counted, and each commit
  once. [source: knope-git]
- How knope treats the non-conventional subject of a merge commit
  ("Merge pull request #N ...", "Merge branch 'main' ...") was not
  exercised; it is expected to be skipped like any other non-conventional
  subject. [unverified: a `knope prepare-release --dry-run` over a range
  holding one merge commit would settle it]

### Reviewing merges and rebases

- `git log --remerge-diff` and `git show --remerge-diff` show how a
  recorded merge differs from git's own mechanical merge of the same
  parents, which is the author's conflict resolution. The option first
  shipped in git 2.36. [source: git-2-36]
- `git range-diff` compares two versions of a commit series, pairs each
  commit with its counterpart and shows only the pairs whose patch changed;
  it ignores merge commits. [source: range-diff]
- Rebasing commits that others have based work on forces each of them to
  rebase in turn, and Pro Git's rule is not to rebase commits that exist
  outside your repository. [source: pro-git-rebase]
- How GitHub's "changes since your last review" view behaves after a
  force push was not found in GitHub's documentation. [unverified]

## Where this project departs from the subject

GitHub's own answer to stale branches is the merge queue. Personal-account
repositories cannot use it, so the template turns the up-to-date
requirement off and relies on CI running on every push to a protected
branch to catch a semantic conflict after the merge instead of before it.

## Not covered

- Rebase-merging a head branch that itself contains merge commits.
  [unverified]
- Organization-level rulesets, which layer on top of the repository's own
  and could re-impose the up-to-date requirement. [unverified]
