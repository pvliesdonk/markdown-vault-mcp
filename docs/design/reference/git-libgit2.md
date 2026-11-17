---
type: Reference
title: Local Git operations through pygit2
description: pygit2 and libgit2 behavior used for commits, history and remote synchronization.
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
  - id: git-abbrev
    title: Git adaptive abbreviation calculation
    resource: https://github.com/git/git/blob/v2.55.0/odb.c
    accessed: 2026-11-16
  - id: short-id
    title: libgit2 short object ID API
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/include/git2/object.h
    accessed: 2026-11-16
  - id: mailmap
    title: libgit2 mailmap API
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/include/git2/mailmap.h
    accessed: 2026-11-16
  - id: blob
    title: libgit2 blob API
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/include/git2/blob.h
    accessed: 2026-11-16
  - id: git-shared
    title: Git core.sharedRepository configuration
    resource: https://github.com/git/git/blob/v2.56.1/Documentation/config/core.adoc
    accessed: 2026-11-17
  - id: git-date-256
    title: Git date parser and formatter
    resource: https://github.com/git/git/blob/v2.56.1/date.c
    accessed: 2026-11-17
  - id: git-pretty
    title: Git pretty formats
    resource: https://github.com/git/git/blob/v2.56.1/Documentation/pretty-formats.adoc
    accessed: 2026-11-17
  - id: git-update-index
    title: Git update-index and split index mode
    resource: https://github.com/git/git/blob/v2.56.1/Documentation/git-update-index.adoc
    accessed: 2026-11-17
  - id: git-remote-config
    title: Git remote configuration
    resource: https://github.com/git/git/blob/v2.56.1/Documentation/config/remote.adoc
    accessed: 2026-11-17
  - id: git-hook-config
    title: Git configured hooks
    resource: https://github.com/git/git/blob/v2.56.1/Documentation/config/hook.adoc
    accessed: 2026-11-17
  - id: git-commit
    title: Git commit message cleanup
    resource: https://github.com/git/git/blob/v2.56.1/Documentation/git-commit.adoc
    accessed: 2026-11-17
  - id: git-pretty-source
    title: Git commit subject formatting
    resource: https://github.com/git/git/blob/v2.56.1/pretty.c
    accessed: 2026-11-17
  - id: git-diff-config
    title: Git diff configuration
    resource: https://github.com/git/git/blob/v2.56.1/Documentation/config/diff.adoc
    accessed: 2026-11-17
  - id: checkout
    title: libgit2 checkout API
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/include/git2/checkout.h
    accessed: 2026-11-17
  - id: merge
    title: libgit2 merge API
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/include/git2/merge.h
    accessed: 2026-11-17
  - id: http-auth
    title: libgit2 HTTP transport authentication
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/src/libgit2/transports/http.c
    accessed: 2026-11-17
  - id: attr-source
    title: libgit2 attribute source precedence
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/src/libgit2/attr.c
    accessed: 2026-11-17
  - id: callbacks
    title: pygit2 remote callbacks
    resource: https://github.com/libgit2/pygit2/blob/v1.21.0/pygit2/callbacks.py
    accessed: 2026-11-17
  - id: rebase-source
    title: libgit2 in-memory rebase implementation
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/src/libgit2/rebase.c
    accessed: 2026-11-17
  - id: rebase-python
    title: pygit2 rebase API
    resource: https://github.com/libgit2/pygit2/blob/v1.21.0/pygit2/rebase.py
    accessed: 2026-11-17
  - id: merge-driver
    title: libgit2 merge driver attribute lookup
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/src/libgit2/merge_driver.c
    accessed: 2026-11-17
  - id: attr-file
    title: libgit2 attribute cache
    resource: https://github.com/libgit2/libgit2/blob/v1.9.7/src/libgit2/attr_file.c
    accessed: 2026-11-17
  - id: clone-proxy
    title: pygit2 clone API
    resource: https://github.com/libgit2/pygit2/blob/v1.21.0/pygit2/__init__.py
    accessed: 2026-11-17
  - id: remote
    title: pygit2 remote API
    resource: https://github.com/libgit2/pygit2/blob/v1.21.0/pygit2/remotes.py
    accessed: 2026-11-17
---

# Local Git operations through pygit2

This reference covers commits, attributes, history and remote synchronization
through pygit2. The native Git stores depend on it.

