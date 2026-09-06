---
title: Git CLI as the git layer drives it
subject: "git command-line behaviour that src/markdown_vault_mcp/git/ depends on: push refusals and wording, credentials, URL forms, pathspecs, staging, check-ignore, partial commits and identity, ancestry and rebase state, log/diff framing, revision reads"
subject_version: "git-scm.com manual pages ('last updated in' 2.42.0–2.55.0; push/commit/log/diff/config say 2.55.0); git source at master and v2.43.0; observed on git 2.43.0"
valid_for: "git 2.x"
researched: 2026-09-06
review_by: 2027-03-06
status: current
sources:
  - id: git-push
    title: git-push(1)
    url: https://git-scm.com/docs/git-push
    accessed: 2026-09-06
  - id: git-commit
    title: git-commit(1)
    url: https://git-scm.com/docs/git-commit
    accessed: 2026-09-06
  - id: git-add
    title: git-add(1)
    url: https://git-scm.com/docs/git-add
    accessed: 2026-09-06
  - id: git-check-ignore
    title: git-check-ignore(1)
    url: https://git-scm.com/docs/git-check-ignore
    accessed: 2026-09-06
  - id: git-merge-base
    title: git-merge-base(1)
    url: https://git-scm.com/docs/git-merge-base
    accessed: 2026-09-06
  - id: git-log
    title: git-log(1)
    url: https://git-scm.com/docs/git-log
    accessed: 2026-09-06
  - id: git-diff
    title: git-diff(1)
    url: https://git-scm.com/docs/git-diff
    accessed: 2026-09-06
  - id: git-rev-list
    title: git-rev-list(1)
    url: https://git-scm.com/docs/git-rev-list
    accessed: 2026-09-06
  - id: git-rev-parse
    title: git-rev-parse(1)
    url: https://git-scm.com/docs/git-rev-parse
    accessed: 2026-09-06
  - id: git-symbolic-ref
    title: git-symbolic-ref(1)
    url: https://git-scm.com/docs/git-symbolic-ref
    accessed: 2026-09-06
  - id: git-clone
    title: git-clone(1) incl. GIT URLS
    url: https://git-scm.com/docs/git-clone
    accessed: 2026-09-06
  - id: gitcredentials
    title: gitcredentials(7)
    url: https://git-scm.com/docs/gitcredentials
    accessed: 2026-09-06
  - id: gitglossary
    title: gitglossary(7), "pathspec"
    url: https://git-scm.com/docs/gitglossary
    accessed: 2026-09-06
  - id: git-config
    title: git-config(1)
    url: https://git-scm.com/docs/git-config
    accessed: 2026-09-06
  - id: git-man
    title: git(1), environment variables
    url: https://git-scm.com/docs/git
    accessed: 2026-09-06
  - id: git-pull
    title: git-pull(1)
    url: https://git-scm.com/docs/git-pull
    accessed: 2026-09-06
  - id: git-rebase
    title: git-rebase(1)
    url: https://git-scm.com/docs/git-rebase
    accessed: 2026-09-06
  - id: gitrevisions
    title: gitrevisions(7)
    url: https://git-scm.com/docs/gitrevisions
    accessed: 2026-09-06
  - id: pretty-formats
    title: pretty-formats
    url: https://git-scm.com/docs/pretty-formats
    accessed: 2026-09-06
  - id: git-ls-tree
    title: git-ls-tree(1)
    url: https://git-scm.com/docs/git-ls-tree
    accessed: 2026-09-06
  - id: git-remote
    title: git-remote(1)
    url: https://git-scm.com/docs/git-remote
    accessed: 2026-09-06
  - id: lfs-spec
    title: Git LFS spec (docs/spec.md)
    url: https://github.com/git-lfs/git-lfs/blob/main/docs/spec.md
    accessed: 2026-09-06
  - id: lfs-pull
    title: git-lfs-pull(1)
    url: https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-pull.adoc
    accessed: 2026-09-06
  - id: src-transport
    title: transport.c (master; also v2.43.0)
    url: https://raw.githubusercontent.com/git/git/master/transport.c
    accessed: 2026-09-06
  - id: src-push
    title: builtin/push.c (master)
    url: https://raw.githubusercontent.com/git/git/master/builtin/push.c
    accessed: 2026-09-06
  - id: src-receive-pack
    title: builtin/receive-pack.c (master)
    url: https://raw.githubusercontent.com/git/git/master/builtin/receive-pack.c
    accessed: 2026-09-06
  - id: src-remote-curl
    title: remote-curl.c (master)
    url: https://raw.githubusercontent.com/git/git/master/remote-curl.c
    accessed: 2026-09-06
  - id: src-credential
    title: credential.c (master)
    url: https://raw.githubusercontent.com/git/git/master/credential.c
    accessed: 2026-09-06
  - id: src-prompt
    title: prompt.c (master)
    url: https://raw.githubusercontent.com/git/git/master/prompt.c
    accessed: 2026-09-06
  - id: src-setup
    title: setup.c (master)
    url: https://raw.githubusercontent.com/git/git/master/setup.c
    accessed: 2026-09-06
  - id: src-commit
    title: builtin/commit.c (master; also v2.43.0)
    url: https://raw.githubusercontent.com/git/git/master/builtin/commit.c
    accessed: 2026-09-06
  - id: src-pathspec
    title: pathspec.c (master)
    url: https://raw.githubusercontent.com/git/git/master/pathspec.c
    accessed: 2026-09-06
  - id: src-diff
    title: diff.c (master)
    url: https://raw.githubusercontent.com/git/git/master/diff.c
    accessed: 2026-09-06
  - id: src-ident
    title: ident.c (master)
    url: https://raw.githubusercontent.com/git/git/master/ident.c
    accessed: 2026-09-06
  - id: src-sequencer
    title: sequencer.c (master; also v2.43.0)
    url: https://raw.githubusercontent.com/git/git/master/sequencer.c
    accessed: 2026-09-06
  - id: relnotes-2.30.3
    title: Git 2.30.3 release notes (CVE-2022-24765)
    url: https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/2.30.3.adoc
    accessed: 2026-09-06
  - id: relnotes-2.48.0
    title: Git 2.48.0 release notes (remote HEAD on fetch)
    url: https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/2.48.0.adoc
    accessed: 2026-09-06
