---
type: Reference
title: Git history and revision queries
description: "git command-line behaviour that the history and revision-read code in src/markdown_vault_mcp/git/ depends on: log -z and --name-status framing, quotePath, pretty-format placeholders, --follow and rename detection, --since/--until date semantics, ls-tree -l, symlink blobs and LFS pointers"
subject_version: "git-scm.com manual pages ('last updated in' 2.42.0–2.55.0; push/commit/log/diff/config say 2.55.0); git source at master and v2.43.0; git 2.56.0 release notes; CPython 3.14 subprocess docs/source; newline capture observed on Python 3.12; observed on git 2.43.0, for the ref-verification and error-exit claims 2.55.0, and for the merge-time-rename `--follow` claim 2.55.0 against 2.56.0; the export-snapshot working-tree comparison claims read in the git 2.56.1 documentation as installed and observed on git 2.56.1"
valid_for: "git 2.x"
generated:
  by: process:researching-references
  at: 2026-09-06T12:14:49+02:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-14T16:35:00+00:00
  - by: process:researching-references-refute
    at: 2026-09-06T13:18:18+02:00
stale_after: 2027-03-06
status: stable
sources:
  - id: python-subprocess
    title: Python 3.14 subprocess — frequently used arguments
    resource: https://docs.python.org/3.14/library/subprocess.html#frequently-used-arguments
    accessed: 2026-11-14
  - id: cpython-subprocess
    title: CPython 3.14 Lib/subprocess.py
    resource: https://github.com/python/cpython/blob/3.14/Lib/subprocess.py
    accessed: 2026-11-14
  - id: git-log
    title: git-log(1)
    resource: https://git-scm.com/docs/git-log
    accessed: 2026-09-06
  - id: git-diff
    title: git-diff(1)
    resource: https://git-scm.com/docs/git-diff
    accessed: 2026-09-06
  - id: git-rev-list
    title: git-rev-list(1)
    resource: https://git-scm.com/docs/git-rev-list
    accessed: 2026-09-06
  - id: git-rev-parse
    title: git-rev-parse(1)
    resource: https://git-scm.com/docs/git-rev-parse
    accessed: 2026-09-25
  - id: git-config
    title: git-config(1)
    resource: https://git-scm.com/docs/git-config
    accessed: 2026-09-06
  - id: pretty-formats
    title: pretty-formats
    resource: https://git-scm.com/docs/pretty-formats
    accessed: 2026-09-06
  - id: git-ls-tree
    title: git-ls-tree(1)
    resource: https://git-scm.com/docs/git-ls-tree
    accessed: 2026-09-06
  - id: lfs-spec
    title: Git LFS spec (docs/spec.md)
    resource: https://github.com/git-lfs/git-lfs/blob/main/docs/spec.md
    accessed: 2026-09-06
  - id: src-diff
    title: diff.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/diff.c
    accessed: 2026-09-06
  - id: relnotes-2.56
    title: Git 2.56.0 release notes (Documentation/RelNotes/2.56.0.adoc)
    resource: https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/2.56.0.adoc
    accessed: 2026-10-28
  - id: git-hash-object
    title: git-hash-object(1) (git 2.56.1 documentation as installed)
    resource: https://git-scm.com/docs/git-hash-object
    accessed: 2026-11-18
  - id: gitattributes
    title: gitattributes(5) (git 2.56.1 documentation as installed)
    resource: https://git-scm.com/docs/gitattributes
    accessed: 2026-11-18
  - id: git-update-index
    title: git-update-index(1) (git 2.56.1 documentation as installed)
    resource: https://git-scm.com/docs/git-update-index
    accessed: 2026-11-18
  - id: git-ls-files
    title: git-ls-files(1) (git 2.56.1 documentation as installed)
    resource: https://git-scm.com/docs/git-ls-files
    accessed: 2026-11-18
  - id: gitmodules
    title: gitmodules(5) (git 2.56.1 documentation as installed)
    resource: https://git-scm.com/docs/gitmodules
    accessed: 2026-11-18
  - id: git-diff-submodules
    title: git-diff(1), --ignore-submodules (git 2.56.1 documentation as installed)
    resource: https://git-scm.com/docs/git-diff
    accessed: 2026-11-18
