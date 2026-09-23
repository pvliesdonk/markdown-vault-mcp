---
type: Reference
title: Git submodules seen from a superproject
description: "How git treats a submodule inside a working tree the way src/markdown_vault_mcp/git/ drives it: what a gitlink is and how to find one, what superproject-level add, ls-files, log and show say about a path inside a submodule, how clone, fetch, merge and pull populate or leave a submodule, and the detached HEAD that update leaves behind"
subject_version: "git-scm.com manual pages ('last updated in' 2.42.0–2.55.0); observed on git 2.55.0"
valid_for: "git 2.x"
generated:
  by: process:researching-references
  at: 2026-09-23T16:25:10+02:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-23T16:30:32+02:00
stale_after: 2027-03-23T16:25:10+02:00
status: stable
sources:
  - id: gitsubmodules
    title: gitsubmodules(7)
    resource: https://git-scm.com/docs/gitsubmodules
    accessed: 2026-09-23
  - id: git-submodule
    title: git-submodule(1)
    resource: https://git-scm.com/docs/git-submodule
    accessed: 2026-09-23
  - id: gitmodules
    title: gitmodules(5)
    resource: https://git-scm.com/docs/gitmodules
    accessed: 2026-09-23
  - id: git-clone
    title: git-clone(1)
    resource: https://git-scm.com/docs/git-clone
    accessed: 2026-09-23
  - id: git-fetch
    title: git-fetch(1)
    resource: https://git-scm.com/docs/git-fetch
    accessed: 2026-09-23
  - id: git-pull
    title: git-pull(1)
    resource: https://git-scm.com/docs/git-pull
    accessed: 2026-09-23
  - id: git-merge
    title: git-merge(1)
    resource: https://git-scm.com/docs/git-merge
    accessed: 2026-09-23
  - id: git-rebase
    title: git-rebase(1)
    resource: https://git-scm.com/docs/git-rebase
    accessed: 2026-09-23
  - id: git-checkout
    title: git-checkout(1)
    resource: https://git-scm.com/docs/git-checkout
    accessed: 2026-09-23
  - id: git-reset
    title: git-reset(1)
    resource: https://git-scm.com/docs/git-reset
    accessed: 2026-09-23
  - id: git-config
    title: git-config(1)
    resource: https://git-scm.com/docs/git-config
    accessed: 2026-09-23
  - id: git-ls-files
    title: git-ls-files(1)
    resource: https://git-scm.com/docs/git-ls-files
    accessed: 2026-09-23
  - id: git-ls-tree
    title: git-ls-tree(1)
    resource: https://git-scm.com/docs/git-ls-tree
    accessed: 2026-09-23
  - id: git-rev-parse
    title: git-rev-parse(1)
    resource: https://git-scm.com/docs/git-rev-parse
    accessed: 2026-09-23
  - id: git-status
    title: git-status(1)
    resource: https://git-scm.com/docs/git-status
    accessed: 2026-09-23
  - id: git-add
    title: git-add(1)
    resource: https://git-scm.com/docs/git-add
    accessed: 2026-09-23
  - id: git-log
    title: git-log(1)
    resource: https://git-scm.com/docs/git-log
    accessed: 2026-09-23
---

# Git submodules seen from a superproject

This page records what git does when a directory inside the vault's working
tree is a submodule and every git command is run, as this project runs them,
with `git -C <superproject root>`. It covers what a gitlink is and how to
enumerate submodules, what the superproject-level staging, history and
revision-read commands answer for a path inside one, how clone, fetch, merge
and pull populate or fail to populate the submodule's working tree, and the
detached HEAD that `submodule update` leaves. It leaves out push recursion,
nested submodules, LFS inside a submodule, and the case where the vault root
*is* the submodule (then `--show-toplevel` is the submodule and nothing on
this page applies). The observations were made against the layout the
`[observed: sandbox]` marker describes below: a superproject with one
submodule at `sub/` holding `note.md`, built with `git submodule add` and
cloned both with and without `--recurse-submodules`, on git 2.55.0.

## Scope

