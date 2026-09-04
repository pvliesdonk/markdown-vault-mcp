# Git subsystem robustness

> **Status:** investigation, not a decision. Nothing here is committed to;
> `design.md` remains authoritative. Unlike the two long-term studies proposed
> in PR #1152 (`vault-service-separation.md`, `decoupling-and-layering.md`),
> this one separates near-term work from long-term work, because part of what
> it finds is cheap.
>
> Written 2026-09-04 against `main` at `c1dd425f`, with the open PRs #1302 and
> #1305 read as pending fixes. Every git behaviour claimed below was run on
> git 2.55.0; where a claim rests on reading rather than running, it says so.

## 1. Summary

The git package is the project's defect concentration point, but the defects
are not random. Forty-one commits touching `git/` carry `fix` in their subject
(about a third are pre-squash "address review round N" commits from March and
April, which inflates the raw count), and the twenty-odd git bugs filed as
issues fall into **five failure classes**, each with one structural cause:

| Class | Cause | Bugs |
|---|---|---|
| 1. Porcelain text surface | ~70 `subprocess.run` call sites across five modules each rebuild git's invariants (literal pathspecs, NUL framing, `core.quotePath`, byte-exact paths, SHA width) one call at a time | #1303 #1304 #1282 #1290 #1284 #749 |
| 2. Whole-repository defaults | git's `add -u`, `diff --cached`, `commit` act on the whole index unless scoped; scoping was added per site, three PRs apart | #894 #1249 #1273 #675 |
| 3. Note lineage | "the same note across a rename" is not a git concept; `--follow` is called from several readers with different thresholds | #338 #683 #1285 #1297 |
| 4. Working tree as merge arena | reconciliation rebases, checks out, aborts, restores and commits siblings *on the served checkout*; every recovery step earned its own bug | #229 #231 #462 #463 #464 #466 #467 #468 #571 #662 #1292 |
| 5. Request identity across threads | the commit runs on the dispatcher thread, which has no request context | #643 #1218 #1226 (closed by `Principal`, #1160) |

Two of those classes, 1 and 2, are ones the test suite **has never been able
to fail on**: the tests that exercise the affected paths patch
`subprocess.run` with a stub that returns empty strings (15 `patch` sites,
41 mentions with `monkeypatch`, in `tests/test_git.py`), so byte-level git
behaviour never runs under them, and the real-repository fixtures were never
fed an adversarial name or a dirty index. Coverage on `strategy.py` is 95.7%
while its health score is 2.03/10 and it carries 14 fixes in six months
(repowise, 2026-09-04). Coverage is measuring the wrong thing.

The recommendation, in order of value per unit of risk:

1. **Now:** a test strategy that runs real git (option D) plus one typed git
   command layer that owns the invariants (option A). Together these remove
   classes 1 and 2 structurally and make class 3 a single-site concern.
2. **Then one of two end states, decided in an ADR:** either move
   reconciliation off the working tree onto trees and refs with git 2.38's
   `merge-tree --write-tree` (option B, on top of A), or serve the vault
   through pygit2 (option C), which removes all four classes with one
   engine but cannot open SHA-256, reftable or partial-clone repositories
   on this year's libgit2 and needs the LFS clean filter wired by hand.
   A spike (§2.5) verified both paths on the same scenarios. Because the
   consumers already depend on protocols rather than the class, C's form is
   an experimental second backend beside the CLI one, selected per
   instance, not a replacement (§3.C, §5).

## 2. Evidence

### 2.1 Shape of the package

| Module | Lines | `subprocess` call sites | Health (repowise) | Fixes / 6 mo |
|---|---|---|---|---|
| `strategy.py` | 2,399 | 25 | 2.03, god class critical, hotspot 99% | 14 |
| `query.py` | 1,571 | 20 | 2.96, hotspot 98% | 5 |
| `conflict.py` | 507 | 11 | 2.70, branch coverage 76.7% | 9 |
| `_run.py` | 275 | 8 | 9.1 | 2 |
| `bootstrap.py` | 239 | 3 | 8.76 | 0 |
| `push_scheduler.py` | 286 | 3 | (not scored) | — |
| tests (`test_git*.py`, 9 files) | 11,397 | — | `test_git.py` is the repository's top hotspot: 31 fixes | — |

At least twenty distinct git subcommands are invoked: `add` (7 sites),
`rev-parse` (5), `rebase` (4), `push`, `ls-files`, `diff` (3 each), `remote`
(2), and `show`, `merge-base`, `merge`, `hash-object`, `fetch`, `clone`,
`check-ignore`, `cat-file`, `config`, `lfs`, `symbolic-ref` (1 each), plus
`log`, `commit` and `checkout` built as argv lists inline (the count is a
grep over argv shapes, so it is a floor). All are **porcelain** except
`rev-parse`, `merge-base`, `hash-object`, `cat-file`, `ls-files`,
`symbolic-ref` and `check-ignore`. Porcelain output is designed for
humans and changes shape with configuration (`core.quotePath`,
`diff.renames`, `rebase.autoStash`, `merge.conflictStyle`), which is where
class 1 comes from.

### 2.2 The bugs, by class, with their fix shape

