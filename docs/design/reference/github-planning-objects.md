---
type: Reference
title: GitHub planning objects
description: GitHub milestone and issue relationship behavior used by roadmapping and release automation.
subject_version: "2026-09; GitHub CLI 2.97.0"
valid_for: "GitHub.com as of 2026-09"
generated:
  by: process:researching-references
  at: 2026-09-12
stale_after: 2027-03-12
verified:
  - by: process:researching-references-refute
    at: 2026-09-12
status: stable
sources:
  - id: milestones-rest
    title: REST API endpoints for milestones
    resource: https://docs.github.com/en/rest/issues/milestones
    accessed: 2026-09-12
  - id: issues-rest
    title: REST API endpoints for issues
    resource: https://docs.github.com/en/rest/issues/issues
    accessed: 2026-09-12
  - id: milestones
    title: About milestones
    resource: https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/about-milestones
    accessed: 2026-09-12
  - id: gh-api
    title: GitHub CLI api manual
    resource: https://cli.github.com/manual/gh_api
    accessed: 2026-09-12
  - id: sub-issues
    title: Adding sub-issues
    resource: https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues
    accessed: 2026-09-12
  - id: sub-issues-rest
    title: REST API endpoints for sub-issues
    resource: https://docs.github.com/en/rest/issues/sub-issues
    accessed: 2026-09-12
  - id: inheritance
    title: A REST API for GitHub Projects, sub-issues improvements, and more
    resource: https://github.blog/changelog/2025-09-11-a-rest-api-for-github-projects-sub-issues-improvements-and-more/
    accessed: 2026-09-12
  - id: dependencies
    title: Creating issue dependencies
    resource: https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies
    accessed: 2026-09-12
  - id: dependencies-ga
    title: Dependencies on issues
    resource: https://github.blog/changelog/2025-08-21-dependencies-on-issues/
    accessed: 2026-09-12
  - id: dependencies-rest
    title: REST API endpoints for issue dependencies
    resource: https://docs.github.com/en/rest/issues/issue-dependencies
    accessed: 2026-09-12
  - id: issue-types
    title: Managing issue types in an organization
    resource: https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/managing-issue-types-in-an-organization
    accessed: 2026-09-12
  - id: gh-294
    title: GitHub CLI 2.94.0 release
    resource: https://github.com/cli/cli/releases/tag/v2.94.0
    accessed: 2026-09-12
  - id: search
    title: Searching issues and pull requests
    resource: https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests
    accessed: 2026-09-12
  - id: attachments
    title: Attaching files
    resource: https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files
    accessed: 2026-09-12
  - id: collapsed
    title: Organizing information with collapsed sections
    resource: https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/organizing-information-with-collapsed-sections
    accessed: 2026-09-12
---

# GitHub planning objects

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

## Scope

This page supports the [roadmapping skill](../../../.agents/skills/roadmapping/SKILL.md),
the package steps in `.github/workflows/release-prepare.yml` and
`.github/workflows/release.yml`, and their release helper. It covers GitHub.com
milestones, native issue relationships, and PR design material. It does not
cover GitHub Projects, enterprise-server compatibility, or version computation.

## Claims

### Milestone identity, ordering, and pagination

- A milestone's repository-local `number` identifies the object in GET and
  PATCH paths; `title` is an editable attribute. Update accepts `title` and
  `state` (`open` or `closed`). [source: milestones-rest]
  [pins: tests/test_release_flow_contract.py::test_release_closes_current_package]
- List milestones defaults to open milestones, sorted by `due_on` ascending;
  its only documented sort fields are `due_on` and `completeness`. It returns
  30 per page by default, at most 100 per page. An ordinal title therefore
  requires sorting the complete result locally. [source: milestones-rest]
  [pins: tests/test_release_flow_contract.py::test_release_prepare_warns_on_current_package]
- `gh api --paginate` fetches all pages; `--slurp` wraps those pages in an
  outer array. Selecting a minimum separately on each page does not select
  the overall minimum. [source: gh-api]
  [pins: tests/test_package_milestones.py::test_api_combines_pages_and_sends_json_null]
- Creating a milestone requires a title; description and due date are
  optional. Milestone updates accept either Issues-write or
  Pull-requests-write repository permissions. [source: milestones-rest]
- Title uniqueness and the exact effect of renaming on existing membership
  were not reproduced in this pass; use numeric identity throughout and
  handle API validation errors. A scratch milestone with one member would
  verify collision and rename behavior. [unverified]

### Membership includes pull requests

- Repository issue listing includes pull requests, identifiable by the
  `pull_request` key; its `milestone` filter takes the milestone's `number`.
  `state=open` selects open members, with the same 30/default, 100/maximum
  pagination limits. [source: issues-rest]
  [pins: tests/test_release_flow_contract.py::test_release_closes_current_package]
- PATCH `/repos/{owner}/{repo}/issues/{number}` accepts one milestone number
  or JSON `null` to clear it. Without push access, milestone changes can be
  silently dropped; successful HTTP status alone does not establish removal.
  Check returned membership or read it again. [source: issues-rest]
  [pins: tests/test_package_milestones.py::test_partial_failure_leaves_package_open_and_retry_resumes]