- pygit2 1.21 supports Python 3.11–3.15 and libgit2 1.9; binary wheels include libgit2. Its own version policy allows breaking changes in a minor version. [source: install]
- An `Index` opened on a path writes to that path; a standalone index is an in-memory flat tree representation. [source: index]
- Git uses `index.lock` to exclude competing writers. The store holds it while writing an alternate index and while a pygit2 reference transaction locks HEAD and its branch. It publishes the index first; if the ref update fails, the operation remains staged and HEAD is unchanged. This is not a crash-atomic transaction across both files. [source: git-lock] [source: transaction] [pins: tests/test_git_libgit2_publication.py::test_failed_ref_publication_leaves_operation_staged]
- Repository discovery, path ignore checks, attributes, and ancestry are exposed by pygit2. [source: repository]
- `find_similar` computes rename scores and accepts a rename threshold; without explicitly requesting detection, configuration may disable it. [source: diff]
- Creating a blob from the working directory reads a repository-relative path and writes to the object database. [source: blob]
- With an effective external `filter.upper.clean` set to `tr a-z A-Z`, Git staged `HELLO` while pygit2 1.21.0's worktree blob contained `hello`. Refuse a path when its filter attribute names a configured, nonempty clean or process driver, or a required filter. Git boolean semantics apply to required, including a valueless true setting and effective overrides; clean and process are commands, so every nonempty value is refused. An unconfigured attribute or empty effective command override is allowed. Tracked index paths are checked at startup and touched paths before writes. [observed: isolated temporary repository, 2026-11-16] [pins: tests/test_git_libgit2_boundaries.py::test_effective_driver_refused_open_and_write]

