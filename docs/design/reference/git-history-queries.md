---
type: Reference
title: Git history and revision queries
description: "git command-line behaviour that the history and revision-read code in src/markdown_vault_mcp/git/ depends on: log -z and --name-status framing, quotePath, pretty-format placeholders, --follow and rename detection, --since/--until date semantics, ls-tree -l, symlink blobs and LFS pointers"
subject_version: "git-scm.com manual pages ('last updated in' 2.42.0–2.55.0; push/commit/log/diff/config say 2.55.0); git source at master and v2.43.0; observed on git 2.43.0"
valid_for: "git 2.x"
generated:
  by: process:researching-references
  at: 2026-09-06
verified:
  - by: process:researching-references-refute
    at: 2026-09-06
stale_after: 2027-03-06
status: stable
sources:
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
    accessed: 2026-09-06
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
- Does not cover: LFS beyond the pointer, submodules. Push outcomes,
  credentials, URL forms and repository discovery are on [Git push,
  credentials and remotes](/git-push-and-remotes.md); pathspecs, staging,
  committing, identity and rebase state are on [Git staging, commits and
  rebase state](/git-staging-and-commits.md).
- Depended on by: `query.py`; `docs/design/design.md` § "Write + Git
  Integration", the "Git history queries" paragraph.

## Claims

### History queries (`query.py`)