- `gh api -X PATCH ... -F milestone=null` sends JSON null; `-f` sends a
  string. Supplying fields otherwise changes the default method to POST,
  so PATCH must be explicit. [source: gh-api]
- Milestone progress includes both issues and PRs; the UI displays their
  open and closed counts. Manual item prioritization is unavailable above
  500 open items. [source: milestones]
- The REST count also includes PRs: vscode milestone 447 had
  `closed_issues=425`, matching 140 closed issues plus 285 closed PRs.
  [observed: gh 2.97.0; GET repos/microsoft/vscode/milestones/447 and paginated GET repos/microsoft/vscode/issues?milestone=447&state=all&per_page=100 on 2026-09-12]
- Closed milestones can retain open members: vscode milestone 207 was
  closed with `open_issues=1`. This observation establishes that state
  combination, not whether the item was reopened after closure.
  [observed: gh api --paginate 'repos/microsoft/vscode/milestones?state=closed&per_page=100' on 2026-09-12]

### Sub-issues and inheritance

- A parent supports up to 100 direct sub-issues and eight nesting levels;
  adding sub-issues requires at least triage permission. The UI supports
  selecting issues from another repository. [source: sub-issues]
- GitHub's September 2025 announcement says sub-issues inherit the parent's
  Project and Milestone by default. It does not establish continuous
  synchronization or identical behavior for every API creation path.
  [source: inheritance]
- The same announcement supports cross-organization parents and children,
  but REST's `sub_issue_id` description still requires the same repository
  owner. Treat this as an unresolved documentation conflict for API writes.
  [source: inheritance] [source: sub-issues-rest]
- REST adds a child using `POST .../issues/{number}/sub_issues` with the
  child's global `sub_issue_id`; `replace_parent` can replace its current
  parent. GET `.../issues/{number}/parent` reads the parent.
  [source: sub-issues-rest]

### Dependencies, types, and CLI versions

- Dependencies are available on Free, Pro, Team, and Enterprise Cloud;
  creation requires triage permission. They express which work blocks
  which other work. [source: dependencies]
- Each dependency relationship type permits 50 linked issues.
  `is:blocked`, `is:blocking`, `blocked-by:`, and `blocking:` are documented
  repository/project search filters. [source: dependencies-ga]
- REST creates a blocking dependency with
  `POST .../issues/{number}/dependencies/blocked_by` and a global `issue_id`,
  not the blocker’s repository-local issue number. GET endpoints expose
  `blocked_by` and `blocking`. [source: dependencies-rest]
- Issue types are managed at organization level, with at most 25 types per
  organization; the documented setup is not a user-account feature.
  [source: issue-types]
- GitHub CLI 2.94.0 introduced issue types, sub-issues, and dependency
  support across issue commands. [source: gh-294]
- Installed CLI 2.97.0 accepts create `--parent`, `--blocked-by`, and
  `--blocking`; edit accepts `--parent`, `--remove-parent`,
  `--add-sub-issue`, `--remove-sub-issue`, and add/remove variants of
  `--blocked-by` and `--blocking`. Edit also provides `--remove-milestone`.
  [observed: gh --version; gh issue create --help; gh issue edit --help on 2026-09-12]
- `milestone:"title"` and `no:milestone` are documented issue/PR search
  qualifiers. [source: search]

### PR design material

- `<details>` with a `<summary>` label folds Markdown content; the content
  can include headings and code blocks. [source: collapsed]
- GitHub documents browser attachments for issues, PRs, and comments, and
  lists `.md` among accepted text formats; the non-media file cap is 25 MB.
  Upload inserts an attachment URL into the editor. The same page describes
  CLI attachment support for images and videos, not Markdown documents.
  [source: attachments]
- A numerical PR-body character limit is not established by these sources.
  If GitHub rejects an oversized body, a human can attach the Markdown spec
  through the documented browser flow and link it from Design; do not rely
  on an undocumented upload API. [unverified]

## Where this project departs from the subject

The [roadmapping convention](../../../.agents/skills/roadmapping/SKILL.md)
defines a package as one release cut, named `NNN content-name`, and selects
the lowest open ordinal. GitHub imposes none of those semantics. The
convention leaves spanning epics without milestones to avoid unintended
child membership, and uses labels for research and refinement across both
user and organization repositories.

The release convention removes open members before retiring a package and
warns on failures. These are project decisions, not claims that GitHub
enforces release readiness. Contract pins above check workflow wiring;
the executable cases in `tests/test_package_milestones.py` cover the helper's
API handling without establishing live GitHub behavior.

## Not covered

- Prior-session UI observations (default Recently updated sort, the
  Alphabetical menu, and undated-item order) were not independently
  reproduced here. Verify the current milestones page before giving UI
  instructions; release automation must use explicit local ordering.
  [unverified]
- Prior-session claims that `parent-issue:` and `no:parent-issue` work in
  REST search, while `has:parent-issue`, `has:parent`, and `has:sub-issues`
  are ignored, were not independently reproduced. Query a fixture with
  known parents and children before relying on these qualifiers; use
  relationship endpoints for authoritative reads. [unverified]
- Live cross-repository dependency creation, automatic milestone inheritance
  through CLI/API paths, title collisions, and failure races require a
  controlled repository fixture. This research pass made no external writes.
  [unverified]