- Effective nonempty `working-tree-encoding` is refused at startup for tracked paths and before writes: native blob creation does not perform Git’s encoding conversion. [observed: isolated temporary repository, 2026-11-16] [pins: tests/test_git_libgit2_capabilities.py::test_encoding_refused_at_open_and_after_open]
- Supported dates are ISO 8601 beginning with `YYYY-MM-DD`, `N units ago` (including compound and dotted forms), `yesterday`, `last <weekday>`, `today`, `midnight`, and `noon`. Empty history boundaries leave history unfiltered; an empty diff timestamp follows Git’s timestamp resolution. Compact and week-date ISO forms are refused because Git can interpret them differently; other unsupported expressions raise an actionable request error. [source: git-date] [pins: tests/test_git_libgit2_capabilities.py::test_supported_dates_match_git]
- Git's `core.sharedRepository` changes repository permissions for group, all, and octal modes; `umask` and false use the process umask. In synthetic repositories Git and libgit2 produced different index, object-directory and ref permissions for shared modes, so the native store refuses them at open and before writes. [source: git-shared] [observed: Git 2.56.1 and pygit2 1.21.0, synthetic repositories with umask 022 and 077] [pins: tests/test_git_libgit2_lineage.py::test_shared_repository_refused_at_open_and_write, tests/test_git_libgit2_lineage.py::test_shared_repository_umask_modes_allowed]
- With the clock frozen at 2024-01-02 09:30 UTC, Git 2.56.1 resolves bare `noon` to the preceding day's 12:00 UTC. [source: git-date-256] [observed: `TZ=UTC GIT_TEST_DATE_NOW=1704187800 git rev-parse --since=noon`] [pins: tests/test_git_libgit2_capabilities.py::test_noon_before_noon_matches_frozen_git]
- Git's `%aI` formats the author date in strict ISO 8601; Git 2.56.1 renders a zero offset as `Z`. [source: git-pretty] [observed: synthetic UTC author commit, Git 2.56.1] [pins: tests/test_git_libgit2_capabilities.py::test_utc_author_timestamp_matches_cli]
- Effective `remote.*.promisor` settings, including worktree overrides, are checked at open and again before writes and history, diff, and revision queries; effective false remains supported. The committed-revision breadcrumb remains best-effort. [source: git-remote-config] [pins: tests/test_git_libgit2_publication.py::test_worktree_promisor_effective_config, tests/test_git_libgit2_publication.py::test_promisor_enabled_after_open_refused]
- Git patch headers for paths containing spaces include a trailing tab, including added and deleted paths. The CLI captures patches in text mode, which normalizes CRLF and lone CR to LF; native query output follows that decoding. [observed: Git 2.56.1, isolated repository] [pins: tests/test_git_libgit2_publication.py::test_vault_literal_patch_matches_cli, tests/test_git_libgit2_publication.py::test_vault_spaced_path_dev_null_headers]
- Git split-index mode keeps changes in `index` alongside a shared index; pygit2 1.21.0 cannot open the mandatory `link` extension. [source: git-update-index] [observed: Git 2.56.1, synthetic repository before and after opening the store] [pins: tests/test_git_libgit2_capabilities.py::test_split_index_refusal_preserves_state]
- On a case-insensitive filesystem, Git sets `core.ignoreCase` to enable filesystem workarounds. A standalone libgit2 index accepted both `Note.md` and `note.md` for the same file, while the CLI retained one tree entry. [source: git-shared] [observed: Git 2.56.1 and pygit2 1.21.0, synthetic APFS repository] [pins: tests/test_git_libgit2_capabilities.py::test_case_variant_write_has_no_duplicate_tree_entries]
- On macOS with `core.precomposeUnicode=true`, native paths are normalized to NFC before index operations so canonically equivalent names do not become duplicate entries. [observed: synthetic APFS repository, 2026-11-17] [pins: tests/test_git_libgit2_capabilities.py::test_precompose_unicode_write_does_not_duplicate_index]
- With `core.autocrlf=true` set after a warmed-up native write, a cached pygit2 repository stored CRLF where Git's path-filtered blob used LF; reopening the pygit2 repository before the next write produced the Git blob. [source: git-shared] [observed: Git 2.56.1 and pygit2 1.21.0, synthetic repository] [pins: tests/test_git_libgit2_capabilities.py::test_autocrlf_changed_after_open_matches_cli_blob]
- For a non-ASCII patch path, Git's CLI with the project's configuration rendered readable UTF-8 while pygit2 returned octal-quoted bytes; tab and newline paths matched in both aggregate and per-commit diffs. [source: git-shared] [observed: Git 2.56.1 and pygit2 1.21.0, synthetic repository] [pins: tests/test_git_libgit2_capabilities.py::test_literal_diff_paths_match_cli]
- A write whose tree equals HEAD still reconciles its touched index paths; a failed write refreshes the worktree health condition where inspection succeeds. History and per-commit diff limits are clamped to 1–100; an omitted diff limit is unbounded. [pins: tests/test_git_libgit2_boundaries.py::test_noop_reconciles_index_without_observer]
- Git subtracts seconds through weeks as durations, but months and years modify local calendar fields. [source: git-date] [pins: tests/test_git_libgit2_capabilities.py::test_supported_dates_match_git]
- Enabled configured commit, index, and reference hooks are refused at open and before writes; disabled events and effective empty event resets remain supported. Git processes multi-valued events in configuration order. [source: git-hook-config] [pins: tests/test_git_libgit2_capabilities.py::test_disabled_configured_hook_does_not_block_write]
- Git cleans commit messages according to `commit.cleanup`; native writes support default, whitespace, scissors, and verbatim, and refuse strip. History and per-commit subjects follow Git's first-paragraph formatting. Reflog entries use the committer identity and Git's `commit:` or `commit (initial):` prefix with the first LF-delimited message line. [source: git-commit] [source: git-pretty-source] [pins: tests/test_git_libgit2_capabilities.py::test_write_subject_cleanup_matches_cli]
- Aggregate and per-commit diff queries refuse effective nondefault context, inter-hunk context, or mnemonic prefixes. Numeric spellings equivalent to the defaults remain supported. [source: git-diff-config] [pins: tests/test_git_libgit2_capabilities.py::test_diff_settings_match_cli_or_refuse]
- Git and libgit2 may assign different similarity scores to the same deleted and added blobs: an isolated seed scored 25% in Git and 30% in libgit2. Queries refuse uncertain pairs instead of choosing either score, including a newly added note with a potentially unrelated deletion in file history or an in-scope deletion in vault or directory history. Outside deletions alone do not trigger scoped refusal. Unique exact-OID pairs remain supported; ambiguous matches, binary summaries, and mode-changing rename diffs refuse actionably. Committed-revision lookup checks the current path without following renames and returns no breadcrumb when it cannot prove a result. Scoped vault and directory history refuses merges inside the requested window. Single-file history and per-commit diffs refuse merge commits that differ from every parent; revision reads refuse traversal across a merge. A local force push reports success when its tracking ref already matches HEAD. `core.symlinks=false` repositories are refused; with `core.filemode=false`, new regular files and symlink-to-file transitions use mode 100644 and existing regular files retain their mode. [observed: isolated repository, 2026-11-16] [pins: tests/test_git_libgit2_capabilities.py::test_inexact_pairing_refused_across_native_queries]