- `log -z` separates commits with NUL; with `--name-status`/`--name-only` each block is `<format>\0`, then (when paths follow) one `\n` and `<status>\0<path>\0` records, `R<score>`/`C<score>` carrying `\0<old>\0<new>\0`; an empty subject is an empty field; the stream ends in the last NUL. The manual documents `-z` path framing only for `--raw`/`--numstat`. [source: git-log] (`-z`) [observed: git 2.43.0, `cat -A` of `log -z --name-status --format=$'\x1e%H%x00...'`] [pins: tests/test_git.py::TestZBlockFraming::test_a_leading_newline_in_a_filename_survives, tests/test_git.py::TestZBlockFraming::test_an_empty_subject_is_a_field_not_padding]
- Under `-z` paths are verbatim; otherwise `core.quotePath` (default true) octal-escapes bytes ≥ 0x80 in double quotes, and `"`, `\`, control characters are escaped even with `core.quotePath=false`. [source: git-config] (`core.quotePath`) [source: git-diff] (`-z`) [observed: git 2.43.0, `"ta\tb.md"` under both settings] [pins: tests/test_git.py::TestQuotedPathsInLogReaders::test_history_survives_a_quote_or_tab_in_the_name, tests/test_git.py::TestQuotedPathsInLogReaders::test_vault_history_reports_usable_non_ascii_paths]
- `%x00` is a NUL; `%H`, `%h`, `%aI` (author date, strict ISO 8601), `%aN`/`%aE` (mailmap-applied), `%s`, `%cI` are documented placeholders. [source: pretty-formats]
- `--follow` "works only for a single file"; two pathspecs die `fatal: --follow requires exactly one pathspec`, but one *directory* pathspec was accepted and yielded rename records for files inside it. `log.follow=true` implies it for a single path. `rev-list` has no `--follow` (unknown option, exit 129). [source: git-log] (`--follow`) [source: src-diff] (`ps->nr != 1`) [source: git-config] (`log.follow`) [source: git-rev-list] [observed: git 2.43.0]
- `--find-renames=<n>` sets the similarity threshold (default 50%) and overrides `diff.renames=false`; `diff.renames` defaults true and governs only porcelain (`diff`, `log`). [source: git-diff] [source: git-config] (`diff.renames`) [observed: git 2.43.0, `-c diff.renames=false ... --find-renames=30` still `R100`] [pins: tests/test_git_revision.py::TestResolvesTheNote::test_rename_that_also_rewrote_half_the_note, tests/test_git_name_reuse.py::TestGenuineLineageIsStillFollowed::test_rename_that_also_rewrote_the_note]
- `diff.renameLimit` (default "currently 1000"; `-l<num>`; 0 = unlimited) caps only the exhaustive inexact pass — exact renames are always found. Over the cap, renames degrade to `D`+`A`, `--follow` stops at the add, and stderr gets `warning: exhaustive rename detection was skipped due to too many files.` and `warning: you may want to set your diff.renameLimit variable to at least <n> and retry the command.` [source: git-config] [source: git-diff] (`-l<num>`) [source: src-diff] (`diff_warn_rename_limit`) [observed: git 2.43.0, six edited renames under `-c diff.renameLimit=2`]
- `--since`/`--after`, `--until`/`--before` compare the **committer** date, inclusive at both bounds; `--since` stops at the first older commit unless `--since-as-filter`. `rev-list --before=<d> -1 HEAD` is the newest commit at or before `<d>`. [source: git-log] (`--since`, `--since-as-filter`) [source: git-rev-list] [observed: git 2.43.0, author dates four days before committer dates: `--until=<committer date>` included the commit, `--until=<author date>` did not] [pins: tests/test_git.py::TestVaultGitHistoryMethods::test_get_history_until_boundary_inclusive, tests/test_git.py::TestGetFileDiff::test_since_timestamp_single_diff]
- `diff --numstat` prints `-\t-\t<path>` for binary, `<added>\t<deleted>\t<path>` otherwise; `--name-status` letters are `A C D M R T U X B`, `R`/`C` carry a score (`R084`) and `<old>` then `<new>`, a symlink replaced by a file is `T`. [source: git-diff] (`--numstat`, `--diff-filter`) [source: src-diff] [observed: git 2.43.0] [pins: tests/test_git.py::TestGetFileDiff::test_get_file_diff_binary_attachment_returns_stat, tests/test_git.py::TestGetFileDiff::test_resolve_path_at_ref_handles_tab_in_filename]
- `hash-object -t tree --stdin </dev/null` is the empty tree (`4b825dc6...` under SHA-1), usable as a `diff` endpoint. [source: git-rev-parse] [observed: git 2.43.0] [pins: tests/test_git.py::TestGetFileDiff::test_empty_tree_resolution_matches_repo_object_format]

### Revision reads (`query.py`: `_tree_entry`, `_blob_text`)

- `ls-tree -l -z <rev> -- <pathspec>` prints `<mode> SP <type> SP <object> SP+ <size> TAB <path>` NUL-terminated (`-` size for a tree); a symlink is mode `120000`, type `blob`, its blob holding the link target; an absent path prints nothing, exit 0; `cat-file blob <sha>` streams raw bytes. [source: git-ls-tree] (`-l`, default format) [observed: git 2.43.0] [pins: tests/test_git_revision.py::TestRefusesRatherThanGuess::test_note_that_was_a_symlink_at_that_revision]
- An LFS pointer is UTF-8 text starting exactly `version https://git-lfs.github.com/spec/v1` (pre-release: `https://hawser.github.com/spec/v1`), then `oid sha256:<hex>`, `size <bytes>`, under 1024 bytes; the version is compared as a plain string. [source: lfs-spec] [pins: tests/test_git_revision.py::TestReviewFindings::test_git_lfs_pointer_is_refused]

## Where this project departs from the subject

By function; the design anchor is `docs/design/design.md` § "Write + Git Integration" unless stated.

- **`_history_log_cmd`, `get_file_history` (`query.py`)** — `--since`/`--until` documented as inclusive (right) without saying they compare committer dates; the directory-branch comment "git rejects `--follow` for anything but a single file" is partial (git rejects >1 pathspec, accepts a directory); `--find-renames=30` and `-c diff.renameLimit=2000` deliberately override `diff.renames`/`diff.renameLimit`. ("Git history queries", #1297.)
- **`_split_z_block` (`query.py`)** — rests on the observed `-z --name-status` framing, which the manual does not document.
- **`_tree_entry` (`query.py`)** — `ls-tree -l -z` with a literal pathspec rather than `<rev>:<path>`; the glob motivation does not apply to `cat-file <rev>:<path>` (a revision expression, exact on 2.43.0), though `ls-tree` still gives mode and size in one call.

## Not covered

- No test covers a diff over `diff.renameLimit`, `--follow` on a directory, or the author-vs-committer date boundary.
