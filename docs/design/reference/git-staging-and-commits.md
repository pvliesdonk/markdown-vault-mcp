---
type: Reference
title: Git staging, commits and rebase state
description: "git command-line behaviour that the staging, committing and pull/rebase code in src/markdown_vault_mcp/git/ depends on: pathspec magic, add and check-ignore, partial commits and identity, merge-base and fast-forward, rebase state on disk and conflict markers"
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
  - id: git-commit
    title: git-commit(1)
    resource: https://git-scm.com/docs/git-commit
    accessed: 2026-09-06
  - id: git-add
    title: git-add(1)
    resource: https://git-scm.com/docs/git-add
    accessed: 2026-09-06
  - id: git-check-ignore
    title: git-check-ignore(1)
    resource: https://git-scm.com/docs/git-check-ignore
    accessed: 2026-09-06
  - id: git-merge-base
    title: git-merge-base(1)
    resource: https://git-scm.com/docs/git-merge-base
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
  - id: gitglossary
    title: gitglossary(7), "pathspec"
    resource: https://git-scm.com/docs/gitglossary
    accessed: 2026-09-06
  - id: git-config
    title: git-config(1)
    resource: https://git-scm.com/docs/git-config
    accessed: 2026-09-06
  - id: git-man
    title: git(1), environment variables
    resource: https://git-scm.com/docs/git
    accessed: 2026-09-06
  - id: git-pull
    title: git-pull(1)
    resource: https://git-scm.com/docs/git-pull
    accessed: 2026-09-06
  - id: git-rebase
    title: git-rebase(1)
    resource: https://git-scm.com/docs/git-rebase
    accessed: 2026-09-06
  - id: gitrevisions
    title: gitrevisions(7)
    resource: https://git-scm.com/docs/gitrevisions
    accessed: 2026-09-06
  - id: lfs-pull
    title: git-lfs-pull(1)
    resource: https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-pull.adoc
    accessed: 2026-09-06
  - id: src-commit
    title: builtin/commit.c (master; also v2.43.0)
    resource: https://raw.githubusercontent.com/git/git/master/builtin/commit.c
    accessed: 2026-09-06
  - id: src-pathspec
    title: pathspec.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/pathspec.c
    accessed: 2026-09-06
  - id: src-diff
    title: diff.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/diff.c
    accessed: 2026-09-06
  - id: src-ident
    title: ident.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/ident.c
    accessed: 2026-09-06
  - id: src-sequencer
    title: sequencer.c (master; also v2.43.0)
    resource: https://raw.githubusercontent.com/git/git/master/sequencer.c
    accessed: 2026-09-06
---

# Git staging, commits and rebase state

What git's command line does where the staging, committing and pull/rebase
code in `src/markdown_vault_mcp/git/` relies on it: how pathspecs are read,
what `add`/`check-ignore`/`commit --only` accept, how identity is taken, how
ancestry and fast-forwards are decided, how a rebase leaves state on disk. Not
a tutorial, not branching strategy. Claims marked `observed:` were reproduced on git 2.43.0 in throwaway
repositories under `/tmp/gitref/`.

## Scope

- Covers: pathspec magic; `add`/`check-ignore`/`commit --only`/`--author`;
  `merge-base --is-ancestor`, `merge --ff-only`, rebase state and conflict
  markers.
- Does not cover: LFS beyond `git lfs pull`, submodules. Push outcomes,
  credentials, URL forms and repository discovery are on [Git push,
  credentials and remotes](/git-push-and-remotes.md); `log`/`diff`/`ls-tree`
  framing is on [Git history and revision queries](/git-history-queries.md).
- Depended on by: `strategy.py`, `conflict.py`, `_run.py` (pathspecs),
  `bootstrap.py` (fetch and clone); `docs/design/design.md` § "Write + Git
  Integration", § "`vault.py`: Thin Façade" (paragraphs "Every pathspec the
  git layer passes is literal", "Per-operation staging guarantee", "Scoping
  the commit itself", "Write identity").

## Claims

### Pathspecs (`_run.py`)