---

# Git CLI as the git layer drives it

What git's command line does where `src/markdown_vault_mcp/git/` relies on it:
how a push is refused and in what words, how credentials are requested, which
URL forms mean SSH, how pathspecs are read, what `add`/`check-ignore`/`commit
--only` accept, how a rebase leaves state on disk, how `log`/`diff`/`ls-tree`
frame output. Not a tutorial, not branching strategy. `[observed]` claims were
reproduced on git 2.43.0 in throwaway repositories under `/tmp/gitref/`;
"gettext" says whether a string is `_()`/`N_()`-wrapped in git's source and so
follows `LANG`/`LC_ALL`.

## Scope

- Covers: push outcomes, exit codes, stderr, `--porcelain`; askpass and
  `GIT_TERMINAL_PROMPT`; GIT URLS; `safe.directory`; pathspec magic;
  `add`/`check-ignore`/`commit --only`/`--author`; `merge-base --is-ancestor`,
  `merge --ff-only`, rebase state and conflict markers; `log -z`,
  `--name-status`, `--follow`, `--find-renames`, `--since`; `ls-tree -l`,
  LFS pointers, symlink blobs.
- Does not cover: hosting-provider policy text, LFS beyond the pointer, hooks
  other than `pre-receive`, submodules.
- Depended on by: every module in `git/`; `docs/design/design.md` § "Write +
  Git Integration", § "`vault.py`: Thin Façade" (paragraphs "Every pathspec
  the git layer passes is literal", "Per-operation staging guarantee",
  "Scoping the commit itself", "Write identity"), and the
  "Tracking-independent remote ref" / "Git history queries" paragraphs.

## Claims

### Push outcomes (`health.py`, `push_scheduler.py`, `strategy.py`)