**Class 1, porcelain text surface.** Each fix added one invariant to one or
a few call sites:

- #749 (June): `@{upstream}` does not resolve on a non-tracking clone. Fix:
  `resolve_tracking_ref`, used by six sites.
- #1282 (Sep 2): `git log --name-only` octal-escapes non-ASCII paths. Fix:
  `-c core.quotePath=false` on the readers that were found.
- #1284 (Sep 3): the SHA validator asserts 40 hex digits; a SHA-256
  repository returns 64. Fix: widen the validator.
- #1290 (open): `text=True` on a `-z` stream rewrites `\r` to `\n`. Five
  readers affected. No fix yet.
- #1303 / #1304 (open, PR #1305): a bare pathspec is a wildmatch pattern, so
  `b[1].md` selects `b1.md`. Fix: `literal_pathspec` moved into `_run.py`,
  applied to every pathspec site the PR could find. The revision readers had
  already defended against this since #1137; the history, diff, staging and
  conflict paths had not.

The pattern is the finding: **the same invariant is discovered per site.**
`literal_pathspec` in `_run.py` is the first step toward one place; there is
no equivalent yet for NUL framing, byte decoding, or `quotePath`.

**Class 2, whole-repository defaults.** Three PRs, one hole each:

- #894 (July): rename staging ran `git add -u` with no pathspec.
- #1249 (Aug 31): the "anything staged?" check ran `git diff --cached
  --quiet` repository-wide.
- #1273 (Sep 2): `git commit` with no pathspec commits the whole index.

Each fix scoped the one command the issue named. Three of the four commands
in the commit path had this shape, and they were fixed on three separate
days. `#1304` then reached the same consequence through a fourth route
(class 1).

**Class 3, note lineage.** `--follow` is a heuristic, and four readers call it:

- #338: single diff lost the pre-rename baseline.
- #683: per-commit attachment diff not rename-aware.
- #1285: `--follow` crosses a delete/re-create boundary and attributes an
  earlier note's commits to a name that was reused. Fix: a `--name-status`
  walk pinned to `--find-renames=30`.
- #1297 (open, PR #1302): the history reader used git's 50% default while
  the revision reader used 30%, so `read(revision=)` served a commit
  `get_history` never listed.

Git detects renames by content similarity per commit pair. A note that is
renamed and rewritten in one commit is, to git, a delete and an add. No
threshold makes two readers agree unless they share one walk. #1285's fix
is that walk; #1297 is the other readers not using it yet.

**Class 4, working tree as merge arena.** The pull pipeline is:

```
quiesce writes → lock → fetch → classify (merge-base --is-ancestor)
  → merge --ff-only
  → else rebase <origin/branch>
      → on conflict: loop ≤50× { diff --diff-filter=U; show REBASE_HEAD:path;
                                 checkout --ours; add; rebase --continue }
      → if still in progress: rebase --abort; checkout <ref> -- <paths>
      → write .conflict-mcp-* siblings; add; commit
  → lfs pull
```

Every arrow after `rebase` mutates the checkout the server is serving reads
from, and every one of them has failed in a way that needed a bug:

- #229 / #231 (March): the design itself. Rebase as fallback; siblings on
  conflict.
- #462: the sibling commit lacked `check=True`, so a failed commit reported
  `conflicts_resolved_with_siblings`.
- #463: post-abort `checkout <ref> -- path` return code ignored; a failed
  restore then wrote a sibling with the wrong side.
- #464: `rev-parse --git-dir` failure silently assumed no rebase in
  progress.
- #466: `REBASE_HEAD` is not a reliable in-progress signal (git keeps it
  after `--continue`). Fix: probe `.git/rebase-merge` / `rebase-apply`.
- #468: the 50-iteration cap and `conflict_resolution_failed` had no tests.
- #571: a write landing on disk before its deferred commit makes the tree
  dirty; the ff-only merge then refuses, and the rebase cannot start.
  Fix: drain the dispatcher before the merge (`_quiesce_writes`).
- #662 / #675: `write_conflict_files` read the original twice, crashed on
  `OSError`, and staged a skipped original.
- #879: the whole pipeline existed twice (`sync_once` and `force_pull`) and
  had diverged; the loop's copy had no restore failure handling.