- A bare pathspec is a pattern: the part after the last slash is `fnmatch(3)`-matched, so `*`, `?`, `[...]` in a note's name select siblings. [source: gitglossary] [observed: git 2.43.0, `ls-files -- 'star*note.md'` also lists `starXnote.md`] [pins: tests/test_git_revision.py::TestResolvesTheNote::test_path_that_looks_like_a_glob_or_an_option, tests/test_git.py::TestGlobMetacharactersInNoteNames::test_delete_stages_only_the_notes_own_removal]
- Long-form magic `:(word,...)pattern`: `literal` disables wildcards, `top` (`:/`) anchors at the toplevel, `glob` is `FNM_PATHNAME` with `**`, `exclude` (`:!`) negates, plus `icase`, `attr`; `glob` and `literal` are incompatible; `GIT_LITERAL_PATHSPECS=1` is the environment-wide `literal`. [source: gitglossary] [source: git-man]
- `:(literal)` is accepted by `add`, `rm`, `ls-files`, `ls-tree`, `diff --cached`, `checkout`, `commit --only`, `log --follow`; `--follow` forbids every magic but `top`/`literal`. [source: src-diff] (`diff_check_follow_pathspec`) [observed: git 2.43.0]
- `check-ignore` takes pathnames: any magic, from argv or `--stdin`, dies `fatal: :(literal)x: pathspec magic not supported by this command: 'literal'`, exit 128. [source: src-pathspec] [observed: git 2.43.0]
- `<rev>:<path>` (`git show HEAD:star*note.md`) is a revision expression naming the exact path, not a pathspec. [source: gitrevisions] [observed: git 2.43.0]

### Staging (`strategy.py`: `_stage_one`, `_stage_rename`, `_ignored_paths`, `_is_tracked`)

- `git add` refuses an explicitly named ignored path (`The following paths are ignored by one of your .gitignore files:`), exit 1, staging nothing from that invocation; `--ignore-errors`, `-A` and `:(literal)` do not change this, while `add -u` on an ignored untracked path exits 0 staging nothing. [source: git-add] (DESCRIPTION, `--update`) [observed: git 2.43.0] [pins: tests/test_git.py::TestRenameStagingSkipsIgnoredPaths::test_moving_an_ignored_file_does_not_raise]
- A pathspec matching neither working tree nor index makes the whole `add` die `fatal: pathspec '<p>' did not match any files`, exit 128, other pathspecs unstaged; `--ignore-missing` is valid only with `--dry-run`. [source: git-add] [observed: git 2.43.0, `add -- ':(literal)nope.md' ':(literal)a.md'`] [pins: tests/test_git.py::TestRenameStagingIsScoped::test_never_committed_old_path_still_records_the_move]
- `add -A -- <p>` and `add -u -- <p>` both stage the deletion of a tracked path missing from the working tree; `-A` over old and new names stages a rename; `:(literal)` works in each. [source: git-add] (`--all`, `--update`) [observed: git 2.43.0] [pins: tests/test_git.py::TestGlobMetacharactersInNoteNames::test_rename_stages_both_sides_of_the_note_and_nothing_else, tests/test_git.py::TestRenameStagingSkipsIgnoredPaths::test_ignored_destination_still_records_the_deletion]
- A tracked file under an ignored directory is stageable with `-u` (index-driven) but a plain `add <path>` refuses it as ignored. [source: git-add] [observed: git 2.43.0] [pins: tests/test_git.py::TestRenameStagingSkipsIgnoredPaths::test_tracked_file_moved_within_an_ignored_directory_records_the_deletion]
- `check-ignore` exits 0 if any path is ignored, 1 if none, 128 on error; `-z` only with `--stdin` (`fatal: -z only makes sense with --stdin`), then input and output are NUL-separated and each ignored path is echoed exactly as given, absolute included; a path outside the repository is fatal. [source: git-check-ignore] (EXIT STATUS, `-z`) [source: src-pathspec] [observed: git 2.43.0] [pins: tests/test_git.py::TestTheExcludeProbeItself::test_it_reports_only_the_excluded_paths, tests/test_git.py::TestTheExcludeProbeItself::test_a_signalled_probe_is_a_failure_too]
- `--no-index` answers from the rules alone, so a tracked file under an ignored directory is reported ignored; without it the index wins. [source: git-check-ignore] (`--no-index`) [observed: git 2.43.0] [pins: tests/test_git.py::TestTheExcludeProbeItself::test_a_tracked_file_under_an_ignored_directory_still_counts]
- `ls-files -- <pathspec>` lists index entries, so a tracked file deleted from the working tree still appears; `diff --cached --quiet -- <pathspec>` exits 1 iff the index differs from HEAD within it. [source: git-add] (`--update`) [observed: git 2.43.0] [pins: tests/test_git.py::TestRenameStagingSkipsIgnoredPaths::test_a_noop_rename_does_not_commit_unrelated_staged_work]

### Committing and identity (`strategy.py`: `_commit_staged`, `_sanitize_git_identity`, `_check_identity`)