---

# Git history and revision queries

What git's command line does where the history and revision-read code in
`src/markdown_vault_mcp/git/` relies on it: how `log`/`diff`/`ls-tree` frame
output, how renames are followed, which dates `--since`/`--until` compare, how
a blob or an LFS pointer reads at a revision. Not a tutorial, not branching
strategy. Claims marked `observed:` were reproduced on git 2.43.0 in throwaway
repositories under `/tmp/gitref/`.

## Scope

- Covers: `log -z`, `--name-status`, `--follow`, `--find-renames`, `--since`;
  `ls-tree -l`, LFS pointers, symlink blobs.
- Also covers, for the OKF export snapshot comparison against a pinned commit
  (`git_snapshot_changes`, `_git_snapshot_hash` in `query.py`): how
  `hash-object --path --stdin` normalizes working bytes, what index flags hide
  from `diff`, and what `diff --ignore-submodules=none` overrides.
- Does not cover: LFS beyond the pointer, other submodule behaviour. Push outcomes,
  credentials, URL forms and repository discovery are on [Git push,
  credentials and remotes](/git-push-and-remotes.md); pathspecs, staging,
  committing, identity and rebase state are on [Git staging, commits and
  rebase state](/git-staging-and-commits.md).
- Depended on by: `query.py`; `docs/design/design.md` § "Write + Git
  Integration", the "Git history queries" paragraph; the OKF export snapshot
  in `src/markdown_vault_mcp/okf_bundle.py`.

## Claims

### History queries (`query.py`)

