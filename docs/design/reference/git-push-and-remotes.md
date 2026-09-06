---
type: Reference
title: Git push, credentials and remotes
description: "git command-line behaviour that the push, credential, remote-URL and repository-discovery code in src/markdown_vault_mcp/git/ depends on: push refusals and wording, --porcelain, askpass and GIT_TERMINAL_PROMPT, URL forms, safe.directory, symbolic-ref and rev-parse"
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
  - id: git-push
    title: git-push(1)
    resource: https://git-scm.com/docs/git-push
    accessed: 2026-09-06
  - id: git-rev-parse
    title: git-rev-parse(1)
    resource: https://git-scm.com/docs/git-rev-parse
    accessed: 2026-09-06
  - id: git-symbolic-ref
    title: git-symbolic-ref(1)
    resource: https://git-scm.com/docs/git-symbolic-ref
    accessed: 2026-09-06
  - id: git-clone
    title: git-clone(1) incl. GIT URLS
    resource: https://git-scm.com/docs/git-clone
    accessed: 2026-09-06
  - id: gitcredentials
    title: gitcredentials(7)
    resource: https://git-scm.com/docs/gitcredentials
    accessed: 2026-09-06
  - id: git-config
    title: git-config(1)
    resource: https://git-scm.com/docs/git-config
    accessed: 2026-09-06
  - id: git-man
    title: git(1), environment variables
    resource: https://git-scm.com/docs/git
    accessed: 2026-09-06
  - id: git-remote
    title: git-remote(1)
    resource: https://git-scm.com/docs/git-remote
    accessed: 2026-09-06
  - id: src-transport
    title: transport.c (master; also v2.43.0)
    resource: https://raw.githubusercontent.com/git/git/master/transport.c
    accessed: 2026-09-06
  - id: src-push
    title: builtin/push.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/builtin/push.c
    accessed: 2026-09-06
  - id: src-receive-pack
    title: builtin/receive-pack.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/builtin/receive-pack.c
    accessed: 2026-09-06
  - id: src-remote-curl
    title: remote-curl.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/remote-curl.c
    accessed: 2026-09-06
  - id: src-credential
    title: credential.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/credential.c
    accessed: 2026-09-06
  - id: src-prompt
    title: prompt.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/prompt.c
    accessed: 2026-09-06
  - id: src-setup
    title: setup.c (master)
    resource: https://raw.githubusercontent.com/git/git/master/setup.c
    accessed: 2026-09-06
  - id: relnotes-2.30.3
    title: Git 2.30.3 release notes (CVE-2022-24765)
    resource: https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/2.30.3.adoc
    accessed: 2026-09-06
  - id: relnotes-2.48.0
    title: Git 2.48.0 release notes (remote HEAD on fetch)
    resource: https://raw.githubusercontent.com/git/git/master/Documentation/RelNotes/2.48.0.adoc
    accessed: 2026-09-06
---

# Git push, credentials and remotes

What git's command line does where the push, credential, remote-URL and
repository-discovery code in `src/markdown_vault_mcp/git/` relies on it: how a
push is refused and in what words, how credentials are requested, which URL
forms mean SSH, how a repository is found and which refs exist. Not a tutorial,
not branching strategy. `[observed: how]` claims were reproduced on git 2.43.0
in throwaway repositories under `/tmp/gitref/`; "gettext" says whether a string
is `_()`/`N_()`-wrapped in git's source and so follows `LANG`/`LC_ALL`.

## Scope

- Covers: push outcomes, exit codes, stderr, `--porcelain`; askpass and
  `GIT_TERMINAL_PROMPT`; GIT URLS; `safe.directory`; `symbolic-ref`,
  `rev-parse` and `origin/HEAD`.
- Does not cover: hosting-provider policy text, hooks other than
  `pre-receive`, submodules. Pathspecs, staging, committing, identity and
  rebase state are on [Git staging, commits and rebase
  state](/git-staging-and-commits.md); `log`/`diff`/`ls-tree` framing is on
  [Git history and revision queries](/git-history-queries.md).
- Depended on by: `health.py`, `push_scheduler.py`, `_run.py`, `bootstrap.py`,
  `conflict.py` (repository discovery); `docs/design/design.md` § "Write +
  Git Integration" and its "Tracking-independent remote ref" paragraph.

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

## Where this project departs from the subject

By function; the design anchor is `docs/design/design.md` § "Write + Git Integration" unless stated.

- **`push_failure_reason` (`health.py`)** — narrower: only `non-fast-forward` / `fetch first` are matched; `needs force`, `stale info`, `already exists`, `remote ref updated since checkout` and `[remote rejected] (pre-receive hook declined)` (the protected-branch shape) all land in `push_failed`, `PushResult.hint` carrying the text. Not pinning `LC_ALL`/`LANG` is safe for the two matched strings (never localised); only surrounding lines change language. `--porcelain` would give the same reason on stdout and is unused. ("Sync health", #1287/#1330.)
- **`_push` (`push_scheduler.py`), `_push_locked` (`strategy.py`)** — `git push origin` without a refspec: a detached managed clone or unpublished branch dies at 128 before transport and reads as `push_failed`. ("Tracking-independent remote ref".)
- **`_is_ssh_remote` (`_run.py`)** — contrary to GIT URLS: only `git@`/`ssh://` count, so `alice@host:repo.git` or `host:repo.git` passes the token check, and `check_remote_protocol`'s HTTPS rewrite assumes `git@host:path`. ("HTTPS token auth".)
- **`_normalize_remote` (`_run.py`)** — compares `remote get-url` (already `insteadOf`-rewritten) with `GIT_REPO_URL`; an `insteadOf` rule on the host fails a correct managed-mode configuration.
- **`_build_askpass_env` (`_run.py`)** — `*sername*` matches git's un-localised prompt, but the same script answers any askpass call git spawns (an SSH passphrase would get the token).
- **`_find_git_root`, `run_git*` (`_run.py`)** — the `safe.directory` refusal (128) is indistinguishable from "not a repository"; a container uid differing from the volume owner silently gets no-git mode. Unmodelled in the design doc.
- **`PushScheduler.do_push`** — assumes a `non_fast_forward` push succeeds after the pull loop's rebase; true unless the remote moved again, when `fetch first` recurs (#957).

## Not covered

- No test asserts `fetch first` is what git prints on the un-fetched path or that `non-fast-forward` needs a prior fetch; `tests/test_git_health.py` feeds both strings synthetically. `[remote rejected] (pre-receive hook declined)` is pinned only for line folding; no test drives a real hook, and GitHub/GitLab protected-branch text was not sampled.
- No test covers a scp-like SSH URL without `git@`, an `insteadOf` rewrite, a `safe.directory` refusal, HTTPS authentication failure, or a detached-HEAD or no-upstream push.
- Localised stderr was unobservable (no `git.mo`); the `_()` claims rest on the source alone. Askpass was observed over HTTPS to github.com only, never for an SSH passphrase.