- #1292: the dry run assumed a fast-forward the real pull refused.
- #1287: with the rebase loop capping out every cycle, pushes failed for
  hours while writes reported success. Fixed by *observing* the condition
  (`SyncHealthTracker`), not by making reconciliation succeed. Its reporter
  closed the conflict class on their side with `merge=union` in
  `.gitattributes` (#1294).

The state machine has at least six intermediate states (fetched, ff-merged,
rebase-in-progress, rebase-aborted, restored, siblings-uncommitted) and each
of them is on disk, in the path of concurrent reads, with `git rebase`'s own
state files as the only record. The fixes above each hardened one
transition. None changed the fact that the served checkout is the arena.

**Class 5, identity across threads.** #1218 found that OIDC claims were read
on the dispatcher thread, where `get_access_token` returns `None`, so every
authenticated write fell back to the static identity. Fixed by resolving a
`Principal` at the tool edge (#1160, #1226). Closed; listed because it is
the same shape as class 1: an invariant (request context does not cross a
`threading.Thread`) discovered at the site that broke.

### 2.3 What the tests can and cannot see

`tests/test_git.py` (7,131 lines, 221 tests) mixes two kinds of test:

- **Real repositories.** `git_repo` and `git_repo_with_remote` fixtures run
  `git init` in `tmp_path`; the newer files (`test_git_health.py`,
  `test_git_name_reuse.py`, `test_git_revision.py`, `test_git_sync.py`,
  `test_git_query.py`) use only these.
- **Stubbed subprocess.** Fifteen `patch("markdown_vault_mcp.git.subprocess.run")`
  sites and further `monkeypatch` sites install a fake returning
  `returncode=0, stdout=""` or a scripted answer per command string. These
  assert on **argv shape** (`"commit" in cmd`, `"--diff-filter=U" in cmd`),
  which is the one thing a real git would not need asserting.

The stubbed tests cannot fail on: glob interpretation (#1303/#1304),
`quotePath` (#1282), text-mode newline translation (#1290), SHA width
(#1284), `--follow` semantics (#1285/#1297), or any recovery transition in
class 4 (`REBASE_HEAD` staleness, a failing `rebase --abort`). All of those
were found by running the code against a real repository by hand, after
release. Memory records the same lesson from the GitLab webhook (#1178):
"55 green tests hid a signature bug that rejected every real delivery."

The package docstring in `git/__init__.py` states that the global patch
target is load-bearing: `import subprocess` is kept there so
`markdown_vault_mcp.git.subprocess.run` patches every submodule at once.
Any option that routes git calls through one wrapper changes that target.

### 2.4 Verified git capabilities (git 2.55.0, 2026-09-04)

Run in a scratch repository; each line is a fact this study leans on.

1. `git merge-tree --write-tree [-z] <ours> <theirs>` (git ≥ 2.38) performs a
   full three-way merge **without a working tree or index**, writes the
   result tree, and on conflict exits 1 with, per conflicted path, the three
   stage entries (base/ours/theirs blob ids) and the CONFLICT messages. The
   result tree contains conflict-marked content for those paths.
2. It honours `.gitattributes` merge drivers: with `log.md merge=union`, a
   both-sides-appended `log.md` merged cleanly to the union while a sibling
   note conflicted. `git rebase` honours the same attribute, which answers
   #1294's open question for the current pipeline too.
3. `git commit-tree <tree> -p <ours> -p <theirs>` then `git update-ref
   refs/heads/<b> <commit>` moves the branch with the checkout untouched.
4. A merge commit `C` built by `commit-tree <T> -p HEAD -p <remote>` has
   `HEAD` as its first parent, so `git merge --ff-only C` is a fast-forward:
   it moves the ref, index and checkout **together**, in one step. A dirty
   tracked or untracked file the merge did not touch survives it
   (`M keep.md`); a dirty tracked file the merge **did** change makes it
   refuse with `Your local changes to the following files would be
   overwritten by merge` and **nothing moves**: `HEAD`, `main`, index and
   checkout all stay where they were. This is the same primitive the
   pipeline already uses for the plain "behind" case. (`update-ref` followed
   by `read-tree -m -u` also works but refuses *after* the ref moved, which
   creates a branch-ahead-of-checkout state; it is not the right finishing
   step.)
5. Sibling files can be assembled without a working tree, frontmatter
   included: `cat-file -p` each side, prepend the `conflict_with` /
   `conflict_date` block, `hash-object -w --stdin` the two new blobs;
   `read-tree <T>` into a temporary `GIT_INDEX_FILE`, `update-index
   --index-info` with the rewritten theirs blob on the note's path and the
   rewritten ours blob on `<stem>.conflict-mcp-<ts><ext>`, `write-tree`,
   `commit-tree`. The resulting commit carries `note.md` = theirs with the
   sibling's name in its frontmatter and the sibling = ours with the note's
   name in its frontmatter, which is #231's symmetric contract, and the
   checkout is untouched until step 4.
6. Reading `git log -z --name-only` as **bytes** and decoding with
   `os.fsdecode` (surrogateescape) round-trips names containing `\r`, `[`,
   non-ASCII and an embedded newline into `:(literal)` pathspecs that
   `ls-files -z` matches exactly. This is the fix shape for #1290 and
   confirms `-z` plus bytes is sufficient for class 1's path bugs.
7. The Debian release behind `python:3.14-slim` (trixie) packages git
   2.47.3, above the 2.38 floor. *Assumption:* the image tag stays on trixie;
   verify with `git --version` inside the built image before relying on it.
   The repository currently declares **no** minimum git version anywhere.

### 2.5 pygit2 spike (pygit2 1.20.0 / libgit2 1.9.6, run 2026-09-04)

The first draft of this study dismissed libgit2 on reading alone. The owner
pushed back, so the same scratch scenarios as §2.4 were run through pygit2
(`cp314` manylinux wheel, built with HTTPS, SSH and threads). Facts:

**Works, and better than the CLI:**

1. **Adversarial names are not a category.** Paths are `str` on the API
   and bytes in the tree; `b[1].md`, `star*note.md`, `ca\rrr.md`,
   `new\nline.md`, `café.md` round-trip through `index.add`, commit, tree
   listing and diff with no quoting, no pathspec magic and no `-z`. An edit
   to `b1.md` produces a delta for `b1.md` only. Class 1 has nowhere to
   occur.
2. **Staging and committing are explicit index operations**, so class 2's
   "whole repository by default" does not exist: an `IndexEntry` is added
   or it is not.
3. **A rename-following walk is ~40 lines**: walk commits topologically,
   compare the tracked path's blob id between parent and child tree, and
   run a whole-tree `diff_to_tree` + `find_similar(rename_threshold=30)`
   only when the entry vanished, and skip a commit whose entry equals any
   parent's (git's TREESAME rule, which is what makes `--follow` drop the
   merge commit and keep the side-branch edit). It returns the same commits
   as `git log --follow --find-renames=30` on every scenario tried,
   including a merge history, and it **stops at the note's birth** instead of crossing into a reused name, which is the
   #1285 behaviour the project already had to build on top of git. The
   "libgit2 has no `--follow`" objection in the first draft was overstated:
   the project already owns a lineage walk; only the record source changes.
4. **In-memory three-way merge**: `merge_trees(base, ours, theirs)` returns
   an index with `conflicts`; `.gitattributes merge=union` **is honoured**
   (`log.md` merged to the union); the ours/theirs entries carry blob ids,
   so the `.conflict-mcp-*` sibling with symmetric frontmatter is assembled
   with `create_blob` + `IndexEntry`, then `write_tree`, `create_commit`
   with two parents. No working tree, no index file, no rebase state.
5. **Checkout is safe by default**: `checkout_tree(strategy=SAFE)` refuses
   on a dirty file the merge changed (`1 conflict prevents checkout`, HEAD
   unchanged) and succeeds past a dirty file it did not touch. `git status`
   afterwards agrees with what libgit2 did.
6. `descendant_of`, `merge_base`, `path_is_ignored`, `UserPass` /
   `Keypair` credential callbacks exist, so the classification probe, the
   ignore probe and token auth have direct equivalents; the `GIT_ASKPASS`
   temp-script mechanism goes away.

**Does not work, or changes behaviour:**

7. **SHA-256 repositories cannot be opened** (`unknown object format
   'sha256'`). libgit2 ships it only behind an experimental build flag and
   the wheels do not enable it; it is scheduled to become supported in
   libgit2 2.0. The project documents 64-digit SHAs as supported
   (`docs/tools/index.md`, from #1284), so this is a withdrawn public
   capability, not a gap.
8. **reftable repositories cannot be opened** (`unsupported extension name
   extensions.refstorage`), and there is no upstream tracking of it. Git's
   default is still the files backend, so this bites only operators who set
   `init.defaultRefFormat=reftable`; it bites them at startup.
9. **Partial clones**: the repository opens, but a blob git has not fetched
   raises `KeyError`; git lazily fetches it. Affects `read(revision=)` and
   diffs on a `--filter=blob:none` clone.
10. **The LFS clean filter does not run on the write path.** With
    `git lfs install --local` and `*.bin filter=lfs` in a committed
    `.gitattributes`, `git add` committed a blob beginning `version
    https://git-lfs.github.com/spec/v1`; the same-sized file added through
    `index.add` was committed as its 2,000 raw bytes. An LFS-tracked
    attachment written through the server would land in history as a full
    blob, which is a silent repository-shape regression. `git lfs clean`
    per path (a subprocess) or a registered pygit2 `Filter` that shells out
    would close it; `git lfs pull` on the read side stays a subprocess
    either way.
11. **Hooks do not run.** A failing `pre-commit` hook stopped `git commit`
    and did not stop `create_commit`. For a server-owned clone this is
    arguably right; it is a behaviour change for an operator relying on
    hooks in the vault repository.
12. **Speed**: the optimised lineage walk takes ~0.2 ms per commit
    (78 ms over 405 commits, 300 files), against ~16 ms for `git log
    --follow` on the same repository, i.e. about 5× slower and linear in
    history length. A 20,000-commit vault would spend ~4 s per uncached
    history call; `limit` / `since` bound it, and the walk is cacheable per
    `(path, HEAD)`.
13. **Merge and rename engines differ from git's.** The same rename was
    scored 36% by git and 50% by libgit2; both cleared the 30% threshold,
    but the two will disagree near it. The merge result matched git on the
    union and conflict scenarios tried; near-threshold and criss-cross
    cases were not tried.

**How common the three unopenable shapes are, and where upstream stands
(checked 2026-09-04):**

- `git init` on git 2.55 still produces SHA-1 objects and files-backend
  refs; both other formats are opt-in (`--object-format=sha256`,
  `--ref-format=reftable` or `init.defaultRefFormat`). A partial clone
  needs an explicit `--filter`. Git's `BreakingChanges` document plans to
  flip **both** defaults in Git 3.0 (targeted late 2026), but conditions
  each flip on "the ecosystem" being ready and names libgit2 explicitly as
  a prerequisite for the reftable flip; there is no plan to deprecate
  SHA-1.
- GitHub does not accept SHA-256 repositories at all (a push fails).
  GitLab has had experimental support since 2023; Codeberg and Gitea
  support it. A vault hosted on GitHub, which is what the docs walk
  operators through, cannot be SHA-256 today; a managed-mode clone made by
  the server inherits the remote's format.
- obsidian-git creates repositories through `isomorphic-git` (mobile) or
  the system `git` (desktop); neither produces reftable or partial clones
  unless the operator asks for them.
- libgit2 is actively maintained: releases 1.9.3 through 1.9.7 between
  May and August 2026 plus 1.8.x backports, over a hundred commits since
  June, 10.6k stars, ~530 open issues. **Reftable support was merged to
  `main` on 2026-05-06 (PR #7117)** and its vendored reftable library is
  kept current by git's own reftable maintainer (PR #7327, July 2026); it
  has not shipped in a 1.9.x release. SHA-256 moves from experimental to
  supported in libgit2 2.0, which is an API/ABI break. Partial clone has
  open issues from 2020 and a feature PR opened 2026-09-04; nothing merged.
- pygit2 released 1.19.2 (March), 1.19.3 (June) and 1.20.0 (August 2026)
  with `cp314` wheels, and tracks libgit2 minors within weeks. A libgit2
  2.0 ABI break implies a pygit2 major, so the SHA-256 and reftable gaps
  close for this project only when both ship and the project pins them.

So: the three shapes are rare today in exactly the deployments this server
targets, and two of the three are on upstream's path to closing; the
withdrawal in option C is a statement about *this year's* libgit2, not a
permanent one. The risk that remains is timing: if Git 3.0 flips the
defaults before libgit2 2.0 and pygit2 catch up, a fresh `git init` by an
operator produces a repository this server cannot open.

Not run: SSH transport through libssh2 (by its API it uses `Keypair` / the
agent, not the operator's `~/.ssh/config`), fetch/push against a real HTTPS
remote, the walk on a repository with tens of thousands of commits, and
rename detection *across* a merge commit.

**dulwich 1.2.14** (pure Python, no rename detection, minimal merge) and
**GitPython** (wraps the same CLI text surface) were not spiked; neither
changes the analysis.

## 3. Options

D is independent of the rest and precedes it. B builds on A. C replaces
both A and B.

### A. One typed git command layer

Replace the ~70 raw `subprocess.run` sites with methods on one object
(`GitRepo` or a module of functions in `_run.py`) that own the invariants:

- every pathspec is emitted as `:(literal)`;
- every path-bearing reader uses `-z`, reads **bytes**, decodes with
  `os.fsdecode`, and never passes `text=True`;
- `-c core.quotePath=false` and any other output-shaping config is set by
  the layer, not the caller, and operator config that changes semantics
  (`rebase.autoStash`, `diff.renames`, `merge.conflictStyle`) is pinned
  explicitly;
- `add`, `diff --cached`, `commit` **require** a pathspec argument; the
  unscoped form is a separate, deliberately named method for the one legacy
  branch that still needs it;
- one `lineage(path)` walk at one threshold serves history, diff and
  revision reads;
- object ids are validated against the repository's hash algorithm, not a
  constant.

Prevents classes 1 and 2 by construction and makes class 3 a single site.
Does not touch class 4.

Costs: the `markdown_vault_mcp.git.subprocess` patch target moves; the
stubbed tests either get rewritten against real repositories (option D) or
re-pointed at the wrapper. `strategy.py`, `query.py`, `conflict.py` all
change, so this is a refactor PR series, not one PR.

### B. Reconcile on trees and refs, update the checkout once

Rebuild the pull's diverged branch as a computation that touches no working
tree until the end:

```
fetch → classify
  → ff: merge --ff-only <remote>            (as today)
  → diverged:
      T = merge-tree --write-tree -z HEAD <remote>
      if conflicts: for each conflicted path, rewrite the theirs (stage 3)
                    and ours (stage 2) blobs with the symmetric
                    conflict_with / conflict_date frontmatter (#231) via
                    hash-object -w; in a temp GIT_INDEX_FILE: read-tree T,
                    put theirs' on the path and ours' on
                    <stem>.conflict-mcp-<ts><ext>; T = write-tree
                                                        (verified, §2.4.5)
      C = commit-tree T -p HEAD -p <remote> -m "..."   (a merge commit)
      merge --ff-only C                    (ref + index + checkout, one step)
  → lfs pull
```

There is no in-progress state on disk between fetch and the final
`merge --ff-only`: a crash mid-way leaves the branch where it was and some
dangling objects. The loop over `REBASE_HEAD`, `--continue`, `--abort`,
`checkout --ours`, and `restore_upstream_paths` disappears; so do #462,
#463, #464, #466, #468 and the half of #571 that is about a dirty tree
refusing the rebase. A dirty file is no longer in the merge's way, only in
the final fast-forward's, which refuses with nothing moved (§2.4.4), so the
pipeline's answer is the one it already gives for a refused fast-forward
today and the next `_quiesce_writes` + pull retries it. Merge drivers keep
working (verified). The dry run becomes honest for free: it is the same
`merge-tree` call without the final step.

Two semantic changes an ADR would have to own (the sibling naming and the
symmetric `conflict_with` frontmatter of #231 are **kept**, see §2.4.5):

- The result is a **merge commit**, not a rebased linear history. Today's
  design rebases so the server's commits sit on top of the remote. A merge
  commit is what obsidian-git and human `git pull` produce, so the shared
  history already contains them; but `get_history` and the changelog-style
  tooling should be checked against merge commits (`--first-parent`
  questions).
- Sibling files are decided **per merge**, not per rebased local commit.
  With three local commits touching one note, today's loop can write up to
  three siblings; B writes one, holding the local tip's content. That is
  arguably the better behaviour, and it is a behaviour change.

Raises the git floor to 2.38 (2022). Requires A first, or the new pipeline
inherits class 1.

### C. libgit2 via pygit2 for repository operations

Replace the in-repository subprocess calls (index, trees, refs, diff,
merge, checkout, blob reads) with pygit2. Keep a subprocess for `git lfs`
(pull on the read side, clean on the write side) and, if wanted, for
`clone`. §2.5 establishes what this buys and what it costs; the summary:

**What it subsumes.** A and B at once, with less code than either: there
is no text surface to build a typed layer over, and `merge_trees` is B's
tree-level reconciliation with the engine in-process. The lineage walk the
project already owns (#1285) becomes ~40 lines over tree diffs. The
`GIT_ASKPASS` temp script, the redaction of git stderr, the `-z` framing,
the `:(literal)` prefixing and the `rebase` state machine all go away. The
test suite cannot stub it the way it stubs `subprocess.run`, so D happens by
necessity.

**What it withdraws or changes, verified in §2.5 unless noted:**

- SHA-256 repositories (documented as supported since #1284), reftable
  repositories, and partial clones cannot be served. This is an
  operator-surface constraint and therefore a `!` change, and it needs a
  startup probe that refuses such a repository with a message naming the
  reason rather than failing later.
- LFS-tracked attachments must be routed through `git lfs clean` on write,
  or they are committed as full blobs.
- Hooks in the vault repository do not run on the server's commits.
- SSH remotes authenticate through libssh2 key or agent callbacks, not the
  operator's `~/.ssh/config` (from the API, not run).
- History calls cost ~0.2 ms per commit walked instead of git's ~0.04 ms.
- A second merge and rename engine sits next to git's in a repository that
  humans and obsidian-git also merge; results agree on the cases tried and
  will disagree near the rename threshold.

**Shape of the change: a second backend next to the first.** The seams
for this already exist. Every consumer outside `git/` depends on a protocol,
not the class: `Vault` holds a `VersionedStore`, `GitQueryManager` a
`HistorySource` and gates revision reads on `RevisionReader`, the tool
layer tests `Syncer` and `SyncHealthReporter`; no private member of the
strategy is reached from outside the package (#1229). The concrete class is
constructed in exactly one place, `_assembly._resolve_git`. So a
`LibGit2Store` implementing `VersionedStore` can be added **beside**
`GitWriteStrategy`, selected by a config field (`git_backend`, default
`cli`, via the `config-contract` chain), and run experimentally on chosen
instances while every other instance keeps the CLI backend. It reuses
`health.py`, `types.py` and `interfaces.py` unchanged. Three seams need
work first:

- `PushScheduler` calls a module-level `_push` that shells out; it needs
  the push as an injected callable.
- `on_write_batch` / `accepts_batch` (#1264) are discovered by attribute on
  the callback, not declared on `Versioner`; the protocol should say so,
  or the new backend silently loses batching.
- `_resolve_git` is typed against the concrete class and builds the three
  git modes' kwargs for it; it becomes a backend selector.

With both backends behind one protocol, D's scenario suite runs
parametrised over `cli` and `libgit2`, which is the acceptance test for the
new backend and the regression net for the old one. Nothing is withdrawn
while both exist: a SHA-256, reftable or partial-clone repository is served
by the CLI backend, and the libgit2 backend refuses those three shapes at
startup with a message naming the backend to use. Retiring the CLI backend
is a separate, later decision, and by then libgit2 2.0 may have closed two
of the three gaps (§2.5).

**Dependency footprint.** A compiled wheel (5.6 MB) in the Docker image,
the mcpb bundle and the plugin channel; `git` itself stays required only
for LFS.

### D. Test strategy: real git, adversarial names, a version floor

Independent of A, B, C, and the cheapest item here:

- Retire the `subprocess.run` stubs in `test_git.py`. Every test that
  scripts a fake answer per command becomes a test against a real
  repository, or is deleted if it only asserted argv shape.
- Property-based filename tests (Hypothesis is already an accepted style in
  this ecosystem, or a fixed adversarial list): names containing `*`, `?`,
  `[`, `]`, `\r`, `\n`, `"`, a tab, non-ASCII, a leading `-`, a trailing
  space, and the four-byte UTF-8 range, exercised through write → commit →
  history → diff → read(revision=) → rename → delete. Class 1 is
  enumerable; enumerate it once.
- A pull-pipeline scenario suite driven through two clones of one bare
  remote (the `git_repo_pair` fixture exists): behind, ahead, diverged
  clean, diverged conflicting, diverged with `merge=union`, dirty tree at
  merge time, crash between steps (kill the subprocess mid-`rebase` today,
  mid-`merge --ff-only` under B). Each scenario asserts the **repository state
  after**, not the argv.
- Declare a minimum git version, and run the suite in CI against that
  version and against latest. The Debian package behind the image is the
  natural floor.

Prevents nothing; detects everything above before release, which is what
has not been happening.

### E. Status quo

Keep fixing per site. Each September bug cost one PR and a review round;
the class 1 sweep in #1305 touched seven files. The cost is bounded per
bug and unbounded in count, because the invariants are rediscovered by
whoever hits the next filename.

## 4. Tradeoffs

### 4.1 What each option would have prevented

`P` = prevented structurally, `D` = detected before release, `–` = untouched.

| Bug | A wrapper | B trees/refs | C libgit2 | D tests |
|---|---|---|---|---|
| #1303/#1304 glob pathspec | P | – | P | D |
| #1282 quotePath | P | – | P | D |
| #1290 `\r` in `-z` reader | P | – | P | D |
| #1284 SHA width | P | – | **regresses**: SHA-256 repos unopenable | D |
| #749 `@{upstream}` | P (one ref resolver) | – | P | D |
| #894/#1249/#1273 unscoped add/diff/commit | P | – | P | D |
| #338/#683/#1285/#1297 lineage | P (one walk) | – | P (one walk, verified; 5× slower) | D |
| #462/#463/#464/#466 recovery steps | – | P (no steps) | P (in-memory, verified) | D |
| #468 loop cap untested | – | P | P | D |
| #571 dirty tree blocks rebase | – | P (half) | P (half, verified) | D |
| #662/#675 sibling write/staging | – | P (siblings from blobs) | P (verified) | D |
| #879 pipeline duplicated | done | – | – | – |
| #1292 dry run wrong | done | P (same call) | P (same call) | D |
| #1287 stranded writes invisible | – (observability) | – | – | D |
| #1218 identity on wrong thread | done | – | – | D |
| #1294 `merge=union` guidance | – | verified honoured | verified honoured | D |
| LFS attachment writes (no issue yet) | unchanged | unchanged | **regresses** unless `git lfs clean` is wired | D |

### 4.2 Criteria

| Criterion | A | B | C | D |
|---|---|---|---|---|
| Classes removed | 1, 2, (3) | 4 | 1, 2, 3, 4 | none |
| Operator interop (obsidian-git, human git, LFS, merge drivers) | unchanged | unchanged; merge commits instead of rebases | merge commits; drivers honoured; hooks skipped; LFS write needs wiring; SHA-256 / reftable / partial clones refused | unchanged |
| Git version floor | unchanged | 2.38 | libgit2 1.9 wheel; `git` + `git-lfs` still on PATH for LFS | declares one |
| Migration size | refactor series across 3 modules; tests re-pointed | one pipeline, ~conflict.py + `_pull_locked` | a second `VersionedStore` beside the first, behind a backend flag; three seams to open first | test-only |
| Blast radius if wrong | argv regressions, caught by D | history shape change | the instances that opt in | none |
| Ongoing cost | one place to learn each invariant | one state machine with no on-disk intermediate states | one engine, no text surface; libgit2 feature lag (SHA-256 due in 2.0) | slower suite (real git per test) |

### 4.3 Evidence vs assumptions

Evidence: the bug classification (issue bodies and fix commits), the
subprocess site counts, the test stub counts, the repowise health and fix
history, every git behaviour in §2.4, and every numbered pygit2 finding in
§2.5.

Inference: that classes 1 and 2 recur because the invariant lives per site.
Supported by the fix history (`literal_pathspec` existed for #1137 and was
not applied to staging until #1304), not proven.

Assumptions, unverified:

- B's frontmatter rewrite was run on plain blobs; `write_conflict_files`
  today merges the fields into *existing* frontmatter with
  `python-frontmatter` and tolerates unparseable input. The same code would
  produce the blob bytes, so the contract holds, but it was not run through
  that code here.
- Whether any consumer depends on the server's commits being rebased onto
  the remote rather than merged. Nothing in `docs/` promises linear history;
  `get_history` on a directory today does not pass `--first-parent`.
- The Debian release behind the published image.
- Whether the project wants to keep supporting Windows-hosted vaults through
  the mcpb bundle; the byte-path decoding in A assumes POSIX filenames
  (`os.fsdecode` is correct on both, but git-for-windows quoting differs).
- How many deployed vaults are SHA-256, reftable or partial clones. The
  defaults and forge support in §2.5 say very few today; the exposure is an
  operator's own checkout in unmanaged mode, and any Git 3.0 default flip
  that lands before libgit2 2.0 does.
- Whether any deployment writes LFS-tracked attachments through the server
  (as opposed to reading them). The docs describe LFS as a read-side
  feature (`git lfs pull`); the write side is untested today.
- pygit2's walk cost on a repository with tens of thousands of commits was
  extrapolated linearly from 405, not measured.

## 5. Recommendation

There are two coherent end states:

- **A + B**: keep the git CLI, hide its text surface behind one typed
  layer, reconcile on trees with `merge-tree`. Serves every repository git
  serves. Two refactor steps, each landable behind current behaviour.
- **C**: pygit2 for repository operations, `git lfs` shelled out. One
  engine, no text surface, and the smaller codebase at the end. Cannot
  open SHA-256, reftable or partial-clone repositories on this year's
  libgit2, and needs `git lfs clean` wired on the write path.

The first draft of this study said "C: no" on the strength of the
`--follow` objection; the spike in §2.5 retired it. The second draft made
C conditional on declaring a supported repository shape; the owner's
position is that a vault is a vault, one shape policy for every instance
of the many deployed, and that the SHA-256 case is not to be withdrawn.
That rules out C as a *replacement* this year, and it does not rule out C
at all: §3.C shows the code is already in the shape to carry a second
backend behind the existing protocols, selected per instance, with the
CLI backend staying the default and serving every repository shape it
serves today. That is the recommended form of C: **an experimental
libgit2 backend beside the CLI one**, promoted to default only when it
has run in the field and libgit2 2.0 has closed the format gaps.

A + B remains the path for the CLI backend either way, because it stays
the default and keeps taking field bugs meanwhile; but with a second
backend planned, A's typed layer should be sized to what the CLI backend
needs, not built as the long-term abstraction.

In order:

1. **D first, in the same cycle as the open class-1 fixes.** The
   adversarial-name suite and the two-clone pipeline scenarios turn the
   September bugs into regression tests, and they are the acceptance suite
   for whichever backend follows. Under C the stubs cannot survive anyway.
2. **Open the three seams in §3.C** (injected push, `accepts_batch` on
   the protocol, backend selection in `_resolve_git`) and add the
   `git_backend` config field through the `config-contract` chain. Small,
   behaviour-preserving, and it is what lets the two backends coexist.
3. **The libgit2 backend as an experimental `VersionedStore`**, developed
   against D's suite parametrised over both backends, with the startup
   probe for the three unopenable shapes and the `git lfs clean` write
   path in its first version. An ADR records the perf envelope
   (§2.5.12), the merge-engine divergence (§2.5.13), the SSH-remote
   question (§4.3) and the promotion criteria.
4. **A, then B, on the CLI backend** as the default keeps taking field
   bugs: A as a per-module refactor series (`_run.py` grows the typed
   surface; `query.py` onto it and the single lineage walk; `strategy.py`'s
   staging and commit helpers onto scoped-only primitives; `conflict.py`
   last), then B as one PR with the git floor (2.38) declared. #1290 is
   fixed by the first step rather than on its own. How much of A is worth
   doing depends on how fast the libgit2 backend earns the default.

Longer term, class 3 is a symptom of git being asked to define note identity.
The decoupling study (PR #1152, §2.2) already frames versioning as a
`VersionedStore` seam; a stable note id owned by the vault would make lineage
a vault question with git as one witness. Out of scope here, noted so the
single lineage walk in A is understood as containment, not resolution.

## 6. Next artifacts

- `quality-attribute-scenario-writer` for the pull pipeline: the crash-mid-
  reconcile and dirty-tree scenarios in §3.D as measurable scenarios.
- `adr-writer` for the C versus A + B decision (§5), with §2.4 and §2.5
  as its evidence sections.
- Issues, per [`authoring-issues-prs`](../../../.agents/skills/authoring-issues-prs/SKILL.md):
  one for D (test fidelity, citing the stub sites), one decision issue for
  the ADR, and then either an epic for A with a child per module plus one
  for B, or one epic for C.

## Appendix: the lineage walk used in §2.5

The walk that returned the same commits as `git log --follow
--find-renames=30` on every scenario tried (linear, rename-and-rewrite,
name reuse, glob names, a merge carrying a side-branch edit), and stops at
the note's birth. A whole-tree diff runs only for a commit in which the
tracked entry is absent from the first parent.

```python
from pygit2.enums import DeltaStatus, SortMode

def _entry(tree, path):
    try:
        return tree[path].id
    except KeyError:
        return None

def lineage(repo, path, threshold=30):
    tracked, out = path, []
    for c in repo.walk(repo.head.target, SortMode.TOPOLOGICAL):
        cur = _entry(c.tree, tracked)
        if cur is None:
            continue                      # renamed away in a newer commit
        if not c.parents:
            out.append((c.id, "A", tracked))
            break
        prevs = [_entry(p.tree, tracked) for p in c.parents]
        if cur in prevs:
            continue                      # TREESAME to a parent: untouched here, or a merge
        prev = prevs[0]
        if prev is not None:
            out.append((c.id, "M", tracked))
            continue
        d = c.parents[0].tree.diff_to_tree(c.tree)
        d.find_similar(rename_threshold=threshold)
        hit = next((x for x in d.deltas if x.new_file.path == tracked), None)
        if hit is not None and hit.status == DeltaStatus.RENAMED:
            out.append((c.id, f"R{hit.similarity:03d}", hit.old_file.path))
            tracked = hit.old_file.path
        else:
            out.append((c.id, "A", tracked))   # birth: never cross into a reused name
            break
    return out
```

Without the TREESAME line the merge commit is reported a second time for
the side-branch edit it carries; with it the walk matches `git log
--follow` (which is not `--first-parent`) on the merge scenario.