- A push is a fast-forward iff the new tip descends from the old; anything else is rejected unless forced. [source: git-push] (NOTE ABOUT FAST-FORWARDS)
- Exit codes: rejected 1; died before the ref report (detached HEAD, no upstream, unreadable remote) 128; success 0 — the manual says only "non-zero if any member push fails". [observed: git 2.43.0, `git push origin` to a bare remote] [source: git-push]
- Per-ref lines go to stderr as ` ! [rejected]        main -> main (<reason>)`: `non-fast-forward` when the local ref is behind a remote tip git already knows, `fetch first` when the remote holds unfetched commits. [source: src-transport] (`print_one_push_report`) [observed: git 2.43.0, same push before and after `git fetch`] [pins: tests/test_git_force_methods.py::TestForcePush::test_non_fast_forward_returns_hint, tests/test_git_health.py::TestStrategyRecordsPushOutcomes::test_a_failed_deferred_push_marks_the_clone_unsynced]
- The same family yields `already exists`, `needs force`, `stale info`, `remote ref updated since checkout`, `new shallow roots not allowed`, `atomic push failed`; hook/policy refusals are `[remote rejected] ... (<server text>)`; a lost status is `[remote failure]`. [source: src-transport] [source: git-push] (`<summary>`)
- `--porcelain` moves the per-ref line to stdout as `<flag>\t<from>:<to>\t<summary> (<reason>)` with full ref names, ends with `Done`, leaves `error:`/`hint:` on stderr, keeps the exit code, and prints nothing when the push dies first. [source: git-push] (OUTPUT) [observed: git 2.43.0]
- The `hint:` block is advice (`advice.pushUpdateRejected`); disabling it removes hints, not the `[rejected] (...)` or `error: failed to push some refs` lines. [source: git-config] [observed: git 2.43.0]
- `git push origin` without a refspec pushes the current branch (`push.default=simple`); no upstream dies `The current branch <b> has no upstream branch.`, detached HEAD `You are not currently on a branch.`, both before transport. [source: git-push] [source: src-push] [observed: git 2.43.0]
- A `pre-receive` hook exiting non-zero refuses every ref; the client prints `[remote rejected] main -> main (pre-receive hook declined)` and relays hook stderr as `remote: ...` lines padded with trailing spaces. [source: src-receive-pack] (`cmd->error_string`) [observed: git 2.43.0, hook `exit 1`, rc=1] [pins: tests/test_git_health.py::TestPushFailureIsDiagnosable::test_the_cause_is_one_line_however_git_wrapped_it]

#### Refusal messages

First stderr line(s) on git 2.43.0 pushing to a bare remote; `<url>` as git prints it.

| Refusal | stderr (2.43.0) | git source | gettext | `--porcelain` (stdout) |
| --- | --- | --- | --- | --- |
| non-fast-forward (fetched, behind) | ` ! [rejected]        main -> main (non-fast-forward)`; `error: failed to push some refs to '<url>'` | `transport.c`; `builtin/push.c` | status+reason no; `error:`/`hint:` yes | `!\trefs/heads/main:refs/heads/main\t[rejected] (non-fast-forward)`, rc=1 |
| fetch first (unfetched remote work) | ` ! [rejected]        main -> main (fetch first)` + error line | same | same | `[rejected] (fetch first)`, rc=1 |
| protected branch / `pre-receive` | `remote: <hook stderr>`; ` ! [remote rejected] main -> main (pre-receive hook declined)` + error line | `transport.c`; reason from `builtin/receive-pack.c` | no (server-authored, over the wire) | `[remote rejected] (pre-receive hook declined)`, rc=1 |
| HTTPS authentication | `fatal: Authentication failed for '<url>/'` (after a `remote:` line) | `remote-curl.c` | yes | none, rc=128 |
| no credential source, prompts off | `fatal: could not read Username for 'https://github.com': terminal prompts disabled` | `prompt.c`; prompt text `credential.c` | no | none, rc=128 |
| dubious ownership | `fatal: detected dubious ownership in repository at '<path>'` + `safe.directory` advice | `setup.c` | yes | none, rc=128 |
| detached HEAD | `fatal: You are not currently on a branch.` + `HEAD:<name-of-remote-branch>` advice | `builtin/push.c` | yes | none, rc=128 |
| no upstream | `fatal: The current branch <b> has no upstream branch.` + `--set-upstream` advice | `builtin/push.c` | yes | none, rc=128 |
| unreachable remote (local path) | `fatal: '<path>' does not appear to be a git repository` / `Could not read from remote repository.` | not traced | unverified | none, rc=128 |

[source: src-transport] [source: src-push] [source: src-receive-pack] [source: src-remote-curl] [source: src-prompt] [source: src-setup] [observed: git 2.43.0]

- `[rejected]`, `[remote rejected]`, `non-fast-forward`, `fetch first` are identical bare strings in `transport.c` at v2.43.0 and master, never gettext-wrapped; `error: failed to push some refs` and every `hint:` line are `_()`/`N_()`. [source: src-transport] [source: src-push]
- Localised output could not be observed: no `de_DE` locale and no `git.mo` catalogues here, so `LANG`/`LC_ALL=de_DE.UTF-8` and `LANGUAGE=de` gave English. [observed: git 2.43.0, `locale -a` = C/C.utf8/POSIX] [unverified] A host with git's `de` catalogue would settle it.

### Credentials and prompts (`_run.py`)

