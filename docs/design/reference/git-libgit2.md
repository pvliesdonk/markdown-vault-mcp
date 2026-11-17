---
type: Reference
title: Local Git operations through pygit2
description: pygit2 and libgit2 behavior used for local commits and history.
subject_version: "pygit2 1.21.0 and libgit2 1.9.7"
valid_for: "pygit2 1.21 and libgit2 1.9"
generated:
  by: process:researching-references
  at: 2026-11-16T19:45:00-08:00
stale_after: 2027-05-16T19:45:00-07:00
status: stable
sources:
  - id: install
    title: pygit2 installation and version compatibility
    resource: https://github.com/libgit2/pygit2/blob/v1.21.0/docs/install.rst
    accessed: 2026-11-16
  - id: index
    title: pygit2 index and working copy
    resource: https://github.com/libgit2/pygit2/blob/v1.21.0/docs/index_file.rst
    accessed: 2026-11-16
  - id: repository
    title: pygit2 repository API
    resource: https://github.com/libgit2/pygit2/blob/v1.21.0/docs/repository.rst
    accessed: 2026-11-16
  - id: diff
    title: libgit2 diff API
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/include/git2/diff.h
    accessed: 2026-11-16
  - id: transaction
    title: pygit2 reference transaction implementation
    resource: https://github.com/libgit2/pygit2/blob/v1.21.0/pygit2/transaction.py
    accessed: 2026-11-16
  - id: git-lock
    title: Git lockfile API
    resource: https://github.com/git/git/blob/v2.55.0/Documentation/technical/api-lockfile.adoc
    accessed: 2026-11-16
  - id: git-date
    title: Git date parser
    resource: https://github.com/git/git/blob/v2.55.0/date.c
    accessed: 2026-11-16
  - id: blob
    title: libgit2 blob API
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/include/git2/blob.h
    accessed: 2026-11-16
---

# Local Git operations through pygit2

This reference covers local commits, attributes and history through pygit2;
remote operations are outside its scope. The native Git store depends on it.

- pygit2 1.21 supports Python 3.11–3.15 and libgit2 1.9; binary wheels include libgit2. Its own version policy allows breaking changes in a minor version. [source: install]
- An `Index` opened on a path writes to that path; a standalone index is an in-memory flat tree representation. [source: index]
- Git uses `index.lock` to exclude competing writers. The store holds it while writing an alternate index and while a pygit2 reference transaction locks HEAD and its branch. It publishes the index first; if the ref update fails, the operation remains staged and HEAD is unchanged. This is not a crash-atomic transaction across both files. [source: git-lock] [source: transaction] [pins: tests/test_git_libgit2_review.py::test_failed_ref_publication_leaves_operation_staged]
- Repository discovery, path ignore checks, attributes, and ancestry are exposed by pygit2. [source: repository]
- `find_similar` computes rename scores and accepts a rename threshold; without explicitly requesting detection, configuration may disable it. [source: diff]
- Creating a blob from the working directory reads a repository-relative path and writes to the object database. [source: blob]
- With an effective external `filter.upper.clean` set to `tr a-z A-Z`, Git staged `HELLO` while pygit2 1.21.0's worktree blob contained `hello`. Refuse a path when its filter attribute names a configured, nonempty clean or process driver; an unconfigured attribute or empty effective override is allowed. Tracked index paths are checked at startup and touched paths before writes. [observed: isolated temporary repository, 2026-11-16] [pins: tests/test_git_libgit2.py::test_effective_external_clean_filter_is_refused]

- A write whose tree equals HEAD still reconciles its touched index paths; a failed write refreshes the worktree health condition where inspection succeeds. History and per-commit diff limits are clamped to 1–100; an omitted diff limit is unbounded. [pins: tests/test_git_libgit2_replan.py::test_noop_reconciles_index_without_observer]
- Git subtracts seconds through weeks as durations, but months and years modify local calendar fields. [source: git-date] [pins: tests/test_git_libgit2_history.py::test_relative_dates_match_git_parser]
- A rename-and-rewrite fixture scored 36% by Git remains detectable by both Git and libgit2 at the selected 30% threshold. [observed: pygit2 1.21.0, 2026-11-16] [pins: tests/test_git_libgit2_history.py::test_rename_and_rewrite_threshold_agrees_with_cli]

## Not covered

Remote transport, checkout, merge and synchronization are deferred to the remote-sync slice.