- `commit --only -- <paths>` commits the *working-tree* content of those paths and nothing else staged; `--only` without paths is `fatal: No paths with --include/--only does not make sense.` outside `--amend`/`--allow-empty`; an unchanged path exits 1 `nothing to commit`. [source: git-commit] (`--only`) [source: src-commit] [observed: git 2.43.0, index `idx`, tree `wt`, commit recorded `wt`] [pins: tests/test_git.py::TestCommitsAreScopedToTheOperation::test_a_real_write_commits_only_its_own_path]
- During a merge (`MERGE_HEAD`) a partial commit dies `fatal: cannot do a partial commit during a merge.`, exit 128; the whole-index form dies on the unmerged paths instead. [source: src-commit] (`COMMIT_PARTIAL`) [observed: git 2.43.0] [pins: tests/test_git.py::TestCommitsAreScopedToTheOperation::test_a_write_during_a_merge_commits_nothing]
- During a conflicted non-interactive rebase (`rebase-merge/` present, no `MERGE_HEAD`) a partial commit of an unrelated path succeeded on 2.43.0 (`<file>: needs merge`, commit on the detached rebase HEAD) while the whole-index form failed `Committing is not possible because you have unmerged files.`; the source at v2.43.0 and master also has `cannot do a partial commit during a rebase.`, keyed on `CHERRY_PICK_HEAD` existing and equalling `REBASE_HEAD`. [observed: git 2.43.0] [source: src-commit] [source: src-sequencer] (`sequencer_determine_whence`) [unverified] Which rebase stops set `CHERRY_PICK_HEAD` was not established; an interactive `edit` stop would show it.
- `--author='Name <email>'` overrides the author only; the committer comes from `user.name`/`user.email`, and `-c user.*` beats `GIT_COMMITTER_*`; an `--author` not of that form is a search pattern and, matching nothing, dies `--author '<v>' is not 'Name <email>' and matches no existing author`. [source: git-commit] (`--author`) [source: src-commit] [observed: git 2.43.0, `cat-file commit`: `author AN <ae@x>` / `committer T <t@x>`] [pins: tests/test_git.py::TestOidcClaimAuthorCommitterSplit::test_principal_sets_author_committer_stays_static, tests/test_git.py::TestCommitterIdentityInCommit::test_custom_committer_in_commit_flags]
- Git drops `\n`, `<`, `>` from an ident and trims leading/trailing crud (control chars, `,` `:` `;` `"` `\` `'`); an interior `\r` survives; a doubled `<` yields no commit. [source: src-ident] (`crud`, `strbuf_addstr_without_crud`) [observed: git 2.43.0, `'Bad\nGuy'` → `BadGuy`, `'Car\rriage'` kept CR] [pins: tests/test_git.py::TestStageAndCommitAuthorSplit::test_newline_in_author_name_is_stripped, tests/test_git.py::TestStageAndCommitAuthorSplit::test_angle_brackets_in_author_name_are_stripped]
- `git config user.email` exits 0 with the value, or 1 printing nothing when unset at every scope; committing with no identity anywhere dies `Author identity unknown`, 128. [source: git-config] (exit status, ret=1) [observed: git 2.43.0, `GIT_CONFIG_GLOBAL=/dev/null`] [pins: tests/test_git.py::TestCheckIdentity::test_check_identity_warns_when_no_user_email]

### Ancestry, fetch, merge, rebase, pull (`strategy.py`, `bootstrap.py`)

- `merge-base --is-ancestor A B` exits 0 if A is an ancestor of B (A == B included), 1 if not, another code (128 for an unknown object) on error. [source: git-merge-base] [observed: git 2.43.0] [pins: tests/test_git_force_methods.py::TestPullDivergenceClassification::test_a_clone_that_is_only_ahead_pulls_nothing, tests/test_git_revision.py::TestRefusesRatherThanGuess::test_revision_that_is_not_an_ancestor]
- `merge --ff-only <ref>` on divergence dies `fatal: Not possible to fast-forward, aborting.`, 128; `git pull` with no `pull.rebase`/`pull.ff`/flag dies `fatal: Need to specify how to reconcile divergent branches.` after the `You have divergent branches` hint. [source: git-pull] (`--ff-only`, `--rebase`) [observed: git 2.43.0] [pins: tests/test_git_force_methods.py::TestPullDivergenceClassification::test_an_available_fast_forward_can_still_fail_on_a_dirty_tree]
- `rebase <ref>` stopping on a conflict exits 1 (`CONFLICT (content): Merge conflict in <path>`, `Could not apply <sha>... <subject>`); `rebase --abort` on a clean tree exits 128 `fatal: No rebase in progress?`. [source: git-rebase] (`--abort`) [observed: git 2.43.0]
- `rev-list --count A..B` counts commits in B not A; `log A..B` with an unknown A dies `ambiguous argument`, 128; `fetch origin` against a missing remote dies `does not appear to be a git repository`, 128. [source: git-rev-list] [observed: git 2.43.0] [pins: tests/test_git_force_methods.py::TestForceMethodsErrorBranches::test_force_pull_fetch_failed_when_remote_unreachable]
- `git lfs pull` is `lfs fetch` + `lfs checkout` for the checked-out ref; without git-lfs, `git lfs` is an unknown command, exit 1. [source: lfs-pull] [observed: git 2.43.0] [pins: tests/test_git.py::TestGitLfsSupport::test_git_lfs_pull_failure_logged_not_raised]

### Conflicted rebase state (`conflict.py`)

- `diff --name-only -z --diff-filter=U` lists unmerged paths NUL-terminated and unquoted; without `-z` a non-ASCII name renders `"n\303\266 te.md"`. [source: git-diff] (`--diff-filter`, `-z`) [observed: git 2.43.0] [pins: tests/test_git.py::TestConflictPathsAreUsable::test_a_non_ascii_conflict_is_resolved_and_saved]
- `REBASE_HEAD` is the commit being replayed and `REBASE_HEAD:<path>` reads its version; the ref outlives a successful `--continue`, so it is no in-progress signal. [source: gitrevisions] [source: git-rebase] [observed: git 2.43.0] [pins: tests/test_git.py::TestRebaseInProgress::test_ignores_stale_rebase_head_ref]
- The merge backend keeps state in `$GIT_DIR/rebase-merge/` (`am` backend: `rebase-apply/`); both vanish on completion or `--abort`; `rev-parse --git-path rebase-merge` finds it in a linked worktree. [source: src-sequencer] (`rebase_path`) [source: git-rev-parse] [observed: git 2.43.0] [pins: tests/test_git.py::TestRebaseInProgress::test_detects_rebase_merge_directory]
- In a rebase "ours" is the upstream side and "theirs" the replayed local commit ("the sides are swapped"), so `checkout --ours -- <path>` takes upstream; `rebase --continue` under `GIT_EDITOR=true` needs no editor. [source: git-rebase] (`--merge`) [observed: git 2.43.0]
- Default markers `<<<<<<< HEAD` / `=======` / `>>>>>>> <sha> (<subject>)`; `merge.conflictStyle=diff3|zdiff3` add `||||||| parent of <sha> (<subject>)` with the base. [source: git-config] [observed: git 2.43.0]

## Where this project departs from the subject

By function; the design anchor is `docs/design/design.md` § "Write + Git Integration" unless stated.

- **`literal_pathspec` (`_run.py`)** — every pathspec `:(literal)`, `check-ignore` exempt; matches git. (§ "`vault.py`", "Every pathspec the git layer passes is literal (#1303, #1304)".)
- **`_ignored_paths`, `_stage_rename` (`strategy.py`)** — exit 1 read as "none", `--no-index --stdin -z`, ignored/untracked old paths dropped, tracked-but-ignored deletions via `-u`; consistent with git as observed. ("Per-operation staging guarantee (#894)", #1238, #1250.)
- **`_commit_staged` (`strategy.py`)** — `commit --only`; the merge refusal is accepted as the right failure; "a partial commit succeeds" during a conflicted rebase held on 2.43.0 but git's source refuses some rebase stops (see the `[unverified]` claim). ("Scoping the commit itself (#1273)".)
- **`_sanitize_git_identity` (`strategy.py`)** — strips `\r \n < >`, a superset of what git drops (git keeps interior `\r`); a name or email sanitised to empty is unhandled and would make `--author` fall into pattern search and die. ("Write identity (#1160)".)
- **`_check_identity` (`strategy.py`)** — reads only `git config user.email`; `GIT_COMMITTER_EMAIL`/`EMAIL` also satisfy git and are ignored, so the warning can fire where commits would succeed.
- **`rebase_in_progress` (`conflict.py`)**, **`_clone_into` (`bootstrap.py`)** — `rebase-merge`/`rebase-apply` under `--git-dir` not `REBASE_HEAD` (#466); full-depth clone; both match git's defaults.

## Not covered

- No test covers an `--author` emptied by sanitising.