- Askpass order: `GIT_ASKPASS`, `core.askPass`, `SSH_ASKPASS`, then the terminal; the program gets the prompt as its argument and answers on stdout, read up to the first `\r`/`\n`. [source: gitcredentials] [source: src-prompt] (`git_prompt`, `do_askpass`)
- Prompts are literally `Username for '<proto>://<host>': ` and `Password for '<proto>://<user>@<host>': `, built as `"%s for '%s': "` from bare `Username`/`Password` — not localised. [source: src-credential] (`credential_ask_one`) [observed: git 2.43.0, askpass logging `$1` against github.com] [pins: tests/test_git.py::TestGitWriteStrategyClass::test_git_env_askpass_uses_configured_username]
- `GIT_TERMINAL_PROMPT=0` disables only the terminal fallback; askpass is still consulted, and with none set git dies `could not read Username for '...': terminal prompts disabled`. `GIT_ASKPASS` names an executable. [source: git-man] [source: src-prompt] [observed: git 2.43.0] [pins: tests/test_git.py::TestGitWriteStrategyClass::test_askpass_script_is_executable_and_cleanup_pops_key]

### Remote URLs (`_run.py`, `bootstrap.py`)

- Accepted forms: `ssh://[<user>@]<host>[:<port>]/<path>`, `git://`, `http[s]://`, `ftp[s]://` (fetch only), scp-like `[<user>@]<host>:/<path>` (only when no slash precedes the first colon), local paths, `file:///path`, `<transport>::<address>`. So `alice@host:repo.git`, `host:repo.git` and `ssh://host/repo.git` are all SSH. [source: git-clone] (GIT URLS)
- `url.<base>.insteadOf` rewrites "in any context that takes a URL", and `git remote get-url` returns the rewritten form; a missing remote exits 2. [source: git-clone] [source: git-remote] (`get-url`) [observed: git 2.43.0]
- `git clone` without `--depth` is a full clone. [source: git-clone] (`--depth`) [observed: git 2.43.0, `--is-shallow-repository` false]
- A clone has `refs/remotes/origin/HEAD` for the remote's active branch; a hand-added `origin`, or a bare remote whose `HEAD` targets no branch, has none until `remote set-head` or a 2.48+ fetch creates it. [source: git-clone] [source: relnotes-2.48.0] [observed: git 2.43.0, `rev-parse --verify --quiet origin/HEAD` rc=0 in a clone, rc=1 in the repo that seeded the remote] [pins: tests/test_git_tracking_ref.py::TestResolveTrackingRef::test_detached_head_falls_back_to_origin_head, tests/test_git_force_methods.py::TestForceMethodsErrorBranches::test_force_pull_no_remote_when_upstream_and_origin_head_missing]

### Repository discovery and refs (`_run.py`, `conflict.py`)

- `rev-parse --show-toplevel` prints the absolute root; outside a repository it exits 128. [source: git-rev-parse] [observed: git 2.43.0]
- Since 2.30.3/2.35.2 (CVE-2022-24765) git refuses to read a repository owned by another user unless listed in `safe.directory`; every command, `rev-parse --show-toplevel` included, dies `detected dubious ownership`, exit 128; root is exempt only for the `SUDO_UID` owner. [source: relnotes-2.30.3] [source: git-config] (`safe.directory`) [source: src-setup] [observed: git 2.43.0, `chown nobody` as root, and a root-owned repo as `nobody`]
- `symbolic-ref --quiet --short HEAD` prints the branch; detached HEAD exits 1 silently (128 without `--quiet`). `rev-parse --verify --quiet <ref>` prints the id or exits non-zero silently. [source: git-symbolic-ref] [source: git-rev-parse] [observed: git 2.43.0] [pins: tests/test_git_tracking_ref.py::TestResolveTrackingRef::test_non_tracking_checkout_still_resolves]
- `rev-parse --git-dir` is relative (`.git`) from the toplevel and absolute from a linked worktree; `--git-path rebase-merge` resolves the per-worktree state directory either way. [source: git-rev-parse] [observed: git 2.43.0, `git worktree add`]

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