- Git's automatic abbreviation selects a starting length from an approximate object count, then extends it if ambiguous; libgit2 starts at the configured length or seven and extends for ambiguity. Full object IDs remain unchanged. Native history and per-commit diff queries refuse unset or `auto` `core.abbrev`; set a numeric length or use the CLI. [source: git-abbrev] [source: short-id] [pins: tests/test_git_libgit2_history.py::test_auto_abbreviation_queries_refuse_actionably]
- libgit2 loads repository mailmap sources and resolves a signature to its canonical identity. [source: mailmap] [pins: tests/test_git_libgit2_history.py::test_history_author_honors_mailmap]

- Safe checkout compares a target with the baseline, worktree and index; it updates the worktree and index without moving HEAD. The `DONT_OVERWRITE_IGNORED` option protects ignored files. [source: checkout] [pins: tests/test_git_libgit2_sync.py::test_staged_work_is_preserved, tests/test_git_libgit2_sync.py::test_incoming_file_does_not_overwrite_ignored_local_file]
- A tree merge detects renames and selects `merge=union` using attributes from the worktree before the index; it does not inspect the incoming tree directly. [source: merge-driver] [pins: tests/test_git_libgit2_sync.py::test_incoming_union_attribute_merges_both_versions]
- Attribute `INDEX_ONLY` lookup uses the in-memory index. Loading the incoming tree there exposes its rules without writing the on-disk staged index; attribute cache freshness follows blob identity. Sync uses a private repository handle for that index so a concurrent `committed_revision` read cannot reset it. [source: attr-source] [source: attr-file] [observed: synthetic two-clone repository, 2026-11-17] [pins: tests/test_git_libgit2_sync.py::test_incoming_union_attribute_merges_both_versions, tests/test_git_libgit2_sync.py::test_dry_run_attribute_guard_survives_breadcrumb_reader]
- In-memory rebase applies operations without changing HEAD or the checkout, skips merge commits, and returns an already-applied indication for empty steps. [source: rebase-source] [source: rebase-python] [pins: tests/test_git_libgit2_sync.py::test_unrelated_remote_history_has_actionable_outcome]
- In pygit2 1.21, the clone and remote APIs default `proxy` to `None`, disabling proxy use; `proxy=True` requests automatic proxy detection. [source: clone-proxy] [source: remote] [pins: tests/test_git_libgit2_sync.py::test_native_transport_requests_proxy_auto_detection] The fixture captures arguments on local transports; it does not establish network proxy connectivity or parity with every Git proxy setting.
- The HTTP transport retries authentication challenges and may invoke credential acquisition again after a 401. Native callbacks limit repeated token requests per operation. [source: http-auth] [pins: tests/test_git_libgit2_sync.py::test_credentials_stop_after_rejected_attempt]
- pygit2 1.21 passes through SSH host keys in its default certificate callback, and does not expose the host key to the Python callback. Native sync refuses SSH. [pins: tests/test_git_libgit2_sync.py::test_ssh_transport_refused_with_action] Its certificate callback rejects invalid certificates. [source: callbacks]
- The push callback reports per-reference rejection even when the push operation returns normally. [source: remote] [unverified] The network server callback has not been exercised against a real server hook in these fixtures.
- A local-path push through pygit2 1.21.0 bypassed a bare repository's executable pre-receive hook. [observed: isolated bare local repository, 2026-11-17] [pins: tests/test_git_libgit2_sync.py::test_local_remote_hook_is_refused_at_startup]
- A reference update can fail after checkout; the native store attempts a path-limited restore, and reports dirty state if that restore fails. [observed: injected transaction and restore failures, 2026-11-17] [pins: tests/test_git_libgit2_sync.py::test_failed_checkout_keeps_vault_search_consistent]

## Not covered

SSH server configuration and remote hook behavior over a network transport have
not been exercised in these local fixtures.