- Python subprocess text mode translates CR and CRLF in stdout/stderr to LF even inside a NUL-framed path; binary capture performs no newline conversion. CPython's default text codec is UTF-8 in UTF-8 mode, otherwise the locale encoding; decoding captured bytes with that codec preserves newline characters, and stdin bytes bypass platform text newline conversion. Stderr can be separately normalized with CRLF→LF followed by CR→LF to retain text-mode behavior. [source: python-subprocess] (Frequently used arguments) [source: cpython-subprocess] (`_text_encoding`, `_translate_newlines`, `_save_input`) [observed: Python 3.12 and git 2.43.0, synthetic CR/CRLF filenames, `log -z --name-only` and `check-ignore -z --stdin`, 2026-11-14] [pins: tests/test_git_path_streams.py::test_capture_defaults_and_error_contract, tests/test_git_path_streams.py::test_capture_round_trips_git_path_stdin, tests/test_git.py::TestQuotedPathsInLogReaders::test_history_survives_a_quote_or_tab_in_the_name]
- `log -z` separates commits with NUL; with `--name-status`/`--name-only` each block is `<format>\0`, then (when paths follow) one `\n` and `<status>\0<path>\0` records, `R<score>`/`C<score>` carrying `\0<old>\0<new>\0`; an empty subject is an empty field; the stream ends in the last NUL. The manual documents `-z` path framing only for `--raw`/`--numstat`. [source: git-log] (`-z`) [observed: git 2.43.0, `cat -A` of `log -z --name-status --format=$'\x1e%H%x00...'`] [pins: tests/test_git.py::TestZBlockFraming::test_a_leading_newline_in_a_filename_survives, tests/test_git.py::TestZBlockFraming::test_an_empty_subject_is_a_field_not_padding]
- Under `-z` paths are verbatim; otherwise `core.quotePath` (default true) octal-escapes bytes ≥ 0x80 in double quotes, and `"`, `\`, control characters are escaped even with `core.quotePath=false`. [source: git-config] (`core.quotePath`) [source: git-diff] (`-z`) [observed: git 2.43.0, `"ta\tb.md"` under both settings] [pins: tests/test_git.py::TestQuotedPathsInLogReaders::test_history_survives_a_quote_or_tab_in_the_name, tests/test_git.py::TestQuotedPathsInLogReaders::test_vault_history_reports_usable_non_ascii_paths]
- `%x00` is a NUL; `%H`, `%h`, `%aI` (author date, strict ISO 8601), `%aN`/`%aE` (mailmap-applied), `%s`, `%cI` are documented placeholders. [source: pretty-formats]
- `--follow` "works only for a single file"; two pathspecs die `fatal: --follow requires exactly one pathspec`, but one *directory* pathspec was accepted and yielded rename records for files inside it. `log.follow=true` implies it for a single path. `rev-list` has no `--follow` (unknown option, exit 129). [source: git-log] (`--follow`) [source: src-diff] (`ps->nr != 1`) [source: git-config] (`log.follow`) [source: git-rev-list] [observed: git 2.43.0]
- A rename made while resolving a merge (`merge --no-commit`, then `git mv`) is in neither parent's diff. On git 2.55.0, `log --follow --name-status -- <new>` printed **no commits at all** for it. From git 2.56.0 ("`git log --follow` has been updated to better handle non-linear history, in which the path being tracked gets renamed differently in multiple history lines"), the same walk crosses the merge and carries on under the old name. It prints **no record for the merge itself**: the next block is the branch commit's `M <old>`, with no `R` joining `<old>` to `<new>`, and the walk carries on past the note's creation (`A <old>`) into an earlier owner of `<old>`. `-m --first-parent --follow` prints the merge's `R096 <old> <new>` on both versions. So a reader of a single-path `--follow` stream takes the path a block names as the path git is following, rather than waiting for an `R` record. [source: relnotes-2.56] [observed: git 2.55.0 vs 2.56.0 (Arch `git-2.55.0-1`, `git-2.56.0-1`), the six-commit stranger/birth/branch/merge-rename repository of the pinned test] [pins: tests/test_git_merge_lineage.py::TestTheBoundaryHoldsAcrossTheMerge::test_a_predecessor_of_the_old_name_stays_out, tests/test_git_merge_lineage.py::TestTheFollowWalkThatCrossesTheMergeItself::test_the_creation_under_the_earlier_name_is_the_boundary]
- `--find-renames=<n>` sets the similarity threshold (default 50%) and overrides `diff.renames=false`; `diff.renames` defaults true and governs only porcelain (`diff`, `log`). [source: git-diff] [source: git-config] (`diff.renames`) [observed: git 2.43.0, `-c diff.renames=false ... --find-renames=30` still `R100`] [pins: tests/test_git_revision.py::TestResolvesTheNote::test_rename_that_also_rewrote_half_the_note, tests/test_git_name_reuse.py::TestGenuineLineageIsStillFollowed::test_rename_that_also_rewrote_the_note]
- `diff.renameLimit` (default "currently 1000"; `-l<num>`; 0 = unlimited) caps only the exhaustive inexact pass — exact renames are always found. Over the cap, renames degrade to `D`+`A`, `--follow` stops at the add, and stderr gets `warning: exhaustive rename detection was skipped due to too many files.` and `warning: you may want to set your diff.renameLimit variable to at least <n> and retry the command.` [source: git-config] [source: git-diff] (`-l<num>`) [source: src-diff] (`diff_warn_rename_limit`) [observed: git 2.43.0, six edited renames under `-c diff.renameLimit=2`]
- `--since`/`--after`, `--until`/`--before` compare the **committer** date, inclusive at both bounds; `--since` stops at the first older commit unless `--since-as-filter`. `rev-list --before=<d> -1 HEAD` is the newest commit at or before `<d>`. [source: git-log] (`--since`, `--since-as-filter`) [source: git-rev-list] [observed: git 2.43.0, author dates four days before committer dates: `--until=<committer date>` included the commit, `--until=<author date>` did not] [pins: tests/test_git.py::TestVaultGitHistoryMethods::test_get_history_until_boundary_inclusive, tests/test_git.py::TestGetFileDiff::test_since_timestamp_single_diff]
- In a repository with no commit yet, `git log` and `git rev-list … HEAD` exit 128 (`your current branch 'main' does not have any commits yet`, `ambiguous argument 'HEAD'`), while `rev-parse --verify --quiet HEAD` exits 1 silently and `symbolic-ref -q HEAD` still prints the branch (exit 0). A branch ref file holding garbage also makes `rev-parse --verify --quiet HEAD` exit 1, but `symbolic-ref -q HEAD` then fails with exit 128 (`No such ref: HEAD`), which is what tells the two apart; outside any repository `rev-parse` exits 128. [observed: git 2.55.0, `git init` with no commit under the files and reftable backends, a garbage `refs/heads/main`] [pins: tests/test_git_unborn_head.py::test_history_is_empty, tests/test_git_unborn_head.py::test_diff_since_a_timestamp_is_empty, tests/test_git_unborn_head.py::test_head_probe_outside_a_repository_is_a_fault, tests/test_git_unborn_head.py::test_corrupt_branch_ref_is_a_fault_not_an_empty_history]
- `merge-base --is-ancestor <a> <b>` exits 0 when *a* is an ancestor of *b*, 1 when it is not (a commit on a branch the checkout left), and 128 with `Not a valid commit name` for an unknown object and `Not a valid object name HEAD` on an unborn HEAD, so only exit 1 means "not an ancestor". [observed: git 2.55.0, a side-branch commit, an ancestor, an all-zero id, `checkout --orphan`] [pins: tests/test_git_revision.py::TestRefusesRatherThanGuess::test_revision_that_is_not_an_ancestor, tests/test_git_revision_faults.py::test_revision_on_an_empty_branch_is_a_refusal]
- git never rejects `--since`/`--until`/`--before` text: it turns what it cannot parse into a date of its own choosing and exits 0. `zzz` and `not a date at all` became about the current time (`--since` kept only a commit made seconds earlier, `--until` kept all), `2026-13-45` a date between two commits, and `rev-list --before=zzz -1 HEAD` returned HEAD. A malformed date therefore filters silently by the wrong date, and a non-zero exit from `log` or `rev-list` is never caused by the date. [observed: git 2.55.0, a repository with one 2020 commit and one new one]
- An unknown revision in a range is fatal and indistinguishable by exit code from other failures: `log deadbeef..HEAD` exits 128 with `fatal: ambiguous argument 'deadbeef..HEAD': unknown revision or path not in the working tree.`, as does `rev-list … HEAD` in a repository with no commits. [observed: git 2.55.0]
- `rev-parse --verify --quiet <name>` does "not output an error message if the first argument is not a valid object name; instead exit with non-zero status silently", and prints the full object id on success. `<sha>^{commit}` exits 0 for a commit and 1 for an unknown name, a prefix no commit uniquely matches, or another kind of object (a tree prints `expected commit type` to stderr despite `--quiet`); the manual promises only "non-zero", 1 is observed. A prefix a commit shares with a blob passes `^{commit}`; `git diff <prefix>..HEAD`, `git diff <prefix>:<path> HEAD:<path>` and `rev-list <prefix>..HEAD` accept it too, while the bare forms `git diff <prefix> HEAD` and `merge-base --is-ancestor <prefix> HEAD` exit 128 with `short object ID … is ambiguous`. `get_file_diff` resolves rename pairing with the bare form, so it carries on with the full id `rev-parse` printed. `--end-of-options` stops a name that starts with `-` being read as an option. [source: git-rev-parse] (`--verify`, `--quiet`, `--end-of-options`) [observed: git 2.55.0, an unknown SHA, a tree id, a valid 7-digit prefix, `--all`, a 4-digit prefix one commit shares with one blob] [pins: tests/test_git.py::TestGetFileDiff::test_unknown_ref_is_an_invalid_request, tests/test_git.py::TestGetFileDiff::test_ref_naming_a_tree_is_an_invalid_request, tests/test_git.py::TestGetFileDiff::test_prefix_a_commit_shares_with_a_blob_resolves]
- `diff <ref> HEAD -- <path>` exits 0 when the path is absent at either end, or at both (empty output), so a failing `diff` against a valid ref is never the path's doing. [observed: git 2.55.0]
- `diff --numstat` prints `-\t-\t<path>` for binary, `<added>\t<deleted>\t<path>` otherwise; `--name-status` letters are `A C D M R T U X B`, `R`/`C` carry a score (`R084`) and `<old>` then `<new>`, a symlink replaced by a file is `T`. [source: git-diff] (`--numstat`, `--diff-filter`) [source: src-diff] [observed: git 2.43.0] [pins: tests/test_git.py::TestGetFileDiff::test_get_file_diff_binary_attachment_returns_stat, tests/test_git.py::TestGetFileDiff::test_resolve_path_at_ref_handles_tab_in_filename]
- `hash-object -t tree --stdin </dev/null` is the empty tree (`4b825dc6...` under SHA-1), usable as a `diff` endpoint. [source: git-rev-parse] [observed: git 2.43.0] [pins: tests/test_git.py::TestGetFileDiff::test_empty_tree_resolution_matches_repo_object_format]

### Revision reads (`query.py`: `_tree_entry`, `_blob_bytes`, `_note_text`)

- `ls-tree -l -z <rev> -- <pathspec>` prints `<mode> SP <type> SP <object> SP+ <size> TAB <path>` NUL-terminated (`-` size for a tree); a symlink is mode `120000`, type `blob`, its blob holding the link target; an absent path prints nothing, exit 0; a submodule entry is mode `160000`, type `commit`, size `-`, and a gitlink later replaced by a file reads as a `T` record in `log --name-status`; `cat-file blob <sha>` streams raw bytes. [source: git-ls-tree] (`-l`, default format) [observed: git 2.43.0; the gitlink on 2.55.0] [pins: tests/test_git_revision.py::TestRefusesRatherThanGuess::test_note_that_was_a_symlink_at_that_revision, tests/test_git_revision_faults.py::test_note_never_at_that_revision_is_a_refusal, tests/test_git_revision_faults.py::test_submodule_at_that_revision_is_a_refusal]
- An LFS pointer is UTF-8 text starting exactly `version https://git-lfs.github.com/spec/v1` (pre-release: `https://hawser.github.com/spec/v1`), then `oid sha256:<hex>`, `size <bytes>`, under 1024 bytes; the version is compared as a plain string. [source: lfs-spec] [pins: tests/test_git_revision.py::TestReviewFindings::test_git_lfs_pointer_is_refused]