- **`push_failure_reason` (`health.py`)** — narrower: only `non-fast-forward` / `fetch first` are matched; `needs force`, `stale info`, `already exists`, `remote ref updated since checkout` and `[remote rejected] (pre-receive hook declined)` (the protected-branch shape) all land in `push_failed`, `PushResult.hint` carrying the text. Not pinning `LC_ALL`/`LANG` is safe for the two matched strings (never localised); only surrounding lines change language. `--porcelain` would give the same reason on stdout and is unused. ("Sync health", #1287/#1330.)
- **`_push` (`push_scheduler.py`), `_push_locked` (`strategy.py`)** — `git push origin` without a refspec: a detached managed clone or unpublished branch dies at 128 before transport and reads as `push_failed`. ("Tracking-independent remote ref".)
- **`_is_ssh_remote` (`_run.py`)** — contrary to GIT URLS: only `git@`/`ssh://` count, so `alice@host:repo.git` or `host:repo.git` passes the token check, and `check_remote_protocol`'s HTTPS rewrite assumes `git@host:path`. ("HTTPS token auth".)
- **`_normalize_remote` (`_run.py`)** — compares `remote get-url` (already `insteadOf`-rewritten) with `GIT_REPO_URL`; an `insteadOf` rule on the host fails a correct managed-mode configuration.
- **`_build_askpass_env` (`_run.py`)** — `*sername*` matches git's un-localised prompt, but the same script answers any askpass call git spawns (an SSH passphrase would get the token).
- **`_find_git_root`, `run_git*` (`_run.py`)** — the `safe.directory` refusal (128) is indistinguishable from "not a repository"; a container uid differing from the volume owner silently gets no-git mode. Unmodelled in the design doc.
- **`literal_pathspec` (`_run.py`)** — every pathspec `:(literal)`, `check-ignore` exempt; matches git. (§ "`vault.py`", "Every pathspec the git layer passes is literal (#1303, #1304)".)
- **`_ignored_paths`, `_stage_rename` (`strategy.py`)** — exit 1 read as "none", `--no-index --stdin -z`, ignored/untracked old paths dropped, tracked-but-ignored deletions via `-u`; consistent with git as observed. ("Per-operation staging guarantee (#894)", #1238, #1250.)
- **`_commit_staged` (`strategy.py`)** — `commit --only`; the merge refusal is accepted as the right failure; "a partial commit succeeds" during a conflicted rebase held on 2.43.0 but git's source refuses some rebase stops (see the `[unverified]` claim). ("Scoping the commit itself (#1273)".)
- **`_sanitize_git_identity` (`strategy.py`)** — strips `\r \n < >`, a superset of what git drops (git keeps interior `\r`); a name or email sanitised to empty is unhandled and would make `--author` fall into pattern search and die. ("Write identity (#1160)".)
- **`_check_identity` (`strategy.py`)** — reads only `git config user.email`; `GIT_COMMITTER_EMAIL`/`EMAIL` also satisfy git and are ignored, so the warning can fire where commits would succeed.
- **`_history_log_cmd`, `get_file_history` (`query.py`)** — `--since`/`--until` documented as inclusive (right) without saying they compare committer dates; the directory-branch comment "git rejects `--follow` for anything but a single file" is partial (git rejects >1 pathspec, accepts a directory); `--find-renames=30` and `-c diff.renameLimit=2000` deliberately override `diff.renames`/`diff.renameLimit`. ("Git history queries", #1297.)
- **`_split_z_block` (`query.py`)** — rests on the observed `-z --name-status` framing, which the manual does not document.
- **`_tree_entry` (`query.py`)** — `ls-tree -l -z` with a literal pathspec rather than `<rev>:<path>`; the glob motivation does not apply to `cat-file <rev>:<path>` (a revision expression, exact on 2.43.0), though `ls-tree` still gives mode and size in one call.
- **`rebase_in_progress` (`conflict.py`)**, **`_clone_into` (`bootstrap.py`)** — `rebase-merge`/`rebase-apply` under `--git-dir` not `REBASE_HEAD` (#466); full-depth clone; both match git's defaults.
- **`PushScheduler.do_push`** — assumes a `non_fast_forward` push succeeds after the pull loop's rebase; true unless the remote moved again, when `fetch first` recurs (#957).

## Not covered

- No test asserts `fetch first` is what git prints on the un-fetched path or that `non-fast-forward` needs a prior fetch; `tests/test_git_health.py` feeds both strings synthetically. `[remote rejected] (pre-receive hook declined)` is pinned only for line folding; no test drives a real hook, and GitHub/GitLab protected-branch text was not sampled.
- No test covers a scp-like SSH URL without `git@`, an `insteadOf` rewrite, a `safe.directory` refusal, HTTPS authentication failure, a detached-HEAD or no-upstream push, an `--author` emptied by sanitising, a diff over `diff.renameLimit`, `--follow` on a directory, or the author-vs-committer date boundary.
- Localised stderr was unobservable (no `git.mo`); the `_()` claims rest on the source alone. Askpass was observed over HTTPS to github.com only, never for an SSH passphrase.