- Covers: gitlinks and `.gitmodules`; enumerating submodules; the answers
  `add`, `ls-files`, `check-ignore`, `diff --cached`, `commit --only`, `log`,
  `show` and `ls-tree` give for a path inside a submodule; `clone`,
  `submodule update`, `fetch`, `merge --ff-only`, `pull` and
  `checkout`/`reset` recursion; detached HEAD after update; status shapes.
- Does not cover: `push --recurse-submodules`; nested submodules; LFS in a
  submodule; sparse or partial clones; `git mv` across the boundary;
  Windows path forms; isomorphic-git (obsidian-git mobile lists "No
  submodules support", see [obsidian-git](obsidian-git.md)).
- Depended on by: nothing yet. `src/markdown_vault_mcp/git/bootstrap.py`
  (clone, root discovery), `git/strategy.py` (staging, pull) and
  `git/query.py` (history, revision reads) will depend on it once the
  submodule epic ([#1561](https://github.com/pvliesdonk/markdown-vault-mcp/issues/1561))
  lands; `docs/design/design.md` § Write + Git Integration records the
  current unsupported status.

## Claims

### What a submodule is on disk and in the tree

- A submodule is "a repository embedded inside another repository"; the
  containing repository is the superproject. The superproject tracks it
  "via a gitlink entry in the tree" at the submodule path and an entry
  `submodule.<name>.path` in `.gitmodules`; the gitlink "contains the object
  name of the commit that the superproject expects the submodule's working
  directory to be at". [source: gitsubmodules]
- In the usual form the submodule's git directory lives under
  `$GIT_DIR/modules/<name>/` of the superproject and the submodule's working
  directory root holds a `.git` **file** pointing at it, not a `.git`
  directory. [source: gitsubmodules] A recursive clone shows exactly `.git`
  and `note.md` in `sub/`. [observed: sandbox, `ls -A sub`]
- A "deinitialized" submodule is a gitlink plus a `.gitmodules` entry with an
  empty working directory. [source: gitsubmodules] That is what a plain
  clone leaves (see below).
- `git ls-files --stage` lists a gitlink with mode `160000`, as
  `160000 <sha> 0\tsub`; `git ls-tree HEAD -- sub` prints
  `160000 commit <sha>\tsub`. [observed: sandbox] git-ls-tree(1) lists
  `commit` among the object types a tree entry can have. [source: git-ls-tree]
- Git only recurses into *active* submodules: one whose
  `submodule.<name>.active` is true, whose path matches `submodule.active`,
  or whose `submodule.<name>.url` is set, evaluated in that order.
  [source: gitsubmodules]

### Finding submodules and telling the two roots apart

- `git rev-parse --show-toplevel` shows the top-level directory of the
  working tree the command runs in. [source: git-rev-parse] Run from inside
  `sub/` it prints the submodule's own root, not the superproject.
  [observed: sandbox]
- `git rev-parse --show-superproject-working-tree` prints the absolute root
  of the superproject that uses the current repository as a submodule, and
  nothing when there is none. [source: git-rev-parse] From inside `sub/` it
  prints the superproject root. [observed: sandbox]
- `git submodule status` prints one line per submodule: the checked-out
  commit and the path, the SHA prefixed with `-` when the submodule is not
  initialised, `+` when the checked-out commit differs from the one in the
  superproject's index, `U` on merge conflicts. [source: git-submodule]
  Shapes seen: `-<sha> sub` in a plain clone, ` <sha> sub (heads/main)` in a
  recursive clone, `+<sha> sub (heads/main)` after the superproject
  fast-forwarded past a gitlink bump. [observed: sandbox]
- `git config --file .gitmodules --get-regexp '^submodule\..*\.path$'`
  lists `submodule.<name>.path <path>` for every declared submodule,
  initialised or not. [observed: sandbox]

### What superproject commands say about a path inside a submodule

- `git add -- sub/note.md` in the superproject fails with
  `fatal: Pathspec 'sub/note.md' is in submodule 'sub'`, exit status 128;
  the `:(literal)` form, `add -u -- <pathspec>` and
  `-c submodule.recurse=true` all fail the same way. git-add(1) does not
  document the message. [observed: sandbox, git 2.55.0]
- git-status(1) states the rule behind it: "modified content or untracked
  files in a submodule cannot be added via `git add` in the superproject to
  prepare a commit". [source: git-status]
- `git ls-files --error-unmatch -- sub/note.md` exits 1 with
  `error: pathspec 'sub/note.md' did not match any file(s) known to git`.
  [observed: sandbox] `ls-files --recurse-submodules` calls `ls-files` in
  each active submodule, "Currently there is only support for the `--cached`
  and `--stage` modes". [source: git-ls-files]
- `git check-ignore --no-index -- sub/note.md` exits 1: the path is not
  ignored, it is simply not the superproject's. [observed: sandbox]
- `git diff --cached --quiet -- sub/note.md` exits 0 after a failed `add`:
  nothing is staged under that pathspec. [observed: sandbox]
- `git commit --only -- sub/note.md` exits 1 with
  `error: pathspec 'sub/note.md' did not match any file(s) known to git`.
  [observed: sandbox]
- `git log -- sub/note.md` in the superproject lists no commits;
  `git show HEAD:sub/note.md` fails with
  `fatal: path 'sub/note.md' exists on disk, but not in 'HEAD'`;
  `git ls-tree -l HEAD -- sub/note.md` prints nothing. [observed: sandbox]
  `--submodule[=<format>]` on `log`/`diff` only changes how a *gitlink
  change* is rendered (short, log, or diff of the submodule contents).
  [source: git-log]
- An unscoped `git add -u` in the superproject stages a submodule whose
  HEAD moved (a committed change inside `sub/`) as `M  sub`, one gitlink
  change; a dirty but uncommitted submodule working tree shows as ` M sub`
  in `status --porcelain` and is not staged. [observed: sandbox]
- The short status marks a submodule `M` when its HEAD differs from the
  recorded commit, `m` for modified content and `?` for untracked files,
  applied recursively. [source: git-status] `--ignore-submodules[=<when>]`
  (`none`, `untracked`, `dirty`, `all`) and `submodule.<name>.ignore` decide
  whether status and the diff family report a submodule as modified at all.
  [source: git-status] [source: git-config]

### Populating a submodule: clone and submodule update

- `git clone` without `--recurse-submodules` leaves `sub/` as an empty
  directory (zero entries) and `status --porcelain` prints nothing about it.
  [observed: sandbox]
- `git clone --recurse-submodules` initialises and clones all submodules
  after the clone, "equivalent to running
  `git submodule update --init --recursive <pathspec>` immediately after the
  clone is finished", and sets `submodule.active` to `.`. [source: git-clone]
- `git submodule update` updates registered submodules "by cloning missing
  submodules, fetching missing commits in submodules and updating the
  working tree"; the default procedure is `checkout`, in which "the commit
  recorded in the superproject will be checked out in the submodule on a
  detached HEAD". `--init` initialises an uninitialised submodule from
  `.gitmodules` first. [source: git-submodule]
- After `clone --recurse-submodules`, `git -C sub symbolic-ref HEAD` fails
  with `fatal: ref HEAD is not a symbolic ref` and
  `rev-parse --abbrev-ref HEAD` prints `HEAD`. [observed: sandbox]
- `update --remote` targets the submodule's remote-tracking branch instead
  of the recorded SHA; the branch is `submodule.<name>.branch`, defaulting to
  the remote HEAD, with `.git/config` overriding `.gitmodules`.
  [source: git-submodule] [source: gitmodules]
- `submodule.<name>.update` (`checkout`, `rebase`, `merge`, `none`) is the
  procedure `git submodule update` uses and affects that command only.
  [source: git-config]

### Keeping a submodule current: fetch, merge, pull, checkout, reset

- `git fetch` recursion defaults to `on-demand`: fetch "changed" submodules,
  those referenced by a newly fetched superproject commit, as long as the
  submodule is present locally; a submodule the upstream newly added
  "cannot be fetched until it is cloned e.g. by `git submodule update`".
  [source: git-fetch] `fetch.recurseSubmodules` defaults to `on-demand`, or
  to `submodule.recurse` when set. [source: git-config]
- git-merge(1) and git-rebase(1) list no `--recurse-submodules` option
  [source: git-merge] [source: git-rebase]; `git merge --ff-only
  --recurse-submodules origin/main` fails with
  `error: unknown option 'recurse-submodules'`. [observed: sandbox]
- `git fetch origin` followed by `git merge --ff-only origin/main` over a
  superproject commit that bumps the gitlink moves the superproject HEAD and
  leaves `sub/note.md` at its old content, with `status --porcelain`
  reporting ` M sub`. A following `git submodule update --init --recursive`
  prints `Submodule path 'sub': checked out '<sha>'` and the content is
  current. [observed: sandbox]
- `git pull --recurse-submodules` controls whether new commits of populated
  submodules are fetched and whether "the working trees of active submodules
  should be updated, too"; via rebase, local submodule commits are rebased;
  via merge, submodule conflicts are resolved and checked out.
  [source: git-pull] `pull --ff-only --recurse-submodules` on a clean clone
  brings `sub/note.md` current. [observed: sandbox]
- With uncommitted modifications inside the submodule, both
  `git submodule update --init --recursive` and
  `git pull --ff-only --recurse-submodules` stop with `Aborting` and
  `fatal: Unable to checkout '<sha>' in submodule path 'sub'`; the
  superproject HEAD is already advanced, the submodule stays at its old
  commit, and status shows `+<sha> sub` / ` M sub`. [observed: sandbox]
  git-checkout(1) states the rule: with `--recurse-submodules`, "If local
  modifications in a submodule would be overwritten the checkout will fail
  unless `-f` is used". [source: git-checkout]
- `git checkout --recurse-submodules` updates active submodules to the
  recorded commit and detaches their HEAD; without it "submodules working
  trees will not be updated". [source: git-checkout]
  `git reset --recurse-submodules` resets active submodule working trees to
  the recorded commit, "also setting the submodules' HEAD to be detached at
  that commit". [source: git-reset]
- `submodule.recurse` is a boolean that turns on `--recurse-submodules` by
  default for the commands that accept it; `--no-recurse-submodules`
  overrides it per call. [source: git-config]
- Committing inside the submodule does not move the superproject HEAD: after
  `git -C sub commit`, `git rev-parse HEAD` in the superproject is unchanged
  and status shows ` M sub`. [observed: sandbox]

### Remotes and credentials

- A submodule's remotes live in its own `$GIT_DIR/config`; gitsubmodules(7)
  gives `git push --recurse-submodules=check` as the example that consults
  them to check "if the submodule has any changes not published to any
  remote". [source: gitsubmodules]
- `git submodule add -b <branch>` records `submodule.<name>.branch` for
  `update --remote`; `.` means the superproject's current branch name;
  unset means the remote HEAD. [source: git-submodule]

## Where this project departs from the subject

No deliberate departure. Today the project does not handle submodules at all:
the git root is discovered once from `SOURCE_DIR` with `--show-toplevel`, so a
submodule inside the vault is scanned like any folder while every git command
addresses the superproject, with the answers recorded above. The design doc
(`docs/design/design.md` § Write + Git Integration) records that status; the
intended behaviour is decided in the submodule epic, not here.

## Not covered

- No claim on this page is pinned: no test in this repository exercises a
  submodule yet. The features under the submodule epic pin the claims they
  depend on when they land.
- `git push --recurse-submodules=on-demand` and `=check` outcomes and wording
  when a submodule commit is unpublished: would be settled by a push sandbox
  against a bare remote for both repositories.
- Nested submodules (a submodule of a submodule): whether `status` and
  `update --recursive` report them at the depth the walker sees.
- `git mv` and `add -A -- <old> <new>` across the submodule boundary: only
  the `ls-files` and `add` refusals above are observed, not the combined
  rename staging the project uses.
- Whether `git lfs pull` in the superproject touches pointers inside a
  submodule.
- The exact `submodule.recurse` command list in git-config(1) (which commands
  honour it and which are `git submodule`-only); read the list before relying
  on the setting instead of an explicit flag.