### Export snapshot comparison (`query.py`: `git_snapshot_changes`, `_git_snapshot_hash`)

- `hash-object --path=<file> --stdin` hashes standard input "as if it were located at the given path": the path decides which Git filters apply before hashing, so the id can differ from the id of the raw bytes. Those filters are the attributes mechanism's, including end-of-line conversion (`text`, `eol`) and a `filter` driver's `clean` command. Without `--path`, input read from standard input is hashed as is ("always implied"); without `-w` nothing is written to the object database. Comparing the printed id with a committed blob id therefore compares working bytes with what `git add` would store, so a clean file with `*.md text eol=crlf` and CRLF working bytes matches its LF blob, which a byte comparison would call modified. [source: git-hash-object] (`--path`, `--no-filters`, `-w`) [source: gitattributes] (`text`, End-of-line conversion, `filter`) [observed: git 2.56.1, `.gitattributes` `*.md text eol=crlf`, LF blob committed, CRLF bytes on stdin: `--path=n.md` printed the blob id, plain `--stdin` a different one, and a changed body under `--path` a different one, 2026-11-18] [pins: tests/test_okf_export_snapshot.py::test_git_flagged_crlf_uses_git_normalization]
- The `--path` argument is the file's name as the repository sees it, run with the repository root as the working directory of the command (`git -C <root>`): the pin covers a vault at the repository root, a vault in a subdirectory and a submodule child, each passing the name relative to its own repository. The documentation says only "the given path" without naming what it is relative to. [source: git-hash-object] (`--path`) [observed: the tests' three layouts on git 2.56.1] [pins: tests/test_okf_export_snapshot.py::test_git_flagged_crlf_uses_git_normalization]
- A path marked `--assume-unchanged` lets Git "assume that the working tree file matches what is recorded in the index"; a `--skip-worktree` path is treated "as unchanged when it is not present in the working directory". `git diff HEAD` therefore lists neither an edit to a flagged file nor the deletion of a skip-worktree file; `ls-files -v` marks `assume-unchanged` files with a lowercase tag and skip-worktree with `S`, and a path carrying both bits prints `s`. [source: git-update-index] (`--assume-unchanged`, Skip-worktree bit) [source: git-ls-files] (`-t`, `-v`) [observed: git 2.56.1, committed notes flagged `--assume-unchanged`, `--skip-worktree` and both bits (set by separate `update-index` calls, in either order), each appended to; `diff --name-only HEAD` printed nothing, also after deleting the skip-worktree one; `ls-files -v` printed `h`, `S`, `s` and `-t` `H`, `S`, `S`; one call given both `--skip-worktree --assume-unchanged` set only the assume-unchanged bit (`h`, flags `8000` in `ls-files --debug`), 2026-11-18] [pins: tests/test_okf_export_snapshot.py::test_git_flagged_files_are_checked_safely]
- `diff --ignore-submodules=none` "will consider the submodule modified when it either contains untracked or modified files or its `HEAD` differs from the commit recorded in the superproject and can be used to override any settings of the `ignore` option in git-config or gitmodules"; `submodule.<name>.ignore=all` otherwise means the submodule "will never be considered modified" by `status`, `update-index`, `diff` and `log`, while `status` and `commit` still show it once staged. A staged gitlink (a new child commit added to the index) whose child checkout has been moved back to the recorded commit is therefore absent from `diff --cached --name-only <commit>` under `ignore=all` and listed (as the submodule path) when `--ignore-submodules=none` is given. [source: git-diff-submodules] (`--ignore-submodules`) [source: gitmodules] (`submodule.<name>.ignore`) [observed: git 2.56.1, `.gitmodules` `submodule.vendor.ignore=all`, empty child commit staged, child reset to the recorded commit: `diff --cached --name-only` printed nothing, with `--ignore-submodules=none` printed `vendor`, 2026-11-18] [pins: tests/test_okf_export_snapshot.py::test_git_gitlink_change_is_reported]

## Where this project departs from the subject

By function; the design anchor is `docs/design/design.md` § "Write + Git Integration" unless stated.

- **`_history_log_args`, `get_file_history` (`query.py`)** — `--since`/`--until` documented as inclusive (right) without saying they compare committer dates; the directory-branch comment "git rejects `--follow` for anything but a single file" is partial (git rejects >1 pathspec, accepts a directory); `--find-renames=30` and `-c diff.renameLimit=2000` deliberately override `diff.renames`/`diff.renameLimit`. ("Git history queries", #1297.)
- **`_split_z_block` (`query.py`)** — rests on the observed `-z --name-status` framing, which the manual does not document.
- **`_tree_entry` (`query.py`)** — `ls-tree -l -z` with a literal pathspec rather than `<rev>:<path>`; the glob motivation does not apply to `cat-file <rev>:<path>` (a revision expression, exact on 2.43.0), though `ls-tree` still gives mode and size in one call.

## Not covered

- The export snapshot claims were observed on git 2.56.1 only. The effect of a `clean` filter driver (as opposed to `eol`) on `hash-object --path` was not checked, nor was any claim on another version.
- No test covers a diff over `diff.renameLimit`, `--follow` on a directory, or the author-vs-committer date boundary.
